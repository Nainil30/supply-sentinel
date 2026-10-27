"""Eval runner (spec 05 section 6).

For each (scenario, run) it builds the scenario snapshot, runs a system on it, scores the outcome
and appends the result to a JSON file that is rewritten after every scenario, so a stopped run
can be resumed with `--resume`.

Systems:
  rules  detect exceptions + rules baseline, no LLM
  agent  the LangGraph app in *eval mode*: it runs to the approval gate, we read the proposals
         from the paused graph state and never resume, so nothing is approved or recorded.

The MCP server runs in-process (agent/mcp_bridge.py), one per run and bound to that scenario's
snapshot, which gives the same isolation as a subprocess per scenario without the start-up cost.
"""

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from supply_sentinel.agent import runner as agent_runner
from supply_sentinel.agent.models import ExceptionCard
from supply_sentinel.engine import loader, rules_baseline
from supply_sentinel.engine.config import EngineConfig, load_config
from supply_sentinel.engine.exceptions import detect_exceptions
from supply_sentinel.engine.models import PartInputs, Recommendation, SecurityFlag
from supply_sentinel.evals import judge as judge_mod
from supply_sentinel.evals import scenario as scn
from supply_sentinel.evals.outcome import RunOutcome, Score, Usage
from supply_sentinel.evals.scorers import (
    action,
    detection,
    efficiency,
    grounding,
    mentions,
    quantity,
    rootcause,
    safety,
    tier,
)
from supply_sentinel.evals.scorers.base import ScoreContext
from supply_sentinel.llm.factory import build_router
from supply_sentinel.llm.router import Router
from supply_sentinel.observability.ledger import Ledger

RESULTS_DIR = Path("evals/results")
EVAL_STATE_DIR = Path("data/state/evals")
STATE_DB = Path("data/state/decisions.sqlite")
SYSTEMS = ("rules", "agent")


def load_target_part(snapshot: Path, part_id: str) -> PartInputs:
    con = loader.open_snapshot(snapshot)
    try:
        for p in loader.load_part_inputs(con):
            if p.part_id == part_id:
                return p
    finally:
        con.close()
    raise KeyError(f"{part_id} not in {snapshot}")


# ---------------------------------------------------------------- systems


def run_rules(sc: scn.Scenario, snapshot: Path, cfg: EngineConfig, run_idx: int = 0) -> RunOutcome:
    t0 = time.monotonic()
    con = loader.open_snapshot(snapshot)
    try:
        parts = {p.part_id: p for p in loader.load_part_inputs(con)}
    finally:
        con.close()
    found = detect_exceptions(list(parts.values()), cfg)
    recs = [
        rules_baseline.recommend(parts[e.part_id], e, cfg, seq=i) for i, e in enumerate(found, 1)
    ]
    return RunOutcome(
        scenario_id=sc.id, system="rules", profile="-", run_idx=run_idx, exceptions=found,
        recs=recs, usage=Usage(seconds=round(time.monotonic() - t0, 3)),
    )  # fmt: skip


def usage_from_ledger(events: list[dict[str, Any]]) -> Usage:
    llm = [e for e in events if e["event"] == "llm_call"]
    return Usage(
        llm_calls=len(llm),
        tool_calls=sum(e["event"] == "tool_call" for e in events),
        input_tokens=sum(int(e.get("input_tokens") or 0) for e in llm),
        output_tokens=sum(int(e.get("output_tokens") or 0) for e in llm),
        seconds=round(sum(float(e.get("seconds") or 0) for e in llm), 2),
    )


def run_agent(
    sc: scn.Scenario,
    snapshot: Path,
    profile: str,
    state_dir: Path,
    *,
    record: bool = False,
    run_idx: int = 0,
    config_dir: Path = Path("config"),
) -> RunOutcome:
    """Run the graph to the approval gate and capture what it proposes."""
    state_dir.mkdir(parents=True, exist_ok=True)
    if STATE_DB.exists():  # decision history for the memory scenarios, isolated per run
        shutil.copyfile(STATE_DB, state_dir / "decisions.sqlite")
    run_id = agent_runner.new_run_id()
    outcome = RunOutcome(scenario_id=sc.id, system="agent", profile=profile, run_idx=run_idx)
    outcome.ledger_path = str(agent_runner.run_dir(state_dir, run_id) / "ledger.jsonl")
    os.environ["SENTINEL_TODAY"] = scn.RUN_DATE
    h = None
    try:
        h = agent_runner.open_run(
            run_id, snapshot, profile, state_dir, cassette=sc.id, record=record,
            config_dir=config_dir,
            settings_override={"eval_mode": True, "only_parts": [sc.target.part_id]},
        )  # fmt: skip
        h.graph.invoke({"run_id": run_id}, h.config)
        values = dict(h.graph.get_state(h.config).values)
        outcome.exceptions = [ExceptionCard(**c).exception for c in values.get("cards", [])]
        outcome.recs = [Recommendation(**r) for r in values.get("recommendations", [])]
        outcome.security_flags = [SecurityFlag(**f) for f in values.get("security_flags", [])]
        outcome.run_flags = ["STALE_DATA"] if values.get("stale") else []
    except Exception as exc:  # a crash is a result, not a reason to stop the suite
        outcome.crashed = True
        outcome.error = f"{type(exc).__name__}: {exc}"
    finally:
        if h is not None:
            h.close()
    events = Ledger(Path(outcome.ledger_path)).read()
    outcome.tool_calls = [e for e in events if e["event"] == "tool_call"]
    outcome.usage = usage_from_ledger(events)
    return outcome


# ---------------------------------------------------------------- scoring

HARD = ("detection", "action", "quantity", "must_not", "safety", "tier")


def score_outcome(
    sc: scn.Scenario,
    outcome: RunOutcome,
    snapshot: Path,
    cfg: EngineConfig,
    judge_router: Router | None = None,
    config_dir: Path = Path("config"),
) -> list[Score]:
    if outcome.crashed:
        return [Score(name=n, passed=False, hard=True, detail="run crashed") for n in HARD]
    ctx = ScoreContext(
        scenario=sc, outcome=outcome, part=load_target_part(snapshot, sc.target.part_id), cfg=cfg,
        snapshot_path=str(snapshot), config_dir=str(config_dir),
    )  # fmt: skip
    return [
        detection.score(ctx),
        action.score(ctx),
        quantity.score(ctx),
        safety.score_must_not(ctx),
        safety.score_safety(ctx),
        tier.score(ctx),
        rootcause.score_root_cause(ctx),
        rootcause.score_stakeholder(ctx),
        mentions.score(ctx),
        judge_mod.score_comms(judge_router, ctx.rec),
        grounding.score(ctx) if outcome.system == "agent" else _na("grounding"),
        efficiency.score(ctx),
    ]


def _na(name: str) -> Score:
    return Score(name=name, passed=None, hard=False, detail="not applicable")


def scenario_passed(scores: list[Score], crashed: bool) -> bool:
    return not crashed and all(s.passed for s in scores if s.hard and s.passed is not None)


def result_row(sc: scn.Scenario, outcome: RunOutcome, scores: list[Score]) -> dict[str, Any]:
    rec = next((r for r in outcome.recs if r.part_id == sc.target.part_id), None)
    return {
        "scenario_id": sc.id, "category": sc.category, "difficulty": sc.difficulty, "gate": sc.gate,
        "run_idx": outcome.run_idx, "passed": scenario_passed(scores, outcome.crashed),
        "crashed": outcome.crashed, "error": outcome.error,
        "scores": {s.name: s.model_dump() for s in scores},
        "rec": None if rec is None else {
            "rec_id": rec.rec_id, "source": rec.source.value, "tier": rec.autonomy_tier,
            "primary": rec.primary_action.model_dump(mode="json"),
            "secondary": rec.secondary_action and rec.secondary_action.model_dump(mode="json"),
            "root_cause": rec.root_cause.value, "stakeholder": rec.stakeholder.value,
        },
        "usage": outcome.usage.model_dump(), "ledger": outcome.ledger_path,
    }  # fmt: skip


# ---------------------------------------------------------------- suites


def results_path(suite: str, system: str, profile: str, results_dir: Path = RESULTS_DIR) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return results_dir / f"{ts}_{suite}_{system}_{profile}.json"


def _save(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def run_suite(
    scenarios: list[scn.Scenario],
    *,
    suite: str,
    system: str,
    profile: str = "replay",
    k: int = 1,
    record: bool = False,
    judge: bool = False,
    resume: Path | None = None,
    results_dir: Path = RESULTS_DIR,
    config_dir: Path = Path("config"),
    log: Any = print,
) -> Path:
    """Run every scenario k times (once for the deterministic rules system); return the file."""
    if system not in SYSTEMS:
        raise ValueError(f"system must be one of {SYSTEMS}")
    cfg = load_config(config_dir)
    k = 1 if system == "rules" else max(1, k)
    label = profile if system == "agent" else "-"
    if resume is not None:
        doc = json.loads(resume.read_text(encoding="utf-8"))
        path = resume
    else:
        path = results_path(suite, system, label, results_dir)
        doc = {
            "meta": {"suite": suite, "system": system, "profile": label,
                     "k": k, "started": datetime.now(UTC).isoformat(), "judge": judge,
                     "run_date": scn.RUN_DATE},
            "results": [],
        }  # fmt: skip
    done = {(r["scenario_id"], r["run_idx"]) for r in doc["results"]}
    judge_router: Router | None = None
    if judge and system == "agent":
        judge_router, _ = build_router(profile, "judge", Ledger(None), record=record,
                                       config_dir=config_dir)  # fmt: skip
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    for sc in scenarios:
        snapshot = scn.build_snapshot(sc)
        for idx in range(k):
            if (sc.id, idx) in done:
                continue
            if system == "rules":
                outcome = run_rules(sc, snapshot, cfg, idx)
            else:
                where = EVAL_STATE_DIR / stamp / f"{sc.id}-{idx}"
                outcome = run_agent(sc, snapshot, profile, where,
                                    record=record, run_idx=idx, config_dir=config_dir)  # fmt: skip
            scores = score_outcome(sc, outcome, snapshot, cfg, judge_router, config_dir)
            row = result_row(sc, outcome, scores)
            doc["results"].append(row)
            _save(path, doc)
            log(f"{sc.id} run {idx}: {'PASS' if row['passed'] else 'FAIL'}"
                + (f"  ({outcome.error})" if outcome.crashed else ""))  # fmt: skip
    doc["meta"]["finished"] = datetime.now(UTC).isoformat()
    _save(path, doc)
    return path
