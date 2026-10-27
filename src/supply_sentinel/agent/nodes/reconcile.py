"""reconcile: find opposing recommendations on one part and consolidate them (spec 04 section 2).

Conditional LLM step. If the consolidated recommendation passes validation it replaces the two
(source RECONCILED). If not, both stay and each carries a CONFLICT flag, so the human sees the
disagreement. Either way the tier is at least `conflict_min_tier`.
"""

from dataclasses import replace
from typing import Any

from supply_sentinel.agent.models import ExceptionCard
from supply_sentinel.agent.nodes.review import _validated
from supply_sentinel.agent.policy import action_value_usd
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists import reconciler
from supply_sentinel.agent.state import AgentState
from supply_sentinel.engine.models import Recommendation, RecSource, SecurityFlag


def _mark_conflict(ctx: RunContext, rec: dict[str, Any], other: str, why: str) -> dict[str, Any]:
    r = Recommendation(**rec)
    flag = SecurityFlag(kind="CONFLICT", field=other, detail=why[:200])
    tier = max(r.autonomy_tier, ctx.policy.conflict_min_tier)
    return r.model_copy(
        update={"security_flags": [*r.security_flags, flag], "autonomy_tier": tier}
    ).model_dump(mode="json")


def _consolidate(
    ctx: RunContext,
    cards: dict[str, ExceptionCard],
    a: dict[str, Any],
    b: dict[str, Any],
    rec_id: str,
) -> tuple[dict[str, Any] | None, str]:
    """Returns (reconciled recommendation or None, why it failed)."""
    card = cards[a["exception_id"]]
    cand = reconciler.reconcile(ctx, card, a, b)
    if cand.draft is None:
        return None, cand.failure or "no draft produced"
    # The consolidated plan may use any action allowed for either exception type.
    types = {cards[x["exception_id"]].exception.type.value for x in (a, b)}
    union = sorted({act for t in types for act in ctx.policy.allowed_actions[t]})
    policy = replace(ctx.policy, allowed_actions={card.exception.type.value: union})
    res, why = _validated(ctx, card, cand, policy)
    assert res is not None and cand.draft is not None
    hard = res.of("policy") + res.of("schema")
    if hard:
        return None, why or f"validation failed: {hard[0].message}"
    d = cand.draft
    flags: list[SecurityFlag] = []
    for f in [
        *Recommendation(**a).security_flags,
        *Recommendation(**b).security_flags,
        *cand.security_flags,
    ]:
        if (f.kind, f.field) not in {(x.kind, x.field) for x in flags}:
            flags.append(f)  # security flags propagate from both inputs
    flags.append(
        SecurityFlag(kind="CONFLICT", field=b["rec_id"], detail=f"reconciled {a['rec_id']}")
    )
    tier = ctx.policy.assign_tier(
        d.primary_action,
        d.secondary_action,
        value_usd=action_value_usd(d.primary_action, cand.tool_log),
        flags=flags,
        grounding_failed=res.grounding.status == "FAILED",
        conflict=True,
    )
    rec = Recommendation(
        rec_id=rec_id,
        exception_id=a["exception_id"],
        part_id=a["part_id"],
        source=RecSource.RECONCILED,
        autonomy_tier=tier,
        security_flags=flags,
        grounding=res.grounding,
        **d.model_dump(),
    )
    return rec.model_dump(mode="json"), ""


def reconcile(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    recs = list(state.get("recommendations", []))
    conflicts = reconciler.directions(recs)
    if not conflicts:
        return {}
    cards = {c["exception"]["exception_id"]: ExceptionCard(**c) for c in state.get("cards", [])}
    out = list(recs)
    errors: list[dict[str, Any]] = []
    for part, ids in sorted(conflicts.items()):
        by_id = {r["rec_id"]: r for r in recs}
        group = [by_id[i] for i in ids]  # [increase rec, decrease rec]
        ctx.ledger.log("reconcile_start", part_id=part, recs=ids)
        merged, why = _consolidate(ctx, cards, group[0], group[1], f"{group[0]['rec_id']}R")
        if merged is not None:
            out = [r for r in out if r["rec_id"] not in ids] + [merged]
            ctx.ledger.log("reconciled", part_id=part, rec_id=merged["rec_id"])
            continue
        errors.append(
            {"node": "reconcile", "exception_id": group[0]["exception_id"], "message": why}
        )
        ctx.ledger.log("reconcile_failed", part_id=part, reason=why)
        for r in group:
            other = next(x["rec_id"] for x in group if x["rec_id"] != r["rec_id"])
            out = [
                _mark_conflict(ctx, r, other, why) if x["rec_id"] == r["rec_id"] else x for x in out
            ]
    return {"recommendations": out, "errors": errors}
