"""Scenario injection (spec 01 section 5).

A scenario is the base snapshot plus an ordered list of perturbations. Each perturbation is
one registered op: a small function `apply(con, **params)` that changes the DuckDB snapshot.
Ops fail loudly (ValueError) if their target does not exist, so a typo in a scenario file can
never silently produce an unperturbed snapshot.

Inputs here are trusted (our own scenario files). `drop_rows` takes a raw SQL predicate, so
never feed it text from a supplier note or any other untrusted field.
"""

import shutil
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import yaml

from supply_sentinel.datagen import decisions
from supply_sentinel.datagen.generator import DEFAULT_SNAPSHOT_DIR, DEFAULT_STATE_DB
from supply_sentinel.datagen.schema import TABLES, UNTRUSTED_FIELDS
from supply_sentinel.datagen.validate import ValidationReport, validate_snapshot

DEFAULT_SCENARIO_DIR = Path("evals/scenarios")
# Ops that may legitimately break validation (the scenario is about missing data).
DATA_BREAKING_OPS = {"drop_rows"}
# Ops that also need the SQLite decision log.
NEEDS_STATE_DB = {"add_decision"}

OPS: dict[str, Callable[..., None]] = {}


def register(name: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    def deco(fn: Callable[..., None]) -> Callable[..., None]:
        OPS[name] = fn
        return fn

    return deco


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(value)


def _exists(con: duckdb.DuckDBPyConnection, table: str, column: str, value: Any) -> bool:
    row = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", [value]).fetchone()
    return bool(row and row[0])


def _require(con: duckdb.DuckDBPyConnection, table: str, column: str, value: Any) -> None:
    if not _exists(con, table, column, value):
        raise ValueError(f"{table}.{column} = {value!r} not found")


def _as_of(con: duckdb.DuckDBPyConnection) -> date:
    row = con.execute("SELECT as_of_date FROM meta").fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


@register("slip_po")
def slip_po(con: duckdb.DuckDBPyConnection, *, po_id: str, weeks: int) -> None:
    """Move a PO's promise date `weeks` later (negative pulls it in)."""
    _require(con, "purchase_orders", "po_id", po_id)
    con.execute(
        "UPDATE purchase_orders SET promise_week_idx = promise_week_idx + ? WHERE po_id = ?",
        [weeks, po_id],
    )


@register("scale_forecast")
def scale_forecast(
    con: duckdb.DuckDBPyConnection, *, platform_id: str, from_week: int, to_week: int, pct: float
) -> None:
    """Change the current forecast by `pct` percent (30 -> x1.30) for buckets overlapping
    weeks `from_week..to_week`. The unscaled values become the previous forecast version."""
    rows = con.execute(
        "SELECT forecast_version, bucket_start, bucket_type, units FROM demand_forecast "
        "WHERE platform_id = ? ORDER BY forecast_version DESC, bucket_start",
        [platform_id],
    ).fetchall()
    if not rows:
        raise ValueError(f"no forecast rows for {platform_id!r}")
    current_v = rows[0][0]
    versions = {r[0] for r in rows}
    previous_v = min(versions) if len(versions) > 1 else current_v - timedelta(days=7)
    current = [r for r in rows if r[0] == current_v]

    con.execute(
        "DELETE FROM demand_forecast WHERE platform_id = ? AND forecast_version = ?",
        [platform_id, previous_v],
    )
    con.executemany(
        "INSERT INTO demand_forecast VALUES (?, ?, ?, ?, ?)",
        [(platform_id, start, kind, units, previous_v) for _, start, kind, units in current],
    )
    as_of = _as_of(con)
    idx = [(start - as_of).days // 7 for _, start, _, _ in current]
    for i, (_, start, kind, units) in enumerate(current):
        end = idx[i] + 1 if kind == "WEEK" or i + 1 == len(current) else idx[i + 1]
        if idx[i] <= to_week and end > from_week:
            con.execute(
                "UPDATE demand_forecast SET units = ? "
                "WHERE platform_id = ? AND bucket_start = ? AND forecast_version = ?",
                [round(units * (1 + pct / 100), 2), platform_id, start, current_v],
            )


@register("set_quality_hold")
def set_quality_hold(
    con: duckdb.DuckDBPyConnection, *, part_id: str, location: str, units: int
) -> None:
    """Put `units` more of the on-hand stock on quality hold.

    Held units stay inside `on_hand_units` (the engine's usable stock is on_hand - hold, as the
    base generator stores it), so only `quality_hold_units` changes. See ADR-0012.
    """
    row = con.execute(
        "SELECT on_hand_units, quality_hold_units FROM inventory "
        "WHERE part_id = ? AND location = ?",
        [part_id, location],
    ).fetchone()
    if row is None:
        raise ValueError(f"no inventory row for {part_id!r} at {location!r}")
    free = row[0] - row[1]
    if free < units:
        raise ValueError(f"cannot hold {units} units; only {free} on hand and not already held")
    con.execute(
        "UPDATE inventory SET quality_hold_units = quality_hold_units + ? "
        "WHERE part_id = ? AND location = ?",
        [units, part_id, location],
    )


@register("set_commit")
def set_commit(
    con: duckdb.DuckDBPyConnection, *, part_id: str, week_idx: int, commit_units: int, note: str
) -> None:
    """Override the supplier commit row for (part, week), creating it if absent."""
    _require(con, "parts", "part_id", part_id)
    existing = con.execute(
        "SELECT COUNT(*) FROM supplier_commits WHERE part_id = ? AND week_idx = ?",
        [part_id, week_idx],
    ).fetchone()
    if existing and existing[0]:
        con.execute(
            "UPDATE supplier_commits SET commit_units = ?, note = ? "
            "WHERE part_id = ? AND week_idx = ?",
            [commit_units, note, part_id, week_idx],
        )
        return
    supplier = con.execute("SELECT supplier_id FROM parts WHERE part_id = ?", [part_id]).fetchone()
    assert supplier is not None
    con.execute(
        "INSERT INTO supplier_commits VALUES (?, ?, ?, ?, ?)",
        [part_id, supplier[0], week_idx, commit_units, note],
    )


@register("add_eol_notice")
def add_eol_notice(
    con: duckdb.DuckDBPyConnection,
    *,
    part_id: str,
    notice_date: date | str,
    ltb_date: date | str | None,
    last_ship_date: date | str | None,
    replacement_part_id: str | None,
    raw_text: str,
) -> None:
    """Insert an end-of-life notice and mark the part EOL_NOTICE."""
    _require(con, "parts", "part_id", part_id)
    if replacement_part_id is not None:
        _require(con, "parts", "part_id", replacement_part_id)
    row = con.execute("SELECT supplier_id FROM parts WHERE part_id = ?", [part_id]).fetchone()
    assert row is not None
    (n,) = con.execute("SELECT COUNT(*) FROM eol_notices").fetchone() or (0,)
    con.execute(
        "INSERT INTO eol_notices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            f"EOL-{n + 1:04d}", part_id, row[0], _as_date(notice_date),
            _as_date(ltb_date) if ltb_date else None,
            _as_date(last_ship_date) if last_ship_date else None,
            replacement_part_id, raw_text,
        ],
    )  # fmt: skip
    con.execute("UPDATE parts SET lifecycle_status = 'EOL_NOTICE' WHERE part_id = ?", [part_id])


@register("set_alternate_status")
def set_alternate_status(
    con: duckdb.DuckDBPyConnection,
    *,
    part_id: str,
    alt_part_id: str,
    status: str,
    qual_lead_time_weeks: int | None = None,
) -> None:
    if status not in ("QUALIFIED", "IN_QUAL", "NOT_STARTED"):
        raise ValueError(f"bad qualification status {status!r}")
    row = con.execute(
        "SELECT COUNT(*) FROM alternates WHERE part_id = ? AND alt_part_id = ?",
        [part_id, alt_part_id],
    ).fetchone()
    if not row or not row[0]:
        raise ValueError(f"no alternate {alt_part_id!r} for {part_id!r}")
    con.execute(
        "UPDATE alternates SET qualification_status = ? WHERE part_id = ? AND alt_part_id = ?",
        [status, part_id, alt_part_id],
    )
    if qual_lead_time_weeks is not None:
        con.execute(
            "UPDATE alternates SET qual_lead_time_weeks = ? WHERE part_id = ? AND alt_part_id = ?",
            [qual_lead_time_weeks, part_id, alt_part_id],
        )


@register("set_platform_phase")
def set_platform_phase(
    con: duckdb.DuckDBPyConnection, *, platform_id: str, phase: str, last_ship_date: date | str
) -> None:
    """Change lifecycle phase and last-ship date; forecast buckets starting after the new
    last-ship date are removed so the snapshot stays lifecycle-consistent."""
    if phase not in ("NPI", "SUSTAIN", "EOL_ANNOUNCED", "EOL"):
        raise ValueError(f"bad lifecycle phase {phase!r}")
    _require(con, "platforms", "platform_id", platform_id)
    last = _as_date(last_ship_date)
    con.execute(
        "UPDATE platforms SET lifecycle_phase = ?, last_ship_date = ? WHERE platform_id = ?",
        [phase, last, platform_id],
    )
    con.execute(
        "DELETE FROM demand_forecast WHERE platform_id = ? AND bucket_start > ?",
        [platform_id, last],
    )


@register("add_decision")
def add_decision(
    con: duckdb.DuckDBPyConnection,
    *,
    part_id: str,
    action_type: str,
    quantity: int | None,
    status: str,
    weeks_ago: int,
    state_db: Path = DEFAULT_STATE_DB,
    rationale: str = "Seeded by scenario for memory tests.",
) -> None:
    """Add a historical planner decision to this snapshot's decision log (SQLite)."""
    _require(con, "parts", "part_id", part_id)
    row = con.execute("SELECT snapshot_id FROM meta").fetchone()
    assert row is not None
    decisions.insert_decision(
        state_db, row[0], part_id, action_type, quantity, status, weeks_ago, rationale
    )


@register("set_on_hand")
def set_on_hand(con: duckdb.DuckDBPyConnection, *, part_id: str, location: str, units: int) -> None:
    """Set on-hand units at one location (quality-hold units are left as they are)."""
    if units < 0:
        raise ValueError("on-hand units cannot be negative")
    _require(con, "inventory", "part_id", part_id)
    row = con.execute(
        "SELECT COUNT(*) FROM inventory WHERE part_id = ? AND location = ?", [part_id, location]
    ).fetchone()
    if not row or not row[0]:
        raise ValueError(f"no inventory row for {part_id!r} at {location!r}")
    con.execute(
        "UPDATE inventory SET on_hand_units = ? WHERE part_id = ? AND location = ?",
        [units, part_id, location],
    )


@register("update_po")
def update_po(
    con: duckdb.DuckDBPyConnection,
    *,
    po_id: str,
    qty_units: int | None = None,
    need_week_idx: int | None = None,
    promise_week_idx: int | None = None,
    cancel_window_weeks: int | None = None,
) -> None:
    """Overwrite the given fields of an open PO (E&O and under-order scenarios)."""
    _require(con, "purchase_orders", "po_id", po_id)
    changes = {
        "qty_units": qty_units, "need_week_idx": need_week_idx,
        "promise_week_idx": promise_week_idx, "cancel_window_weeks": cancel_window_weeks,
    }  # fmt: skip
    given = {k: v for k, v in changes.items() if v is not None}
    if not given:
        raise ValueError("update_po needs at least one field to change")
    sets = ", ".join(f"{k} = ?" for k in given)
    con.execute(f"UPDATE purchase_orders SET {sets} WHERE po_id = ?", [*given.values(), po_id])


@register("add_po")
def add_po(
    con: duckdb.DuckDBPyConnection,
    *,
    part_id: str,
    qty_units: int,
    need_week_idx: int,
    promise_week_idx: int | None = None,
    cancel_window_weeks: int = 0,
) -> None:
    """Add an open PO at the part's unit cost and supplier. Ids are PO-S0001, PO-S0002, ..."""
    row = con.execute(
        "SELECT supplier_id, unit_cost_usd FROM parts WHERE part_id = ?", [part_id]
    ).fetchone()
    if row is None:
        raise ValueError(f"parts.part_id = {part_id!r} not found")
    (n,) = con.execute(
        "SELECT COUNT(*) FROM purchase_orders WHERE po_id LIKE 'PO-S%'"
    ).fetchone() or (0,)
    promise = need_week_idx if promise_week_idx is None else promise_week_idx
    con.execute(
        "INSERT INTO purchase_orders VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)",
        [f"PO-S{n + 1:04d}", part_id, row[0], qty_units, need_week_idx, promise,
         cancel_window_weeks, row[1]],
    )  # fmt: skip


@register("set_cancel_window")
def set_cancel_window(con: duckdb.DuckDBPyConnection, *, part_id: str, weeks: int) -> None:
    """Set the cancel window of every open PO of a part (a PO is cancellable if need > window)."""
    _require(con, "purchase_orders", "part_id", part_id)
    con.execute(
        "UPDATE purchase_orders SET cancel_window_weeks = ? WHERE part_id = ?", [weeks, part_id]
    )


@register("add_alternate")
def add_alternate(
    con: duckdb.DuckDBPyConnection,
    *,
    part_id: str,
    alt_part_id: str,
    qualification_status: str,
    qual_lead_time_weeks: int,
    notes: str = "",
) -> None:
    """Link an alternate part. If `alt_part_id` is new, it is created as a copy of `part_id`
    (same supplier, cost and lead time) with zero stock and no BOM lines."""
    if qualification_status not in ("QUALIFIED", "IN_QUAL", "NOT_STARTED"):
        raise ValueError(f"bad qualification status {qualification_status!r}")
    _require(con, "parts", "part_id", part_id)
    if not _exists(con, "parts", "part_id", alt_part_id):
        con.execute(
            "INSERT INTO parts SELECT ?, 'Alternate for ' || part_id, commodity, supplier_id, "
            "unit_cost_usd, lead_time_weeks, moq, order_multiple, 'ACTIVE', shelf_life_months "
            "FROM parts WHERE part_id = ?",
            [alt_part_id, part_id],
        )
        for loc in ("CM_HUB", "SERVICE_DEPOT"):
            con.execute("INSERT INTO inventory VALUES (?, ?, 0, 0)", [alt_part_id, loc])
    if _exists(con, "alternates", "alt_part_id", alt_part_id):
        raise ValueError(f"{alt_part_id!r} is already an alternate of a part")
    con.execute(
        "INSERT INTO alternates VALUES (?, ?, ?, ?, ?)",
        [part_id, alt_part_id, qualification_status, qual_lead_time_weeks, notes],
    )


@register("drop_rows")
def drop_rows(con: duckdb.DuckDBPyConnection, *, table: str, where: str) -> None:
    """Delete rows matching a SQL predicate (missing-data scenarios). At least one must match."""
    if table not in TABLES or table == "meta":
        raise ValueError(f"cannot drop rows from {table!r}")
    if not where.strip() or ";" in where or "--" in where:
        raise ValueError("drop_rows needs a single, non-empty predicate")
    row = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()
    if not row or not row[0]:
        raise ValueError(f"no rows in {table} match {where!r}")
    con.execute(f"DELETE FROM {table} WHERE {where}")


@register("set_meta")
def set_meta(con: duckdb.DuckDBPyConnection, *, as_of_date: date | str) -> None:
    """Change the snapshot's as-of date (a stale snapshot)."""
    con.execute("UPDATE meta SET as_of_date = ?", [_as_date(as_of_date)])


@register("inject_text")
def inject_text(
    con: duckdb.DuckDBPyConnection, *, table: str, row_key: dict[str, Any], field: str, text: str
) -> None:
    """Write (possibly adversarial) text into an untrusted free-text field."""
    if UNTRUSTED_FIELDS.get(table) != field:
        raise ValueError(f"{table}.{field} is not an untrusted text field")
    columns = {r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
    if not row_key or not set(row_key) <= columns:
        raise ValueError(f"row_key must name columns of {table}: {sorted(columns)}")
    where = " AND ".join(f"{col} = ?" for col in row_key)
    params = list(row_key.values())
    row = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()
    if not row or not row[0]:
        raise ValueError(f"no row in {table} matches {row_key}")
    con.execute(f"UPDATE {table} SET {field} = ? WHERE {where}", [text, *params])


def apply_op(
    con: duckdb.DuckDBPyConnection,
    name: str,
    params: dict[str, Any],
    state_db: Path = DEFAULT_STATE_DB,
) -> None:
    if name not in OPS:
        raise ValueError(f"unknown op {name!r}; known ops: {sorted(OPS)}")
    kwargs = dict(params)
    if name in NEEDS_STATE_DB:
        kwargs.setdefault("state_db", state_db)
    OPS[name](con, **kwargs)


def build_scenario(
    scenario_id: str,
    perturbations: list[dict[str, Any]],
    base_snapshot: str = "base",
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    state_db: Path = DEFAULT_STATE_DB,
) -> tuple[Path, ValidationReport]:
    """Copy the base snapshot, apply `perturbations` in order, validate, return (path, report).

    Raises if validation fails, unless the scenario uses an op that deliberately breaks data.
    """
    if scenario_id == base_snapshot:
        raise ValueError("a scenario id must differ from the base snapshot id")
    base_path = snapshot_dir / f"{base_snapshot}.duckdb"
    if not base_path.exists():
        raise FileNotFoundError(f"{base_path} not found; run `sentinel datagen` first")
    path = snapshot_dir / f"{scenario_id}.duckdb"
    for stale in (path, Path(f"{path}.wal")):
        stale.unlink(missing_ok=True)
    shutil.copyfile(base_path, path)

    decisions.copy_history(state_db, base_snapshot, scenario_id)
    con = duckdb.connect(str(path))
    try:
        con.execute("UPDATE meta SET snapshot_id = ?", [scenario_id])
        for step in perturbations:
            params = dict(step)
            apply_op(con, str(params.pop("op")), params, state_db)
        report = validate_snapshot(con)
    finally:
        con.close()
    if not {str(s["op"]) for s in perturbations} & DATA_BREAKING_OPS:
        report.raise_if_failed()
    return path, report


def load_perturbations(
    scenario_id: str,
    scenario_dir: Path = DEFAULT_SCENARIO_DIR,
    resolver: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Read `perturbations:` from evals/scenarios/<id>.yaml.

    Resolver tokens (`{first_open_po:<part>}`) need the base snapshot, so the caller passes a
    `resolver` (see evals.scenario). Without one, a token is an error.
    """
    file = scenario_dir / f"{scenario_id}.yaml"
    if not file.exists():
        raise FileNotFoundError(f"scenario file {file} not found")
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    steps: list[dict[str, Any]] = data.get("perturbations") or []
    if resolver is not None:
        return resolver(steps)
    for step in steps:
        for key, value in step.items():
            if isinstance(value, str) and value.startswith("{") and ":" in value:
                raise ValueError(
                    f"resolver token {value!r} in {key!r} needs a resolver (see evals.scenario)"
                )
    return steps
