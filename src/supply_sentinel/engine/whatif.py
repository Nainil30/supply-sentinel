"""What-if simulation (spec 02 section 6). Pure: builds changed copies of the inputs and re-nets."""

from supply_sentinel.engine.models import PartInputs, WhatIfResult
from supply_sentinel.engine.netting import add_receipt, move_po_receipt, net_position


def simulate(
    inp: PartInputs,
    *,
    po_slip: dict[str, int] | None = None,
    slip_all_weeks: int = 0,
    demand_change_pct: dict[str, float] | None = None,
    expedite: dict[str, int] | None = None,
    release_hold_units: int = 0,
    allocation: dict[str, float] | None = None,
    horizon_weeks: int = 26,
) -> WhatIfResult:
    """Apply the changes and compare against the unchanged plan.

    - `po_slip` / `slip_all_weeks`: move promise weeks later (negative pulls in).
    - `demand_change_pct`: platform -> percent change of its production forecast (30 = +30%).
    - `expedite`: `{"qty": units, "to_week": week_idx}` adds a receipt.
    - `release_hold_units`: quality-hold units released into usable stock (capped at the hold).
    - `allocation`: platform -> share (0 to 1) of its demand that is served; the rest is cut.
    """
    base = net_position(inp, horizon_weeks)
    scenario = inp.model_copy(deep=True)

    slips = {po.po_id: slip_all_weeks for po in inp.pos}
    for po_id, weeks in (po_slip or {}).items():
        slips[po_id] = slips.get(po_id, 0) + weeks
    for po_id, weeks in slips.items():
        if weeks:
            current = next(p for p in scenario.pos if p.po_id == po_id).promise_week_idx
            scenario = move_po_receipt(scenario, po_id, max(0, current + weeks))

    for wu in scenario.where_used:
        pct = (demand_change_pct or {}).get(wu.platform_id)
        if pct is not None:
            wu.production = [x * (1 + pct / 100) for x in wu.production]
        share = (allocation or {}).get(wu.platform_id)
        if share is not None:
            wu.production = [x * share for x in wu.production]
            wu.service = [x * share for x in wu.service]

    if expedite:
        scenario = add_receipt(scenario, "EXPEDITE", expedite["qty"], expedite["to_week"])
    released = min(max(release_hold_units, 0), scenario.quality_hold_units)
    scenario.quality_hold_units -= released

    result = net_position(scenario, horizon_weeks)
    return WhatIfResult(
        part_id=inp.part_id,
        pab=result.pab,
        first_shortage_week=result.first_shortage_week,
        max_shortfall_units=result.max_shortfall_units,
        baseline_first_shortage_week=base.first_shortage_week,
        baseline_max_shortfall_units=base.max_shortfall_units,
        shortfall_delta_units=round(result.max_shortfall_units - base.max_shortfall_units, 6),
    )
