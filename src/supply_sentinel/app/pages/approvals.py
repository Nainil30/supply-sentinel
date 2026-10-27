"""Approvals: pending recommendations grouped by tier. Approve, approve with edit, reject."""

import streamlit as st

from supply_sentinel.app.components import common
from supply_sentinel.app.services import ServiceError

svc = common.page_header(
    "Approvals", "Only a human can approve. Decisions apply when you resume the run."
)
run_id = svc.current_run_id()
if run_id is None:
    st.info("No agent run yet. Start one on the Run page.")
    st.stop()

recs = {r.rec_id: r for r in svc.recommendations(run_id)}
pending = svc.pending(run_id)
staged = svc.staged(run_id)
st.caption(f"Run {run_id}. {len(pending)} waiting, {len(staged)} decided but not applied.")

if not pending:
    st.success("Nothing is waiting for approval in this run.")

TIER_LABEL = {2: " (approve with a reason)", 1: " (approve)", 0: " (held for review)"}
for tier in (2, 1, 0):
    group = [p for p in pending if p["tier"] == tier]
    if not group:
        continue
    st.subheader(f"Tier {tier}{TIER_LABEL[tier]}")
    for p in group:
        rec = recs[p["rec_id"]]
        second = svc.needs_second_approver(rec)
        decided = staged.get(p["rec_id"])
        title = f"{p['rec_id']}  {p['part_id']}  {p['action']} {common.qty(p['quantity'])}"
        if decided:
            title += f"   [{decided.decision}]"
        with st.expander(title, expanded=decided is None):
            m = st.columns(4)
            m[0].metric("Spend", common.usd(svc.spend_usd(rec)))
            m[1].metric("Source", p["source"])
            m[2].metric("Grounding", p["grounding"])
            m[3].metric("Flags", ", ".join(p["flags"]) or "none")
            st.write(rec.root_cause_explanation)
            if second:
                limit = svc.policy.second_approver_over_usd
                st.warning(f"Spend is over ${limit:,.0f}: a second approver is required.")
            reason = st.text_input("Reason", key=f"reason_{p['rec_id']}")
            qty = st.number_input(
                "Edited quantity",
                min_value=0.0,
                value=float(p["quantity"] or 0.0),
                step=1.0,
                key=f"qty_{p['rec_id']}",
                disabled=p["quantity"] is None,
            )
            second_ok = (
                st.checkbox("Second approver confirms", key=f"second_{p['rec_id']}")
                if second
                else False
            )
            buttons = zip(
                st.columns(3),
                ("Approve", "Approve with edit", "Reject"),
                ("APPROVED", "EDITED", "REJECTED"),
                strict=True,
            )
            for col, label, decision in buttons:
                if col.button(label, key=f"{decision}_{p['rec_id']}"):
                    try:
                        edited = qty if decision == "EDITED" else None
                        svc.stage(run_id, p["rec_id"], decision, reason, edited, second_ok)
                        st.rerun()
                    except ServiceError as exc:
                        st.error(str(exc))

if pending:
    st.divider()
    if st.button("Apply decisions and finish the run", type="primary", disabled=not staged):
        outcomes = svc.resume(run_id)
        st.success(", ".join(f"{o['rec_id']}: {o['status']}" for o in outcomes) or "Run finished.")
    if not staged:
        st.caption("Decide at least one recommendation to enable this.")
