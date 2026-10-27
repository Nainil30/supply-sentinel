"""Hand-made fixtures for the LTB, E&O and reconciler specialists (three each).

Each case is a small, readable situation: the exception, what the tools returned, what a model
proposed, and what the validator and policy must say. Numbers are invented but internally
consistent (for example 145,400 = 145,318 net rounded up to the order multiple).
"""

from supply_sentinel.agent.models import ExceptionCard, RecommendationDraft
from supply_sentinel.agent.policy import Policy
from supply_sentinel.agent.specialists import reconciler
from supply_sentinel.agent.validate import validate_draft
from supply_sentinel.engine.models import (
    ActionItem,
    ActionType,
    Confidence,
    ExceptionRecord,
    ExceptionType,
    Recommendation,
    RecRootCause,
    RecSource,
    Severity,
    Stakeholder,
)

POLICY = Policy.load()


def card(exc: ExceptionRecord) -> ExceptionCard:
    base = Recommendation(
        rec_id="REC-BASE-1",
        exception_id=exc.exception_id,
        part_id=exc.part_id,
        source=RecSource.RULES_BASELINE,
        primary_action=ActionItem(action_type=ActionType.MONITOR),
        root_cause=RecRootCause.NONE,
        root_cause_explanation="baseline",
        confidence=Confidence.LOW,
        stakeholder=Stakeholder.PM,
        draft_message="baseline",
        autonomy_tier=0,
    )
    return ExceptionCard(exception=exc, baseline=base, platforms=["ORB-100"])


def draft(**kw) -> RecommendationDraft:
    body = {
        "root_cause": "EOL",
        "root_cause_explanation": "The supplier ends production soon.",
        "baseline_agreement": "AGREE",
        "stakeholder": "GCM",
        "draft_message": "Please approve the buy.",
        **kw,
    }
    return RecommendationDraft.model_validate(body)


EOL_EXC = ExceptionRecord(
    exception_id="EXC-EOL-P-X", type=ExceptionType.EOL_NOTICE, part_id="P-X",
    severity=Severity.P1, days_to_ltb=18,
)  # fmt: skip
LTB_LOG = {
    "tc1": {
        "result": {
            "sizing": {"net_units": 145318, "gross_units": 167521, "order_qty_units": 145400},
            "bridge": {"sizing": {"order_qty_units": 20000}, "alt_part_id": "P-Y"},
        }
    }
}


def test_ltb_1_buys_the_engine_order_quantity():
    d = draft(
        primary_action={"action_type": "LTB_BUY", "quantity": 145400},
        evidence=[
            {"tool_call_id": "tc1", "field_path": "result.sizing.order_qty_units", "value": 145400}
        ],
        draft_message="Please approve a last-time buy of 145,400 units.",
    )
    res = validate_draft(d, card(EOL_EXC), LTB_LOG, POLICY)
    assert res.ok and res.grounding.status == "PASSED"
    assert POLICY.assign_tier(d.primary_action) == 2  # LTB_BUY always needs a reason


def test_ltb_2_gross_instead_of_net_is_flagged():
    d = draft(
        primary_action={"action_type": "LTB_BUY", "quantity": 167521},
        evidence=[
            {"tool_call_id": "tc1", "field_path": "result.sizing.gross_units", "value": 167521}
        ],
    )
    res = validate_draft(d, card(EOL_EXC), LTB_LOG, POLICY)
    assert [i.check for i in res.issues] == ["sanity"]  # not an order_qty_units value


def test_ltb_3_bridge_buy_plus_qualify_alternate_is_allowed():
    d = draft(
        primary_action={"action_type": "BRIDGE_BUY", "quantity": 20000},
        secondary_action={"action_type": "QUALIFY_ALTERNATE"},
        evidence=[
            {
                "tool_call_id": "tc1",
                "field_path": "result.bridge.sizing.order_qty_units",
                "value": 20000,
            }
        ],
        draft_message="Bridge buy 20,000 units while P-Y qualifies.",
    )
    res = validate_draft(d, card(EOL_EXC), LTB_LOG, POLICY)
    assert res.ok


EXS_EXC = ExceptionRecord(
    exception_id="EXC-EXS-P-X", type=ExceptionType.EXCESS, part_id="P-X",
    severity=Severity.P2, excess_units=1434, excess_usd=21000.0, cancellable_po_qty=1000,
)  # fmt: skip
EO_LOG = {
    "tc1": {"pos": [{"po_id": "PO-20001", "qty_units": 1000, "cancellable": True},
                    {"po_id": "PO-20002", "qty_units": 500, "cancellable": False}]},
}  # fmt: skip


def test_eo_1_cancel_a_cancellable_po():
    d = draft(
        primary_action={"action_type": "CANCEL_PO", "po_ids": ["PO-20001"]},
        root_cause="EXCESS_FORECAST_DROP",
        evidence=[{"tool_call_id": "tc1", "field_path": "pos[0].qty_units", "value": 1000}],
        draft_message="Cancel PO-20001 for 1,000 units.",
        stakeholder="GSM",
    )
    res = validate_draft(d, card(EXS_EXC), EO_LOG, POLICY)
    assert res.ok
    assert POLICY.assign_tier(d.primary_action) == 2


def test_eo_2_unknown_po_is_rejected_by_policy():
    d = draft(
        primary_action={"action_type": "CANCEL_PO", "po_ids": ["PO-99999"]},
        root_cause="EXCESS_OVERBUY",
    )
    res = validate_draft(d, card(EXS_EXC), EO_LOG, POLICY)
    assert any("PO-99999" in i.message for i in res.of("policy"))


def test_eo_3_a_buying_action_is_not_allowed_on_excess():
    d = draft(
        primary_action={"action_type": "PULL_IN_PO", "quantity": 500, "po_ids": ["PO-20002"]},
        root_cause="EXCESS_OVERBUY",
    )
    res = validate_draft(d, card(EXS_EXC), EO_LOG, POLICY)
    assert any("not allowed for EXCESS" in i.message for i in res.of("policy"))


def test_eo_4_non_cancellable_po_cannot_be_cancelled():
    d = draft(
        primary_action={"action_type": "CANCEL_PO", "po_ids": ["PO-20002"]},
        root_cause="EXCESS_OVERBUY",
    )
    res = validate_draft(d, card(EXS_EXC), EO_LOG, POLICY)
    assert any("not cancellable" in i.message for i in res.of("policy"))


def rec(rec_id: str, part: str, action: str) -> dict:
    return {"rec_id": rec_id, "part_id": part, "primary_action": {"action_type": action}}


def test_reconciler_1_pull_in_versus_push_out_is_a_conflict():
    recs = [rec("R1", "P-X", "PULL_IN_PO"), rec("R2", "P-X", "PUSH_OUT_PO")]
    assert reconciler.directions(recs) == {"P-X": ["R1", "R2"]}


def test_reconciler_2_same_direction_or_different_parts_are_not_conflicts():
    same = [rec("R1", "P-X", "PULL_IN_PO"), rec("R2", "P-X", "LTB_BUY")]
    apart = [rec("R1", "P-X", "PULL_IN_PO"), rec("R2", "P-Y", "CANCEL_PO")]
    neutral = [rec("R1", "P-X", "ESCALATE_SUPPLIER"), rec("R2", "P-X", "CANCEL_PO")]
    three = [*same[:1], rec("R2", "P-X", "PUSH_OUT_PO"), rec("R3", "P-X", "DATA_ISSUE_FLAG")]
    assert reconciler.directions(three) == {"P-X": ["R1", "R2"]}  # neutral rec left alone
    assert not reconciler.directions(same)
    assert not reconciler.directions(apart)
    assert not reconciler.directions(neutral)


def test_reconciler_3_consolidated_plan_may_mix_actions_of_both_types():
    from dataclasses import replace

    shortage = ExceptionRecord(
        exception_id="EXC-SHT-P-X", type=ExceptionType.SHORTAGE, part_id="P-X",
        severity=Severity.P1, max_shortfall_units=1000.0,
    )  # fmt: skip
    d = draft(
        primary_action={"action_type": "PULL_IN_PO", "quantity": 1000, "po_ids": ["PO-20001"]},
        secondary_action={"action_type": "PUSH_OUT_PO", "po_ids": ["PO-20002"]},
        root_cause="SUPPLIER_SLIP",
        baseline_agreement="DEVIATE",
        deviation_reason="Keep the shortage fixed first, then push out the later PO.",
        alternatives_considered=[{"action_type": "CANCEL_PO", "why_not": "supply is needed"}],
        evidence=[{"tool_call_id": "r1", "field_path": "primary_action.quantity", "value": 1000}],
        draft_message="Pull in PO-20001 by 1,000 units and push out PO-20002.",
    )
    log = {"r1": {"primary_action": {"quantity": 1000}}, "tc1": EO_LOG["tc1"]}
    plain = validate_draft(d, card(shortage), log, POLICY)
    assert any("PUSH_OUT_PO is not allowed for SHORTAGE" in i.message for i in plain.of("policy"))
    union = sorted({*POLICY.allowed_actions["SHORTAGE"], *POLICY.allowed_actions["EXCESS"]})
    mixed = replace(POLICY, allowed_actions={"SHORTAGE": union})
    assert not validate_draft(d, card(shortage), log, mixed).of("policy")


def test_conflict_raises_tier_to_at_least_two():
    t = POLICY.assign_tier(ActionItem(action_type=ActionType.PULL_IN_PO), conflict=True)
    assert t == 2  # PULL_IN_PO alone is tier 1
