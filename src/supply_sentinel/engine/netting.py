"""Netting: gross requirements, receipts and projected available balance (spec 02 section 1).

Pure functions. Week 0 is the as-of week. All series are plain lists indexed by `week_idx`.
"""

import math

from supply_sentinel.engine.models import Netting, PartInputs, PurchaseOrder

EPS = 1e-6  # float noise tolerance when comparing balances to zero


def gross_requirements(inp: PartInputs, n_weeks: int) -> list[float]:
    """GR[w] = sum over platforms of (production + service demand) x qty_per."""
    total = [0.0] * n_weeks
    for wu in inp.where_used:
        for w in range(n_weeks):
            prod = wu.production[w] if w < len(wu.production) else 0.0
            serv = wu.service[w] if w < len(wu.service) else 0.0
            total[w] += (prod + serv) * wu.qty_per
    return total


def platform_gross(inp: PartInputs, platform_id: str, n_weeks: int) -> list[float]:
    """Weekly GR of the part caused by one platform."""
    wu = next(p for p in inp.where_used if p.platform_id == platform_id)
    one = inp.model_copy(update={"where_used": [wu]})
    return gross_requirements(one, n_weeks)


def receipts(inp: PartInputs, n_weeks: int) -> list[float]:
    """R[w] = the supplier commit for week w if a commit row exists, else the PO promise qty.

    A PO promised before week 0 (overdue) is counted in week 0.
    """
    po_by_week = [0.0] * n_weeks
    for po in inp.pos:
        w = max(0, po.promise_week_idx)
        if w < n_weeks:
            po_by_week[w] += po.qty_units
    return [float(inp.commits[w]) if w in inp.commits else po_by_week[w] for w in range(n_weeks)]


def net_position(inp: PartInputs, horizon_weeks: int = 26) -> Netting:
    """PAB[-1] = usable on-hand; PAB[w] = PAB[w-1] + R[w] - GR[w]."""
    gr = gross_requirements(inp, horizon_weeks)
    rec = receipts(inp, horizon_weeks)
    pab: list[float] = []
    balance = float(inp.usable_on_hand)
    for w in range(horizon_weeks):
        balance = round(balance + rec[w] - gr[w], 6)
        pab.append(balance)
    low = min(pab) if pab else 0.0
    first = next((w for w, v in enumerate(pab) if v < -EPS), None)
    recovery = None
    if first is not None:
        recovery = next((w for w in range(first + 1, horizon_weeks) if pab[w] >= -EPS), None)
    lookback = gr[:13]
    mean_gr = sum(lookback) / len(lookback) if lookback else 0.0
    coverage = inp.usable_on_hand / mean_gr if mean_gr > EPS else None
    return Netting(
        horizon_weeks=horizon_weeks,
        gross_requirements=gr,
        receipts=rec,
        pab=pab,
        first_shortage_week=first,
        max_shortfall_units=round(-low, 6) if first is not None else 0.0,
        recovery_week=recovery,
        coverage_weeks=round(coverage, 2) if coverage is not None else None,
    )


def move_po_receipt(inp: PartInputs, po_id: str, new_week: int) -> PartInputs:
    """Return a copy where one PO's receipt lands in `new_week`.

    Supplier commits override PO quantities week by week, so the commit rows move with the PO:
    the old week's commit shrinks and the new week's commit (if it has a row) grows.
    """
    out = inp.model_copy(deep=True)
    po = next(p for p in out.pos if p.po_id == po_id)
    old = max(0, po.promise_week_idx)
    moved = po.qty_units
    if old in out.commits:
        # move only what the supplier actually committed; a commit cut stays a commit cut
        moved = min(po.qty_units, out.commits[old])
        out.commits[old] -= moved
    po.promise_week_idx = new_week
    if new_week in out.commits:
        out.commits[new_week] += moved
    return out


def add_receipt(inp: PartInputs, po_id: str, qty_units: int, week: int) -> PartInputs:
    """Return a copy with one extra receipt (an expedite or a new PO) in `week`."""
    out = inp.model_copy(deep=True)
    out.pos.append(
        PurchaseOrder(po_id=po_id, qty_units=qty_units, need_week_idx=week, promise_week_idx=week)
    )
    if week in out.commits:
        out.commits[week] += qty_units
    return out


def round_to_order_multiple(qty: float, order_multiple: int, moq: int) -> int:
    """Round up to the order multiple, and never below the MOQ."""
    units = max(math.ceil(qty - EPS), 0)
    units = math.ceil(units / order_multiple) * order_multiple
    if units and units < moq:
        units = math.ceil(moq / order_multiple) * order_multiple
    return units


def commit_gaps(inp: PartInputs, n_weeks: int) -> dict[int, int]:
    """Weeks (with a commit row) where commit_units < the quantity POs promise that week."""
    promised: dict[int, int] = {}
    for po in inp.pos:
        w = max(0, po.promise_week_idx)
        promised[w] = promised.get(w, 0) + po.qty_units
    return {
        w: promised[w] - units
        for w, units in sorted(inp.commits.items())
        if w < n_weeks and w in promised and units < promised[w]
    }
