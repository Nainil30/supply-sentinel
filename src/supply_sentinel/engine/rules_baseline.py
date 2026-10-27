"""Rules baseline (spec 02 section 7): a fair, template-driven opponent for the agent.

Known blind spots by design: no decision history, no alternates, no cross-exception reasoning,
no allocation, no reading of text. The evals measure what those blind spots cost.
"""

import math

from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.eo import compute_eo_exposure
from supply_sentinel.engine.ltb import compute_ltb_quantity
from supply_sentinel.engine.models import (
    ActionItem,
    ActionType,
    EvidenceRef,
    ExceptionRecord,
    ExceptionType,
    PartInputs,
    Recommendation,
    RecRootCause,
    RecSource,
    RootCause,
    Stakeholder,
)
from supply_sentinel.engine.netting import (
    EPS,
    commit_gaps,
    round_to_order_multiple,
)
from supply_sentinel.engine.root_cause import explain_root_cause

CAUSE_TO_REC: dict[RootCause, RecRootCause] = {
    RootCause.QUALITY_HOLD: RecRootCause.QUALITY_HOLD,
    RootCause.COMMIT_GAP: RecRootCause.COMMIT_GAP,
    RootCause.SUPPLIER_SLIP: RecRootCause.SUPPLIER_SLIP,
    RootCause.DEMAND_SPIKE: RecRootCause.DEMAND_SPIKE,
    RootCause.STRUCTURAL: RecRootCause.STRUCTURAL,
}


def _units(x: float) -> int:
    return math.ceil(x - EPS)


def _decide(
    inp: PartInputs, exc: ExceptionRecord, cfg: EngineConfig
) -> tuple[ActionItem, RecRootCause, Stakeholder, str, list[EvidenceRef]]:
    """Returns (action, root cause, stakeholder, one-line explanation, evidence)."""
    pid = inp.part_id
    if exc.type is ExceptionType.SHORTAGE:
        shortfall = exc.max_shortfall_units or 0.0
        rc = explain_root_cause(inp, cfg.horizon_weeks)
        evidence = [
            EvidenceRef(
                tool_call_id="engine:detect",
                field_path="max_shortfall_units",
                value=shortfall,
            ),
            EvidenceRef(
                tool_call_id="engine:root_cause", field_path="primary", value=rc.primary.value
            ),
        ]
        cause = CAUSE_TO_REC[rc.primary]
        text = f"{pid} runs short by {_units(shortfall)} units from week {exc.first_shortage_week}."
        if rc.primary is RootCause.QUALITY_HOLD:
            qty = min(inp.quality_hold_units, _units(shortfall))
            action = ActionItem(action_type=ActionType.REVIEW_QUALITY_HOLD, quantity=qty)
            return (
                action,
                cause,
                Stakeholder.QUALITY,
                text + " Quality hold stock would clear it.",
                evidence,
            )
        if rc.primary is RootCause.COMMIT_GAP:
            gaps = commit_gaps(inp, cfg.commit_gap_weeks)
            action = ActionItem(
                action_type=ActionType.ESCALATE_SUPPLIER, quantity=sum(gaps.values())
            )
            return (
                action,
                cause,
                Stakeholder.GSM,
                text + " The supplier commit is below the PO.",
                evidence,
            )
        if rc.primary is RootCause.SUPPLIER_SLIP:
            slipped = sorted(
                (po for po in inp.pos if po.promise_week_idx > po.need_week_idx),
                key=lambda po: po.need_week_idx,
            )
            action = ActionItem(
                action_type=ActionType.PULL_IN_PO,
                quantity=_units(shortfall),
                po_ids=[slipped[0].po_id] if slipped else None,
                target_week_idx=slipped[0].need_week_idx if slipped else None,
            )
            return (
                action,
                cause,
                Stakeholder.GSM,
                text + " A PO is promised after its need date.",
                evidence,
            )
        first = exc.first_shortage_week or 0
        if first < inp.lead_time_weeks:
            action = ActionItem(action_type=ActionType.EXPEDITE, quantity=_units(shortfall))
        else:
            qty = round_to_order_multiple(shortfall, inp.order_multiple, inp.moq)
            action = ActionItem(action_type=ActionType.PLACE_PO, quantity=qty)
        return action, cause, Stakeholder.GSM, text + " No supply-side cause was found.", evidence

    if exc.type is ExceptionType.COMMIT_GAP:
        evidence = [
            EvidenceRef(
                tool_call_id="engine:detect", field_path="gap_units", value=exc.gap_units or 0
            )
        ]
        action = ActionItem(action_type=ActionType.ESCALATE_SUPPLIER, quantity=exc.gap_units)
        text = f"{pid} has commits below PO quantity by {exc.gap_units} units."
        return action, RecRootCause.COMMIT_GAP, Stakeholder.GSM, text, evidence

    if exc.type is ExceptionType.EOL_NOTICE:
        ltb = compute_ltb_quantity(inp, cfg)
        qty = ltb.sizing.order_qty_units
        evidence = [
            EvidenceRef(tool_call_id="engine:ltb", field_path="sizing.order_qty_units", value=qty)
        ]
        text = f"{pid} has an end-of-life notice; last-time-buy is {exc.days_to_ltb} days away."
        if qty > 0:
            action = ActionItem(action_type=ActionType.LTB_BUY, quantity=qty)
        else:
            action = ActionItem(action_type=ActionType.MONITOR)
        return action, RecRootCause.EOL, Stakeholder.GCM, text, evidence

    eo = compute_eo_exposure(inp, cfg)
    if exc.type is ExceptionType.OBSOLESCENCE_RISK:
        evidence = [
            EvidenceRef(
                tool_call_id="engine:eo", field_path="obsolete_units", value=eo.obsolete_units
            )
        ]
        action = ActionItem(action_type=ActionType.DISPOSITION_REVIEW, quantity=eo.obsolete_units)
        text = f"{pid} supply exceeds lifetime need by {_units(eo.obsolete_units)} units."
        return action, RecRootCause.LIFECYCLE_END, Stakeholder.FINANCE, text, evidence

    evidence = [
        EvidenceRef(tool_call_id="engine:eo", field_path="excess_units", value=eo.excess_units)
    ]
    text = f"{pid} holds {_units(eo.excess_units)} units more than 52-week need plus safety stock."
    if eo.cancel_qty_recommended > 0:
        action = ActionItem(action_type=ActionType.CANCEL_PO, quantity=eo.cancel_qty_recommended)
    else:
        action = ActionItem(action_type=ActionType.PUSH_OUT_PO, quantity=eo.residual_excess_units)
    return action, RecRootCause.EXCESS_OVERBUY, Stakeholder.FINANCE, text, evidence


def recommend(
    inp: PartInputs, exc: ExceptionRecord, cfg: EngineConfig, seq: int = 1
) -> Recommendation:
    """The rules baseline recommendation for one exception."""
    action, cause, stakeholder, text, evidence = _decide(inp, exc, cfg)
    qty = f" Quantity: {_units(action.quantity)}." if action.quantity is not None else ""
    message = (
        f"[{exc.severity.value}] {action.action_type.value} for {inp.part_id}. {text}{qty} "
        "Rules engine suggestion; please review."
    )
    return Recommendation(
        rec_id=f"REC-BASE-{seq}",
        exception_id=exc.exception_id,
        part_id=inp.part_id,
        source=RecSource.RULES_BASELINE,
        primary_action=action,
        root_cause=cause,
        root_cause_explanation=text,
        evidence=evidence,
        stakeholder=stakeholder,
        draft_message=message,
        autonomy_tier=cfg.action_tiers.get(action.action_type.value, 1),
    )
