"""Snapshot loader: the only place in the engine that reads DuckDB.

It turns a snapshot into one `PartInputs` per part. Weekly forecast buckets map straight to a
week index. A monthly bucket is spread evenly over the Mondays inside it (up to the platform's
last ship date), so every downstream function works on one weekly series.
"""

from datetime import date, timedelta
from pathlib import Path

import duckdb

from supply_sentinel.engine.models import (
    Alternate,
    EolNotice,
    PartInputs,
    PurchaseOrder,
    WhereUsed,
)

MIN_SERIES_WEEKS = 60  # always cover the 52-week E&O window
WEEKS_PER_YEAR = 52


def open_snapshot(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)


def week_idx(d: date, as_of: date) -> int:
    return (d - as_of).days // 7


def _next_month_start(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def production_series(
    rows: list[tuple[date, str, float]], n_weeks: int, as_of: date, last_ship: date
) -> list[float]:
    out = [0.0] * n_weeks
    for start, kind, units in rows:
        if kind == "WEEK":
            w = week_idx(start, as_of)
            if 0 <= w < n_weeks and start <= last_ship:
                out[w] += units
            continue
        end = _next_month_start(start)
        mondays: list[int] = []
        monday = as_of + timedelta(weeks=max(0, -((start - as_of).days // -7)))  # ceil to Monday
        while monday < end and monday <= last_ship:
            mondays.append(week_idx(monday, as_of))
            monday += timedelta(weeks=1)
        mondays = [w for w in mondays if 0 <= w < n_weeks]
        for w in mondays:
            out[w] += units / len(mondays)
    return out


def service_series(
    spares_by_year: dict[int, int], n_weeks: int, as_of: date, eosl: date
) -> list[float]:
    """Weekly spares demand: the year's spares divided by 52, until end of service life."""
    out = [0.0] * n_weeks
    for w in range(n_weeks):
        monday = as_of + timedelta(weeks=w)
        if monday <= eosl:
            out[w] = spares_by_year.get(monday.year, 0) / WEEKS_PER_YEAR
    return out


def load_part_inputs(con: duckdb.DuckDBPyConnection) -> list[PartInputs]:
    as_of: date = con.execute("SELECT as_of_date FROM meta").fetchone()[0]  # type: ignore[index]
    platforms = {
        r[0]: r
        for r in con.execute(
            "SELECT platform_id, lifecycle_phase, last_ship_date, eosl_date, asp_usd FROM platforms"
        ).fetchall()
    }
    n_weeks = max([MIN_SERIES_WEEKS] + [week_idx(r[3], as_of) + 1 for r in platforms.values()])

    versions = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT forecast_version FROM demand_forecast ORDER BY 1 DESC LIMIT 2"
        ).fetchall()
    ]
    current_v = versions[0]
    previous_v = versions[1] if len(versions) > 1 else current_v

    def forecast(version: date) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for pid, p in platforms.items():
            rows = con.execute(
                "SELECT bucket_start, bucket_type, units FROM demand_forecast "
                "WHERE platform_id = ? AND forecast_version = ? ORDER BY bucket_start",
                [pid, version],
            ).fetchall()
            out[pid] = production_series(rows, n_weeks, as_of, p[2])
        return out

    production = forecast(current_v)
    production_prev = forecast(previous_v)
    service: dict[str, list[float]] = {}
    for pid, p in platforms.items():
        spares = dict(
            con.execute(
                "SELECT year, spares_units FROM service_demand WHERE platform_id = ?", [pid]
            ).fetchall()
        )
        service[pid] = service_series(spares, n_weeks, as_of, p[3])

    accuracy: dict[str, list[tuple[int, float]]] = {}
    for pid, lag, mape in con.execute(
        "SELECT platform_id, lag_weeks, mape FROM forecast_accuracy"
    ).fetchall():
        accuracy.setdefault(pid, []).append((lag, mape))

    bom: dict[str, list[tuple[str, int]]] = {}
    for pid, part, qty in con.execute(
        "SELECT platform_id, part_id, qty_per FROM bom "
        "WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)",
        [as_of, as_of],
    ).fetchall():
        bom.setdefault(part, []).append((pid, qty))

    inventory = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT part_id, SUM(on_hand_units), SUM(quality_hold_units) FROM inventory GROUP BY 1"
        ).fetchall()
    }
    pos: dict[str, list[PurchaseOrder]] = {}
    for po_id, part, qty, need, promise, cancel in con.execute(
        "SELECT po_id, part_id, qty_units, need_week_idx, promise_week_idx, cancel_window_weeks "
        "FROM purchase_orders WHERE status != 'CLOSED' ORDER BY po_id"
    ).fetchall():
        pos.setdefault(part, []).append(
            PurchaseOrder(
                po_id=po_id,
                qty_units=qty,
                need_week_idx=need,
                promise_week_idx=promise,
                cancel_window_weeks=cancel,
            )
        )
    commits: dict[str, dict[int, int]] = {}
    for part, week, units in con.execute(
        "SELECT part_id, week_idx, commit_units FROM supplier_commits"
    ).fetchall():
        commits.setdefault(part, {})[week] = commits.setdefault(part, {}).get(week, 0) + units
    alternates: dict[str, list[Alternate]] = {}
    for part, alt, status, qual in con.execute(
        "SELECT part_id, alt_part_id, qualification_status, qual_lead_time_weeks FROM alternates"
    ).fetchall():
        alternates.setdefault(part, []).append(
            Alternate(alt_part_id=alt, qualification_status=status, qual_lead_time_weeks=qual)
        )
    notices: dict[str, EolNotice] = {}
    for nid, part, ltb_date, last_ship, repl in con.execute(
        "SELECT notice_id, part_id, ltb_date, last_ship_date, replacement_part_id "
        "FROM eol_notices ORDER BY notice_date"
    ).fetchall():
        notices[part] = EolNotice(
            notice_id=nid, ltb_date=ltb_date, last_ship_date=last_ship, replacement_part_id=repl
        )

    parts: list[PartInputs] = []
    for pid, desc, commodity, cost, lead, moq, mult, status in con.execute(
        "SELECT part_id, description, commodity, unit_cost_usd, lead_time_weeks, moq, "
        "order_multiple, lifecycle_status FROM parts ORDER BY part_id"
    ).fetchall():
        where_used = []
        for plat, qty_per in sorted(bom.get(pid, [])):
            lags = accuracy.get(plat)
            mape = min(lags, key=lambda x: abs(x[0] - lead))[1] if lags else None
            where_used.append(
                WhereUsed(
                    platform_id=plat,
                    qty_per=qty_per,
                    lifecycle_phase=str(platforms[plat][1]),
                    asp_usd=platforms[plat][4],
                    production=production[plat],
                    production_prev=production_prev[plat],
                    service=service[plat],
                    mape=mape,
                )
            )
        on_hand, hold = inventory.get(pid, (0, 0))
        parts.append(
            PartInputs(
                part_id=pid,
                description=desc,
                commodity=commodity,
                as_of=as_of,
                unit_cost_usd=cost,
                lead_time_weeks=lead,
                moq=moq,
                order_multiple=mult,
                lifecycle_status=str(status),
                on_hand_units=on_hand,
                quality_hold_units=hold,
                pos=pos.get(pid, []),
                commits=commits.get(pid, {}),
                where_used=where_used,
                alternates=alternates.get(pid, []),
                eol_notice=notices.get(pid),
            )
        )
    return parts
