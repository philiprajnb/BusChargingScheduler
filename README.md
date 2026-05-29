# Bus Charging Scheduler

A scheduling system for electric buses on the Bengaluru → Kochi route, built with Python + Streamlit.

**Live app:** https://buschargingscheduler.streamlit.app/
**GitHub:** https://github.com/philiprajnb-labs/BusChargingScheduler

---

## Running Locally

```bash
# 1. Clone and enter the repo
git clone https://github.com/philiprajnb-labs/BusChargingScheduler.git
cd BusChargingScheduler

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
streamlit run app.py
```

Open http://localhost:8501. Use the dropdown to pick a scenario.

---

## How to Change Weights

Weights live in each scenario's JSON file under the `"weights"` key:

```json
"weights": {
  "individual": 1.0,
  "operator": 2.0,
  "overall": 1.0
}
```

- **individual** — penalizes buses that have already accrued long wait times (reduces unfairness to late buses)
- **operator** — promotes fairness across operator fleets (raises priority of operators who are running behind fleet average)
- **overall** — prefers buses that arrived earliest (FCFS pressure)

Change any value and re-run the app. No code changes needed.

---

## How to Add a New Scheduling Rule

1. Open `scheduler/rules.py`.

2. Write a function with this signature:
   ```python
   def my_rule(ctx: ScoringContext) -> float:
       # lower return value = higher priority
       return ...
   ```
   `ScoringContext` carries: `bus`, `arrival_min`, `accumulated_wait_min`, `operator_avg_wait`, `global_avg_wait`.

3. Add a weight field to `scheduler/models.py` → `Weights` dataclass:
   ```python
   my_rule: float = 1.0
   ```

4. Wire it into the `score()` function in `scheduler/rules.py`:
   ```python
   def score(ctx, weights):
       return (
           weights.individual * individual_cost(ctx)
           + weights.operator  * operator_cost(ctx)
           + weights.overall   * overall_cost(ctx)
           + weights.my_rule   * my_rule(ctx)      # add this
       )
   ```

5. Add `"my_rule": 1.0` to the `"weights"` block of any scenario JSON file.

That's it — no engine changes needed.

---

## Assumptions

| Topic | Assumption |
|-------|-----------|
| Speed | Constant 60 km/h for all buses |
| Charging | Always charges to 100% in exactly 25 min |
| Charge necessity | "Charge only when forced": a bus charges at a stop only if skipping it would leave insufficient range to reach the next chargeable stop or terminus |
| Range rule | A bus starts with a full battery (240 km) |
| Station endpoints | Bengaluru and Kochi have no chargers; charging happens at A, B, C, D only |
| Queue re-scoring | The charger queue is re-scored with the latest fleet stats before each slot is assigned, so weights reflect real-time fairness state |
| Tie-breaking | Equal scores → earlier arrival wins (FCFS) |

---

## Project Structure

```
app.py                  Streamlit UI entry point
scheduler/
  models.py             Data models: Route, Bus, Scenario, BusResult, etc.
  engine.py             Event-driven simulation engine
  rules.py              Soft-rule scoring functions
scenarios/
  scenario_1.json       Even spacing (baseline)
  scenario_2.json       Bunched start
  scenario_3.json       Asymmetric load
  scenario_4.json       Operator-heavy (KPN), operator weight=2.0
  scenario_5.json       Worst-case convergence
ARCHITECTURE.md         Design decisions, data model, extensibility
```
