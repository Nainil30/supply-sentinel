"""Reconciler: resolves two recommendations that pull one part in opposite directions.

Example: the shortage specialist wants to pull a PO in while the E&O specialist wants to push
out another PO of the same part. The reconciler sees both, may test options with
simulate_scenario, and proposes one consolidated recommendation.
"""

import json
from typing import Any

from supply_sentinel.agent.models import Candidate, ExceptionCard
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists.base import run_specialist
from supply_sentinel.engine.models import ActionType

INCREASE = {
    ActionType.PLACE_PO,
    ActionType.EXPEDITE,
    ActionType.PULL_IN_PO,
    ActionType.LTB_BUY,
    ActionType.BRIDGE_BUY,
}
DECREASE = {ActionType.CANCEL_PO, ActionType.PUSH_OUT_PO}
ALLOWED_TOOLS = ("get_part_position", "get_open_pos", "simulate_scenario")

GUIDE = """Two specialists disagree about one part. Protect shipments first: never cancel or push out supply that the shortage window needs. Cancel or push out only quantity beyond that need. Use simulate_scenario to test the combined plan before you propose it. The consolidated recommendation may use an action from either specialist."""


def directions(recs: list[dict[str, Any]]) -> dict[str, set[str]]:
    """part_id -> rec_ids, only for parts that have both an increase and a decrease action."""
    by_part: dict[str, list[dict[str, Any]]] = {}
    for r in recs:
        by_part.setdefault(r["part_id"], []).append(r)
    conflicts: dict[str, set[str]] = {}
    for part, group in by_part.items():
        kinds = {ActionType(r["primary_action"]["action_type"]) for r in group}
        if kinds & INCREASE and kinds & DECREASE:
            conflicts[part] = {r["rec_id"] for r in group}
    return conflicts


def user_text(a: dict[str, Any], b: dict[str, Any]) -> str:
    def brief(r: dict[str, Any]) -> dict[str, Any]:
        keep = ("exception_id", "source", "primary_action", "secondary_action", "root_cause",
                "root_cause_explanation", "confidence")  # fmt: skip
        return {k: r[k] for k in keep if r.get(k) is not None}

    return (
        f'Part {a["part_id"]}. Recommendation r1 (evidence id "r1"): '
        f"{json.dumps(brief(a), separators=(',', ':'))}\n"
        f'Recommendation r2 (evidence id "r2"): {json.dumps(brief(b), separators=(",", ":"))}\n'
        'The exception card and rules suggestion for r1 are evidence ids "card" and "baseline". '
        "Decide one consolidated plan. Set baseline_agreement to DEVIATE and explain in "
        "deviation_reason how you resolved the conflict; list the rejected option in "
        "alternatives_considered. Cite tool results (tc1, tc2, ...) and r1/r2 values in evidence."
    )


def reconcile(
    ctx: RunContext, card: ExceptionCard, a: dict[str, Any], b: dict[str, Any]
) -> Candidate:
    return run_specialist(
        ctx,
        card,
        None,
        "reconciler",
        user_text=user_text(a, b),
        allowed=ALLOWED_TOOLS,
        skill_body=GUIDE,
        extra_log={"r1": a, "r2": b},
    )
