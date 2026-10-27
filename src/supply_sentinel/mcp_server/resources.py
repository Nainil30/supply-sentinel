"""MCP resources (spec 03 section 4): read-only reference material."""

import json
from datetime import date
from typing import Any

from supply_sentinel.mcp_server.context import Snapshot, ToolInputError

GLOSSARY = """# Supply chain glossary (Orbis Networks, fictional)

| Term | Meaning |
|---|---|
| LTB | Last-time buy: a final order placed before a supplier stops making a part. |
| EOSL | End of service life: the date Orbis stops supporting a platform in the field. |
| PAB | Projected available balance: on hand plus receipts minus gross requirements, by week. |
| NRND | Not recommended for new designs: the supplier still ships but plans to retire the part. |
| E&O | Excess and obsolete inventory: stock beyond need, or stock with no future use. |
| OTD | On-time delivery rate of a supplier. |
| MOQ | Minimum order quantity a supplier accepts. |
| GSM | Global supply manager: owns the supplier relationship. |
| GCM | Global commodity manager: owns the commodity strategy and pricing. |
"""


def _md_table(headers: list[str], rows: list[tuple[Any, ...]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def platforms(snap: Snapshot) -> str:
    cols = [
        "platform_id",
        "description",
        "lifecycle_phase",
        "launch_date",
        "last_ship_date",
        "eosl_date",
        "asp_usd",
    ]
    rows = snap.query(f"SELECT {', '.join(cols)} FROM platforms ORDER BY platform_id")
    as_json = [dict(zip(cols, r, strict=True)) for r in rows]
    return _md_table(cols, rows) + "\n\n```json\n" + json.dumps(as_json, default=str) + "\n```"


def platform_bom(snap: Snapshot, platform_id: str) -> str:
    rows = snap.query(
        "SELECT b.part_id, p.description, b.qty_per, s.name, p.lead_time_weeks "
        "FROM bom b JOIN parts p USING (part_id) JOIN suppliers s USING (supplier_id) "
        "WHERE b.platform_id = ? AND b.effective_from <= ? "
        "AND (b.effective_to IS NULL OR b.effective_to >= ?) ORDER BY b.part_id",
        [platform_id, snap.as_of, snap.as_of],
    )
    if not rows:
        raise ToolInputError(
            f"No BOM for platform '{platform_id}'. Read supply://catalog/platforms for valid ids."
        )
    return _md_table(["part_id", "description", "qty_per", "supplier", "lead_time_weeks"], rows)


def autonomy_policy(snap: Snapshot) -> str:
    return (snap.config_dir / "autonomy.yaml").read_text(encoding="utf-8")


def snapshot_info(snap: Snapshot) -> str:
    tables = ["parts", "platforms", "bom", "purchase_orders", "supplier_commits", "eol_notices"]
    counts = {t: snap.query(f"SELECT COUNT(*) FROM {t}")[0][0] for t in tables}
    return json.dumps(
        {
            "snapshot_id": snap.snapshot_id,
            "as_of_date": snap.as_of.isoformat(),
            "staleness_days": (date.today() - snap.as_of).days,
            "row_counts": counts,
            "db_file": snap.db_path.name,
        },
        indent=2,
    )


def glossary() -> str:
    return GLOSSARY
