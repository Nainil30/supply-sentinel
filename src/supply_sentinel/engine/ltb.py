"""Last-time-buy sizing as a newsvendor problem (spec 02 section 4).

Buying one unit too many costs Co (overage); buying one too few costs Cu (underage). The best
quantity covers demand with probability CR = Cu / (Cu + Co), the critical ratio.
"""

import math
from collections.abc import Callable
from statistics import NormalDist

from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.models import (
    BridgeOption,
    LtbResult,
    NewsvendorResult,
    PartInputs,
)
from supply_sentinel.engine.netting import EPS, gross_requirements

__all__ = [
    "NewsvendorResult",
    "compute_ltb_quantity",
    "lifetime_need",
    "newsvendor_size",
    "newsvendor_target",
]

WEEKS_PER_YEAR = 52
QUALIFIED_BRIDGE_WEEKS = 8


def newsvendor_target(mu_units: float, sigma_units: float, critical_ratio: float) -> float:
    """Q* = mu + z * sigma, with z the standard normal quantile of the critical ratio."""
    ratio = min(max(critical_ratio, 1e-6), 1 - 1e-6)
    return mu_units + NormalDist().inv_cdf(ratio) * sigma_units


def newsvendor_size(
    *,
    mu_units: float,
    sigma_units: float,
    unit_cost_usd: float,
    salvage_pct: float,
    holding_rate: float,
    avg_years_held: float,
    penalty_multiple: float,
    usable_on_hand_units: int,
    open_po_units: int,
    order_multiple: int,
    moq: int,
) -> NewsvendorResult:
    c = unit_cost_usd
    overage = c - salvage_pct * c + holding_rate * c * avg_years_held
    underage = (penalty_multiple - 1) * c
    ratio = underage / (underage + overage)
    ratio_used = min(max(ratio, 1e-6), 1 - 1e-6)
    z = NormalDist().inv_cdf(ratio_used)
    q_star = newsvendor_target(mu_units, sigma_units, ratio)
    gross = max(0, math.ceil(round(q_star, 6)))
    net = max(0, gross - usable_on_hand_units - open_po_units)
    order_qty = 0
    if net > 0:
        order_qty = math.ceil(net / order_multiple) * order_multiple
        if order_qty < moq:
            order_qty = math.ceil(moq / order_multiple) * order_multiple
    return NewsvendorResult(
        mu_units=round(mu_units, 4),
        sigma_units=round(sigma_units, 4),
        unit_cost_usd=c,
        overage_cost_usd=round(overage, 4),
        underage_cost_usd=round(underage, 4),
        critical_ratio=round(ratio, 6),
        z=round(z, 6),
        q_star_units=round(q_star, 4),
        gross_units=gross,
        usable_on_hand_units=usable_on_hand_units,
        open_po_units=open_po_units,
        net_units=net,
        order_qty_units=order_qty,
        spend_usd=round(order_qty * c, 2),
    )


def lifetime_need(inp: PartInputs) -> float:
    """Production to the last ship date plus service to EOSL, all where-used platforms.

    The demand arrays already stop at those dates, so this is the sum of the whole series.
    """
    return sum(gross_requirements(inp, _series_len(inp)))


def _series_len(inp: PartInputs) -> int:
    lengths = [len(wu.production) for wu in inp.where_used]
    lengths += [len(wu.service) for wu in inp.where_used]
    return max(lengths, default=0)


def _need_weighted_cv(inp: PartInputs, cv_floor: float) -> float:
    n = _series_len(inp)
    weighted = 0.0
    total = 0.0
    for wu in inp.where_used:
        need = (sum(wu.production[:n]) + sum(wu.service[:n])) * wu.qty_per
        if wu.mape is not None:
            weighted += wu.mape * need
            total += need
    mape = weighted / total if total > EPS else 0.0
    return max(mape, cv_floor)


def _avg_years_held(first_receipt_week: int, last_need_week: int) -> float:
    """Average time a unit sits: half the span from receipt to the end of need."""
    return max(0, last_need_week - first_receipt_week) / WEEKS_PER_YEAR / 2


def _last_need_week(gross: list[float]) -> int:
    return max((w + 1 for w, g in enumerate(gross) if g > EPS), default=0)


def compute_ltb_quantity(inp: PartInputs, cfg: EngineConfig) -> LtbResult:
    """Full LTB sizing, plus a bridge option when an alternate is not yet usable."""
    n = _series_len(inp)
    gross = gross_requirements(inp, n)
    mu = sum(gross)
    cv = _need_weighted_cv(inp, cfg.cv_floor)
    years = _avg_years_held(inp.lead_time_weeks, _last_need_week(gross))
    open_po = sum(po.qty_units for po in inp.pos)
    penalty = cfg.penalty_multiple(inp.commodity)

    def size(mu_units: float, avg_years: float) -> NewsvendorResult:
        return newsvendor_size(
            mu_units=mu_units,
            sigma_units=cv * mu_units,
            unit_cost_usd=inp.unit_cost_usd,
            salvage_pct=cfg.salvage_pct,
            holding_rate=cfg.holding_rate,
            avg_years_held=avg_years,
            penalty_multiple=penalty,
            usable_on_hand_units=inp.usable_on_hand,
            open_po_units=open_po,
            order_multiple=inp.order_multiple,
            moq=inp.moq,
        )

    sizing = size(mu, years)
    bridge = _bridge_option(inp, cfg, gross, size)
    return LtbResult(
        part_id=inp.part_id,
        cv=round(cv, 4),
        avg_years_held=round(years, 4),
        sizing=sizing,
        requires_second_approver=sizing.spend_usd > cfg.second_approver_over_usd,
        bridge=bridge,
    )


def _bridge_option(
    inp: PartInputs,
    cfg: EngineConfig,
    gross: list[float],
    size: Callable[[float, float], NewsvendorResult],
) -> BridgeOption | None:
    """Cover demand until an alternate is usable: the same math on a shorter window."""
    if not inp.alternates:
        return None
    preferred = inp.eol_notice.replacement_part_id if inp.eol_notice else None
    alt = min(
        inp.alternates,
        key=lambda a: (
            a.alt_part_id != preferred,
            a.qualification_status != "QUALIFIED",
            a.qual_lead_time_weeks,
        ),
    )
    if alt.qualification_status == "QUALIFIED":
        window = QUALIFIED_BRIDGE_WEEKS
    else:
        window = alt.qual_lead_time_weeks + cfg.bridge_cutover_buffer_weeks
    mu_window = sum(gross[:window])
    years = _avg_years_held(inp.lead_time_weeks, window)
    return BridgeOption(
        alt_part_id=alt.alt_part_id,
        qualification_status=alt.qualification_status,
        qual_lead_time_weeks=alt.qual_lead_time_weeks,
        window_weeks=window,
        sizing=size(mu_window, years),
    )
