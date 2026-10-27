"""Start, inspect and resume runs. This is what the CLI (and later the UI) calls."""

import json
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from supply_sentinel.agent.graph import build_graph
from supply_sentinel.agent.mcp_bridge import McpBridge
from supply_sentinel.agent.models import Decision
from supply_sentinel.agent.policy import Policy
from supply_sentinel.agent.runtime import RunContext, load_settings
from supply_sentinel.agent.skills_loader import SkillLibrary
from supply_sentinel.llm.factory import build_router
from supply_sentinel.llm.types import ChatClient
from supply_sentinel.mcp_server.context import Snapshot
from supply_sentinel.observability import tracing
from supply_sentinel.observability.ledger import Ledger

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_run_id() -> str:
    """Sortable id: 10 base-32 chars of milliseconds, then 6 random ones (ULID-like, shorter)."""
    ms = int(time.time() * 1000)
    head = "".join(_B32[(ms >> (5 * i)) & 31] for i in range(9, -1, -1))
    return head + "".join(secrets.choice(_B32) for _ in range(6))


@dataclass
class RunHandle:
    ctx: RunContext
    graph: Any
    conn: sqlite3.Connection
    config: dict[str, Any]

    def close(self) -> None:
        self.conn.close()


def run_dir(state_dir: Path, run_id: str) -> Path:
    return state_dir / "runs" / run_id


def open_run(
    run_id: str,
    snapshot_path: Path,
    profile: str,
    state_dir: Path,
    *,
    cassette: str | None = None,
    record: bool = False,
    max_exceptions: int | None = None,
    config_dir: Path = Path("config"),
    skills_dir: Path = Path("skills"),
    client: ChatClient | None = None,
    settings_override: dict[str, Any] | None = None,
) -> RunHandle:
    """Build the context and compile the graph. `client` injects a scripted model for tests.

    `settings_override` is how the eval harness sets `eval_mode` and `only_parts`.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(run_dir(state_dir, run_id) / "ledger.jsonl", run_id)
    cassette = cassette or snapshot_path.stem
    router, model_profile = build_router(
        profile, cassette, ledger, record=record, config_dir=config_dir, client=client
    )
    settings = load_settings(config_dir)
    if max_exceptions is not None:
        settings["max_exceptions_per_run"] = max_exceptions
    settings.update(settings_override or {})
    tracing.init_tracing(settings)
    wall = float(
        model_profile.limits.get(
            "specialist_wall_seconds", settings["specialist_limits"]["wall_seconds"]
        )
    )
    snap = Snapshot(snapshot_path, state_dir=state_dir, config_dir=config_dir)
    ctx = RunContext(
        run_id=run_id,
        snap=snap,
        bridge=McpBridge(snap),
        router=router,
        skills=SkillLibrary(skills_dir),
        policy=Policy.load(config_dir),
        settings=settings,
        ledger=ledger,
        state_dir=state_dir,
        profile=profile,
        wall_seconds=wall,
    )
    conn = sqlite3.connect(state_dir / "checkpoints.sqlite", check_same_thread=False)
    graph = build_graph(ctx, SqliteSaver(conn))
    parallel = int(model_profile.limits.get("concurrency", settings["max_parallel_specialists"]))
    config = {
        "configurable": {"thread_id": run_id},
        "max_concurrency": min(parallel, int(settings["max_parallel_specialists"])),
        "recursion_limit": 60,
    }
    return RunHandle(ctx, graph, conn, config)


def write_meta(state_dir: Path, run_id: str, meta: dict[str, Any]) -> None:
    d = run_dir(state_dir, run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (state_dir / "latest_run").write_text(run_id, encoding="utf-8")


def read_meta(state_dir: Path, run_id: str) -> dict[str, Any]:
    path = run_dir(state_dir, run_id) / "meta.json"
    if not path.exists():
        raise FileNotFoundError(f"No run '{run_id}' under {state_dir / 'runs'}.")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def latest_run(state_dir: Path) -> str:
    p = state_dir / "latest_run"
    if not p.exists():
        raise FileNotFoundError("No runs yet. Start one with `sentinel run`.")
    return p.read_text(encoding="utf-8").strip()


def start(
    snapshot_path: Path,
    profile: str,
    state_dir: Path,
    *,
    cassette: str | None = None,
    record: bool = False,
    max_exceptions: int | None = None,
    config_dir: Path = Path("config"),
    skills_dir: Path = Path("skills"),
    client: ChatClient | None = None,
    on_node: Callable[[str], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run until the approval gate (or the end). Returns (run_id, pending recommendations).

    `on_node` is called with each graph node's name as it finishes (the UI's progress bar).
    """
    run_id = new_run_id()
    meta = {
        "snapshot_path": str(snapshot_path), "profile": profile, "cassette": cassette,
        "max_exceptions": max_exceptions, "config_dir": str(config_dir),
        "skills_dir": str(skills_dir),
    }  # fmt: skip
    write_meta(state_dir, run_id, meta)
    h = open_run(run_id, snapshot_path, profile, state_dir, cassette=cassette, record=record,
                 max_exceptions=max_exceptions, config_dir=config_dir, skills_dir=skills_dir,
                 client=client)  # fmt: skip
    try:
        with tracing.run_span(run_id, run_dir(state_dir, run_id), "run"):
            trace_id = tracing.current_trace_id()
            h.ctx.ledger.log(
                "run_start", profile=profile, snapshot=str(snapshot_path), trace_id=trace_id,
                max_exceptions=max_exceptions, replay=profile == "replay",
            )  # fmt: skip
            _drive(h, {"run_id": run_id}, on_node)
        tracing.flush()
        return run_id, pending(h)
    finally:
        h.close()


def _drive(h: RunHandle, first: Any, on_node: Callable[[str], None] | None) -> None:
    """graph.invoke, or graph.stream when someone wants per-node progress."""
    if on_node is None:
        h.graph.invoke(first, h.config)
        return
    for update in h.graph.stream(first, h.config, stream_mode="updates"):
        for node in update:
            if not node.startswith("__"):
                on_node(node)


def pending(h: RunHandle) -> list[dict[str, Any]]:
    snap = h.graph.get_state(h.config)
    for task in snap.tasks:
        for itr in task.interrupts:
            return list(itr.value["pending"])
    return []


def open_existing(state_dir: Path, run_id: str, client: ChatClient | None = None) -> RunHandle:
    meta = read_meta(state_dir, run_id)
    return open_run(
        run_id, Path(meta["snapshot_path"]), meta["profile"], state_dir, cassette=meta["cassette"],
        max_exceptions=meta["max_exceptions"], config_dir=Path(meta["config_dir"]),
        skills_dir=Path(meta["skills_dir"]), client=client,
    )  # fmt: skip


def decisions_path(state_dir: Path, run_id: str) -> Path:
    return run_dir(state_dir, run_id) / "decisions.json"


def load_decisions(state_dir: Path, run_id: str) -> dict[str, Decision]:
    p = decisions_path(state_dir, run_id)
    if not p.exists():
        return {}
    return {d["rec_id"]: Decision(**d) for d in json.loads(p.read_text(encoding="utf-8"))}


def save_decision(state_dir: Path, run_id: str, decision: Decision) -> None:
    """Stage a human decision. Nothing is final until `resume`."""
    current = load_decisions(state_dir, run_id)
    current[decision.rec_id] = decision
    decisions_path(state_dir, run_id).write_text(
        json.dumps([d.model_dump() for d in current.values()], indent=2), encoding="utf-8"
    )


def decider() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "planner"


def resume(state_dir: Path, run_id: str, client: ChatClient | None = None) -> dict[str, Any]:
    """Feed the staged decisions into the paused graph; finalize and write the brief."""
    h = open_existing(state_dir, run_id, client)
    try:
        if not h.graph.get_state(h.config).next:
            return dict(h.graph.get_state(h.config).values)  # already finished
        staged = load_decisions(state_dir, run_id)
        answer = [
            staged.get(p["rec_id"], Decision(rec_id=p["rec_id"], decision="DEFERRED")).model_dump()
            for p in pending(h)
        ]
        with tracing.run_span(run_id, run_dir(state_dir, run_id), "resume"):
            h.ctx.ledger.log(
                "run_resume", trace_id=tracing.current_trace_id(), decisions=len(answer)
            )
            h.graph.invoke(Command(resume=answer), h.config)
        tracing.flush()
        return dict(h.graph.get_state(h.config).values)
    finally:
        h.close()
