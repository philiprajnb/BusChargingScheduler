"""
Event-driven simulation engine for bus charging scheduling.

Approach: greedy simulation advancing in time. At each charging station,
buses queue up and are served one at a time. Priority within the queue is
determined by the composite soft-rule score (lower = served first).

Assumptions documented here:
- Speed is constant at constraints.speed_kmh for all buses
- Charging always restores full battery in exactly constraints.charge_time_min
- Charging strategy: "charge only when necessary" — a bus charges at a stop only if
  skipping it would leave insufficient range to reach the next charging stop or terminus.
  This is the most natural greedy strategy and produces a valid minimum-charge schedule.
- Buses that don't need to charge at a stop pass through instantly.
- All times are in minutes from 00:00 on the departure day.
- Events are plain 3-tuples (time, bus_idx, stop_name) for heap compatibility.
"""

from __future__ import annotations
import heapq
from collections import defaultdict

from .models import BusResult, ChargeEvent, Constraints, Route, Scenario
from .rules import ScoringContext, score


class _BusState:
    """Mutable simulation state for a single bus."""

    def __init__(self, idx: int, bus, route_stops: list[str], constraints: Constraints):
        self.idx = idx
        self.bus = bus
        self.route_stops = route_stops
        self.constraints = constraints

        self.stop_idx = 0          # index into route_stops
        self.current_time = bus.departure_min
        self.km_since_last_charge = 0.0
        self.accumulated_wait = 0.0
        self.charge_events: list[ChargeEvent] = []

    @property
    def current_stop(self) -> str:
        return self.route_stops[self.stop_idx]

    def is_done(self) -> bool:
        return self.stop_idx >= len(self.route_stops) - 1


def _build_lookups(route: Route, constraints: Constraints, direction: str):
    """
    Returns (travel_times, distances) dicts keyed by (from_stop, to_stop)
    in the travel direction of `direction`.

    distance_from_prev=0 on the origin stop means we must derive edge distances
    from the forward route and apply them symmetrically for the reverse direction.
    """
    fwd = route.stops  # original order: Bengaluru, A, B, C, D, Kochi
    # Build canonical edge distance map (bidirectional)
    edge_dist: dict[tuple[str, str], float] = {}
    for i in range(len(fwd) - 1):
        a, b = fwd[i].name, fwd[i + 1].name
        d = fwd[i + 1].distance_from_prev
        edge_dist[(a, b)] = d
        edge_dist[(b, a)] = d  # reverse edge has same distance

    stops = fwd if direction == "BK" else list(reversed(fwd))
    travel = {}
    dists = {}
    for i in range(len(stops) - 1):
        a, b = stops[i].name, stops[i + 1].name
        d = edge_dist[(a, b)]
        travel[(a, b)] = d / constraints.speed_kmh * 60
        dists[(a, b)] = d
    return travel, dists


def _must_charge(state: _BusState, at_stop: str, dists: dict, stops: list[str]) -> bool:
    """
    True if the bus MUST charge at `at_stop` to avoid exceeding max range.
    Looks ahead: if there is no charger between here and the next stop that has one
    (or the terminus), and we can't make it, we must charge now.
    """
    idx = stops.index(at_stop)
    max_range = state.constraints.max_range_km
    km_remaining = max_range - state.km_since_last_charge

    # Accumulate distance to each upcoming stop
    km_ahead = 0.0
    for j in range(idx, len(stops) - 1):
        km_ahead += dists.get((stops[j], stops[j + 1]), 0.0)
        if km_remaining < km_ahead:
            return True
        # Once we hit the next chargeable intermediate stop, we can defer to there
        next_stop = stops[j + 1]
        if j + 1 < len(stops) - 1:  # not terminus
            break  # re-evaluate at that stop
    return False


class Simulator:
    """
    Self-contained event-driven simulation.
    All shared mutable state lives in this object, avoiding messy function signatures.
    """

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.constraints = scenario.constraints
        self.weights = scenario.weights
        self.stations = scenario.stations

        fwd = scenario.route.stop_names()
        rev = list(reversed(fwd))

        # Per-direction lookups
        self.travel: dict[str, dict] = {}
        self.dists: dict[str, dict] = {}
        for direction in ("BK", "KB"):
            t, d = _build_lookups(scenario.route, self.constraints, direction)
            self.travel[direction] = t
            self.dists[direction] = d

        # Bus states
        self.states: list[_BusState] = []
        for i, bus in enumerate(scenario.buses):
            stops = fwd if bus.direction == "BK" else rev
            self.states.append(_BusState(i, bus, stops, self.constraints))

        # Charger availability per station: min-heap of free-at times
        self.charger_free: dict[str, list[float]] = {}
        for name, station in self.stations.items():
            heap = [0.0] * station.charger_count
            heapq.heapify(heap)
            self.charger_free[name] = heap

        # Per-station waiting queues: heap of (score, arrival_time, bus_idx)
        self.station_queues: dict[str, list] = defaultdict(list)

        # Operator wait tracking for fair scoring
        self.op_total_wait: dict[str, float] = defaultdict(float)
        self.op_count: dict[str, int] = defaultdict(int)

        # Results dict, filled as buses finish
        self.results: dict[int, BusResult] = {}

        # Global event heap: (time, bus_idx, stop_name)
        self.events: list[tuple] = []
        for state in self.states:
            heapq.heappush(self.events, (state.current_time, state.idx, state.current_stop))

    def _op_avg(self) -> dict[str, float]:
        return {
            op: self.op_total_wait[op] / self.op_count[op]
            for op in self.op_total_wait if self.op_count[op] > 0
        }

    def _global_avg(self) -> float:
        total = sum(self.op_total_wait.values())
        count = sum(self.op_count.values())
        return total / count if count > 0 else 0.0

    def _score_bus(self, state: _BusState, arrival_min: float) -> float:
        ctx = ScoringContext(
            bus=state.bus,
            arrival_min=arrival_min,
            accumulated_wait_min=state.accumulated_wait,
            operator_avg_wait=self._op_avg(),
            global_avg_wait=self._global_avg(),
        )
        return score(ctx, self.weights)

    def _advance(self, state: _BusState, from_stop: str, depart_time: float) -> None:
        """Push the next arrival event for this bus."""
        idx = state.route_stops.index(from_stop)
        if idx + 1 >= len(state.route_stops):
            self.results[state.idx] = BusResult(
                bus=state.bus,
                charge_events=state.charge_events,
                final_arrival_min=depart_time,
                total_wait_min=state.accumulated_wait,
            )
            return
        next_stop = state.route_stops[idx + 1]
        dist = self.dists[state.bus.direction][(from_stop, next_stop)]
        tt = self.travel[state.bus.direction][(from_stop, next_stop)]
        state.km_since_last_charge += dist
        state.stop_idx = idx + 1
        state.current_time = depart_time + tt
        heapq.heappush(self.events, (state.current_time, state.idx, next_stop))

    def _serve_station(self, stop: str) -> None:
        """
        Serve as many queued buses as there are free chargers.
        Re-scores the queue before each pick to use latest fleet stats.
        """
        charger_heap = self.charger_free[stop]
        queue = self.station_queues[stop]

        while charger_heap and queue:
            earliest_charger_free = charger_heap[0]

            # Re-score all waiting buses before selecting the best
            candidates = []
            while queue:
                _, arr, bidx = heapq.heappop(queue)
                s = self.states[bidx]
                sc = self._score_bus(s, arr)
                candidates.append((sc, arr, bidx))
            candidates.sort()

            # Push all back except the winner
            best = candidates[0]
            for item in candidates[1:]:
                heapq.heappush(queue, item)

            _, arr_time, bus_idx = best
            state = self.states[bus_idx]

            charge_start = max(earliest_charger_free, arr_time)
            wait = charge_start - arr_time
            charge_end = charge_start + self.constraints.charge_time_min

            # Update charger heap
            heapq.heapreplace(charger_heap, charge_end)

            # Update bus state
            state.km_since_last_charge = 0.0
            state.accumulated_wait += wait
            state.charge_events.append(ChargeEvent(
                stop_name=stop,
                arrival_min=arr_time,
                wait_min=wait,
                charge_start_min=charge_start,
                charge_end_min=charge_end,
            ))

            # Update fleet stats
            self.op_total_wait[state.bus.operator] += wait
            self.op_count[state.bus.operator] += 1

            # Schedule bus to next stop
            self._advance(state, stop, charge_end)

    def run(self) -> list[BusResult]:
        """Execute the full simulation. Returns results in original bus order."""
        while self.events:
            time, bus_idx, stop = heapq.heappop(self.events)
            state = self.states[bus_idx]

            # Skip stale events (bus already advanced past this stop)
            if state.current_stop != stop:
                continue

            if state.is_done():
                self.results[bus_idx] = BusResult(
                    bus=state.bus,
                    charge_events=state.charge_events,
                    final_arrival_min=time,
                    total_wait_min=state.accumulated_wait,
                )
                continue

            stops = state.route_stops
            dsts = self.dists[state.bus.direction]
            needs_charge = (stop in self.stations) and _must_charge(state, stop, dsts, stops)

            if not needs_charge:
                self._advance(state, stop, time)
                continue

            # Enqueue at station
            sc = self._score_bus(state, time)
            heapq.heappush(self.station_queues[stop], (sc, time, bus_idx))
            self._serve_station(stop)

        return [self.results[i] for i in range(len(self.states))]


def simulate(scenario: Scenario) -> list[BusResult]:
    """Public API: run the simulation for a scenario and return per-bus results."""
    return Simulator(scenario).run()
