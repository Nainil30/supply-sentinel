"""Seeded generator for the base snapshot (spec 01 sections 2-3, 6).

Same seed -> identical table contents. Each component draws from its own random stream
(`SeedSequence([seed, stream])`), so changing one component does not reshuffle the others.
"""

import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from supply_sentinel.datagen import catalog, decisions
from supply_sentinel.datagen.catalog import AS_OF, HISTORY_WEEKS, PARTS, PLATFORMS, SUPPLIERS
from supply_sentinel.datagen.demand import (
    forecast_buckets,
    forecast_level,
    mean_level,
    service_annual_units,
    service_weekly_rate,
    week_idx_of,
    week_start,
)
from supply_sentinel.datagen.schema import TABLES, create_schema
from supply_sentinel.datagen.validate import ValidationReport, coverage_weeks, validate_snapshot

DEFAULT_SNAPSHOT_DIR = Path("data/snapshots")
DEFAULT_STATE_DB = decisions.DEFAULT_DB
SAMPLE_SUMMARY = Path("data/sample/base_summary.md")

# stream ids for independent random generators
S_HISTORY, S_ACCURACY, S_FORECAST, S_INVENTORY, S_PO, S_COMMIT, S_DECISION = range(1, 8)

ACCURACY_LAGS = (1, 4, 13)
PREVIOUS_FORECAST_FACTOR = {"ORB-S16": 0.97, "ORB-S4": 1.03, "ORB-M1": 0.99}
CANCEL_WINDOW_FRACTION = {"HIGH": 0.70, "MEDIUM": 0.50, "LOW": 0.35}
SLIP_RATE = 0.15  # average share of POs promised later than needed


def _rng(seed: int, stream: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, stream]))


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple[Any, ...]]) -> None:
    if rows:
        marks = ",".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)


def table_hashes(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """SHA-256 of each table's rows in a canonical order (used to prove determinism)."""
    out = {}
    for table in TABLES:
        rows = con.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
        out[table] = hashlib.sha256(repr(rows).encode()).hexdigest()
    return out


def _noise(rng: np.random.Generator, sigma: float) -> float:
    """Mean-one lognormal multiplier."""
    return float(rng.lognormal(-(sigma**2) / 2, sigma)) if sigma > 0 else 1.0


def _weekly_gross(usage: dict[str, list[tuple[Any, int]]], part_id: str, t: int) -> float:
    return sum(qty * forecast_level(p, t) for p, qty in usage.get(part_id, []))


def _load_static(con: duckdb.DuckDBPyConnection) -> None:
    _insert(con, "platforms", [
        (p.platform_id, p.description, p.lifecycle_phase, p.launch_date, p.last_ship_date,
         p.eosl_date, p.asp_usd) for p in PLATFORMS
    ])  # fmt: skip
    _insert(con, "suppliers", list(SUPPLIERS))
    _insert(con, "parts", [
        (s.part_id, s.description, s.commodity, s.supplier_id, s.unit_cost_usd, s.lead_time_weeks,
         s.moq, s.order_multiple, s.lifecycle_status, None) for s in PARTS
    ])  # fmt: skip
    launch = {p.platform_id: p.launch_date for p in PLATFORMS}
    _insert(con, "bom", [
        (pid, part, qty, launch[pid], None) for pid, lines in catalog.BOM.items()
        for part, qty in lines
    ])  # fmt: skip
    _insert(con, "alternates", list(catalog.ALTERNATES))


def _load_history_and_forecast(
    con: duckdb.DuckDBPyConnection, seed: int
) -> dict[str, list[tuple[int, int]]]:
    """Shipments, forecast (two versions), service demand. Returns production history."""
    rng = _rng(seed, S_HISTORY)
    ship_rows: list[tuple[Any, ...]] = []
    production: dict[str, list[tuple[int, int]]] = {}
    for p in PLATFORMS:
        launch_t = week_idx_of(p.launch_date)
        weeks = [t for t in range(-HISTORY_WEEKS, 0) if mean_level(p, t) > 0]
        levels = np.array([mean_level(p, t) for t in weeks])
        noise = np.array([_noise(rng, p.noise_sigma) for _ in weeks])
        if len(weeks):
            # Rescale so noise averages exactly 1 (volume-weighted); the forecast bias
            # is then the only systematic gap between forecast and actuals.
            noise = noise * levels.sum() / (levels * noise).sum()
        production[p.platform_id] = [
            (t, round(float(lv * nz))) for t, lv, nz in zip(weeks, levels, noise, strict=True)
        ]
        for t, units in production[p.platform_id]:
            ship_rows.append((p.platform_id, week_start(t), units, "PRODUCTION"))
        for t in range(max(-HISTORY_WEEKS, launch_t), 0):
            if p.platform_id == "ORB-L1":
                spares = int(rng.integers(2, 5))
            else:
                spares = int(rng.poisson(p.service_per_week))
            ship_rows.append((p.platform_id, week_start(t), spares, "SERVICE"))
    _insert(con, "shipments_history", ship_rows)

    rng = _rng(seed, S_FORECAST)
    current, previous = AS_OF, AS_OF - timedelta(days=7)
    fc_rows: list[tuple[Any, ...]] = []
    for p in PLATFORMS:
        factor = PREVIOUS_FORECAST_FACTOR.get(p.platform_id, 1.0)
        for start, kind, fc_units in forecast_buckets(p):
            fc_rows.append((p.platform_id, start, kind, fc_units, current))
            prev_units = round(fc_units * factor * float(rng.uniform(0.99, 1.01)), 2)
            fc_rows.append((p.platform_id, start, kind, prev_units, previous))
    _insert(con, "demand_forecast", fc_rows)

    _insert(con, "service_demand", [
        (p.platform_id, year, service_annual_units(p, year))
        for p in PLATFORMS for year in range(AS_OF.year, p.eosl_date.year + 1)
    ])  # fmt: skip
    return production


def _load_forecast_accuracy(
    con: duckdb.DuckDBPyConnection, seed: int, production: dict[str, list[tuple[int, int]]]
) -> None:
    """MAPE, bias and sigma of archived forecasts vs actuals over the last 52 weeks.

    The archived forecast for a past week is its noise-free level times the platform bias times
    a small lag-dependent error. Bias > 0 means the forecast was too high.
    """
    rng = _rng(seed, S_ACCURACY)
    rows = []
    for p in PLATFORMS:
        recent = [(t, a) for t, a in production[p.platform_id] if t >= -52 and a > 0]
        if len(recent) < 4:
            continue
        for lag in ACCURACY_LAGS:
            errs, apes, actuals = [], [], []
            for t, actual in recent:
                f = forecast_level(p, t) * float(rng.lognormal(0, 0.03 * math.sqrt(lag)))
                errs.append(f - actual)
                apes.append(abs(f - actual) / actual)
                actuals.append(actual)
            rows.append((
                p.platform_id, lag, round(float(np.mean(apes)), 4),
                round(float(np.sum(errs) / np.sum(actuals)), 4),
                round(float(np.std(errs, ddof=1)), 2),
            ))  # fmt: skip
    _insert(con, "forecast_accuracy", rows)


def _load_inventory(con: duckdb.DuckDBPyConnection, seed: int, cover: dict[str, float]) -> None:
    """On-hand at the CM hub (by weeks of cover) and at the service depot (by spares need)."""
    rng = _rng(seed, S_INVENTORY)
    usage = _usage()
    rows: list[tuple[Any, ...]] = []
    hold_candidates = []
    for spec in sorted(PARTS, key=lambda s: s.part_id):
        avg12 = sum(_weekly_gross(usage, spec.part_id, t) for t in range(12)) / 12
        weeks = cover.get(spec.part_id, round(float(rng.uniform(3.0, 10.0)), 1))
        hub = round(weeks * avg12)
        rows.append((spec.part_id, "CM_HUB", hub, 0))
        if hub > 0 and not spec.anchor:
            hold_candidates.append(spec.part_id)
        depot_weeks = float(rng.uniform(20, 40))
        depot_need = sum(
            qty * service_weekly_rate(p, 0) for p, qty in usage.get(spec.part_id, [])
        )  # fmt: skip
        rows.append((spec.part_id, "SERVICE_DEPOT", round(depot_need * depot_weeks), 0))
    held = set(rng.choice(hold_candidates, size=3, replace=False).tolist())
    rows = [
        (part, loc, oh, round(oh * 0.02) if part in held and loc == "CM_HUB" else 0)
        for part, loc, oh, _ in rows
    ]
    _insert(con, "inventory", rows)


def _usage() -> dict[str, list[tuple[Any, int]]]:
    by_id = {p.platform_id: p for p in PLATFORMS}
    usage: dict[str, list[tuple[Any, int]]] = defaultdict(list)
    for pid, lines in catalog.BOM.items():
        for part, qty in lines:
            usage[part].append((by_id[pid], qty))
    return usage


def _load_pos_and_commits(con: duckdb.DuckDBPyConnection, seed: int) -> None:
    rng = _rng(seed, S_PO)
    usage = _usage()
    suppliers = {s[0]: s for s in SUPPLIERS}
    mean_miss = sum(1 - s[4] for s in SUPPLIERS) / len(SUPPLIERS)
    # PO timing follows the cover already chosen in _load_inventory, so read it back
    on_hand = dict(
        con.execute(
            "SELECT part_id, on_hand_units FROM inventory WHERE location = 'CM_HUB'"
        ).fetchall()
    )

    raw: list[tuple[str, str, int, int, int, str, int, float]] = []
    for spec in sorted(PARTS, key=lambda s: s.part_id):
        horizon = spec.lead_time_weeks + 8
        gross = [_weekly_gross(usage, spec.part_id, t) for t in range(horizon + 1)]
        avg12 = sum(gross[:12]) / 12
        if avg12 <= 0:
            continue
        weeks_cover = on_hand[spec.part_id] / avg12
        start = max(1, int(weeks_cover) - 2)  # two weeks of overlap act as safety stock
        k = 2 if spec.lead_time_weeks > 16 else 1
        while k * avg12 < spec.moq and k < 8:
            k *= 2
        supplier = suppliers[spec.supplier_id]
        slip_rate = SLIP_RATE * (1 - supplier[4]) / mean_miss
        window = round(CANCEL_WINDOW_FRACTION[supplier[5]] * spec.lead_time_weeks)
        need = start
        while need <= max(spec.lead_time_weeks, start):
            demand = sum(gross[need : need + k])
            if demand > 0:
                mult = spec.order_multiple
                qty = max(spec.moq, math.ceil(demand / mult) * mult)
                slip = 0
                if rng.random() < slip_rate:
                    slip = int(rng.choice([1, 2, 3], p=[0.5, 0.3, 0.2]))
                partial = need <= 2 and rng.random() < 0.3
                raw.append((
                    spec.part_id, spec.supplier_id, qty, need, need + slip,
                    "PARTIAL" if partial else "OPEN", window, spec.unit_cost_usd,
                ))  # fmt: skip
            need += k

    po_rows = [(f"PO-{10001 + i}", *row) for i, row in enumerate(raw)]
    _insert(con, "purchase_orders", po_rows)

    rng = _rng(seed, S_COMMIT)
    commits: dict[tuple[str, str, int], int] = defaultdict(int)
    for _, part, sup, qty, _need, promise, _status, _win, _cost in po_rows:
        if promise <= 11:
            commits[(part, sup, promise)] += qty
    commit_rows = []
    for (part, sup, week), units in sorted(commits.items()):
        note = str(rng.choice(catalog.NOTES_PLAIN)) if rng.random() < 0.10 else None
        commit_rows.append((part, sup, week, units, note))
    _insert(con, "supplier_commits", commit_rows)


NOTICE_TEXT = (
    "Product change notice from Meridian Memory: the DDR4 8GB module is now classified Not "
    "Recommended for New Designs (NRND). Production continues. A last-time-buy date and a "
    "last-ship date will be communicated in a later notice. Contact your account manager "
    "with roadmap questions."
)


def build_base(
    seed: int = 42,
    snapshot_id: str = "base",
    out_dir: Path = DEFAULT_SNAPSHOT_DIR,
    state_db: Path = DEFAULT_STATE_DB,
    validate: bool = True,
) -> Path:
    """Generate the base snapshot, seed the decision log, validate, and return the file path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{snapshot_id}.duckdb"
    for stale in (path, Path(f"{path}.wal")):
        stale.unlink(missing_ok=True)

    cover = dict(catalog.TIGHT_COVERAGE_WEEKS)
    con = duckdb.connect(str(path))
    try:
        create_schema(con)
        _insert(con, "meta", [
            (snapshot_id, AS_OF, seed, catalog.GENERATOR_VERSION, datetime.combine(AS_OF, time()))
        ])  # fmt: skip
        _load_static(con)
        production = _load_history_and_forecast(con, seed)
        _load_forecast_accuracy(con, seed, production)
        _load_inventory(con, seed, cover)
        _load_pos_and_commits(con, seed)
        _insert(con, "eol_notices", [
            ("EOL-0001", "P-DDR4-8G", "SUP-06", date(2026, 9, 14), None, None, None, NOTICE_TEXT)
        ])  # fmt: skip
    finally:
        con.close()

    decisions.seed_history(state_db, snapshot_id, _rng(seed, S_DECISION))
    if validate:
        report = validate_path(path)
        report.raise_if_failed()
    return path


def validate_path(path: Path) -> ValidationReport:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return validate_snapshot(con)
    finally:
        con.close()


def hashes_for(path: Path) -> dict[str, str]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return table_hashes(con)
    finally:
        con.close()


def write_summary(path: Path, out: Path = SAMPLE_SUMMARY) -> Path:
    """Write the committed, human-readable description of a snapshot."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        meta = con.execute("SELECT snapshot_id, as_of_date, seed FROM meta").fetchone()
        assert meta is not None
        lines = [
            f"# Snapshot summary: `{meta[0]}`",
            "",
            f"Fictional OEM *Orbis Networks*. As-of date {meta[1]} (Monday, `week_idx` 0), "
            f"seed {meta[2]}. Generated by `sentinel datagen`; synthetic data only.",
            "",
            "## Row counts",
            "",
            "| Table | Rows |",
            "|---|---:|",
        ]
        for t in TABLES:
            (n,) = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone() or (0,)
            lines.append(f"| {t} | {n} |")

        lines += ["", "## Platforms", "",
                  "| Platform | Phase | Last ship | BOM lines | Weekly units next 12 wks |",
                  "|---|---|---|---:|---:|"]  # fmt: skip
        for pid, phase, last in con.execute(
            "SELECT platform_id, lifecycle_phase, last_ship_date FROM platforms ORDER BY 1"
        ).fetchall():
            (n_bom,) = con.execute("SELECT COUNT(*) FROM bom WHERE platform_id=?", [pid]).fetchone()  # type: ignore[misc]
            (wk,) = con.execute(
                "SELECT COALESCE(SUM(units), 0) / 12 FROM demand_forecast WHERE platform_id = ? "
                "AND bucket_type = 'WEEK' AND forecast_version = ? AND bucket_start < ?",
                [pid, AS_OF, AS_OF + timedelta(weeks=12)],
            ).fetchone()  # type: ignore[misc]
            lines.append(f"| {pid} | {phase} | {last} | {n_bom} | {wk:.1f} |")

        cover = sorted(coverage_weeks(con).values())
        bins = [(0, 3), (3, 5), (5, 7), (7, 9), (9, 12), (12, 10_000)]
        lines += ["", "## Weeks of cover at the CM hub (parts with production demand)", "",
                  "| Weeks | Parts |", "|---|---:|"]  # fmt: skip
        for lo, hi in bins:
            label = f"{lo}-{hi}" if hi < 10_000 else f"{lo}+"
            lines.append(f"| {label} | {sum(lo <= c < hi for c in cover)} |")
        median = (
            cover[len(cover) // 2]
            if len(cover) % 2
            else sum(cover[len(cover) // 2 - 1 : len(cover) // 2 + 1]) / 2
        )
        lines.append(f"\nMedian cover: {median:.1f} weeks across {len(cover)} parts.")

        lines += ["", "## Forecast accuracy (lag 4 weeks)", "",
                  "| Platform | MAPE | Bias | Sigma (units) |", "|---|---:|---:|---:|"]  # fmt: skip
        for pid, mape, bias, sigma in con.execute(
            "SELECT platform_id, mape, bias, sigma_units FROM forecast_accuracy "
            "WHERE lag_weeks = 4 ORDER BY 1"
        ).fetchall():
            lines.append(f"| {pid} | {mape:.1%} | {bias:+.1%} | {sigma:.1f} |")

        lines += ["", "## Open purchase orders", "", "| Status | POs | Slipped (promise > need) |",
                  "|---|---:|---:|"]  # fmt: skip
        for status, n, slipped in con.execute(
            "SELECT status, COUNT(*), SUM((promise_week_idx > need_week_idx)::INT) "
            "FROM purchase_orders GROUP BY 1 ORDER BY 1"
        ).fetchall():
            lines.append(f"| {status} | {n} | {slipped} |")

        lines += ["", "## Natural exceptions", "",
                  "Not computed yet: the exception engine arrives in Phase 2, which will list the "
                  "2-6 natural exceptions of the base snapshot here.", ""]  # fmt: skip
    finally:
        con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out
