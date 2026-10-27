"""Exception detail: evidence tables, the recommendation card, the draft message, the trace."""

import pandas as pd
import streamlit as st

from supply_sentinel.app.components import common

svc = common.page_header("Exception detail")
run_id = svc.current_run_id()
rows = svc.exceptions(run_id)
if not rows:
    st.write("No open exceptions.")
    st.stop()

ids = [r.exception_id for r in rows]
wanted = st.session_state.get("exception_id")
index = ids.index(wanted) if wanted in ids else 0
exc_id = st.selectbox("Exception", ids, index=index)
st.session_state["exception_id"] = exc_id
row = next(r for r in rows if r.exception_id == exc_id)
ev = svc.evidence(row.part_id)

with st.container(border=True):
    h = st.columns(4)
    h[0].metric("Type", row.type)
    h[1].metric("Severity", row.severity)
    h[2].metric("Part", row.part_id, help=ev.position.description)
    h[3].metric("Impact", common.usd(row.impact_usd))
    st.caption(
        f"{ev.position.description}. Platforms: {', '.join(row.platforms) or '-'}. "
        f"Weeks to impact: {row.weeks_to_impact if row.weeks_to_impact is not None else '-'}. "
        f"Rules baseline: {row.baseline_action}."
    )

st.subheader("Evidence")
pab = pd.DataFrame(
    [
        {"week": w.week_idx, "gross requirement": w.gross_req, "receipts": w.receipts, "PAB": w.pab}
        for w in ev.position.weeks
    ]
)
left, right = st.columns([3, 2])
left.caption("Projected available balance by week (week 0 = as-of week)")
left.dataframe(pab, hide_index=True, height=240)
right.line_chart(pab, x="week", y="PAB", height=240)

t_po, t_commit, t_where, t_hist = st.tabs(
    ["Open POs", "Supplier commits", "Where used", "Decision history"]
)
with t_po:
    st.dataframe(pd.DataFrame([p.model_dump() for p in ev.pos.pos]), hide_index=True)
with t_commit:
    commits = pd.DataFrame(
        [{k: v for k, v in c.model_dump().items() if k != "note"} for c in ev.commits.rows]
    )
    st.dataframe(commits, hide_index=True)
    for c in ev.commits.rows:
        if c.note is not None:
            st.caption(f"Week {c.week_idx} supplier note")
            n = c.note
            common.untrusted_box(n.source, n.text, n.injection_suspected, n.matched_patterns)
with t_where:
    st.dataframe(pd.DataFrame([p.model_dump() for p in ev.where_used.platforms]), hide_index=True)
with t_hist:
    st.dataframe(pd.DataFrame([d.model_dump() for d in ev.history.decisions]), hide_index=True)

st.subheader("Recommendation")
recs = [r for r in svc.recommendations(run_id) if r.exception_id == exc_id]
if not recs:
    st.info("No agent recommendation for this exception yet. Start a run on the Run page.")
status = svc.decision_status(run_id)
for rec in recs:
    act = rec.primary_action
    with st.container(border=True):
        top = st.columns(5)
        top[0].metric("Primary action", act.action_type.value)
        top[1].metric("Quantity", common.qty(act.quantity))
        top[2].metric("Tier", rec.autonomy_tier)
        top[3].metric("Source", rec.source.value)
        top[4].metric("Status", status.get(rec.rec_id, "-"))
        if rec.secondary_action:
            sec = rec.secondary_action
            st.write(f"Secondary action: {sec.action_type.value} {common.qty(sec.quantity)}")
        st.write(f"**Root cause:** {rec.root_cause.value}. {rec.root_cause_explanation}")
        st.write(
            f"**Versus rules baseline:** {rec.baseline_agreement.value}"
            + (f". {rec.deviation_reason}" if rec.deviation_reason else "")
        )
        g = rec.grounding
        (st.success if g.status == "PASSED" else st.warning)(
            f"Grounding: {g.status} ({g.checked} values checked)"
            + (f"; ungrounded: {', '.join(g.ungrounded_values)}" if g.ungrounded_values else "")
        )
        for f in rec.security_flags:
            st.warning(f"{f.kind} on {f.field}: {f.detail}")
        if rec.evidence:
            st.caption("Cited numbers. Hover a value to see the tool call it came from.")
            cols = st.columns(min(4, len(rec.evidence)))
            for i, e in enumerate(rec.evidence):
                cols[i % len(cols)].metric(
                    e.field_path, str(e.value), help=f"tool call {e.tool_call_id}: {e.field_path}"
                )
        if rec.alternatives_considered:
            st.write("**Alternatives considered**")
            for a in rec.alternatives_considered:
                st.write(f"- {a.action_type.value}: {a.why_not}")
        if rec.risks:
            st.write("**Risks**")
            for risk in rec.risks:
                st.write(f"- {risk}")
        st.write(
            f"**Confidence:** {rec.confidence.value}. **Stakeholder:** {rec.stakeholder.value}"
        )
        st.caption("Draft message (copy with the icon at the top right)")
        st.code(rec.draft_message, language=None, wrap_lines=True)

        if act.quantity is not None and run_id is not None:
            new_qty = st.number_input(
                "Edit quantity (re-validated before approval)",
                min_value=0.0,
                value=float(act.quantity),
                step=1.0,
                key=f"edit_{rec.rec_id}",
            )
            if new_qty != float(act.quantity):
                check = svc.check_edit(rec, run_id, new_qty)
                for msg in check.errors:
                    st.error(msg)
                for msg in check.warnings:
                    st.warning(f"Sanity check: {msg} A reason is required to approve this edit.")
                if not check.errors and not check.warnings:
                    st.success(
                        "Edited quantity passes validation. Approve it on the Approvals page."
                    )

if run_id is not None:
    with st.expander("Trace"):
        info = svc.run_info(run_id)
        st.write(f"Run {run_id}. Trace id: {info.trace_id or 'not recorded'}.")
        link = svc.trace_link(run_id)
        if link:
            st.link_button("Open Phoenix", link)
        spans = svc.spans(run_id, exc_id)
        if spans:
            ordered = sorted(spans, key=lambda x: x["start_ns"])
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "span": sp["name"],
                            "ms": round((sp["end_ns"] - sp["start_ns"]) / 1e6, 1),
                            "status": sp["status"],
                        }
                        for sp in ordered
                    ]
                ),
                hide_index=True,
            )
        else:
            st.caption("No spans recorded for this exception (tracing off, or no run).")
