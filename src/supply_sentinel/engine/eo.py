"""Excess and obsolete (E&O) exposure (spec 02 section 5)."""

from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.models import EoResult, PartInputs
from supply_sentinel.engine.netting import EPS, gross_requirements

ENDING_PHASES = {"EOL_ANNOUNCED", "EOL"}


def all_platforms_ending(inp: PartInputs) -> bool:
    return bool(inp.where_used) and all(
        wu.lifecycle_phase in ENDING_PHASES for wu in inp.where_used
    )


def avg_weekly_gross(inp: PartInputs, cfg: EngineConfig) -> float:
    """Average weekly gross requirement over weeks 0-12."""
    gr = gross_requirements(inp, cfg.safety_lookback_weeks)
    return sum(gr) / cfg.safety_lookback_weeks


def compute_eo_exposure(inp: PartInputs, cfg: EngineConfig) -> EoResult:
    c = inp.unit_cost_usd
    open_po = sum(po.qty_units for po in inp.pos)
    supply = inp.usable_on_hand + open_po
    need_52w = sum(gross_requirements(inp, cfg.need_52w_weeks))
    safety = cfg.safety_weeks * avg_weekly_gross(inp, cfg)
    excess = max(0.0, supply - need_52w - safety)
    cancellable = sum(po.qty_units for po in inp.pos if po.need_week_idx > po.cancel_window_weeks)
    cancel_qty = min(excess, float(cancellable))
    residual = excess - cancel_qty

    series_len = max([len(wu.production) for wu in inp.where_used] + [cfg.need_52w_weeks])
    lifetime = sum(gross_requirements(inp, series_len))
    obsolete = max(0.0, supply - lifetime) if all_platforms_ending(inp) else 0.0
    return EoResult(
        part_id=inp.part_id,
        supply_units=supply,
        need_52w_units=round(need_52w, 4),
        safety_stock_units=round(safety, 4),
        excess_units=round(excess, 4),
        excess_usd=round(excess * c, 2),
        cancellable_po_qty=cancellable,
        cancel_qty_recommended=round(cancel_qty, 4),
        avoidable_by_cancel_usd=round(cancel_qty * c, 2),
        residual_excess_units=round(residual, 4),
        residual_excess_usd=round(residual * c, 2),
        lifetime_need_units=round(lifetime, 4),
        all_platforms_ending=all_platforms_ending(inp),
        obsolete_units=round(obsolete, 4) if obsolete > EPS else 0.0,
        obsolete_usd=round(obsolete * c, 2) if obsolete > EPS else 0.0,
    )
