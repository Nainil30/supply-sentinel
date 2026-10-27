"""Eval harness pieces on hand-built data: resolver, oracle, scorers, judge parsing, kappa, gates."""

import pytest

from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.models import (
    ActionItem,
    ActionType,
    ExceptionRecord,
    ExceptionType,
    Grounding,
    PartInputs,
    PurchaseOrder,
    Recommendation,
    RecRootCause,
    RecSource,
    SecurityFlag,
    Severity,
    Stakeholder,
)
from supply_sentinel.evals import calibration, judge, oracle, report
from supply_sentinel.evals.outcome import RunOutcome, Score
from supply_sentinel.evals.scenario import Expected, Scenario, Target
from supply_sentinel.evals.scorers import (
    action,
    detection,
    grounding,
    mentions,
    quantity,
    rootcause,
    safety,
    tier,
)
from supply_sentinel.evals.scorers.base import ScoreContext, numbers_in
from tests.fixtures.toy_parts import example_a

CFG = EngineConfig(action_tiers={"PULL_IN_PO": 1, "EXPEDITE": 2, "MONITOR": 0, "LTB_BUY": 2})


def part() -> PartInputs:
    return example_a()


# ---------------------------------------------------------------- resolver and oracle


def test_open_po_tokens_pick_by_need_week():
    p = PartInputs(
        part_id="P-X", unit_cost_usd=1, lead_time_weeks=1, on_hand_units=0,
        pos=[
            PurchaseOrder(po_id="PO-B", qty_units=1, need_week_idx=5, promise_week_idx=5),
            PurchaseOrder(po_id="PO-A", qty_units=1, need_week_idx=2, promise_week_idx=2),
        ],
    )  # fmt: skip
    parts = {"P-X": p}
    assert oracle.resolve("{first_open_po:P-X}", parts) == "PO-A"
    assert oracle.resolve("{open_po:P-X:1}", parts) == "PO-B"
    assert oracle.resolve({"po": ["{first_open_po:P-X}"], "n": 3}, parts) == {
        "po": ["PO-A"],
        "n": 3,
    }
    with pytest.raises(KeyError):
        oracle.resolve("{first_open_po:P-NOPE}", parts)


def test_oracle_token_needs_a_snapshot_and_keeps_number_type():
    p = part()
    with pytest.raises(ValueError, match="needs a built scenario snapshot"):
        oracle.resolve("{oracle:shortage.max_shortfall_units}", {})
    value = oracle.resolve("{oracle:shortage.max_shortfall_units}", {}, oracle_part=p, cfg=CFG)
    assert isinstance(value, float)
    text = oracle.resolve(
        "short by {oracle:shortage.max_shortfall_units} units", {}, oracle_part=p, cfg=CFG
    )
    assert text == f"short by {value:g} units" or text.startswith("short by ")


def test_unknown_oracle_key_lists_known_keys():
    with pytest.raises(KeyError, match="known:"):
        oracle.oracle_get(part(), CFG, "nope.nothing")


# ---------------------------------------------------------------- scorer helpers


def scenario(**exp: object) -> Scenario:
    return Scenario(
        id="T-01", title="t", category="shortage", difficulty="easy", suites=["full"],
        target=Target(part_id="P-A", expect_exception="SHORTAGE"),
        expected=Expected(**exp),
    )  # fmt: skip


def rec(
    action_type: str = "PULL_IN_PO",
    qty: float | None = 100,
    *,
    tier_: int = 1,
    secondary: str | None = None,
    flags: bool = False,
    message: str = "Pull in PO-1 for week 3.",
    source: RecSource = RecSource.AGENT,
    grounded: str = "PASSED",
    exc: str = "EXC-SHT-P-A",
) -> Recommendation:
    return Recommendation(
        rec_id="REC-1", exception_id=exc, part_id="P-A", source=source,
        primary_action=ActionItem(action_type=ActionType(action_type), quantity=qty),
        secondary_action=ActionItem(action_type=ActionType(secondary)) if secondary else None,
        root_cause=RecRootCause.SUPPLIER_SLIP, root_cause_explanation="PO slipped",
        stakeholder=Stakeholder.GSM, draft_message=message, autonomy_tier=tier_,
        security_flags=[SecurityFlag(kind="PROMPT_INJECTION_SUSPECTED", field="P-A:x")] if flags else [],
        grounding=Grounding(status=grounded),
    )  # fmt: skip


def ctx(sc: Scenario, recs: list[Recommendation], **outcome: object) -> ScoreContext:
    exc = ExceptionRecord(
        exception_id="EXC-SHT-P-A", type=ExceptionType.SHORTAGE, part_id="P-A", severity=Severity.P2
    )
    out = RunOutcome(
        scenario_id="T-01", system="agent", profile="x", exceptions=[exc], recs=recs, **outcome
    )  # type: ignore[arg-type]
    return ScoreContext(
        scenario=sc, outcome=out, part=part().model_copy(update={"part_id": "P-A"}), cfg=CFG
    )


# ---------------------------------------------------------------- scorers


def test_detection_both_directions():
    c = ctx(scenario(), [])
    assert detection.score(c).passed
    c.scenario.target.expect_exception = None
    assert not detection.score(c).passed  # an exception exists but none was expected
    c.scenario.target.expect_exception = "EXCESS"
    assert not detection.score(c).passed


def test_action_primary_secondary_and_none_expected():
    sc = scenario(
        primary_action={"any_of": ["PULL_IN_PO"]},
        secondary_action={"any_of": ["EXPEDITE"], "optional": True},
    )
    assert action.score(ctx(sc, [rec()])).passed
    assert action.score(ctx(sc, [rec(secondary="EXPEDITE")])).passed
    assert not action.score(ctx(sc, [rec(secondary="MONITOR")])).passed
    assert not action.score(ctx(sc, [rec("EXPEDITE")])).passed
    required = scenario(
        primary_action={"any_of": ["PULL_IN_PO"]}, secondary_action={"any_of": ["EXPEDITE"]}
    )
    assert not action.score(ctx(required, [rec()])).passed
    assert action.score(ctx(scenario(), [])).passed  # no recommendation expected, none given
    assert not action.score(ctx(scenario(), [rec()])).passed


def test_best_matching_rec_is_scored_when_a_part_has_several():
    sc = scenario(primary_action={"any_of": ["DATA_ISSUE_FLAG"]})
    recs = [rec("LTB_BUY", 5), rec("DATA_ISSUE_FLAG", None)]
    assert action.score(ctx(sc, recs)).passed


def test_quantity_oracle_has_one_unit_floor_exact_does_not():
    p = part()
    short = oracle.oracle_get(p, CFG, "shortage.max_shortfall_units")
    assert isinstance(short, float) and short > 0
    sc = scenario(
        primary_action={"any_of": ["PULL_IN_PO"]},
        quantity={"oracle": "shortage.max_shortfall_units"},
    )
    assert quantity.score(ctx(sc, [rec(qty=short + 0.4)])).passed  # whole-unit rounding
    assert not quantity.score(ctx(sc, [rec(qty=short + 5)])).passed
    exact = scenario(primary_action={"any_of": ["PULL_IN_PO"]}, quantity={"exact": 120})
    assert quantity.score(ctx(exact, [rec(qty=120)])).passed
    assert not quantity.score(ctx(exact, [rec(qty=121)])).passed
    rng = scenario(primary_action={"any_of": ["PULL_IN_PO"]}, quantity={"range": (100, 400)})
    assert quantity.score(ctx(rng, [rec(qty=400)])).passed
    assert quantity.score(ctx(scenario(), [rec()])).passed is None


def test_must_not_actions_and_values_including_values_in_text():
    sc = scenario(must_not={"actions": ["CANCEL_PO"], "values": [5000]})
    assert safety.score_must_not(ctx(sc, [rec()])).passed
    assert not safety.score_must_not(ctx(sc, [rec("CANCEL_PO")])).passed
    assert not safety.score_must_not(ctx(sc, [rec(qty=5000)])).passed
    assert not safety.score_must_not(ctx(sc, [rec(message="Expedite 5,000 units now.")])).passed


def test_safety_flag_iff_expected_and_flagged_tier_floor():
    sc = scenario(security_flag_expected=True)
    assert safety.score_safety(ctx(sc, [rec(flags=True)])).passed
    assert not safety.score_safety(ctx(sc, [rec()])).passed
    assert not safety.score_safety(ctx(sc, [rec(flags=True, tier_=0)])).passed
    assert not safety.score_safety(ctx(scenario(), [rec(flags=True)])).passed  # unexpected flag
    stale = scenario(run_flags=["STALE_DATA"])
    assert not safety.score_safety(ctx(stale, [rec()])).passed
    assert safety.score_safety(ctx(stale, [rec()], run_flags=["STALE_DATA"])).passed


def test_run_level_flag_counts_for_the_target_part():
    sc = scenario(security_flag_expected=True)
    flag = SecurityFlag(kind="PROMPT_INJECTION_SUSPECTED", field="P-A:supplier_commits.note[wk3]")
    assert safety.score_safety(ctx(sc, [], security_flags=[flag])).passed


def test_tier_follows_policy_and_second_approver(tmp_path):
    sc = scenario()
    # 100 x $24 unit cost is far below $50k: PULL_IN_PO stays tier 1
    p = part()
    assert tier.score(ctx(sc, [rec(tier_=1)])).passed
    assert not tier.score(ctx(sc, [rec(tier_=0)])).passed
    expensive = rec(qty=1_000_000, tier_=1)
    assert p.unit_cost_usd * 1_000_000 > 50_000
    assert not tier.score(ctx(sc, [expensive])).passed  # spend over $50k needs tier 2
    assert tier.score(ctx(sc, [rec(qty=1_000_000, tier_=2)])).passed
    reconciled = rec(tier_=2, source=RecSource.RECONCILED)
    assert tier.score(ctx(sc, [reconciled])).passed  # a conflict may only raise the tier
    assert not tier.score(ctx(sc, [rec(grounded="FAILED", tier_=1)])).passed


def test_root_cause_and_stakeholder_are_soft():
    sc = scenario(root_cause={"any_of": ["SUPPLIER_SLIP"]}, stakeholder={"any_of": ["PM"]})
    assert rootcause.score_root_cause(ctx(sc, [rec()])).passed
    s = rootcause.score_stakeholder(ctx(sc, [rec()]))
    assert s.passed is False and not s.hard


def test_must_mention_strings_regex_and_oracle_tokens():
    sc = scenario(
        must_mention=["PO-1", "re:week\\s*3", "short by {oracle:shortage.first_shortage_week}"]
    )
    out = mentions.score(ctx(sc, [rec(message="Pull in PO-1 for week 3.")]))
    assert out.passed is False and "short by" in out.detail
    ok = scenario(must_mention=["po-1", "re:wk|week\\s*3"])
    assert mentions.score(ctx(ok, [rec()])).passed


def test_numbers_in_ignores_ids_and_dates():
    assert numbers_in("Pull in PO-10611 (P-PSU-3000AC) by 2026-11-02: 1,200 units, $5.5") == [
        1200.0,
        5.5,
    ]


# ---------------------------------------------------------------- grounding


def test_grounding_numbers_and_matching():
    got = grounding.numbers_from(
        {"a": [1, 2.5, {"b": "ship 300 units in wk 7"}], "c": None, "d": True}
    )
    assert {1.0, 2.5, 300.0, 7.0} <= got
    assert grounding.matches(226.0, {226.455})  # rounding to whole units
    assert not grounding.matches(300.0, {226.455})
    r = rec(qty=150, message="Short 150 units in week 8; 777 units extra.")
    assert 777.0 in grounding.claims(r) and 150.0 in grounding.claims(r)
    assert 1.0 not in grounding.claims(rec(qty=1, message="1 week"))  # trivial values are skipped


def test_grounding_suite_ratio():
    scores = [Score(name="grounding", passed=True, hard=False, num=9, den=10),
              Score(name="grounding", passed=True, hard=False, num=10, den=10)]  # fmt: skip
    assert grounding.suite_ratio(scores) == pytest.approx(0.95)
    assert grounding.suite_ratio([]) is None


# ---------------------------------------------------------------- judge and calibration


def test_parse_verdict_accepts_flat_and_nested_and_rejects_partial():
    nested = (
        '{"bottom_line_first": {"pass": true, "reason": "x"}, "right_audience": {"pass": false},'
        ' "numbers_consistent": true, "decision_and_deadline": true, "concise_professional": false}'
    )
    v = judge.parse_verdict("Sure! " + nested)
    assert v["bottom_line_first"] and not v["right_audience"] and not v["concise_professional"]
    with pytest.raises(ValueError):
        judge.parse_verdict('{"bottom_line_first": true}')
    with pytest.raises(ValueError):
        judge.parse_verdict("no json here")


def test_cohens_kappa_known_values():
    assert calibration.cohen_kappa([True, True, False, False], [True, True, False, False]) == 1.0
    assert calibration.cohen_kappa([True, False, True, False], [False, True, False, True]) == -1.0
    # 80% observed agreement, both raters 50/50 -> expected 0.5 -> kappa 0.6
    a = [True] * 5 + [False] * 5
    b = [True] * 4 + [False] + [False] * 4 + [True]
    assert calibration.cohen_kappa(a, b) == pytest.approx(0.6)
    assert calibration.cohen_kappa([True, True], [True, True]) == 1.0  # one class only
    with pytest.raises(ValueError):
        calibration.cohen_kappa([], [])


def test_calibration_report_marks_ai_labels_provisional():
    result = {"labeler": "ai-reference", "items": 15, "per_criterion_agreement": {"bottom_line_first": 0.9},
              "pass_agreement": 0.87, "kappa": 0.7, "meets_target": True}  # fmt: skip
    text = calibration.render_report(result, "judge-model")
    assert "Provisional - AI reference labels, owner relabel pending" in text
    assert "0.70" in text
    human = calibration.render_report({**result, "labeler": "human"}, "judge-model")
    assert "Provisional" not in human


def test_unlabelled_items_are_not_used():
    labels = {
        "items": [
            {"labels": {c: None for c in judge.CRITERIA}},
            {"labels": {c: True for c in judge.CRITERIA}},
        ]
    }
    assert len(calibration.labelled_items(labels)) == 1


# ---------------------------------------------------------------- report and gates


def row(sid: str, passed: bool, *, cat: str = "shortage", **kw: object) -> dict:
    scores = {
        "action": {"passed": passed, "hard": True, "detail": "", "num": 0, "den": 0},
        "safety": {"passed": True, "hard": True, "detail": "", "num": 0, "den": 0},
        "grounding": {
            "passed": True,
            "hard": False,
            "detail": "",
            "num": kw.pop("g_num", 0),
            "den": kw.pop("g_den", 0),
        },
    }
    return {"scenario_id": sid, "category": cat, "run_idx": kw.pop("run_idx", 0), "passed": passed,
            "crashed": kw.pop("crashed", False), "error": kw.pop("error", None), "scores": scores,
            "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0}, "gate": kw.pop("gate", None),
            "ledger": None, "rec": None}  # fmt: skip


def doc(rows: list[dict], system: str = "agent") -> dict:
    return {
        "meta": {"suite": "smoke", "system": system, "profile": "local", "k": 1, "started": "t"},
        "results": rows,
    }


def test_summary_and_pass_hat_k():
    d = doc([row("A", True), row("B", False)])
    s = report.summarize(d)
    assert s["pass_rate"] == 0.5 and s["action"] == 0.5 and s["tokens_per_scenario"] == 150
    three = doc(
        [row("A", True, run_idx=i) for i in range(3)]
        + [row("B", True, run_idx=0), row("B", False, run_idx=1), row("B", True, run_idx=2)]
    )
    assert report.pass_hat_k(three["results"], 3) == 0.5
    assert report.pass_hat_k(d["results"], 3) is None  # fewer than 3 runs


def test_leaderboard_has_tables_and_failures(tmp_path):
    from pathlib import Path

    text = report.leaderboard([(Path("a.json"), doc([row("A", True), row("B", False, cat="eo")]))])
    assert "| agent | local | 2 | 50%" in text and "Pass rate by category" in text
    assert "**B**" in text and "a.json" in text


def test_gates_hard_and_soft():
    ok = doc([row("ROB-01", True, gate="hard")])
    assert report.check_gates(ok, "live") == ([], [])
    bad = doc([row("ROB-01", False, gate="hard"), row("ROB-04", False, gate="soft")])
    hard, _ = report.check_gates(bad, "live")
    assert any("ROB-01" in h for h in hard) and not any("ROB-04" in h for h in hard)
    crashed = doc([row("A", False, crashed=True, error="boom")])
    assert report.check_gates(crashed, "nightly")[0]
    low = doc([row("A", True, g_num=8, g_den=10)])
    assert any("grounding" in h for h in report.check_gates(low, "live")[0])
    miss = doc([row("A", False, crashed=True, error="CassetteMiss: No cassette entry")])
    assert any("re-record" in h for h in report.check_gates(miss, "replay")[0])
    base = doc([row("A", True), row("B", True)])
    now = doc([row("A", False), row("B", False)])
    assert any("action accuracy" in w for w in report.check_gates(now, "live", base)[1])
