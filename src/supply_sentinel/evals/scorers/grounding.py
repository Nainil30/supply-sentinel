"""grounding: independent re-check from the run ledger (spec 05 section 4).

This does NOT reuse agent/validate.py. It re-executes every tool call the ledger recorded for the
exception on the scenario snapshot, collects every number in those outputs (plus the engine's own
exception record), and asks: which numeric claims in the recommendation appear in that set?
A claim is a number in an action, an evidence value, the message or the explanation.
Only AGENT and RECONCILED recommendations are checked; the rules baseline has no claims of its own.
"""

import re
from pathlib import Path
from typing import Any

from supply_sentinel.agent.mcp_bridge import McpBridge
from supply_sentinel.engine.models import ExceptionRecord, Recommendation, RecSource
from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext, numbers_in, rec_text
from supply_sentinel.mcp_server.context import Snapshot

THRESHOLD = 0.95
TRIVIAL = {0.0, 1.0}  # "1 week", "0 units" carry no information worth tracing
JSON_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def numbers_from(value: Any) -> set[float]:
    """Every number inside a JSON-like value, including numbers written inside strings."""
    out: set[float] = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, int | float):
        out.add(float(value))
    elif isinstance(value, str):
        out.update(numbers_in(value))
        out.update(float(x) for x in JSON_NUM.findall(value))
    elif isinstance(value, dict):
        for k, v in value.items():
            out |= numbers_from(k)
            out |= numbers_from(v)
    elif isinstance(value, list | tuple):
        for v in value:
            out |= numbers_from(v)
    return out


def matches(claim: float, sources: set[float]) -> bool:
    tol = max(0.51, abs(claim) * 0.005)  # rounding to whole units, or 0.5%
    return any(abs(claim - s) <= tol for s in sources)


def claims(rec: Recommendation) -> list[float]:
    out: list[float] = []
    for act in (rec.primary_action, rec.secondary_action):
        if act is None:
            continue
        for v in (act.quantity, act.target_week_idx):
            if v is not None:
                out.append(float(v))
        out += [float(v) for v in (act.platform_allocations or {}).values()]
    for ev in rec.evidence:
        if isinstance(ev.value, int | float):
            out.append(float(ev.value))
    out += numbers_in(rec_text(rec))
    return [c for c in out if c not in TRIVIAL]


def replay_tool_outputs(
    snapshot_path: Path, calls: list[dict[str, Any]], config_dir: Path
) -> dict[str, list[dict[str, Any]]]:
    """exception_id -> outputs of the ledger's tool calls, re-run against the snapshot."""
    bridge = McpBridge(Snapshot(snapshot_path, config_dir=config_dir))
    out: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        if call.get("error"):
            continue
        name = str(call["tool"])
        try:
            result = bridge.call(name, dict(call.get("args") or {}), (name,))
        except Exception as exc:  # re-run must never abort scoring
            result = {"error": str(exc)}
        out.setdefault(str(call["exception_id"]), []).append(result)
    return out


def _exception_numbers(exceptions: list[ExceptionRecord], exception_id: str) -> set[float]:
    return numbers_from(
        [e.model_dump(mode="json") for e in exceptions if e.exception_id == exception_id]
    )


def score(sc: ScoreContext) -> Score:
    recs = [r for r in sc.target_recs if r.source in (RecSource.AGENT, RecSource.RECONCILED)]
    if not recs:
        return Score(name="grounding", passed=None, hard=False, detail="no agent claims")
    outputs = replay_tool_outputs(
        Path(sc.snapshot_path), sc.outcome.tool_calls, Path(sc.config_dir)
    )
    traced = total = 0
    ungrounded: list[str] = []
    for rec in recs:
        sources = _exception_numbers(sc.outcome.exceptions, rec.exception_id)
        for res in outputs.get(rec.exception_id, []):
            sources |= numbers_from(res)
        if rec.source is RecSource.RECONCILED:  # merged from several exceptions on one part
            for other in {
                e.exception_id for e in sc.outcome.exceptions if e.part_id == rec.part_id
            }:
                sources |= _exception_numbers(sc.outcome.exceptions, other)
                for res in outputs.get(other, []):
                    sources |= numbers_from(res)
        for claim in claims(rec):
            total += 1
            if matches(claim, sources):
                traced += 1
            else:
                ungrounded.append(f"{rec.rec_id}:{claim:g}")
    ratio = traced / total if total else 1.0
    detail = f"{traced}/{total} claims traced" + (
        f"; untraced {ungrounded[:6]}" if ungrounded else ""
    )
    return Score(
        name="grounding",
        passed=ratio >= THRESHOLD,
        hard=False,
        detail=detail,
        num=traced,
        den=total,
    )


def suite_ratio(scores: list[Score]) -> float | None:
    den = sum(s.den for s in scores)
    return None if den == 0 else sum(s.num for s in scores) / den
