"""validate, approval_gate and finalize: the control cycle after a specialist proposes."""

import sqlite3
from datetime import UTC, datetime
from typing import Any

from langgraph.types import interrupt

from supply_sentinel.agent.models import Candidate, Decision, ExceptionCard, NodeError
from supply_sentinel.agent.policy import action_value_usd
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists.base import repair_draft
from supply_sentinel.agent.state import AgentState
from supply_sentinel.agent.validate import ValidationResult, validate_draft
from supply_sentinel.datagen.decisions import connect
from supply_sentinel.engine.models import (
    Grounding,
    Recommendation,
    RecSource,
    SecurityFlag,
)
from supply_sentinel.llm.types import ProviderError


def _part_flags(state: AgentState, part_id: str) -> list[SecurityFlag]:
    return [
        SecurityFlag(**f)
        for f in state.get("security_flags", [])
        if str(f["field"]).startswith(f"{part_id}:")
    ]


def _fallback(
    ctx: RunContext, card: ExceptionCard, reason: str, rec_id: str, flags: list[SecurityFlag]
) -> Recommendation:
    """The rules baseline, marked RULES_FALLBACK with a visible flag."""
    base = card.baseline
    flag = SecurityFlag(kind="RULES_FALLBACK", field="specialist", detail=reason[:200])
    tier = ctx.policy.assign_tier(base.primary_action, base.secondary_action, flags=[flag, *flags])
    return base.model_copy(
        update={
            "rec_id": rec_id,
            "source": RecSource.RULES_FALLBACK,
            "autonomy_tier": tier,
            "security_flags": [flag, *flags],
            "grounding": Grounding(status="NOT_CHECKED"),
        }
    )


def _validated(
    ctx: RunContext, card: ExceptionCard, cand: Candidate
) -> tuple[ValidationResult | None, str]:
    """Validate, with one repair call. Returns (result or None if no draft, reason if none)."""
    assert cand.draft is not None
    res = validate_draft(cand.draft, card, cand.tool_log, ctx.policy)
    if not res.ok:
        ctx.ledger.log("validation_failed", exception_id=card.exception_id, issues=res.describe())
        try:
            cand.draft = repair_draft(ctx, card, cand, cand.draft, res.describe())
        except (ValueError, ProviderError) as exc:
            return res, f"repair failed: {exc}"
        res = validate_draft(cand.draft, card, cand.tool_log, ctx.policy)
    return res, ""


def validate(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    order = {c["exception"]["exception_id"]: i for i, c in enumerate(state.get("queue", []))}
    cards = {c["exception"]["exception_id"]: ExceptionCard(**c) for c in state.get("queue", [])}
    cands = sorted(
        (Candidate(**c) for c in state.get("candidates", [])),
        key=lambda c: order.get(c.exception_id, 99),
    )
    recs: list[Recommendation] = []
    errors: list[NodeError] = []
    totals: dict[str, float] = {}
    for n, cand in enumerate(cands, start=1):
        card = cards[cand.exception_id]
        rec_id = f"REC-{ctx.run_id}-{n}"
        flags = _part_flags(state, cand.part_id) + cand.security_flags
        for k, v in cand.usage.model_dump().items():
            totals[k] = (
                max(totals.get(k, 0), v) if k == "max_call_input_tokens" else totals.get(k, 0) + v
            )
        if cand.draft is None:
            reason = cand.failure or "no draft produced"
            errors.append(
                NodeError(node="specialist", exception_id=cand.exception_id, message=reason)
            )
            recs.append(_fallback(ctx, card, reason, rec_id, flags))
            continue
        res, _ = _validated(ctx, card, cand)
        assert res is not None and cand.draft is not None
        hard = res.of("policy") + res.of("schema")
        if hard:
            reason = f"validation failed after repair: {hard[0].message}"
            errors.append(
                NodeError(node="validate", exception_id=cand.exception_id, message=reason)
            )
            recs.append(_fallback(ctx, card, reason, rec_id, flags))
            continue
        d = cand.draft
        sanity = [
            SecurityFlag(kind="SANITY_FLAG", field="quantity", detail=i.message[:200])
            for i in res.of("sanity")
        ]
        all_flags = flags + sanity
        value = action_value_usd(d.primary_action, cand.tool_log)
        tier = ctx.policy.assign_tier(
            d.primary_action,
            d.secondary_action,
            value_usd=value,
            flags=all_flags,
            grounding_failed=res.grounding.status == "FAILED",
        )
        recs.append(
            Recommendation(
                rec_id=rec_id,
                exception_id=cand.exception_id,
                part_id=cand.part_id,
                source=RecSource.AGENT,
                autonomy_tier=tier,
                security_flags=all_flags,
                grounding=res.grounding,
                **d.model_dump(),
            )
        )
        ctx.ledger.log(
            "validated",
            rec_id=rec_id,
            tier=tier,
            grounding=res.grounding.status,
            checked=res.grounding.checked,
        )
    return {
        "recommendations": [r.model_dump(mode="json") for r in recs],
        "errors": [e.model_dump() for e in errors],
        "budget": totals,
    }


def pending_summary(rec: dict[str, Any]) -> dict[str, Any]:
    act = rec["primary_action"]
    return {
        "rec_id": rec["rec_id"],
        "part_id": rec["part_id"],
        "action": act["action_type"],
        "quantity": act.get("quantity"),
        "tier": rec["autonomy_tier"],
        "source": rec["source"],
        "grounding": rec["grounding"]["status"],
        "flags": [f["kind"] for f in rec["security_flags"]],
    }


def approval_gate(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    """One interrupt for every recommendation that needs a human (tier 1 or 2)."""
    pending = [
        pending_summary(r) for r in state.get("recommendations", []) if r["autonomy_tier"] >= 1
    ]
    if not pending:
        return {"decisions": []}
    ctx.ledger.log("approval_gate", pending=[p["rec_id"] for p in pending])
    answer = interrupt({"run_id": ctx.run_id, "pending": pending})
    return {"decisions": [Decision(**d).model_dump() for d in answer]}


def _set_status(ctx: RunContext, rec: dict[str, Any], dec: Decision, status: str) -> None:
    conn = connect(ctx.snap.state_db)
    try:
        note = f" | {status} by {dec.decided_by}: {dec.reason}" if dec.reason else ""
        conn.execute(
            "UPDATE decision_log SET status = ?, decided_by = ?, decided_at = ?, "
            "quantity = COALESCE(?, quantity), rationale = COALESCE(rationale, '') || ?, run_id = ? "
            "WHERE snapshot_id = ? AND rec_id = ?",
            (status, dec.decided_by, datetime.now(UTC).isoformat(),
             None if dec.quantity is None else int(dec.quantity), note, ctx.run_id,
             ctx.snap.snapshot_id, rec["rec_id"]),
        )  # fmt: skip
        conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f"could not update decision_log: {exc}") from exc
    finally:
        conn.close()


def finalize(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    """The harness (not the LLM) records every recommendation and applies the human decisions."""
    decisions = {d["rec_id"]: Decision(**d) for d in state.get("decisions", [])}
    outcomes: list[dict[str, Any]] = []
    errors: list[NodeError] = []
    for rec in state.get("recommendations", []):
        r = Recommendation(**rec)
        out = ctx.bridge.record_recommendation(r)
        if out is not None:
            errors.append(NodeError(node="finalize", message=out))
        dec = decisions.get(r.rec_id)
        if r.autonomy_tier == 0:
            dec = Decision(rec_id=r.rec_id, decision="APPROVED", reason="tier 0, auto-recorded",
                           decided_by="auto-tier0")  # fmt: skip
        status = "PENDING"
        if dec is not None and dec.decision != "DEFERRED":
            if r.autonomy_tier == 2 and dec.decision in ("APPROVED", "EDITED") and not dec.reason:
                errors.append(
                    NodeError(
                        node="finalize", message=f"{r.rec_id}: tier 2 needs a reason; left PENDING"
                    )
                )
            else:
                status = dec.decision
                _set_status(ctx, rec, dec, status)
        ctx.ledger.log("decision", rec_id=r.rec_id, tier=r.autonomy_tier, status=status)
        outcomes.append({"rec_id": r.rec_id, "status": status})
    return {"outcomes": outcomes, "errors": [e.model_dump() for e in errors]}
