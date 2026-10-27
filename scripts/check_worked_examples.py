"""Recompute worked examples A, B and C from docs/specs/02_ENGINE.md with the standard library
only, then compare with what the engine returns. Exit code 1 on any mismatch.

The arithmetic below is written the way you would do it on paper or in a spreadsheet, so it can
be read side by side with the spec. Run: `uv run python scripts/check_worked_examples.py`.
"""

import math
import sys
from statistics import NormalDist

from supply_sentinel.engine import eo, exceptions, ltb, netting
from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.models import PartInputs, PurchaseOrder, WhereUsed

FAILURES: list[str] = []


def check(label: str, expected: float, actual: float, tol: float) -> None:
    ok = abs(expected - actual) <= tol
    print(f"{'ok  ' if ok else 'FAIL'} {label}: by hand {expected:,.4f}, engine {actual:,.4f}")
    if not ok:
        FAILURES.append(label)


def by_hand_example_a() -> list[float]:
    gr = {1: 30, 2: 30, 3: 40, 4: 40, 5: 30, 6: 30}
    receipts = {5: 60 + 60}  # PO-A1 and PO-A2 both promised for week 5
    balance, pab = 80.0, []
    for week in range(1, 7):
        balance += receipts.get(week, 0) - gr[week]
        pab.append(balance)
    return pab


def by_hand_example_b() -> dict[str, float]:
    mu, sigma, c, multiple = 1200, 150, 38, 50
    overage = c - 0.10 * c + 0.20 * c * 2
    underage = (5 - 1) * c
    ratio = underage / (underage + overage)
    z = NormalDist().inv_cdf(ratio)
    q_star = mu + z * sigma
    gross = math.ceil(q_star)
    net = max(0, gross - 180 - 100)
    order = math.ceil(net / multiple) * multiple
    return {
        "Co": overage, "Cu": underage, "CR": ratio, "z": z, "Q*": q_star,
        "gross": gross, "net": net, "order": order, "spend": order * c,
        "Q* at CR 0.90": mu + NormalDist().inv_cdf(0.90) * sigma,
        "Q* at CR 0.95": mu + NormalDist().inv_cdf(0.95) * sigma,
    }  # fmt: skip


def by_hand_example_c() -> dict[str, float]:
    c, on_hand, pos, cancellable, need, safety = 31, 900, 600, 200, 1100, 80
    supply = on_hand + pos
    excess = supply - need - safety
    return {
        "supply": supply, "excess units": excess, "excess usd": excess * c,
        "avoidable usd": min(excess, cancellable) * c,
        "residual units": excess - min(excess, cancellable),
    }  # fmt: skip


def platform(weekly: list[float]) -> WhereUsed:
    padded = weekly + [0.0] * (60 - len(weekly))
    return WhereUsed(
        platform_id="ORB-T1", qty_per=1, lifecycle_phase="SUSTAIN", asp_usd=24000,
        production=padded, production_prev=padded, service=[0.0] * 60,
    )  # fmt: skip


def main() -> int:
    cfg = EngineConfig()

    hand_a = by_hand_example_a()
    part_a = PartInputs(
        part_id="P-EXA", unit_cost_usd=100, lead_time_weeks=12, on_hand_units=80,
        pos=[
            PurchaseOrder(po_id="PO-A1", qty_units=60, need_week_idx=3, promise_week_idx=5),
            PurchaseOrder(po_id="PO-A2", qty_units=60, need_week_idx=5, promise_week_idx=5),
        ],
        where_used=[platform([0, 30, 30, 40, 40, 30, 30])],
    )  # fmt: skip
    engine_a = netting.net_position(part_a, cfg.horizon_weeks)
    for week, expected in enumerate(hand_a, start=1):
        check(f"A: PAB week {week}", expected, engine_a.pab[week], 1e-6)
    check("A: max shortfall", -min(hand_a), engine_a.max_shortfall_units, 1e-6)
    revenue = exceptions.revenue_at_risk(part_a, engine_a)
    check("A: revenue at risk", 60 * 24000, revenue, 1e-6)  # 60 units x $24,000 ASP

    hand_b = by_hand_example_b()
    engine_b = ltb.newsvendor_size(
        mu_units=1200, sigma_units=150, unit_cost_usd=38, salvage_pct=0.10, holding_rate=0.20,
        avg_years_held=2, penalty_multiple=5, usable_on_hand_units=180, open_po_units=100,
        order_multiple=50, moq=1,
    )  # fmt: skip
    pairs = {
        "Co": engine_b.overage_cost_usd, "Cu": engine_b.underage_cost_usd,
        "CR": engine_b.critical_ratio, "z": engine_b.z, "Q*": engine_b.q_star_units,
        "gross": engine_b.gross_units, "net": engine_b.net_units,
        "order": engine_b.order_qty_units, "spend": engine_b.spend_usd,
    }  # fmt: skip
    for name, actual in pairs.items():
        check(f"B: {name}", hand_b[name], actual, 1e-3)
    check("B: Q* at CR 0.90", hand_b["Q* at CR 0.90"], ltb.newsvendor_target(1200, 150, 0.90), 1e-6)
    check("B: Q* at CR 0.95", hand_b["Q* at CR 0.95"], ltb.newsvendor_target(1200, 150, 0.95), 1e-6)

    hand_c = by_hand_example_c()
    weekly = [40.0] * 13 + [20.0] * 29  # 52-week need 1,100; average over weeks 0-12 is 40
    part_c = PartInputs(
        part_id="P-FAN-80", unit_cost_usd=31, lead_time_weeks=10, on_hand_units=900,
        pos=[
            PurchaseOrder(po_id="PO-C1", qty_units=400, need_week_idx=2, promise_week_idx=2,
                          cancel_window_weeks=4),
            PurchaseOrder(po_id="PO-C2", qty_units=200, need_week_idx=10, promise_week_idx=10,
                          cancel_window_weeks=4),
        ],
        where_used=[platform(weekly)],
    )  # fmt: skip
    engine_c = eo.compute_eo_exposure(part_c, cfg)
    check("C: supply", hand_c["supply"], engine_c.supply_units, 0)
    check("C: excess units", hand_c["excess units"], engine_c.excess_units, 1e-6)
    check("C: excess usd", hand_c["excess usd"], engine_c.excess_usd, 1e-6)
    check("C: avoidable usd", hand_c["avoidable usd"], engine_c.avoidable_by_cancel_usd, 1e-6)
    check("C: residual units", hand_c["residual units"], engine_c.residual_excess_units, 1e-6)

    print("ALL MATCH" if not FAILURES else f"MISMATCH in: {', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
