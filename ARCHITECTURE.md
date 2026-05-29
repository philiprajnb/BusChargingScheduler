# Architecture

## Problem Statement

Schedule 20 electric buses across a fixed 540 km route (Bengaluru → A → B → C → D → Kochi) with 4 single-charger stations. Buses must charge at least twice. Contention at chargers is resolved by a tunable weighted priority score.

---

## Scheduling Approach

### Why event-driven simulation (not LP/OR)?

Linear programming would find a globally optimal schedule but would require re-solving from scratch every time a new constraint (priority buses, driver shifts, ToD pricing) is added. It's also harder to explain to non-technical stakeholders.

An event-driven simulation with pluggable scoring functions:
- Mirrors how dispatch systems actually work in practice
- Makes each rule independently testable
- Can be extended by adding a function, not restructuring a constraint matrix
- Is defensible in an interview: the simulation runs in O(N·S·log N) where N=buses and S=stations

### Charging strategy: "charge only when forced"

A bus charges at a stop only if skipping it would leave insufficient range to reach the next chargeable stop or terminus. This is computed in `_must_charge()` in `engine.py`.

**Tradeoff:** This minimises total charging stops but means buses may arrive at a station with almost-empty batteries and high urgency. An alternative is "charge whenever possible" (maximise buffer). For this problem the forced-charge strategy is correct because the range constraint is clear, stations have queues, and early charging would just shift contention upstream.

### Queue priority: composite weighted score

When multiple buses are waiting for the single charger at a station, they are ranked by:

```
score = w_individual × individual_cost(bus)
      + w_operator   × operator_cost(bus)
      + w_overall    × overall_cost(bus)
```

Lower score = higher priority. Weights come from the scenario JSON file — there is no hardcoded weight anywhere in the codebase.

The queue is **re-scored before each slot is assigned** so that a bus that was already waited upon influences the operator fairness calculation in real time.

---

## Data Model

### Scenario JSON schema (annotated)

```json
{
  "name": "...",
  "description": "...",
  "route": {
    "stops": [
      { "name": "Bengaluru", "distance_from_prev": 0 },
      { "name": "A",         "distance_from_prev": 100 },
      ...
    ]
  },
  "network": {
    "stations": {
      "A": { "charger_count": 1 },
      "B": { "charger_count": 1 },
      ...
    }
  },
  "constraints": {
    "max_range_km": 240,
    "charge_time_min": 25,
    "speed_kmh": 60
  },
  "weights": {
    "individual": 1.0,
    "operator": 1.0,
    "overall": 1.0
  },
  "buses": [
    { "id": "BK01", "operator": "KPN", "direction": "BK", "departure_min": 1140 },
    ...
  ]
}
```

`distance_from_prev` on the first stop is always 0 (no predecessor). The engine builds a **bidirectional edge-distance map** at load time so reversed-direction (KB) routes correctly compute each leg's distance without relying on the raw field value.

---

## Anticipated Future Changes

The data model and engine are designed to accommodate these without structural rewrites:

| Change | How it's handled |
|--------|-----------------|
| **More buses** | Just add entries to `buses[]` in the JSON |
| **More stations** | Add stops to `route.stops[]` and entries to `network.stations{}` |
| **Multiple chargers per station** | Change `charger_count` in the JSON — the engine uses a min-heap of size `charger_count` per station |
| **New route** | Create a new scenario JSON with a different `route.stops` array |
| **Multiple routes sharing stations** | Model each route as its own scenario JSON; stations with shared chargers would need a shared-charger reservation layer (next natural abstraction) |
| **Priority buses (hard constraint)** | Add `"priority": true` to a bus entry; add a pre-filter in `_serve_station` that always picks priority buses first before scoring |
| **Different battery sizes per bus** | Add `"max_range_km"` to each bus entry in `buses[]`; override `constraints.max_range_km` per bus in `_BusState` |
| **Variable charging speeds** | Add `"charge_rate_kwh"` per station and `"battery_kwh"` per bus; compute `charge_time_min` dynamically in the engine instead of using the fixed constraint |
| **Time-of-day electricity costs** | Add a `cost_schedule` array to each station in the JSON; add an `energy_cost` soft rule to `rules.py` |
| **Driver shift constraints** | Add `"driver_shift_end_min"` to each bus; add a `shift_pressure` rule that penalises buses close to shift end (promotes faster path) |
| **New operators** | Just use a new string in `"operator"` field — no code change needed |
| **New scheduling rules** | Add a function to `rules.py`, a field to `Weights`, and wire it into `score()` — engine untouched |

---

## Key Design Decisions

### One weights location

`Weights` is a dataclass in `models.py` loaded from the scenario JSON. The `score()` function in `rules.py` is the only place weights are applied. Changing a weight requires editing one JSON key.

### Simulator as a class

`engine.py` exposes a `Simulator` class rather than a set of functions with shared mutable state passed as arguments. This makes the simulation:
- Easier to test in isolation (instantiate with a scenario, call `.run()`)
- Ready for future parallelism (multiple Simulator instances per scenario variant)
- Readable — all state is `self.*`

### Models are pure data, engine does simulation

`models.py` contains only dataclasses and one view-helper method (`route_stops`). No scheduling logic lives in models. This separation makes it easy to swap the engine (e.g., replace with an LP solver) without touching data loading or the UI.

### Scenario files are the single source of truth

There are no hardcoded route distances, stop names, operators, or weights anywhere in the Python code. Running a new scenario = creating a new JSON file.
