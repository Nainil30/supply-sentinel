"""Nodes that need no LLM: load_snapshot, ingest_documents, detect_exceptions, prioritize."""

import os
from datetime import date
from typing import Any

from supply_sentinel.agent.models import ExceptionCard
from supply_sentinel.agent.nodes.extract import extract_mismatches
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists import eo, ltb, shortage
from supply_sentinel.agent.state import AgentState
from supply_sentinel.engine import rules_baseline
from supply_sentinel.engine.models import SecurityFlag, Severity
from supply_sentinel.mcp_server import untrusted

SEVERITY_RANK = {Severity.P1: 0, Severity.P2: 1, Severity.P3: 2}


def today() -> date:
    """The run date. SENTINEL_TODAY pins it so replayed runs do not drift."""
    override = os.environ.get("SENTINEL_TODAY")
    return date.fromisoformat(override) if override else date.today()


def load_snapshot(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    age = (today() - ctx.snap.as_of).days
    stale = age > int(ctx.settings["staleness_days"])
    ctx.ledger.log("load_snapshot", snapshot_id=ctx.snap.snapshot_id, age_days=age, stale=stale)
    return {
        "run_id": ctx.run_id,
        "snapshot_id": ctx.snap.snapshot_id,
        "as_of_date": ctx.snap.as_of.isoformat(),
        "profile": ctx.profile,
        "stale": stale,
    }


def ingest_documents(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    """Scan supplier notes and EOL notice text for injection patterns, then run the extractor.

    A flag's `field` starts with the part id so later nodes can attach it to the right exception.
    """
    flags: list[dict[str, Any]] = []
    parts = {c["exception"]["part_id"] for c in state.get("cards", [])}
    for part_id in sorted(parts):
        rows = ctx.snap.query(
            "SELECT 'supplier_commits.note[wk' || week_idx || ']', note FROM supplier_commits "
            "WHERE part_id = ? AND note IS NOT NULL AND note != ''",
            [part_id],
        ) + ctx.snap.query(
            "SELECT 'eol_notices.raw_text', raw_text FROM eol_notices WHERE part_id = ?",
            [part_id],
        )
        for where, text in rows:
            hits = untrusted.scan(str(text))
            if hits:
                flags.append(
                    SecurityFlag(
                        kind="PROMPT_INJECTION_SUSPECTED",
                        field=f"{part_id}:{where}",
                        detail=",".join(hits),
                    ).model_dump()
                )
    ctx.ledger.log("ingest_documents", parts=len(parts), flags=len(flags))
    issues = extract_mismatches(ctx, state.get("cards", []))
    return {"security_flags": flags, "data_issues": issues}


def detect_exceptions(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    """Engine exceptions with the rules-baseline recommendation attached (no LLM)."""
    cards: list[dict[str, Any]] = []
    for i, exc in enumerate(ctx.snap.exceptions, start=1):
        inp = ctx.snap.parts[exc.part_id]
        baseline = rules_baseline.recommend(inp, exc, ctx.snap.cfg, seq=i)
        platforms = [wu.platform_id for wu in inp.where_used]
        card = ExceptionCard(exception=exc, baseline=baseline, platforms=platforms)
        cards.append(card.model_dump(mode="json"))
    ctx.ledger.log("detect_exceptions", count=len(cards))
    return {"cards": cards}


def _impact(card: dict[str, Any]) -> float:
    e = card["exception"]
    return float(e.get("revenue_at_risk_usd") or e.get("excess_usd") or e.get("obsolete_usd") or 0)


def prioritize(ctx: RunContext, state: AgentState) -> dict[str, Any]:
    """Sort by severity then impact, cap the run, and route by exception type."""
    only = ctx.settings.get("only_parts")  # eval harness: investigate just the scenario's part
    cards = sorted(
        [c for c in state.get("cards", []) if not only or c["exception"]["part_id"] in only],
        key=lambda c: (SEVERITY_RANK[Severity(c["exception"]["severity"])], -_impact(c)),
    )[: int(ctx.settings["max_exceptions_per_run"])]
    handled = (*shortage.HANDLES, *ltb.HANDLES, *eo.HANDLES)
    queue = [c for c in cards if c["exception"]["type"] in handled]
    deferred = [c for c in cards if c["exception"]["type"] not in handled]
    ctx.ledger.log("prioritize", queued=len(queue), deferred=len(deferred))
    return {"queue": queue, "deferred": deferred}
