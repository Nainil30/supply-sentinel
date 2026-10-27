"""Hand-built `PartInputs` for the worked examples in docs/specs/02_ENGINE.md section 8.

None of this touches DuckDB or generated data, so the numbers can be checked with a pencil.
"""

from supply_sentinel.engine.models import PartInputs, PurchaseOrder, WhereUsed

HORIZON = 60  # weeks of demand arrays in the toy fixtures


def pad(values: list[float], n: int = HORIZON) -> list[float]:
    return values + [0.0] * (n - len(values))


def platform(
    production: list[float],
    *,
    platform_id: str = "ORB-T1",
    qty_per: int = 1,
    asp: float = 24000.0,
    phase: str = "SUSTAIN",
    production_prev: list[float] | None = None,
    service: list[float] | None = None,
    mape: float | None = None,
) -> WhereUsed:
    return WhereUsed(
        platform_id=platform_id,
        qty_per=qty_per,
        lifecycle_phase=phase,
        asp_usd=asp,
        production=pad(production),
        production_prev=pad(production_prev if production_prev is not None else production),
        service=pad(service or []),
        mape=mape,
    )


def example_a(**overrides: object) -> PartInputs:
    """Supplier slip: usable 80, GR weeks 1-6 = 30,30,40,40,30,30; PO-A1 slips from week 3 to 5."""
    data: dict[str, object] = {
        "part_id": "P-EXA",
        "unit_cost_usd": 100.0,
        "lead_time_weeks": 12,
        "moq": 1,
        "order_multiple": 1,
        "on_hand_units": 80,
        "pos": [
            PurchaseOrder(po_id="PO-A1", qty_units=60, need_week_idx=3, promise_week_idx=5),
            PurchaseOrder(po_id="PO-A2", qty_units=60, need_week_idx=5, promise_week_idx=5),
        ],
        "where_used": [platform([0, 30, 30, 40, 40, 30, 30])],
    }
    data.update(overrides)
    return PartInputs(**data)  # type: ignore[arg-type]


def example_c() -> PartInputs:
    """E&O: on-hand 900, POs 600 (200 cancellable), 52-week need 1,100, safety stock 80, c = $31."""
    weekly = [40.0] * 13 + [20.0] * 29  # weeks 0-12 average 40 -> safety 80; total 520 + 580
    return PartInputs(
        part_id="P-FAN-80",
        unit_cost_usd=31.0,
        lead_time_weeks=10,
        moq=100,
        order_multiple=50,
        on_hand_units=900,
        pos=[
            PurchaseOrder(po_id="PO-C1", qty_units=400, need_week_idx=2, promise_week_idx=2,
                          cancel_window_weeks=4),
            PurchaseOrder(po_id="PO-C2", qty_units=200, need_week_idx=10, promise_week_idx=10,
                          cancel_window_weeks=4),
        ],
        where_used=[platform(weekly, asp=5000.0)],
    )  # fmt: skip
