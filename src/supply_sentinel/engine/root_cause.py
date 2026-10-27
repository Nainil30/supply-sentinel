"""Root cause by counterfactual replay (spec 02 section 3).

Each counterfactual changes one factor in a copy of the inputs and re-nets. The first factor, in
spec order, that removes the shortage entirely is primary. Others that cut the maximum shortfall
by at least 25% are contributing.
"""

from collections.abc import Callable

from supply_sentinel.engine.models import (
    Counterfactual,
    PartInputs,
    RootCause,
    RootCauseResult,
)
from supply_sentinel.engine.netting import move_po_receipt, net_position

CONTRIBUTING_MIN_REDUCTION_PCT = 25.0


def _release_hold(inp: PartInputs) -> PartInputs:
    return inp.model_copy(update={"quality_hold_units": 0})


def _promised_qty(inp: PartInputs) -> PartInputs:
    return inp.model_copy(update={"commits": {}})


def _no_slip(inp: PartInputs) -> PartInputs:
    out = inp
    for po in inp.pos:
        if po.promise_week_idx > po.need_week_idx:
            out = move_po_receipt(out, po.po_id, po.need_week_idx)
    return out


def _previous_forecast(inp: PartInputs) -> PartInputs:
    out = inp.model_copy(deep=True)
    for wu in out.where_used:
        wu.production = list(wu.production_prev)
    return out


COUNTERFACTUALS: list[tuple[RootCause, Callable[[PartInputs], PartInputs]]] = [
    (RootCause.QUALITY_HOLD, _release_hold),
    (RootCause.COMMIT_GAP, _promised_qty),
    (RootCause.SUPPLIER_SLIP, _no_slip),
    (RootCause.DEMAND_SPIKE, _previous_forecast),
]


def explain_root_cause(inp: PartInputs, horizon_weeks: int = 26) -> RootCauseResult:
    base = net_position(inp, horizon_weeks)
    if base.first_shortage_week is None:
        raise ValueError(f"{inp.part_id} has no shortage to explain")
    results: list[Counterfactual] = []
    for cause, change in COUNTERFACTUALS:
        cf = net_position(change(inp), horizon_weeks)
        reduction = (base.max_shortfall_units - cf.max_shortfall_units) / base.max_shortfall_units
        results.append(
            Counterfactual(
                cause=cause,
                pab=cf.pab,
                max_shortfall_units=cf.max_shortfall_units,
                removes_shortage=cf.first_shortage_week is None,
                shortfall_reduction_pct=round(100 * reduction, 1),
            )
        )
    primary = next((c.cause for c in results if c.removes_shortage), RootCause.STRUCTURAL)
    contributing = [
        c.cause
        for c in results
        if c.cause is not primary and c.shortfall_reduction_pct >= CONTRIBUTING_MIN_REDUCTION_PCT
    ]
    return RootCauseResult(
        part_id=inp.part_id,
        primary=primary,
        contributing=contributing,
        baseline_pab=base.pab,
        counterfactuals=results,
    )
