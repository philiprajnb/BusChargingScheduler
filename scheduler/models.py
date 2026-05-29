"""
Data models for the Bus Charging Scheduler.

Design goals:
- Fully data-driven: scenarios are loaded from JSON, nothing hardcoded
- Forward-compatible: charger_count per station, arbitrary routes, variable battery sizes
- Immutable inputs, mutable simulation state kept separate
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class Stop:
    """A point on the route. May or may not have a charger."""
    name: str
    distance_from_prev: float  # km from previous stop (0 for origin)

    @property
    def has_charger(self) -> bool:
        return self.distance_from_prev > 0  # origin/terminus never charge


@dataclass
class Station:
    """A charging station at a named stop."""
    stop_name: str
    charger_count: int  # supports future multi-charger stations


@dataclass
class Route:
    """Ordered sequence of stops. Direction is the canonical order."""
    stops: list[Stop]

    def stop_names(self) -> list[str]:
        return [s.name for s in self.stops]


@dataclass
class Constraints:
    max_range_km: float       # battery range on full charge
    charge_time_min: float    # always charges to full in this fixed duration
    speed_kmh: float          # constant speed assumption


@dataclass
class Weights:
    """
    Tunable soft-rule weights. Lives here so there is exactly ONE place to change them.
    individual: minimize per-bus wait time
    operator:   fairness across operator fleets
    overall:    minimize total network time
    """
    individual: float = 1.0
    operator: float = 1.0
    overall: float = 1.0


@dataclass
class Bus:
    id: str
    operator: str
    direction: str        # "BK" (Bengaluru→Kochi) or "KB" (Kochi→Bengaluru)
    departure_min: float  # minutes from reference time 00:00


@dataclass
class Scenario:
    name: str
    description: str
    route: Route
    stations: dict[str, Station]   # keyed by stop_name
    constraints: Constraints
    weights: Weights
    buses: list[Bus]

    @staticmethod
    def from_file(path: str) -> "Scenario":
        with open(path) as f:
            data = json.load(f)

        stops = [Stop(**s) for s in data["route"]["stops"]]
        route = Route(stops=stops)

        stations = {
            name: Station(stop_name=name, **cfg)
            for name, cfg in data["network"]["stations"].items()
        }

        constraints = Constraints(**data["constraints"])
        weights = Weights(**data["weights"])
        buses = [Bus(**b) for b in data["buses"]]

        return Scenario(
            name=data["name"],
            description=data["description"],
            route=route,
            stations=stations,
            constraints=constraints,
            weights=weights,
            buses=buses,
        )


# --- Simulation output models ---

@dataclass
class ChargeEvent:
    """One charging stop for a bus."""
    stop_name: str
    arrival_min: float    # absolute minutes from reference time
    wait_min: float       # time queued before charger available
    charge_start_min: float
    charge_end_min: float

    @property
    def total_delay_min(self) -> float:
        return self.wait_min


@dataclass
class BusResult:
    """Full simulation output for a single bus."""
    bus: Bus
    charge_events: list[ChargeEvent]
    final_arrival_min: float   # arrival at terminal
    total_wait_min: float      # sum of all waits

    def route_stops(self, route: Route, constraints: Constraints) -> list[dict]:
        """
        Returns a flat list of rows for the per-bus timetable view.
        Each row: stop, arrival, wait, charge_end, departure, km_since_last_charge.

        Uses a bidirectional edge-distance map so KB buses get correct distances
        (Bengaluru.distance_from_prev=0 in the data, but the edge A↔Bengaluru is 100km).
        """
        # Build bidirectional edge distances from the canonical forward route
        fwd = route.stops
        edge_dist: dict[tuple[str, str], float] = {}
        for i in range(len(fwd) - 1):
            a, b = fwd[i].name, fwd[i + 1].name
            d = fwd[i + 1].distance_from_prev
            edge_dist[(a, b)] = d
            edge_dist[(b, a)] = d

        stops = fwd if self.bus.direction == "BK" else list(reversed(fwd))
        speed = constraints.speed_kmh
        charge_map = {ce.stop_name: ce for ce in self.charge_events}

        rows = []
        km_since = 0.0

        for i, stop in enumerate(stops):
            if i == 0:
                rows.append({
                    "stop": stop.name,
                    "arrival": None,
                    "wait_min": None,
                    "charge_end": None,
                    "departure": self.bus.departure_min,
                    "km_since_last_charge": 0,
                })
                continue

            prev_stop = stops[i - 1].name
            dist = edge_dist[(prev_stop, stop.name)]
            travel_time = dist / speed * 60
            arrival = rows[-1]["departure"] + travel_time
            km_since += dist

            if stop.name in charge_map:
                ce = charge_map[stop.name]
                rows.append({
                    "stop": stop.name,
                    "arrival": ce.arrival_min,
                    "wait_min": ce.wait_min,
                    "charge_end": ce.charge_end_min,
                    "departure": ce.charge_end_min,
                    "km_since_last_charge": round(km_since, 1),
                })
                km_since = 0.0
            else:
                rows.append({
                    "stop": stop.name,
                    "arrival": arrival,
                    "wait_min": 0,
                    "charge_end": None,
                    "departure": arrival,
                    "km_since_last_charge": round(km_since, 1),
                })

        return rows
