"""Snapshot validation (spec 01 section 4). Fails loudly with a readable report."""

from dataclasses import dataclass, field
from statistics import median

import duckdb

from supply_sentinel.datagen import catalog

ALL_PLATFORMS = ["ORB-S16", "ORB-S8", "ORB-S4", "ORB-L4", "ORB-L1", "ORB-M1"]

# Anchor part -> {platform: qty_per}, copied from spec 01 section 1.3 (independent of catalog.BOM).
ANCHOR_USAGE: dict[str, dict[str, int]] = {
    "P-ASIC-102T": {"ORB-S16": 1},
    "P-ASIC-51T": {"ORB-S8": 1},
    "P-CPU-8C": {"ORB-S16": 1, "ORB-S8": 1, "ORB-S4": 1},
    "P-CPU-4C": {"ORB-L4": 1, "ORB-L1": 1, "ORB-M1": 1},
    "P-BMC-A": dict.fromkeys(ALL_PLATFORMS, 1),
    "P-BMC-B": {},
    "P-CPLD-S4": {"ORB-S4": 1},
    "P-PSU-3000AC": {"ORB-S16": 2, "ORB-S8": 2},
    "P-PSU-2000AC": {"ORB-S4": 2, "ORB-L4": 2},
    "P-PSU-650AC": {"ORB-L1": 1, "ORB-M1": 1},
    "P-FAN-80": {"ORB-S16": 6, "ORB-S8": 6, "ORB-S4": 5},
    "P-FAN-40": {"ORB-L4": 4, "ORB-L1": 3, "ORB-M1": 2},
    "P-DDR5-16G": {"ORB-S16": 2, "ORB-S8": 2},
    "P-DDR4-8G": {"ORB-S4": 1, "ORB-L4": 1, "ORB-L1": 1, "ORB-M1": 1},
    "P-RTM-224G": {"ORB-S16": 8},
    "P-CLK-J1": {"ORB-S16": 1, "ORB-S8": 1, "ORB-S4": 1, "ORB-L4": 1},
    "P-SSD-240": {"ORB-S16": 1, "ORB-S8": 1, "ORB-S4": 1, "ORB-L4": 1},
    **{f"P-PCBA-{p[4:]}": {p: 1} for p in ALL_PLATFORMS},
}

# (child table, child column, parent table, parent column, may be NULL)
FOREIGN_KEYS = [
    ("parts", "supplier_id", "suppliers", "supplier_id", False),
    ("bom", "platform_id", "platforms", "platform_id", False),
    ("bom", "part_id", "parts", "part_id", False),
    ("alternates", "part_id", "parts", "part_id", False),
    ("alternates", "alt_part_id", "parts", "part_id", False),
    ("shipments_history", "platform_id", "platforms", "platform_id", False),
    ("demand_forecast", "platform_id", "platforms", "platform_id", False),
    ("service_demand", "platform_id", "platforms", "platform_id", False),
    ("inventory", "part_id", "parts", "part_id", False),
    ("purchase_orders", "part_id", "parts", "part_id", False),
    ("purchase_orders", "supplier_id", "suppliers", "supplier_id", False),
    ("supplier_commits", "part_id", "parts", "part_id", False),
    ("supplier_commits", "supplier_id", "suppliers", "supplier_id", False),
    ("eol_notices", "part_id", "parts", "part_id", False),
    ("eol_notices", "supplier_id", "suppliers", "supplier_id", False),
    ("eol_notices", "replacement_part_id", "parts", "part_id", True),
    ("forecast_accuracy", "platform_id", "platforms", "platform_id", False),
]


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


class SnapshotValidationError(Exception):
    pass


@dataclass
class ValidationReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name, "PASS" if passed else "FAIL", detail))

    def skip(self, name: str, detail: str) -> None:
        self.checks.append(CheckResult(name, "SKIP", detail))

    def render(self) -> str:
        width = max(len(c.name) for c in self.checks)
        lines = [f"{c.status:<4}  {c.name:<{width}}  {c.detail}".rstrip() for c in self.checks]
        return "\n".join(lines)

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise SnapshotValidationError("Snapshot validation failed:\n" + self.render())


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


def coverage_weeks(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """CM-hub on-hand divided by average weekly gross requirement over the next 12 weeks."""
    rows = con.execute(
        """
        WITH cur AS (SELECT MAX(forecast_version) AS v FROM demand_forecast),
        wk AS (
            SELECT f.platform_id, f.units FROM demand_forecast f, cur, meta
            WHERE f.forecast_version = cur.v AND f.bucket_type = 'WEEK'
              AND f.bucket_start < meta.as_of_date + 84
        ),
        dem AS (
            SELECT b.part_id, SUM(b.qty_per * wk.units) / 12 AS weekly
            FROM bom b JOIN wk USING (platform_id) GROUP BY 1
        )
        SELECT d.part_id, i.on_hand_units / d.weekly
        FROM dem d JOIN inventory i ON i.part_id = d.part_id AND i.location = 'CM_HUB'
        WHERE d.weekly > 0
        """
    ).fetchall()
    return {part: float(weeks) for part, weeks in rows}


def _check_referential_integrity(con: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    problems = []
    for child, col, parent, pcol, nullable in FOREIGN_KEYS:
        null_clause = f"{col} IS NOT NULL AND " if nullable else ""
        n = _scalar(
            con,
            f"SELECT COUNT(*) FROM {child} WHERE {null_clause}{col} NOT IN "
            f"(SELECT {pcol} FROM {parent})",
        )
        if n:
            problems.append(f"{child}.{col}: {n} orphan rows")
    report.add("referential_integrity", not problems, "; ".join(problems) or "all FKs resolve")


def _check_coverage(con: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    cover = coverage_weeks(con)
    if not cover:
        report.add("coverage_sanity", False, "no parts with production demand")
        return
    med = median(cover.values())
    report.add("coverage_sanity", 3 <= med <= 12, f"median cover {med:.1f} weeks (need 3-12)")


def _check_lifecycle(con: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    late = _scalar(
        con,
        "SELECT COUNT(*) FROM demand_forecast f JOIN platforms p USING (platform_id) "
        "WHERE f.units > 0 AND f.bucket_start > p.last_ship_date",
    )
    ended = [
        r[0]
        for r in con.execute(
            "SELECT p.platform_id FROM platforms p, meta m WHERE p.last_ship_date < m.as_of_date "
            "AND EXISTS (SELECT 1 FROM demand_forecast f WHERE f.platform_id = p.platform_id "
            "AND f.units > 0)"
        ).fetchall()
    ]
    bad_bom = _scalar(
        con,
        "SELECT COUNT(*) FROM bom b JOIN platforms p USING (platform_id) "
        "WHERE b.effective_from < p.launch_date OR b.effective_to > p.eosl_date",
    )
    detail = f"{late} forecast rows after last ship; ended platforms with forecast: {ended}"
    report.add("lifecycle_sanity", late == 0 and not ended and bad_bom == 0, detail)


def _check_consistency(con: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    checks = {
        "negative inventory": "SELECT COUNT(*) FROM inventory "
        "WHERE on_hand_units < 0 OR quality_hold_units < 0",
        "non-positive part cost": "SELECT COUNT(*) FROM parts WHERE unit_cost_usd <= 0",
        "non-positive PO cost or qty": "SELECT COUNT(*) FROM purchase_orders "
        "WHERE unit_cost_usd <= 0 OR qty_units <= 0",
        "non-positive ASP": "SELECT COUNT(*) FROM platforms WHERE asp_usd <= 0",
        "negative forecast": "SELECT COUNT(*) FROM demand_forecast WHERE units < 0",
        "meta rows != 1": "SELECT ABS(COUNT(*) - 1) FROM meta",
    }
    problems = [f"{label}: {n}" for label, sql in checks.items() if (n := _scalar(con, sql))]
    report.add("data_consistency", not problems, "; ".join(problems) or "ok")


def _check_anchors(con: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    problems = []
    for spec in (s for s in catalog.PARTS if s.anchor):
        row = con.execute(
            "SELECT supplier_id, unit_cost_usd, lead_time_weeks, moq, order_multiple "
            "FROM parts WHERE part_id = ?",
            [spec.part_id],
        ).fetchone()
        want = (spec.supplier_id, spec.unit_cost_usd, spec.lead_time_weeks, spec.moq,
                spec.order_multiple)  # fmt: skip
        if row is None:
            problems.append(f"{spec.part_id} missing")
        elif tuple(row) != want:
            problems.append(f"{spec.part_id} attributes {tuple(row)} != {want}")
    for part, usage in ANCHOR_USAGE.items():
        got = dict(
            con.execute("SELECT platform_id, qty_per FROM bom WHERE part_id = ?", [part]).fetchall()
        )
        if got != usage:
            problems.append(f"{part} used on {got}, expected {usage}")
    report.add("anchor_parts", not problems, "; ".join(problems) or "all anchors match the spec")


def _check_shape(con: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    problems = []
    n_parts = _scalar(con, "SELECT COUNT(*) FROM parts")
    if not 45 <= n_parts <= 60:
        problems.append(f"{n_parts} parts (need 45-60)")
    if _scalar(con, "SELECT COUNT(*) FROM platforms") != 6:
        problems.append("platform count is not 6")
    if _scalar(con, "SELECT COUNT(*) FROM suppliers") != 12:
        problems.append("supplier count is not 12")
    for pid, n in con.execute("SELECT platform_id, COUNT(*) FROM bom GROUP BY 1").fetchall():
        if not 10 <= n <= 14:
            problems.append(f"{pid} has {n} BOM lines (need 10-14)")
    shared = _scalar(
        con, "SELECT COUNT(*) FROM (SELECT part_id FROM bom GROUP BY 1 HAVING COUNT(*) >= 2)"
    )
    if shared < 10:
        problems.append(f"only {shared} shared parts (need 10)")
    report.add("shape", not problems, "; ".join(problems) or f"{n_parts} parts, {shared} shared")


def validate_snapshot(con: duckdb.DuckDBPyConnection) -> ValidationReport:
    """Run every check. The caller decides whether to raise (`report.raise_if_failed()`)."""
    report = ValidationReport()
    _check_referential_integrity(con, report)
    _check_coverage(con, report)
    _check_lifecycle(con, report)
    _check_consistency(con, report)
    _check_anchors(con, report)
    _check_shape(con, report)
    report.skip("natural_exceptions", "enabled in Phase 2, once the engine exists")
    return report
