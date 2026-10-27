"""Decision log in SQLite (spec 01 section 2).

It lives outside DuckDB because the MCP server, the agent and the UI all write to it from
different processes, while DuckDB snapshots are read-only. The primary key is
(snapshot_id, rec_id) so a scenario snapshot can carry its own copy of the history.
"""

import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np

from supply_sentinel.datagen.catalog import AS_OF

DEFAULT_DB = Path("data/state/decisions.sqlite")

DDL = """
CREATE TABLE IF NOT EXISTS decision_log (
    rec_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, exception_id TEXT, part_id TEXT,
    action_type TEXT NOT NULL, quantity INTEGER,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'EDITED', 'REJECTED')),
    decided_by TEXT, decided_at TEXT, rationale TEXT, run_id TEXT,
    PRIMARY KEY (snapshot_id, rec_id)
);
"""

COLUMNS = [
    "rec_id", "snapshot_id", "exception_id", "part_id", "action_type", "quantity",
    "status", "decided_by", "decided_at", "rationale", "run_id",
]  # fmt: skip

# (part_id, action_type, quantity, status, rationale) historical planner decisions.
HISTORY_TEMPLATES: list[tuple[str, str, int | None, str, str]] = [
    ("P-PSU-3000AC", "PULL_IN_PO", 40, "APPROVED", "Two-week supplier slip; pulled in next lot."),
    ("P-DDR5-16G", "ESCALATE_SUPPLIER", None, "APPROVED", "Allocation cut; escalated to supplier."),
    ("P-FAN-80", "EXPEDITE", 100, "EDITED", "Expedite cut to shortfall only."),
    ("P-CPU-8C", "MONITOR", None, "APPROVED", "Small gap, recovers next week."),
    ("P-RTM-224G", "PUSH_OUT_PO", 200, "REJECTED", "NPI ramp risk; kept original dates."),
    ("P-CAGE-QSFPDD", "CANCEL_PO", 300, "APPROVED", "S4 forecast cut; cancelled last lot."),
    ("P-SSD-240", "REVIEW_QUALITY_HOLD", None, "APPROVED", "Hold released after inspection."),
    ("P-CPLD-S4", "MONITOR", None, "APPROVED", "Watching S4 tail before any last-time buy."),
    ("P-HSINK-L", "EXPEDITE", 150, "REJECTED", "Air freight not worth a two-day gap."),
    ("P-CLK-J1", "PULL_IN_PO", 250, "APPROVED", "Quarter-end lift on L4 used the buffer."),
]  # fmt: skip

PLANNERS = ["planner-a", "planner-b", "planner-c"]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)
    return conn


def insert_decision(
    db_path: Path,
    snapshot_id: str,
    part_id: str,
    action_type: str,
    quantity: int | None,
    status: str,
    weeks_ago: int,
    rationale: str = "",
    decided_by: str = "planner-a",
    exception_id: str | None = None,
    rec_id: str | None = None,
) -> str:
    """Insert one decision dated `weeks_ago` weeks before the as-of date; returns the rec_id."""
    conn = connect(db_path)
    try:
        if rec_id is None:
            (n,) = conn.execute(
                "SELECT COUNT(*) FROM decision_log WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            rec_id = f"REC-H{n + 1:03d}"
        decided_at = datetime.combine(AS_OF, time(14, 0)) - timedelta(weeks=weeks_ago)
        row = (
            rec_id, snapshot_id, exception_id or f"EXC-{rec_id[4:]}", part_id, action_type,
            quantity, status, decided_by, decided_at.isoformat(), rationale, f"hist-{rec_id[4:]}",
        )  # fmt: skip
        conn.execute(f"INSERT INTO decision_log VALUES ({','.join('?' * len(COLUMNS))})", row)
        conn.commit()
    finally:
        conn.close()
    return rec_id


def seed_history(db_path: Path, snapshot_id: str, rng: np.random.Generator) -> int:
    """Replace the snapshot's history with 6-10 plausible decisions from the last 8 weeks."""
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM decision_log WHERE snapshot_id = ?", (snapshot_id,))
        conn.commit()
    finally:
        conn.close()
    n = int(rng.integers(6, 11))
    picks = rng.choice(len(HISTORY_TEMPLATES), size=n, replace=False)
    for i, idx in enumerate(sorted(int(x) for x in picks), start=1):
        part_id, action, qty, status, rationale = HISTORY_TEMPLATES[idx]
        insert_decision(
            db_path, snapshot_id, part_id, action, qty, status,
            weeks_ago=int(rng.integers(1, 9)), rationale=rationale,
            decided_by=PLANNERS[int(rng.integers(0, len(PLANNERS)))], rec_id=f"REC-H{i:03d}",
        )  # fmt: skip
    return n


def copy_history(db_path: Path, src_snapshot: str, dst_snapshot: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM decision_log WHERE snapshot_id = ?", (dst_snapshot,))
        conn.execute(
            "INSERT INTO decision_log SELECT rec_id, ?, exception_id, part_id, action_type, "
            "quantity, status, decided_by, decided_at, rationale, run_id "
            "FROM decision_log WHERE snapshot_id = ?",
            (dst_snapshot, src_snapshot),
        )
        conn.commit()
    finally:
        conn.close()


def list_decisions(db_path: Path, snapshot_id: str) -> list[dict[str, object]]:
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "SELECT * FROM decision_log WHERE snapshot_id = ? ORDER BY rec_id", (snapshot_id,)
        )
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        conn.close()
