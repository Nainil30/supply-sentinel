"""Validator and policy tests: grounded, ungrounded, wrong PO id, disallowed action, tiers."""

from pathlib import Path

import pytest

from supply_sentinel.agent.models import ExceptionCard, RecommendationDraft
from supply_sentinel.agent.policy import Policy, action_value_usd
from supply_sentinel.agent.validate import (
    extract_numbers,
    numbers_match,
    resolve_path,
    validate_draft,
)
from supply_sentinel.engine.models import (
    ActionItem,
    ActionType,
    ExceptionRecord,
    Recommendation,
    RecRootCause,
    RecSource,
    SecurityFlag,
    Stakeholder,
)

POLICY = Policy.load(Path("config"))

TOOL_LOG = {
    "card": {"max_shortfall_units": 1049},
    "tc1": {
        "unit_cost_usd": 38.0,
        "max_shortfall_units": 1049.0,
        "first_shortage_week": 2,
        "weeks": [{"pab": -10.5}, {"pab": -1049.0}],
    },
    "tc2": {"pos": [{"po_id": "PO-10551", "qty_units": 1500}]},
}


def card(exc_type: str = "SHORTAGE") -> ExceptionCard:
    exc = ExceptionRecord(
        exception_id="EXC-SHT-P-X",
        type=exc_type,
        part_id="P-X",
        severity="P1",
        max_shortfall_units=1049.0,
        first_shortage_week=2,
    )
    base = Recommendation(
        rec_id="REC-0",
        exception_id=exc.exception_id,
        part_id="P-X",
        source=RecSource.RULES_BASELINE,
        primary_action=ActionItem(action_type=ActionType.PULL_IN_PO, quantity=1049),
        root_cause=RecRootCause.SUPPLIER_SLIP,
        root_cause_explanation="x",
        stakeholder=Stakeholder.GSM,
        draft_message="x",
        autonomy_tier=1,
    )
    return ExceptionCard(exception=exc, baseline=base)


def draft(**over) -> RecommendationDraft:
    data = {
        "primary_action": {"action_type": "PULL_IN_PO", "quantity": 1049, "po_ids": ["PO-10551"]},
        "root_cause": "SUPPLIER_SLIP",
        "root_cause_explanation": "PO-10551 slipped and the part is 1049 units short from week 2.",
        "evidence": [
            {"tool_call_id": "tc1", "field_path": "max_shortfall_units", "value": 1049},
            {"tool_call_id": "tc1", "field_path": "first_shortage_week", "value": 2},
        ],
        "stakeholder": "GSM",
        "draft_message": "Please pull in PO-10551: 1049 units short from week 2.",
    }
    data.update(over)
    return RecommendationDraft.model_validate(data)


def test_grounded_draft_passes():
    res = validate_draft(draft(), card(), TOOL_LOG, POLICY)
    assert res.ok, res.describe()
    assert res.grounding.status == "PASSED" and res.grounding.checked >= 5


def test_ungrounded_number_in_message_is_caught():
    res = validate_draft(
        draft(draft_message="Pull in PO-10551: 1200 units short."), card(), TOOL_LOG, POLICY
    )
    assert res.grounding.status == "FAILED"
    assert any("1200" in v for v in res.grounding.ungrounded_values)


def test_evidence_value_must_match_tool_output():
    bad = [{"tool_call_id": "tc1", "field_path": "max_shortfall_units", "value": 900}]
    res = validate_draft(draft(evidence=bad), card(), TOOL_LOG, POLICY)
    assert res.grounding.status == "FAILED"
    assert any("tool returned 1049" in v for v in res.grounding.ungrounded_values)


def test_missing_tool_call_and_path_are_caught():
    ev = [
        {"tool_call_id": "tc9", "field_path": "x", "value": 1},
        {"tool_call_id": "tc1", "field_path": "nope", "value": 1},
    ]
    res = validate_draft(draft(evidence=ev), card(), TOOL_LOG, POLICY)
    assert (
        sum(
            "no such tool call" in v or "path not found" in v
            for v in res.grounding.ungrounded_values
        )
        == 2
    )


def test_wrong_po_id_is_a_policy_issue():
    d = draft(
        primary_action={"action_type": "PULL_IN_PO", "quantity": 1049, "po_ids": ["PO-99999"]}
    )
    res = validate_draft(d, card(), TOOL_LOG, POLICY)
    assert any("PO-99999" in i.message for i in res.of("policy"))


def test_disallowed_action_for_exception_type():
    d = draft(primary_action={"action_type": "LTB_BUY", "quantity": 1049})
    res = validate_draft(d, card(), TOOL_LOG, POLICY)
    assert any("not allowed for SHORTAGE" in i.message for i in res.of("policy"))


def test_pull_in_needs_quantity_and_po_ids():
    res = validate_draft(
        draft(primary_action={"action_type": "PULL_IN_PO"}), card(), TOOL_LOG, POLICY
    )
    messages = " ".join(i.message for i in res.of("policy"))
    assert "needs a quantity" in messages and "needs po_ids" in messages


def test_sanity_caps_quantity_unless_explained():
    big = {"action_type": "EXPEDITE", "quantity": 2000}
    res = validate_draft(draft(primary_action=big), card(), TOOL_LOG, POLICY)
    assert any("1.25" in i.message for i in res.of("sanity"))
    explained = draft(primary_action=big, deviation_reason="MOQ forces a larger lot.")
    assert not validate_draft(explained, card(), TOOL_LOG, POLICY).of("sanity")


def test_deviate_requires_reason_and_alternatives():
    res = validate_draft(draft(baseline_agreement="DEVIATE"), card(), TOOL_LOG, POLICY)
    assert len(res.of("schema")) == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$4.2M at risk", [4_200_000.0]),
        ("1,049 units from wk3", [1049.0, 3.0]),
        ("PO-10551 on ORB-DC-100 due 2026-11-09", []),
        ("12k units", [12_000.0]),
    ],
)
def test_extract_numbers(text, expected):
    assert extract_numbers(text) == expected


def test_numbers_match_tolerances():
    assert numbers_match(1049, 1049.0)
    assert not numbers_match(1050, 1049)  # integers are exact
    assert numbers_match(12.34, 12.36)  # floats within 0.5%
    assert not numbers_match(12.34, 13.0)


def test_resolve_path():
    assert resolve_path(TOOL_LOG["tc1"], "weeks[1].pab") == -1049.0
    assert resolve_path(TOOL_LOG["tc1"], "$.first_shortage_week") == 2
    with pytest.raises(KeyError):
        resolve_path(TOOL_LOG["tc1"], "weeks[5].pab")


# ------------------------------------------------------------------ tiers


def pull_in(qty=100):
    return ActionItem(action_type=ActionType.PULL_IN_PO, quantity=qty, po_ids=["PO-1"])


def test_tier_comes_from_action():
    assert POLICY.assign_tier(ActionItem(action_type=ActionType.MONITOR)) == 0
    assert POLICY.assign_tier(pull_in()) == 1
    assert POLICY.assign_tier(ActionItem(action_type=ActionType.EXPEDITE, quantity=5)) == 2


def test_secondary_action_can_raise_the_tier():
    exp = ActionItem(action_type=ActionType.EXPEDITE, quantity=5)
    assert POLICY.assign_tier(pull_in(), exp) == 2


def test_escalations():
    mon = ActionItem(action_type=ActionType.MONITOR)
    flag = [SecurityFlag(kind="PROMPT_INJECTION_SUSPECTED", field="x")]
    assert POLICY.assign_tier(mon, flags=flag) == 1
    assert POLICY.assign_tier(mon, grounding_failed=True) == 2
    assert POLICY.assign_tier(mon, conflict=True) == 2
    assert POLICY.assign_tier(pull_in(), value_usd=60_000) == 2
    assert POLICY.assign_tier(pull_in(), value_usd=40_000) == 1


def test_action_value_uses_tool_unit_cost():
    assert action_value_usd(pull_in(1049), TOOL_LOG) == pytest.approx(1049 * 38.0)
    assert action_value_usd(ActionItem(action_type=ActionType.MONITOR), TOOL_LOG) is None
