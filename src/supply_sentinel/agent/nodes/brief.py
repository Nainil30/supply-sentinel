"""weekly_brief: a markdown brief built from validated fields; the LLM only writes the summary.

The tables are produced by code, so every number in them is a validated field. The summary
paragraph comes from the `writer` role and passes the same number-grounding check; if it fails
twice the brief says so instead of printing unchecked prose.
"""

import json
from typing import Any

from supply_sentinel.agent import context
from supply_sentinel.agent.models import ExceptionCard
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.state import AgentState
from supply_sentinel.agent.validate import extract_numbers, numbers_match
from supply_sentinel.llm.types import Msg, ProviderError

WRITER_SYSTEM = (
    "You write the opening summary of a weekly supply brief for a planner. Use 2 to 4 plain "
    "sentences. Use only the facts in the JSON the user sends; every number you write must "
    "appear there exactly. Do not add advice beyond the facts. Reply with the summary text only."
)


def _numbers_in(obj: Any) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int | float):
        return [float(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers_in(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _numbers_in(v)]
    if isinstance(obj, str):
        return extract_numbers(obj)
    return []


def _summary(ctx: RunContext, facts: dict[str, Any]) -> tuple[str | None, str]:
    """Returns (summary or None, note). One repair attempt on ungrounded numbers."""
    allowed = _numbers_in(facts)
    messages = [
        Msg(role="system", content=WRITER_SYSTEM),
        Msg(role="user", content=json.dumps(facts, separators=(",", ":"))),
    ]
    for _ in range(2):
        try:
            resp = ctx.router.complete(
                "writer",
                messages,
                [],
                tag="weekly_brief",
                est_input_tokens=context.messages_tokens(messages),
            )
        except ProviderError as exc:
            return None, f"writer unavailable: {exc}"
        text = resp.content.strip()
        bad = [n for n in extract_numbers(text) if not any(numbers_match(n, a) for a in allowed)]
        if text and not bad:
            return text, ""
        messages += [
            Msg(role="assistant", content=text),
            Msg(
                role="user",
                content=f"These numbers are not in the facts: {bad}. Rewrite the summary without them.",
            ),
        ]
    return None, "summary failed the grounding check"


def weekly_brief(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    recs = state.get("recommendations", [])
    outcomes = {o["rec_id"]: o["status"] for o in state.get("outcomes", [])}
    deferred = [ExceptionCard(**c) for c in state.get("deferred", [])]
    rows = []
    for r in recs:
        act = r["primary_action"]
        rows.append(
            {
                "rec_id": r["rec_id"], "part_id": r["part_id"], "action": act["action_type"],
                "quantity": act.get("quantity"), "tier": r["autonomy_tier"], "source": r["source"],
                "grounding": r["grounding"]["status"], "status": outcomes.get(r["rec_id"], "-"),
            }
        )  # fmt: skip
    facts = {"as_of_date": state["as_of_date"], "recommendations": rows,
             "exceptions_not_yet_investigated": len(deferred)}  # fmt: skip
    summary, note = _summary(ctx, facts) if rows else ("No exceptions needed a specialist.", "")

    lines = [f"# Weekly supply brief - as of {state['as_of_date']}", "",
             f"Run `{ctx.run_id}` on snapshot `{state['snapshot_id']}`, profile `{ctx.profile}`.", ""]  # fmt: skip
    if state.get("stale"):
        lines += ["> **Data warning:** the snapshot is older than the staleness limit.", ""]
    lines += ["## Summary", "", summary or f"_Summary unavailable ({note})._", ""]
    lines += ["## Recommendations", "",
              "| Rec | Part | Action | Qty | Tier | Source | Grounding | Status |",
              "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for x in rows:
        qty = "" if x["quantity"] is None else f"{x['quantity']:,.0f}"
        lines.append(f"| {x['rec_id']} | {x['part_id']} | {x['action']} | {qty} | {x['tier']} | {x['source']} | {x['grounding']} | {x['status']} |")  # fmt: skip
    flagged = [r for r in recs if r["security_flags"]]
    if flagged:
        lines += ["", "## Flags", ""]
        for r in flagged:
            lines.append(f"- {r['rec_id']}: " + ", ".join(f["kind"] for f in r["security_flags"]))
    if deferred:
        lines += ["", "## Not yet investigated by an agent specialist (rules baseline only)", ""]
        for c in deferred:
            a = c.baseline.primary_action
            q = "" if a.quantity is None else f" x {a.quantity:,.0f}"
            lines.append(f"- {c.exception_id} ({c.exception.type.value}, {c.exception.severity.value}): baseline {a.action_type.value}{q}")  # fmt: skip
    budget = state.get("budget", {})
    lines += ["", "## Run cost", "",
              f"{budget.get('llm_calls', 0)} specialist LLM calls, {budget.get('tool_calls', 0)} tool calls, "
              f"largest input {budget.get('max_call_input_tokens', 0)} tokens (limit {context.MAX_INPUT_TOKENS})."]  # fmt: skip
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.run_dir / "weekly_brief.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.ledger.log("weekly_brief", path=str(path), summary_ok=summary is not None)
    return {"brief_path": str(path)}
