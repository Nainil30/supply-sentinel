"""Exception queue: sortable, filterable table."""

import pandas as pd
import streamlit as st

from supply_sentinel.app.components import common

svc = common.page_header("Exception queue", "Click a column header to sort. Select a row to open it.")
run_id = svc.current_run_id()
rows = svc.exceptions(run_id)

types = sorted({r.type for r in rows})
sevs = sorted({r.severity for r in rows})
plats = sorted({p for r in rows for p in r.platforms})
statuses = sorted({r.status for r in rows})
f1, f2, f3, f4 = st.columns(4)
sel_types = f1.multiselect("Type", types)
sel_sevs = f2.multiselect("Severity", sevs)
sel_plats = f3.multiselect("Platform", plats)
sel_status = f4.multiselect("Status", statuses)

shown = [
    r for r in rows
    if (not sel_types or r.type in sel_types)
    and (not sel_sevs or r.severity in sel_sevs)
    and (not sel_plats or set(r.platforms) & set(sel_plats))
    and (not sel_status or r.status in sel_status)
]  # fmt: skip
frame = pd.DataFrame(
    [
        {"id": r.exception_id, "type": r.type, "severity": r.severity, "part": r.part_id,
         "platforms": ", ".join(r.platforms), "weeks to impact": r.weeks_to_impact,
         "impact $": r.impact_usd, "baseline action": r.baseline_action,
         "agent action": r.agent_action, "status": r.status}
        for r in shown
    ]
)  # fmt: skip
st.caption(f"{len(shown)} of {len(rows)} exceptions.")
if frame.empty:
    st.write("Nothing matches these filters.")
else:
    event = st.dataframe(
        frame, hide_index=True, use_container_width=True, on_select="rerun",
        selection_mode="single-row", key="queue_table",
    )  # fmt: skip
    if event.selection.rows:
        chosen = shown[event.selection.rows[0]].exception_id
        st.session_state["exception_id"] = chosen
        st.page_link("pages/detail.py", label=f"Open {chosen} in Exception detail")
