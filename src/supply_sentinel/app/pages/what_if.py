"""What-if: change POs, demand, expedites or holds and compare PAB before and after."""

import pandas as pd
import streamlit as st

from supply_sentinel.app.components import common
from supply_sentinel.app.services import ServiceError

svc = common.page_header("What-if", "Runs the engine's simulate_scenario. Nothing is saved.")
parts = svc.part_ids()
wanted = st.session_state.get("exception_id")
default_part = next((r.part_id for r in svc.exceptions() if r.exception_id == wanted), parts[0])
part = st.selectbox("Part", parts, index=parts.index(default_part))
ev = svc.evidence(part)

st.subheader("Controls")
slips: dict[str, int] = {}
if ev.pos.pos:
    st.caption("Slip a PO (weeks)")
    cols = st.columns(min(4, len(ev.pos.pos)))
    for i, po in enumerate(ev.pos.pos):
        slips[po.po_id] = cols[i % len(cols)].number_input(
            po.po_id, -8, 12, 0, key=f"slip_{part}_{po.po_id}"
        )
demand: dict[str, float] = {}
if ev.where_used.platforms:
    st.caption("Change demand (% per platform)")
    cols = st.columns(min(4, len(ev.where_used.platforms)))
    for i, pl in enumerate(ev.where_used.platforms):
        demand[pl.platform_id] = cols[i % len(cols)].number_input(
            pl.platform_id, -90.0, 200.0, 0.0, step=5.0, key=f"dem_{part}_{pl.platform_id}"
        )
e1, e2, e3 = st.columns(3)
exp_qty = e1.number_input("Expedite qty", 0, 1_000_000, 0, step=10)
exp_week = e2.number_input("Expedite to week", 0, 25, 0)
hold = e3.number_input("Release hold units", 0, 1_000_000, 0, step=10)

try:
    res = svc.what_if(
        part,
        po_slip={k: v for k, v in slips.items() if v},
        demand_change_pct={k: v for k, v in demand.items() if v},
        expedite_qty=int(exp_qty) if exp_qty else None,
        expedite_to_week=int(exp_week) if exp_qty else None,
        release_hold_units=int(hold),
    )
except ServiceError as exc:
    st.error(str(exc))
    st.stop()

r = res.result
before = svc.baseline_pab(part)
n = min(len(before), len(r.pab))
frame = pd.DataFrame({"week": range(n), "before": before[:n], "after": r.pab[:n]})
m = st.columns(3)
m[0].metric(
    "First shortage week",
    "none" if r.first_shortage_week is None else r.first_shortage_week,
    help=f"Before: {r.baseline_first_shortage_week}",
)
m[1].metric(
    "Max shortfall (units)",
    f"{r.max_shortfall_units:,.0f}",
    delta=f"{r.shortfall_delta_units:,.0f}",
    delta_color="inverse",
)
m[2].metric("Max shortfall before", f"{r.baseline_max_shortfall_units:,.0f}")
st.line_chart(frame, x="week", y=["before", "after"], height=300)
st.dataframe(frame, hide_index=True)
