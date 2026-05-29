"""
Bus Charging Scheduler — Streamlit UI

Three views:
1. Scenario Input  — the raw bus list for the selected scenario
2. Per-Bus Timetable — each bus's journey: stops, arrival, wait, charge end, departure
3. Per-Station View — charge order at each intermediate station (A, B, C, D)

All times displayed as HH:MM (reference: 00:00 = midnight).
"""

import os
import glob as _glob
import streamlit as st
import pandas as pd

from scheduler.models import Scenario
from scheduler.engine import simulate

st.set_page_config(page_title="Bus Charging Scheduler", layout="wide")

# ── helpers ────────────────────────────────────────────────────────────────────

def fmt_time(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"

def fmt_wait(w: float | None) -> str:
    if w is None or w == 0:
        return "—"
    return f"{w:.0f} min"

@st.cache_data
def load_and_run(scenario_path: str):
    scenario = Scenario.from_file(scenario_path)
    results = simulate(scenario)
    return scenario, results

# ── scenario picker ────────────────────────────────────────────────────────────

scenario_files = sorted(_glob.glob(os.path.join("scenarios", "scenario_*.json")))
scenario_labels = {
    os.path.basename(p).replace(".json", "").replace("_", " ").title(): p
    for p in scenario_files
}

st.title("Bus Charging Scheduler")

col_sel, col_weights = st.columns([3, 2])
with col_sel:
    chosen_label = st.selectbox("Select scenario", list(scenario_labels.keys()))

scenario_path = scenario_labels[chosen_label]
scenario, results = load_and_run(scenario_path)

with col_weights:
    w = scenario.weights
    st.markdown(
        f"**Weights** — individual: `{w.individual}` · operator: `{w.operator}` · overall: `{w.overall}`"
    )

st.markdown(f"*{scenario.description}*")
st.divider()

# ── tabs ───────────────────────────────────────────────────────────────────────

tab1, tab2, tab3 = st.tabs(["📋 Scenario Input", "🚌 Per-Bus Timetable", "🔌 Per-Station View"])

# ── Tab 1: Scenario Input ──────────────────────────────────────────────────────

with tab1:
    st.subheader("Bus Schedule Input")

    rows = []
    for bus in scenario.buses:
        rows.append({
            "Bus ID": bus.id,
            "Operator": bus.operator,
            "Direction": "Bengaluru → Kochi" if bus.direction == "BK" else "Kochi → Bengaluru",
            "Departure": fmt_time(bus.departure_min),
        })
    df = pd.DataFrame(rows)
    bk_df = df[df["Direction"].str.startswith("Bengaluru")]
    kb_df = df[df["Direction"].str.startswith("Kochi")]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Bengaluru → Kochi**")
        st.dataframe(bk_df.drop(columns="Direction"), use_container_width=True, hide_index=True)
    with c2:
        st.markdown("**Kochi → Bengaluru**")
        st.dataframe(kb_df.drop(columns="Direction"), use_container_width=True, hide_index=True)

    st.subheader("Constraints & Weights")
    c = scenario.constraints
    conf_rows = [
        {"Parameter": "Max Range", "Value": f"{c.max_range_km} km"},
        {"Parameter": "Charge Time", "Value": f"{c.charge_time_min} min (always full)"},
        {"Parameter": "Speed", "Value": f"{c.speed_kmh} km/h (constant)"},
        {"Parameter": "Weight: individual", "Value": scenario.weights.individual},
        {"Parameter": "Weight: operator", "Value": scenario.weights.operator},
        {"Parameter": "Weight: overall", "Value": scenario.weights.overall},
    ]
    st.dataframe(pd.DataFrame(conf_rows), use_container_width=True, hide_index=True)

# ── Tab 2: Per-Bus Timetable ───────────────────────────────────────────────────

with tab2:
    st.subheader("Per-Bus Timetable")
    st.caption("Shows each stop in travel order. Stops in *italic* are pass-throughs (no charging).")

    for result in sorted(results, key=lambda r: r.bus.id):
        bus = result.bus
        label = f"{bus.id} — {bus.operator} ({'Bengaluru→Kochi' if bus.direction == 'BK' else 'Kochi→Bengaluru'})  |  Total wait: **{result.total_wait_min:.0f} min**  |  Arrived: **{fmt_time(result.final_arrival_min)}**"

        with st.expander(label):
            rows = result.route_stops(scenario.route, scenario.constraints)
            table = []
            for row in rows:
                charged = row["charge_end"] is not None
                table.append({
                    "Stop": row["stop"],
                    "Arrival": fmt_time(row["arrival"]),
                    "Wait": fmt_wait(row["wait_min"]) if charged else "—",
                    "Charge End": fmt_time(row["charge_end"]) if charged else "—",
                    "Departure": fmt_time(row["departure"]),
                    "Charged": "✅" if charged else "",
                    "km Since Last Charge": row["km_since_last_charge"] if row["km_since_last_charge"] else "—",
                })
            st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)

# ── Tab 3: Per-Station View ────────────────────────────────────────────────────

with tab3:
    st.subheader("Per-Station Charging Order")
    st.caption("Order in which buses were served at each charging station.")

    # Reconstruct station logs from results
    station_log: dict[str, list[dict]] = {}
    for result in results:
        for ce in result.charge_events:
            station_log.setdefault(ce.stop_name, []).append({
                "Bus ID": result.bus.id,
                "Operator": result.bus.operator,
                "Direction": result.bus.direction,
                "Arrival": fmt_time(ce.arrival_min),
                "Wait": fmt_wait(ce.wait_min),
                "Charge Start": fmt_time(ce.charge_start_min),
                "Charge End": fmt_time(ce.charge_end_min),
            })

    charging_stops = [s.name for s in scenario.route.stops if s.name in scenario.stations]

    cols = st.columns(len(charging_stops))
    for col, stop_name in zip(cols, charging_stops):
        with col:
            st.markdown(f"**Station {stop_name}**")
            logs = sorted(
                station_log.get(stop_name, []),
                key=lambda x: x["Charge Start"]
            )
            if logs:
                df = pd.DataFrame(logs)
                df.insert(0, "#", range(1, len(df) + 1))
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No buses charged here.")
