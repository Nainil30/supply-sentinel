"""Worked examples A, B and C from docs/specs/02_ENGINE.md section 8, written before the engine."""

import math

import pytest

from supply_sentinel.engine import eo, exceptions, ltb, netting, root_cause, rules_baseline
from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.models import (
    ActionType,
    ExceptionType,
    RootCause,
    Severity,
)
from tests.fixtures.toy_parts import example_a, example_c

CFG = EngineConfig()


# ---------------------------------------------------------------- Example A


def test_example_a_pab_as_promised():
    result = netting.net_position(example_a(), CFG.horizon_weeks)
    assert result.pab[1:7] == [50, 20, -20, -60, 30, 0]
    assert result.first_shortage_week == 3
    assert result.max_shortfall_units == 60
    assert result.recovery_week == 5


def test_example_a_pab_if_po_a1_on_time():
    inp = example_a()
    inp.pos[0].promise_week_idx = 3
    assert netting.net_position(inp, CFG.horizon_weeks).pab[1:7] == [50, 20, 40, 0, 30, 0]


def test_example_a_exception_and_severity():
    (exc,) = exceptions.detect_part_exceptions(example_a(), CFG)
    assert exc.type is ExceptionType.SHORTAGE
    assert exc.exception_id == "EXC-SHT-P-EXA"
    assert exc.first_shortage_week == 3
    assert exc.max_shortfall_units == 60
    assert exc.recovery_week == 5
    assert exc.revenue_at_risk_usd == pytest.approx(1_440_000)
    assert exc.severity is Severity.P1


def test_example_a_root_cause_is_supplier_slip():
    result = root_cause.explain_root_cause(example_a(), CFG.horizon_weeks)
    assert result.primary is RootCause.SUPPLIER_SLIP
    slip = next(c for c in result.counterfactuals if c.cause is RootCause.SUPPLIER_SLIP)
    assert slip.pab[1:7] == [50, 20, 40, 0, 30, 0]
    assert slip.removes_shortage


def test_example_a_baseline_pulls_in_po_a1():
    inp = example_a()
    (exc,) = exceptions.detect_part_exceptions(inp, CFG)
    rec = rules_baseline.recommend(inp, exc, CFG)
    assert rec.primary_action.action_type is ActionType.PULL_IN_PO
    assert rec.primary_action.po_ids == ["PO-A1"]
    assert rec.primary_action.quantity == 60


# ---------------------------------------------------------------- Example B


def example_b() -> ltb.NewsvendorResult:
    return ltb.newsvendor_size(
        mu_units=1200,
        sigma_units=150,
        unit_cost_usd=38,
        salvage_pct=0.10,
        holding_rate=0.20,
        avg_years_held=2,
        penalty_multiple=5,
        usable_on_hand_units=180,
        open_po_units=100,
        order_multiple=50,
        moq=1,
    )


def test_example_b_newsvendor_values():
    r = example_b()
    assert r.overage_cost_usd == pytest.approx(49.40)
    assert r.underage_cost_usd == pytest.approx(152.00)
    assert r.critical_ratio == pytest.approx(0.7547, abs=1e-4)
    assert r.z == pytest.approx(0.6894, abs=1e-3)
    assert r.q_star_units == pytest.approx(1303.41, abs=0.5)
    assert r.gross_units == 1304
    assert r.net_units == 1024
    assert r.order_qty_units == 1050
    assert r.spend_usd == pytest.approx(39_900)


def test_example_b_service_level_sensitivity():
    assert ltb.newsvendor_target(1200, 150, 0.90) == pytest.approx(1392.2, abs=0.1)
    assert ltb.newsvendor_target(1200, 150, 0.95) == pytest.approx(1446.7, abs=0.1)


def test_example_b_moq_lifts_a_small_order():
    r = ltb.newsvendor_size(
        mu_units=100, sigma_units=10, unit_cost_usd=10, salvage_pct=0.1, holding_rate=0.2,
        avg_years_held=1, penalty_multiple=5, usable_on_hand_units=0, open_po_units=0,
        order_multiple=10, moq=500,
    )  # fmt: skip
    assert r.net_units < 500
    assert r.order_qty_units == 500


def test_ltb_net_zero_when_covered():
    r = ltb.newsvendor_size(
        mu_units=100, sigma_units=10, unit_cost_usd=10, salvage_pct=0.1, holding_rate=0.2,
        avg_years_held=1, penalty_multiple=5, usable_on_hand_units=500, open_po_units=0,
        order_multiple=10, moq=100,
    )  # fmt: skip
    assert r.net_units == 0
    assert r.order_qty_units == 0
    assert r.spend_usd == 0


# ---------------------------------------------------------------- Example C


def test_example_c_eo_values():
    r = eo.compute_eo_exposure(example_c(), CFG)
    assert r.supply_units == 1500
    assert r.need_52w_units == pytest.approx(1100)
    assert r.safety_stock_units == pytest.approx(80)
    assert r.excess_units == pytest.approx(320)
    assert r.excess_usd == pytest.approx(9920)
    assert r.cancellable_po_qty == 200
    assert r.avoidable_by_cancel_usd == pytest.approx(6200)
    assert r.residual_excess_units == pytest.approx(120)
    assert r.residual_excess_usd == pytest.approx(3720)
    assert r.cancel_qty_recommended == pytest.approx(200)


def test_example_c_exception_severity_and_baseline():
    inp = example_c()
    (exc,) = exceptions.detect_part_exceptions(inp, CFG)
    assert exc.type is ExceptionType.EXCESS
    assert exc.severity is Severity.P3
    rec = rules_baseline.recommend(inp, exc, CFG)
    assert rec.primary_action.action_type is ActionType.CANCEL_PO
    assert rec.primary_action.quantity == 200
    assert not math.isnan(rec.primary_action.quantity)
