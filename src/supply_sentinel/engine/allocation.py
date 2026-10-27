"""Allocation of scarce supply across platforms (spec 02 section 5b).

Demand is grouped into priority classes: service demand is its own class (SERVICE); production
demand takes the platform's lifecycle phase (EOL counts as EOL_ANNOUNCED). Classes are served in
`allocation_priority` order; inside a class supply is split pro-rata to demand.
"""

from collections import defaultdict

from supply_sentinel.engine.models import AllocationResult, PartInputs, PlatformAllocation
from supply_sentinel.engine.netting import EPS, net_position


def _window_demand(series: list[float], start: int, end: int, qty_per: int) -> float:
    return sum(series[start : end + 1]) * qty_per if start < len(series) else 0.0


def allocate(inp: PartInputs, priority: list[str], horizon_weeks: int = 26) -> AllocationResult:
    """Allocate supply over the shortage window (or the whole horizon if there is no shortage)."""
    net = net_position(inp, horizon_weeks)
    start = net.first_shortage_week if net.first_shortage_week is not None else 0
    if net.first_shortage_week is None:
        end = horizon_weeks - 1
    elif net.recovery_week is not None:
        end = net.recovery_week - 1
    else:
        end = horizon_weeks - 1
    carry_in = float(inp.usable_on_hand)
    if start > 0:
        carry_in = max(0.0, net.pab[start - 1])
    available = carry_in + sum(net.receipts[start : end + 1])

    # claims: (platform_id, class, demand)
    claims: list[tuple[str, str, float]] = []
    for wu in inp.where_used:
        production = _window_demand(wu.production, start, end, wu.qty_per)
        service = _window_demand(wu.service, start, end, wu.qty_per)
        phase = "EOL_ANNOUNCED" if wu.lifecycle_phase == "EOL" else wu.lifecycle_phase
        claims.append((wu.platform_id, phase, production))
        claims.append((wu.platform_id, "SERVICE", service))

    classes = list(priority) + sorted({c for _, c, _ in claims} - set(priority))
    allocated: dict[tuple[str, str], float] = {}
    remaining = available
    for cls in classes:
        group = [(p, d) for p, c, d in claims if c == cls]
        total = sum(d for _, d in group)
        if total <= EPS:
            continue
        served = min(remaining, total)
        for p, d in group:
            allocated[(p, cls)] = allocated.get((p, cls), 0.0) + served * d / total
        remaining -= served

    demand_by: dict[str, float] = defaultdict(float)
    alloc_by: dict[str, float] = defaultdict(float)
    for p, _, d in claims:
        demand_by[p] += d
    for (p, _), a in allocated.items():
        alloc_by[p] += a
    rows = [
        PlatformAllocation(
            platform_id=wu.platform_id,
            demand_units=round(demand_by[wu.platform_id], 4),
            allocated_units=round(alloc_by[wu.platform_id], 4),
            unmet_units=round(demand_by[wu.platform_id] - alloc_by[wu.platform_id], 4),
            share=round(alloc_by[wu.platform_id] / available, 4) if available > EPS else 0.0,
        )
        for wu in inp.where_used
    ]
    return AllocationResult(
        part_id=inp.part_id,
        window_start_week=start,
        window_end_week=end,
        available_units=round(available, 4),
        total_demand_units=round(sum(demand_by.values()), 4),
        allocations=rows,
    )
