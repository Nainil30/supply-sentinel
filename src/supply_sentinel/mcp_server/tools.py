"""MCP tool functions (spec 03 section 3).

Each function takes the `Snapshot` first and returns a Pydantic model. They are plain
functions so unit tests can call them without a server; `server.py` registers them with
descriptions and annotations. Formulas live in `engine/`; this file only looks up, filters
and reshapes engine output.
"""

from supply_sentinel.engine import rules_baseline
from supply_sentinel.engine.allocation import allocate
from supply_sentinel.engine.eo import compute_eo_exposure
from supply_sentinel.engine.ltb import compute_ltb_quantity
from supply_sentinel.engine.models import ExceptionRecord, ExceptionType, Recommendation, Severity
from supply_sentinel.engine.netting import (
    net_position,
    platform_gross,
    round_to_order_multiple,
)
from supply_sentinel.engine.root_cause import explain_root_cause
from supply_sentinel.engine.whatif import simulate
from supply_sentinel.mcp_server import schemas as s
from supply_sentinel.mcp_server.context import Snapshot, ToolInputError
from supply_sentinel.mcp_server.untrusted import wrap_untrusted

SEVERITY_RANK = {Severity.P1: 1, Severity.P2: 2, Severity.P3: 3}


def _weeks_to_impact(exc: ExceptionRecord) -> int | None:
    if exc.type == ExceptionType.SHORTAGE:
        return exc.first_shortage_week
    if exc.type == ExceptionType.COMMIT_GAP and exc.gap_weeks:
        return min(exc.gap_weeks)
    if exc.type == ExceptionType.EOL_NOTICE and exc.days_to_ltb is not None:
        return exc.days_to_ltb // 7
    return None


def list_exceptions(
    snap: Snapshot,
    type: str | None = None,
    platform_id: str | None = None,
    min_severity: str | None = None,
) -> s.ExceptionList:
    if type is not None and type not in ExceptionType.__members__:
        raise ToolInputError(
            f"Unknown type '{type}'. Use one of {list(ExceptionType.__members__)}."
        )
    if min_severity is not None and min_severity not in Severity.__members__:
        raise ToolInputError(f"Unknown min_severity '{min_severity}'. Use P1, P2 or P3.")
    rows: list[s.ExceptionSummary] = []
    for i, exc in enumerate(snap.exceptions, start=1):
        inp = snap.parts[exc.part_id]
        platforms = [wu.platform_id for wu in inp.where_used]
        if type and exc.type.value != type:
            continue
        if platform_id and platform_id not in platforms:
            continue
        if min_severity and SEVERITY_RANK[exc.severity] > SEVERITY_RANK[Severity(min_severity)]:
            continue
        rec = rules_baseline.recommend(inp, exc, snap.cfg, seq=i)
        exposure = exc.excess_usd if exc.type == ExceptionType.EXCESS else exc.obsolete_usd
        rows.append(
            s.ExceptionSummary(
                exception_id=exc.exception_id,
                type=exc.type.value,
                part_id=exc.part_id,
                platforms=platforms,
                severity=exc.severity.value,
                weeks_to_impact=_weeks_to_impact(exc),
                revenue_at_risk_usd=exc.revenue_at_risk_usd,
                exposure_usd=exposure,
                baseline_action=rec.primary_action.action_type.value,
                baseline_quantity=rec.primary_action.quantity,
            )
        )
    rows.sort(key=lambda r: (r.severity, -(r.revenue_at_risk_usd or r.exposure_usd or 0.0)))
    return s.ExceptionList(
        exceptions=rows[: s.MAX_ROWS],
        total_count=len(rows),
        truncated=len(rows) > s.MAX_ROWS,
        **snap.base(),
    )


def get_part_position(snap: Snapshot, part_id: str, horizon_weeks: int = 26) -> s.PartPosition:
    inp = snap.part(part_id)
    if not 1 <= horizon_weeks <= 52:
        raise ToolInputError("horizon_weeks must be between 1 and 52.")
    net = net_position(inp, horizon_weeks)
    n = min(horizon_weeks, s.MAX_WEEKS)
    weeks = [
        s.WeekRow(
            week_idx=w,
            gross_req=round(net.gross_requirements[w], 2),
            receipts=round(net.receipts[w], 2),
            pab=round(net.pab[w], 2),
        )
        for w in range(n)
    ]
    shortfall = round_to_order_multiple(net.max_shortfall_units, inp.order_multiple, inp.moq)
    return s.PartPosition(
        part_id=part_id,
        description=inp.description,
        usable_on_hand=inp.usable_on_hand,
        quality_hold_units=inp.quality_hold_units,
        coverage_weeks=net.coverage_weeks,
        lead_time_weeks=inp.lead_time_weeks,
        moq=inp.moq,
        order_multiple=inp.order_multiple,
        unit_cost_usd=inp.unit_cost_usd,
        weeks=weeks,
        first_shortage_week=net.first_shortage_week,
        max_shortfall_units=net.max_shortfall_units,
        shortfall_rounded_to_multiple=shortfall,
        recovery_week=net.recovery_week,
        truncated=horizon_weeks > s.MAX_WEEKS,
        **snap.base(),
    )


def compute_allocation(snap: Snapshot, part_id: str) -> s.Allocation:
    inp = snap.part(part_id)
    return s.Allocation(
        part_id=part_id,
        priority_order=snap.cfg.allocation_priority,
        result=allocate(inp, snap.cfg.allocation_priority, snap.cfg.horizon_weeks),
        **snap.base(),
    )


def get_where_used(snap: Snapshot, part_id: str) -> s.WhereUsed:
    inp = snap.part(part_id)
    n = snap.cfg.horizon_weeks
    gross = {wu.platform_id: sum(platform_gross(inp, wu.platform_id, n)) for wu in inp.where_used}
    total = sum(gross.values())
    dates = {
        r[0]: (r[1], r[2])
        for r in snap.query("SELECT platform_id, last_ship_date, eosl_date FROM platforms")
    }
    return s.WhereUsed(
        part_id=part_id,
        platforms=[
            s.PlatformUse(
                platform_id=wu.platform_id,
                qty_per=wu.qty_per,
                lifecycle_phase=wu.lifecycle_phase,
                last_ship_date=dates[wu.platform_id][0],
                eosl_date=dates[wu.platform_id][1],
                share_of_gross_req_pct=round(100 * gross[wu.platform_id] / total, 2)
                if total
                else 0.0,
            )
            for wu in inp.where_used
        ],
        **snap.base(),
    )


def get_open_pos(snap: Snapshot, part_id: str) -> s.OpenPos:
    snap.part(part_id)
    rows = snap.query(
        "SELECT po_id, qty_units, need_week_idx, promise_week_idx, cancel_window_weeks, "
        "unit_cost_usd FROM purchase_orders WHERE part_id = ? AND status != 'CLOSED' "
        "ORDER BY promise_week_idx, po_id",
        [part_id],
    )
    pos = [
        s.OpenPo(
            po_id=r[0],
            qty_units=r[1],
            need_week_idx=r[2],
            promise_week_idx=r[3],
            slip_weeks=r[3] - r[2],
            cancellable=r[2] > r[4],
            unit_cost_usd=r[5],
        )
        for r in rows
    ]
    return s.OpenPos(
        part_id=part_id,
        pos=pos[: s.MAX_ROWS],
        total_count=len(pos),
        truncated=len(pos) > s.MAX_ROWS,
        **snap.base(),
    )


def get_supplier_commits(snap: Snapshot, part_id: str, weeks: int = 12) -> s.SupplierCommits:
    inp = snap.part(part_id)
    if not 1 <= weeks <= 52:
        raise ToolInputError("weeks must be between 1 and 52.")
    n = min(weeks, s.MAX_WEEKS)
    notes: dict[int, str] = {}
    for week, note in snap.query(
        "SELECT week_idx, note FROM supplier_commits WHERE part_id = ? "
        "AND note IS NOT NULL AND note != ''",
        [part_id],
    ):
        notes[week] = f"{notes[week]} | {note}" if week in notes else note
    promised: dict[int, int] = {}
    for po in inp.pos:
        w = max(0, po.promise_week_idx)
        promised[w] = promised.get(w, 0) + po.qty_units
    rows = []
    for w in range(n):
        if w not in inp.commits and w not in promised:
            continue
        commit = inp.commits.get(w)
        p = promised.get(w, 0)
        rows.append(
            s.CommitRow(
                week_idx=w,
                commit_units=commit,
                po_promised_units=p,
                gap_units=max(0, p - commit) if commit is not None else 0,
                note=wrap_untrusted(f"supplier_commits.note[{part_id},wk{w}]", notes[w])
                if w in notes
                else None,
            )
        )
    return s.SupplierCommits(
        part_id=part_id, rows=rows, truncated=weeks > s.MAX_WEEKS, **snap.base()
    )


def get_demand_profile(snap: Snapshot, platform_id: str) -> s.DemandProfile:
    plat = {wu.platform_id: wu for inp in snap.parts.values() for wu in inp.where_used}.get(
        platform_id
    )
    if plat is None:
        known = sorted(r[0] for r in snap.query("SELECT platform_id FROM platforms"))
        raise ToolInputError(f"Unknown platform_id '{platform_id}'. Known platforms: {known}.")
    actuals = snap.query(
        "SELECT week_start, SUM(units) FROM shipments_history WHERE platform_id = ? "
        "AND channel = 'PRODUCTION' GROUP BY 1 ORDER BY 1 DESC LIMIT 13",
        [platform_id],
    )
    n = s.MAX_WEEKS
    cur = plat.production[:n]
    prev = plat.production_prev[:n]
    change = (sum(cur) - sum(prev)) / sum(prev) * 100 if sum(prev) else 0.0
    acc = snap.query(
        "SELECT mape, bias FROM forecast_accuracy WHERE platform_id = ? "
        "ORDER BY abs(lag_weeks - 13) LIMIT 1",
        [platform_id],
    )
    return s.DemandProfile(
        platform_id=platform_id,
        actuals_last_13_weeks=[
            s.ActualRow(week_start=r[0], units=int(r[1])) for r in reversed(actuals)
        ],
        forecast_current=[s.ForecastRow(week_idx=i, units=round(u, 2)) for i, u in enumerate(cur)],
        forecast_previous=[
            s.ForecastRow(week_idx=i, units=round(u, 2)) for i, u in enumerate(prev)
        ],
        forecast_change_pct=round(change, 2),
        mape=acc[0][0] if acc else None,
        bias=acc[0][1] if acc else None,
        truncated=len(plat.production) > n,
        **snap.base(),
    )


def get_eol_notice(snap: Snapshot, part_id: str) -> s.EolNoticeOut:
    snap.part(part_id)
    rows = snap.query(
        "SELECT notice_id, notice_date, ltb_date, last_ship_date, replacement_part_id, raw_text "
        "FROM eol_notices WHERE part_id = ? ORDER BY notice_date DESC LIMIT 1",
        [part_id],
    )
    if not rows:
        raise ToolInputError(
            f"No end-of-life notice for {part_id}. Call list_exceptions with type=EOL_NOTICE."
        )
    nid, ndate, ltb, last_ship, repl, text = rows[0]
    return s.EolNoticeOut(
        part_id=part_id,
        notice_id=nid,
        notice_date=ndate,
        ltb_date=ltb,
        last_ship_date=last_ship,
        replacement_part_id=repl,
        days_to_ltb=(ltb - snap.as_of).days if ltb else None,
        raw_text=wrap_untrusted(f"eol_notices.raw_text[{nid}]", text or ""),
        **snap.base(),
    )


def get_alternates(snap: Snapshot, part_id: str) -> s.Alternates:
    inp = snap.part(part_id)
    costs = dict(snap.query("SELECT part_id, unit_cost_usd FROM parts"))
    return s.Alternates(
        part_id=part_id,
        alternates=[
            s.AlternateOut(
                alt_part_id=a.alt_part_id,
                qualification_status=a.qualification_status,
                qual_lead_time_weeks=a.qual_lead_time_weeks,
                unit_cost_usd=costs.get(a.alt_part_id, inp.unit_cost_usd),
            )
            for a in inp.alternates
        ],
        **snap.base(),
    )


def get_decision_history(snap: Snapshot, part_id: str, limit: int = 5) -> s.DecisionHistory:
    snap.part(part_id)
    from supply_sentinel.datagen.decisions import connect

    limit = max(1, min(limit, s.MAX_ROWS))
    if not snap.state_db.exists():
        return s.DecisionHistory(part_id=part_id, decisions=[], **snap.base())
    conn = connect(snap.state_db)
    try:
        rows = conn.execute(
            "SELECT rec_id, action_type, quantity, status, decided_at, rationale "
            "FROM decision_log WHERE snapshot_id = ? AND part_id = ? "
            "ORDER BY COALESCE(decided_at, '') DESC, rec_id DESC LIMIT ?",
            (snap.snapshot_id, part_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return s.DecisionHistory(
        part_id=part_id,
        decisions=[
            s.DecisionRow(
                rec_id=r[0],
                action_type=r[1],
                quantity=r[2],
                status=r[3],
                decided_at=r[4],
                rationale=r[5],
            )
            for r in rows
        ],
        **snap.base(),
    )


def explain_root_cause_tool(snap: Snapshot, part_id: str) -> s.RootCauseOut:
    inp = snap.part(part_id)
    res = explain_root_cause(inp, snap.cfg.horizon_weeks)
    cfs = []
    for cf in res.counterfactuals:
        first = next((w for w, v in enumerate(cf.pab) if v < -1e-6), None)
        cfs.append(
            s.CounterfactualOut(
                cause=cf.cause.value,
                max_shortfall_after=cf.max_shortfall_units,
                first_shortage_week_after=first,
                removes_shortage=cf.removes_shortage,
                shortfall_reduction_pct=cf.shortfall_reduction_pct,
            )
        )
    return s.RootCauseOut(
        part_id=part_id,
        primary_cause=res.primary.value,
        contributing_causes=[c.value for c in res.contributing],
        counterfactuals=cfs,
        **snap.base(),
    )


def compute_ltb_quantity_tool(
    snap: Snapshot,
    part_id: str,
    shortage_penalty_multiple: float | None = None,
    cv_floor: float | None = None,
) -> s.LtbOut:
    inp = snap.part(part_id)
    cfg = snap.cfg.model_copy(deep=True)
    if cv_floor is not None:
        cfg.cv_floor = cv_floor
    if shortage_penalty_multiple is not None:
        cfg.shortage_penalty_multiple = {"default": shortage_penalty_multiple}
    return s.LtbOut(result=compute_ltb_quantity(inp, cfg), **snap.base())


def compute_eo_exposure_tool(snap: Snapshot, part_id: str) -> s.EoOut:
    return s.EoOut(result=compute_eo_exposure(snap.part(part_id), snap.cfg), **snap.base())


def simulate_scenario(
    snap: Snapshot,
    part_id: str,
    po_slip: dict[str, int] | None = None,
    slip_all_weeks: int = 0,
    demand_change_pct: dict[str, float] | None = None,
    expedite_qty: int | None = None,
    expedite_to_week: int | None = None,
    release_hold_units: int = 0,
    allocation: dict[str, float] | None = None,
) -> s.WhatIfOut:
    inp = snap.part(part_id)
    unknown = sorted(set(po_slip or {}) - {po.po_id for po in inp.pos})
    if unknown:
        raise ToolInputError(f"Unknown po_id {unknown} for {part_id}. Call get_open_pos first.")
    if (expedite_qty is None) != (expedite_to_week is None):
        raise ToolInputError("Pass expedite_qty and expedite_to_week together.")
    expedite = (
        {"qty": expedite_qty, "to_week": expedite_to_week}
        if expedite_qty is not None and expedite_to_week is not None
        else None
    )
    result = simulate(
        inp,
        po_slip=po_slip,
        slip_all_weeks=slip_all_weeks,
        demand_change_pct=demand_change_pct,
        expedite=expedite,
        release_hold_units=release_hold_units,
        allocation=allocation,
        horizon_weeks=snap.cfg.horizon_weeks,
    )
    return s.WhatIfOut(result=result, **snap.base())


def record_recommendation(
    snap: Snapshot, recommendation: Recommendation
) -> s.RecordedRecommendation:
    """Insert a PENDING row into decision_log. The only write the server can make."""
    import sqlite3

    from supply_sentinel.datagen.decisions import connect

    snap.part(recommendation.part_id)  # raises with a hint if the part is unknown
    conn = connect(snap.state_db)
    try:
        action = recommendation.primary_action
        qty = int(action.quantity) if action.quantity is not None else None
        try:
            conn.execute(
                "INSERT INTO decision_log (rec_id, snapshot_id, exception_id, part_id, "
                "action_type, quantity, status, rationale) VALUES (?,?,?,?,?,?, 'PENDING', ?)",
                (
                    recommendation.rec_id,
                    snap.snapshot_id,
                    recommendation.exception_id,
                    recommendation.part_id,
                    action.action_type.value,
                    qty,
                    recommendation.root_cause_explanation,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ToolInputError(
                f"{recommendation.rec_id} is already recorded for this snapshot. Use a new rec_id."
            ) from exc
        conn.commit()
    finally:
        conn.close()
    return s.RecordedRecommendation(rec_id=recommendation.rec_id, status="PENDING", **snap.base())
