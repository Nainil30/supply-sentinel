"""Overview: snapshot facts, KPI row, top 10 exceptions by severity."""

from pathlib import Path

import pandas as pd
import streamlit as st

from supply_sentinel.app.components import common

DETAIL_PAGE = Path(__file__).with_name("detail.py")  # absolute: see streamlit_app.py

svc = common.page_header("Overview", "Weekly supply sweep for Orbis Networks (synthetic data).")
ov = svc.overview()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Snapshot", ov.snapshot_id)
c2.metric("As of", ov.as_of)
c3.metric("Profile", ov.profile)
c4.metric("Data age", f"{ov.age_days} days")
if ov.stale:
    st.warning(
        f"This snapshot is {ov.age_days} days old, past the staleness limit. Refresh before acting."
    )
else:
    st.success("Snapshot is fresh.")

st.subheader("Key numbers")
k = st.columns(5)
k[0].metric(
    "Open exceptions",
    sum(ov.by_type.values()),
    help=", ".join(f"{t}: {n}" for t, n in sorted(ov.by_type.items())),
)
k[1].metric("Revenue at risk (shortages)", common.usd(ov.revenue_at_risk_usd))
k[2].metric("Pending LTB spend", common.usd(ov.pending_ltb_spend_usd))
k[3].metric("E&O exposure", common.usd(ov.eo_exposure_usd))
k[4].metric("Pending approvals", ov.pending_approvals)

if ov.by_type:
    st.caption(
        "Open exceptions by type: " + ", ".join(f"{t} {n}" for t, n in sorted(ov.by_type.items()))
    )
if ov.run_id is None:
    st.info("No agent run yet. Open the Run page to start one in replay mode.")

st.subheader("Top 10 exceptions by severity")
rows = svc.exceptions(ov.run_id)[:10]
frame = pd.DataFrame(
    [
        {"id": r.exception_id, "type": r.type, "severity": r.severity, "part": r.part_id,
         "impact $": r.impact_usd, "agent action": r.agent_action, "status": r.status}
        for r in rows
    ]
)  # fmt: skip
if frame.empty:
    st.write("No open exceptions.")
else:
    event = st.dataframe(
        frame, hide_index=True, use_container_width=True, on_select="rerun",
        selection_mode="single-row", key="overview_table",
    )  # fmt: skip
    picked = event.selection.rows
    if picked:
        st.session_state["exception_id"] = rows[picked[0]].exception_id
        st.page_link(DETAIL_PAGE, label=f"Open {rows[picked[0]].exception_id} in Exception detail")
