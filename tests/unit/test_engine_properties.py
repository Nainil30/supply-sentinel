"""Property tests (spec 02 section 9): conservation, monotonicity, root cause, determinism."""

from hypothesis import given, settings
from hypothesis import strategies as st
from pytest import approx

from supply_sentinel.engine import exceptions, ltb, netting, root_cause
from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.models import PartInputs, PurchaseOrder, RootCause
from tests.fixtures.toy_parts import platform

CFG = EngineConfig()
H = CFG.horizon_weeks
SETTINGS = settings(max_examples=100, deadline=None)

demand = st.lists(st.integers(0, 50), min_size=H, max_size=H)
po_strategy = st.builds(
    PurchaseOrder,
    po_id=st.just("PO-X"),
    qty_units=st.integers(1, 500),
    need_week_idx=st.integers(0, H - 1),
    promise_week_idx=st.integers(0, H - 1),
)


def part(on_hand: int, gr: list[int], pos: list[PurchaseOrder]) -> PartInputs:
    numbered = [po.model_copy(update={"po_id": f"PO-{i}"}) for i, po in enumerate(pos)]
    return PartInputs(
        part_id="P-PROP",
        unit_cost_usd=10.0,
        lead_time_weeks=4,
        on_hand_units=on_hand,
        pos=numbered,
        where_used=[platform([float(x) for x in gr])],
    )


@SETTINGS
@given(on_hand=st.integers(0, 2000), gr=demand, pos=st.lists(po_strategy, max_size=6))
def test_conservation(on_hand, gr, pos):
    inp = part(on_hand, gr, pos)
    net = netting.net_position(inp, H)
    expected = on_hand + sum(net.receipts) - sum(net.gross_requirements)
    assert net.pab[-1] == approx(expected, abs=1e-4)
    assert sum(net.receipts) == sum(po.qty_units for po in pos)  # no commits: every PO lands


@SETTINGS
@given(
    on_hand=st.integers(0, 2000),
    gr=demand,
    pos=st.lists(po_strategy, max_size=6),
    extra_qty=st.integers(1, 500),
    extra_week=st.integers(0, H - 1),
)
def test_adding_a_receipt_never_lowers_any_pab(on_hand, gr, pos, extra_qty, extra_week):
    inp = part(on_hand, gr, pos)
    before = netting.net_position(inp, H).pab
    after = netting.net_position(netting.add_receipt(inp, "PO-NEW", extra_qty, extra_week), H).pab
    assert all(a >= b - 1e-9 for a, b in zip(after, before, strict=True))


newsvendor_args = dict(
    mu=st.floats(10, 5000),
    cost=st.floats(1, 500),
    years=st.floats(0, 5),
    multiple=st.floats(1.5, 10),
)


@SETTINGS
@given(
    mu=newsvendor_args["mu"],
    cost=newsvendor_args["cost"],
    years=newsvendor_args["years"],
    multiple=newsvendor_args["multiple"],
    bump=st.floats(0.1, 5),
    cv=st.floats(0.05, 0.5),
)
def test_higher_underage_cost_never_lowers_target(mu, cost, years, multiple, bump, cv):
    def q_star(m: float) -> float:
        return ltb.newsvendor_size(
            mu_units=mu, sigma_units=cv * mu, unit_cost_usd=cost, salvage_pct=0.1,
            holding_rate=0.2, avg_years_held=years, penalty_multiple=m,
            usable_on_hand_units=0, open_po_units=0, order_multiple=1, moq=1,
        ).q_star_units  # fmt: skip

    assert q_star(multiple + bump) >= q_star(multiple) - 1e-6


@SETTINGS
@given(mu=st.floats(10, 5000), cv=st.floats(0.05, 0.4), more=st.floats(0.01, 0.3))
def test_more_uncertainty_raises_target_when_ratio_above_half(mu, cv, more):
    # default penalty multiple 5 and holding 0.2/yr over < 2 years keeps CR well above 0.5
    def q_star(c: float) -> float:
        return ltb.newsvendor_size(
            mu_units=mu, sigma_units=c * mu, unit_cost_usd=20, salvage_pct=0.1,
            holding_rate=0.2, avg_years_held=1, penalty_multiple=5,
            usable_on_hand_units=0, open_po_units=0, order_multiple=1, moq=1,
        ).q_star_units  # fmt: skip

    assert ltb.newsvendor_size(
        mu_units=mu, sigma_units=cv * mu, unit_cost_usd=20, salvage_pct=0.1, holding_rate=0.2,
        avg_years_held=1, penalty_multiple=5, usable_on_hand_units=0, open_po_units=0,
        order_multiple=1, moq=1,
    ).critical_ratio > 0.5  # fmt: skip
    assert q_star(cv + more) > q_star(cv)


@SETTINGS
@given(
    per_week=st.integers(1, 40),
    weeks=st.integers(2, 12),
    slip=st.integers(1, 6),
)
def test_a_slip_only_perturbation_is_diagnosed_as_a_slip(per_week, weeks, slip):
    """One PO covers all demand when on time (week 0); promising it `slip` weeks later starves
    weeks 0..slip-1 and nothing else is wrong."""
    gr = [per_week] * weeks + [0] * (H - weeks)
    po = PurchaseOrder(
        po_id="PO-1",
        qty_units=per_week * weeks,
        need_week_idx=0,
        promise_week_idx=slip,
    )
    inp = part(0, gr, [po])
    on_time = netting.net_position(
        inp.model_copy(update={"pos": [po.model_copy(update={"promise_week_idx": 0})]}), H
    )
    assert on_time.first_shortage_week is None
    result = root_cause.explain_root_cause(inp, H)
    assert result.primary is RootCause.SUPPLIER_SLIP


@SETTINGS
@given(on_hand=st.integers(0, 500), gr=demand, pos=st.lists(po_strategy, max_size=4))
def test_engine_is_deterministic(on_hand, gr, pos):
    inp = part(on_hand, gr, pos)
    first = [e.model_dump() for e in exceptions.detect_part_exceptions(inp, CFG)]
    second = [
        e.model_dump() for e in exceptions.detect_part_exceptions(inp.model_copy(deep=True), CFG)
    ]
    assert first == second
    assert netting.net_position(inp, H) == netting.net_position(inp, H)
