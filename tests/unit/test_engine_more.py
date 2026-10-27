"""Engine behaviour beyond the three worked examples: severity, root causes, bridge, allocation,
what-if and rules baseline coverage. Numbers are worked by hand in the comments."""

import datetime as dt

import pytest

from supply_sentinel.engine import (
    allocation,
    eo,
    exceptions,
    ltb,
    netting,
    root_cause,
    rules_baseline,
    severity,
    whatif,
)
from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.models import (
    ActionType,
    Alternate,
    EolNotice,
    ExceptionType,
    PartInputs,
    PurchaseOrder,
    RootCause,
    Severity,
)
from tests.fixtures.toy_parts import example_a, platform

CFG = EngineConfig()


def base_part(**overrides: object) -> PartInputs:
    data: dict[str, object] = {
        "part_id": "P-T",
        "unit_cost_usd": 10.0,
        "lead_time_weeks": 4,
        "moq": 10,
        "order_multiple": 10,
        "on_hand_units": 100,
        "where_used": [platform([10.0] * 30)],
    }
    data.update(overrides)
    return PartInputs(**data)  # type: ignore[arg-type]


def shortage_of(inp: PartInputs):
    return next(
        e for e in exceptions.detect_part_exceptions(inp, CFG) if e.type is ExceptionType.SHORTAGE
    )


# ---------------------------------------------------------------- severity


@pytest.mark.parametrize(
    ("revenue", "week", "expected"),
    [
        (250_000, 4, Severity.P1),
        (249_999, 4, Severity.P2),  # revenue just under the P1 line, week within 8
        (1_000_000, 4, Severity.P1),
        (1_000_000, 5, Severity.P2),  # P1 also needs the shortage inside four weeks
        (1_000_000, 9, Severity.P2),  # revenue is high but the shortage is late: P2 by revenue
        (49_999, 9, Severity.P3),
        (49_999, 8, Severity.P2),
    ],
)
def test_shortage_severity(revenue, week, expected):
    assert severity.shortage_severity(revenue, week) is expected


def test_other_severity_tables():
    assert severity.commit_gap_severity([3, 9]) is Severity.P1
    assert severity.commit_gap_severity([5]) is Severity.P2
    assert severity.commit_gap_severity([9]) is Severity.P3
    assert [severity.eol_severity(d) for d in (30, 31, 90, 91)] == [
        Severity.P1,
        Severity.P2,
        Severity.P2,
        Severity.P3,
    ]
    assert [severity.exposure_severity(x) for x in (100_000, 20_000, 19_999)] == [
        Severity.P1,
        Severity.P2,
        Severity.P3,
    ]


# ---------------------------------------------------------------- netting and root causes


def test_commits_override_po_quantity_in_their_week():
    inp = example_a(commits={3: 0, 5: 120})  # supplier commits nothing in week 3
    assert netting.receipts(inp, 8)[3] == 0
    assert netting.receipts(inp, 8)[5] == 120


def test_coverage_weeks():
    # usable 80 / mean(GR weeks 0-12) = 80 / (200 / 13) = 5.2
    assert netting.net_position(example_a()).coverage_weeks == pytest.approx(5.2)


def test_root_cause_quality_hold():
    # need 10/wk for 10 weeks = 100; on hand 100 of which 50 held -> short 50; releasing fixes it
    inp = base_part(
        quality_hold_units=50, where_used=[platform([10.0] * 10 + [0.0] * 20)], on_hand_units=100
    )
    assert root_cause.explain_root_cause(inp).primary is RootCause.QUALITY_HOLD


def test_root_cause_commit_gap():
    # PO of 100 promised week 2 but the supplier commits 40; need 10/wk for 12 weeks, on hand 30
    inp = base_part(
        on_hand_units=30,
        where_used=[platform([10.0] * 12 + [0.0] * 18)],
        pos=[PurchaseOrder(po_id="PO-1", qty_units=100, need_week_idx=2, promise_week_idx=2)],
        commits={2: 40},
    )
    # promised: 30 + 100 = 130 >= 120 need, so no shortage; with the commit: 30 + 40 = 70 < 120
    assert root_cause.explain_root_cause(inp).primary is RootCause.COMMIT_GAP


def test_root_cause_demand_spike():
    # forecast rose from 10 to 20/wk; stock 100 covers the old rate for 10 weeks
    inp = base_part(
        where_used=[platform([20.0] * 5 + [0.0] * 25, production_prev=[10.0] * 5 + [0.0] * 25)],
        on_hand_units=60,
    )  # need 100 vs 60 now (short 40); old forecast needs 50 (fine)
    assert root_cause.explain_root_cause(inp).primary is RootCause.DEMAND_SPIKE


def test_root_cause_structural_with_contributing_cause():
    # stock 10, need 20/wk x 20 weeks = 400 -> short 390; old forecast 15/wk -> short 290
    inp = base_part(
        on_hand_units=10,
        where_used=[platform([20.0] * 20 + [0.0] * 10, production_prev=[15.0] * 20 + [0.0] * 10)],
    )
    result = root_cause.explain_root_cause(inp)
    assert result.primary is RootCause.STRUCTURAL
    demand = next(c for c in result.counterfactuals if c.cause is RootCause.DEMAND_SPIKE)
    assert demand.shortfall_reduction_pct == pytest.approx(25.6, abs=0.1)  # (390 - 290) / 390
    assert result.contributing == [RootCause.DEMAND_SPIKE]


def test_no_shortage_raises():
    with pytest.raises(ValueError):
        root_cause.explain_root_cause(base_part(on_hand_units=100_000))


# ---------------------------------------------------------------- LTB with bridge


def eol_part(status: str = "IN_QUAL", qual_weeks: int = 12) -> PartInputs:
    # production 10/week for weeks 0-19 (mu = 200), no service, forecast MAPE 0.2
    return base_part(
        commodity="CPLD",
        on_hand_units=50,
        where_used=[platform([10.0] * 20, mape=0.2)],
        alternates=[
            Alternate(
                alt_part_id="P-ALT", qualification_status=status, qual_lead_time_weeks=qual_weeks
            )
        ],
        eol_notice=EolNotice(notice_id="EOL-1", ltb_date=None, last_ship_date=None),
    )


def test_ltb_from_part_data():
    r = ltb.compute_ltb_quantity(eol_part(), CFG)
    assert r.sizing.mu_units == pytest.approx(200)
    assert r.cv == pytest.approx(0.2)
    # receipt in week 4 (lead time), need ends in week 20: 16 weeks, half of that on average
    assert r.avg_years_held == pytest.approx(16 / 52 / 2, abs=1e-4)
    assert r.sizing.sigma_units == pytest.approx(40)
    assert r.sizing.net_units == r.sizing.gross_units - 50
    assert r.sizing.order_qty_units % 10 == 0
    assert not r.requires_second_approver


def test_cv_never_below_floor():
    inp = eol_part()
    inp.where_used[0].mape = 0.02
    assert ltb.compute_ltb_quantity(inp, CFG).cv == pytest.approx(0.10)


def test_bridge_window_depends_on_qualification_status():
    in_qual = ltb.compute_ltb_quantity(eol_part("IN_QUAL", 12), CFG).bridge
    assert in_qual is not None
    assert in_qual.window_weeks == 20  # 12 weeks qualification + 8 weeks cutover buffer
    assert in_qual.sizing.mu_units == pytest.approx(200)  # production runs 20 weeks
    qualified = ltb.compute_ltb_quantity(eol_part("QUALIFIED", 0), CFG).bridge
    assert qualified is not None
    assert qualified.window_weeks == 8
    assert qualified.sizing.mu_units == pytest.approx(80)
    no_alt = eol_part().model_copy(update={"alternates": []})
    assert ltb.compute_ltb_quantity(no_alt, CFG).bridge is None


def test_second_approver_over_threshold():
    inp = eol_part().model_copy(update={"unit_cost_usd": 5_000.0})
    assert ltb.compute_ltb_quantity(inp, CFG).requires_second_approver


def test_asic_commodity_uses_higher_penalty():
    plain = ltb.compute_ltb_quantity(eol_part(), CFG).sizing
    asic = ltb.compute_ltb_quantity(eol_part().model_copy(update={"commodity": "ASIC"}), CFG).sizing
    assert asic.underage_cost_usd > plain.underage_cost_usd
    assert asic.q_star_units > plain.q_star_units


# ---------------------------------------------------------------- E&O, obsolescence, gaps


def test_obsolescence_supersedes_excess():
    # platform is EOL_ANNOUNCED; lifetime need 100 (10 x 10 weeks); supply 100 + 400 on a PO
    inp = base_part(
        on_hand_units=100,
        unit_cost_usd=100.0,
        where_used=[platform([10.0] * 10, phase="EOL_ANNOUNCED")],
        pos=[PurchaseOrder(po_id="PO-1", qty_units=400, need_week_idx=30, promise_week_idx=30)],
    )
    (exc,) = exceptions.detect_part_exceptions(inp, CFG)
    assert exc.type is ExceptionType.OBSOLESCENCE_RISK
    result = eo.compute_eo_exposure(inp, CFG)
    assert result.obsolete_units == pytest.approx(400)
    assert result.obsolete_usd == pytest.approx(40_000)
    assert exc.severity is Severity.P2
    rec = rules_baseline.recommend(inp, exc, CFG)
    assert rec.primary_action.action_type is ActionType.DISPOSITION_REVIEW
    assert rec.primary_action.quantity == pytest.approx(400)


def test_commit_gap_exception_only_without_shortage():
    inp = base_part(
        on_hand_units=500,
        pos=[PurchaseOrder(po_id="PO-1", qty_units=100, need_week_idx=6, promise_week_idx=6)],
        commits={6: 70},
    )
    (exc,) = exceptions.detect_part_exceptions(inp, CFG)
    assert exc.type is ExceptionType.COMMIT_GAP
    assert exc.gap_weeks == [6]
    assert exc.gap_units == 30
    assert exc.severity is Severity.P2
    rec = rules_baseline.recommend(inp, exc, CFG)
    assert rec.primary_action.action_type is ActionType.ESCALATE_SUPPLIER
    assert rec.primary_action.quantity == 30


# ---------------------------------------------------------------- allocation


def test_allocation_serves_service_first_then_priority_class():
    # on hand 100; ORB-A: 30/wk production (NPI) + 10/wk service; ORB-B: 30/wk (SUSTAIN).
    # PAB: wk0 30, wk1 -40 -> window is weeks 1-3 with carry-in 30
    inp = base_part(
        on_hand_units=100,
        where_used=[
            platform([30.0] * 4, platform_id="ORB-A", phase="NPI", service=[10.0] * 4),
            platform([30.0] * 4, platform_id="ORB-B", phase="SUSTAIN"),
        ],
    )
    result = allocation.allocate(inp, CFG.allocation_priority, horizon_weeks=4)
    by = {a.platform_id: a for a in result.allocations}
    assert (result.window_start_week, result.window_end_week) == (1, 3)
    assert result.available_units == pytest.approx(30)
    assert result.total_demand_units == pytest.approx(210)  # A 90 + 30 service, B 90
    assert by["ORB-A"].allocated_units == pytest.approx(30)  # all of it covers service first
    assert by["ORB-B"].allocated_units == pytest.approx(0)
    assert by["ORB-B"].unmet_units == pytest.approx(90)


def test_allocation_pro_rata_inside_a_class():
    # on hand 60, demand 40/wk: PAB wk0 20, wk1 -20 -> window is week 1 with carry-in 20.
    # Same class (SUSTAIN): demand 30 and 10 share the 20 units 3:1
    inp = base_part(
        on_hand_units=60,
        where_used=[
            platform([30.0] * 2, platform_id="ORB-A", phase="SUSTAIN"),
            platform([10.0] * 2, platform_id="ORB-B", phase="SUSTAIN"),
        ],
    )
    by = {
        a.platform_id: a
        for a in allocation.allocate(inp, CFG.allocation_priority, horizon_weeks=2).allocations
    }
    assert by["ORB-A"].allocated_units == pytest.approx(15)
    assert by["ORB-B"].allocated_units == pytest.approx(5)
    assert by["ORB-A"].share == pytest.approx(0.75)


# ---------------------------------------------------------------- what-if


def test_whatif_slip_all_and_expedite():
    base = whatif.simulate(example_a())
    assert base.shortfall_delta_units == 0
    slipped = whatif.simulate(example_a(), slip_all_weeks=2)
    assert slipped.max_shortfall_units > base.max_shortfall_units
    fixed = whatif.simulate(example_a(), expedite={"qty": 60, "to_week": 3})
    assert fixed.max_shortfall_units == 0
    assert fixed.first_shortage_week is None
    assert fixed.shortfall_delta_units == -60


def test_whatif_pull_in_matches_example_a():
    result = whatif.simulate(example_a(), po_slip={"PO-A1": -2})
    assert result.pab[1:7] == [50, 20, 40, 0, 30, 0]
    assert result.max_shortfall_units == 0


def test_whatif_demand_change_hold_release_and_allocation():
    inp = example_a(quality_hold_units=10, on_hand_units=90)  # usable 80, shortfall 60
    assert whatif.simulate(inp, release_hold_units=10).max_shortfall_units == 50
    assert whatif.simulate(inp, release_hold_units=99).max_shortfall_units == 50  # capped at hold
    assert whatif.simulate(inp, demand_change_pct={"ORB-T1": -100}).max_shortfall_units == 0
    assert whatif.simulate(inp, allocation={"ORB-T1": 0.5}).max_shortfall_units == 0


def test_whatif_does_not_mutate_inputs():
    inp = example_a()
    before = inp.model_dump()
    whatif.simulate(
        inp, slip_all_weeks=3, expedite={"qty": 5, "to_week": 1}, demand_change_pct={"ORB-T1": 50}
    )
    assert inp.model_dump() == before


# ---------------------------------------------------------------- rules baseline


def test_baseline_expedite_inside_lead_time_and_place_po_beyond():
    inside = base_part(on_hand_units=0, lead_time_weeks=12, where_used=[platform([10.0] * 30)])
    rec = rules_baseline.recommend(inside, shortage_of(inside), CFG)
    assert rec.primary_action.action_type is ActionType.EXPEDITE
    assert rec.primary_action.quantity == 260  # 26 weeks x 10 units, nothing on hand
    # 12 weeks of 10/wk = 120 needed by week 11; stock 115 -> short 5 from week 11 (>= lead 4)
    outside = base_part(
        on_hand_units=115, lead_time_weeks=4, where_used=[platform([10.0] * 12 + [0.0] * 18)]
    )
    rec2 = rules_baseline.recommend(outside, shortage_of(outside), CFG)
    assert rec2.primary_action.action_type is ActionType.PLACE_PO
    assert rec2.primary_action.quantity == 10  # shortfall 5 rounded up to the order multiple


def test_baseline_quality_hold():
    held = base_part(
        on_hand_units=100, quality_hold_units=50, where_used=[platform([10.0] * 10 + [0.0] * 20)]
    )
    rec = rules_baseline.recommend(held, shortage_of(held), CFG)
    assert rec.primary_action.action_type is ActionType.REVIEW_QUALITY_HOLD
    assert rec.primary_action.quantity == 50


def test_baseline_ltb_buy_and_monitor():
    part = eol_part()
    part.eol_notice = EolNotice(notice_id="E", ltb_date=dt.date(2026, 11, 20), last_ship_date=None)
    exc = next(
        e
        for e in exceptions.detect_part_exceptions(part, CFG)
        if e.type is ExceptionType.EOL_NOTICE
    )
    assert exc.days_to_ltb == 18
    assert exc.severity is Severity.P1
    rec = rules_baseline.recommend(part, exc, CFG)
    assert rec.primary_action.action_type is ActionType.LTB_BUY
    assert rec.primary_action.quantity == ltb.compute_ltb_quantity(part, CFG).sizing.order_qty_units
    covered = part.model_copy(update={"on_hand_units": 100_000})
    rec = rules_baseline.recommend(covered, exc, CFG)
    assert rec.primary_action.action_type is ActionType.MONITOR


def test_baseline_excess_without_cancellable_pos_pushes_out():
    inp = base_part(
        on_hand_units=5_000,
        unit_cost_usd=10.0,
        where_used=[platform([10.0] * 52)],  # need 520 + safety 20
    )
    (exc,) = exceptions.detect_part_exceptions(inp, CFG)
    assert exc.type is ExceptionType.EXCESS
    rec = rules_baseline.recommend(inp, exc, CFG)
    assert rec.primary_action.action_type is ActionType.PUSH_OUT_PO
    assert rec.primary_action.quantity == pytest.approx(5_000 - 520 - 20)


def test_every_baseline_message_is_short_and_labeled():
    for inp in (example_a(),):
        for exc in exceptions.detect_part_exceptions(inp, CFG):
            rec = rules_baseline.recommend(inp, exc, CFG)
            assert rec.source.value == "RULES_BASELINE"
            assert len(rec.draft_message.split()) <= 150
