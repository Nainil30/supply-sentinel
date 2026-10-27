"""Everything the Planner Console reads or writes (spec 07).

Pages call this module and nothing else. It goes through the engine tools (the same functions
the MCP server exposes), the agent runner, the decision store and the results folder. No SQL and
no arithmetic beyond counting and summing engine outputs lives in a page.
"""

import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from supply_sentinel.agent import runner
from supply_sentinel.agent.models import Decision
from supply_sentinel.agent.nodes.intake import today
from supply_sentinel.agent.policy import Policy, action_value_usd
from supply_sentinel.agent.runtime import load_settings
from supply_sentinel.agent.validate import check_sanity
from supply_sentinel.engine.models import ActionType, ExceptionType, Recommendation
from supply_sentinel.evals import report
from supply_sentinel.mcp_server import schemas as s
from supply_sentinel.mcp_server import tools
from supply_sentinel.mcp_server.context import Snapshot
from supply_sentinel.observability import tracing

DEFAULT_STATE_DIR = Path("data/state")
DEFAULT_SNAPSHOT = Path("data/snapshots/base.duckdb")
PHOENIX_UI = "http://localhost:6006"
LTB_ACTIONS = {ActionType.LTB_BUY.value, ActionType.BRIDGE_BUY.value}
NEEDS_QTY = {"PLACE_PO", "EXPEDITE", "PULL_IN_PO", "LTB_BUY", "BRIDGE_BUY"}


class ServiceError(ValueError):
    """A refused request. The message is shown to the planner as is."""


@dataclass
class RunInfo:
    run_id: str
    profile: str
    waiting: bool  # paused at the approval gate
    finished: bool
    pending_count: int
    trace_id: str | None = None


@dataclass
class Overview:
    snapshot_id: str
    as_of: str
    age_days: int
    stale: bool
    profile: str
    by_type: dict[str, int]
    revenue_at_risk_usd: float
    pending_ltb_spend_usd: float
    eo_exposure_usd: float
    pending_approvals: int
    run_id: str | None


@dataclass
class ExceptionRow:
    exception_id: str
    type: str
    severity: str
    part_id: str
    platforms: list[str]
    weeks_to_impact: int | None
    impact_usd: float | None
    baseline_action: str
    agent_action: str
    status: str


@dataclass
class Evidence:
    position: s.PartPosition
    pos: s.OpenPos
    commits: s.SupplierCommits
    where_used: s.WhereUsed
    history: s.DecisionHistory


@dataclass
class Checked:
    """Result of re-validating an edit before it is staged."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Services:
    def __init__(
        self,
        state_dir: Path = DEFAULT_STATE_DIR,
        snapshot_path: Path = DEFAULT_SNAPSHOT,
        profile: str = "replay",
        cassette: str | None = None,
        results_dir: Path = Path("evals/results"),
        config_dir: Path = Path("config"),
        hosted: bool = False,
    ) -> None:
        self.state_dir = state_dir
        self.snapshot_path = snapshot_path
        self.profile = profile
        self.cassette = cassette
        self.results_dir = results_dir
        self.config_dir = config_dir
        self.hosted = hosted  # hosted demo: live profiles cannot be started
        self.policy = Policy.load(config_dir)
        self.settings = load_settings(config_dir)
        self._snap: Snapshot | None = None

    @classmethod
    def from_env(cls) -> "Services":
        """`sentinel ui` passes its options as environment variables (Streamlit reruns scripts)."""
        env = os.environ
        return cls(
            state_dir=Path(env.get("SENTINEL_STATE_DIR", str(DEFAULT_STATE_DIR))),
            snapshot_path=Path(env.get("SENTINEL_DB", str(DEFAULT_SNAPSHOT))),
            profile=env.get("SENTINEL_UI_PROFILE", "replay"),
            cassette=env.get("SENTINEL_UI_CASSETTE") or None,
            hosted=env.get("SENTINEL_HOSTED") == "1",
        )

    @property
    def snap(self) -> Snapshot:
        if self._snap is None:
            self._snap = Snapshot(self.snapshot_path, self.state_dir, self.config_dir)
        return self._snap

    # ------------------------------------------------------------------ runs

    def run_ids(self) -> list[str]:
        d = self.state_dir / "runs"
        if not d.exists():
            return []
        return sorted((p.name for p in d.iterdir() if (p / "meta.json").exists()), reverse=True)

    def current_run_id(self) -> str | None:
        try:
            rid = runner.latest_run(self.state_dir)
        except FileNotFoundError:
            return None
        return rid if (self.state_dir / "runs" / rid / "meta.json").exists() else None

    def run_info(self, run_id: str) -> RunInfo:
        meta = runner.read_meta(self.state_dir, run_id)
        h = runner.open_existing(self.state_dir, run_id)
        try:
            state = h.graph.get_state(h.config)
            pend = runner.pending(h)
            started = [e for e in h.ctx.ledger.read() if e["event"] == "run_start"]
            return RunInfo(
                run_id=run_id,
                profile=meta["profile"],
                waiting=bool(state.next),
                finished=bool(state.values) and not state.next,
                pending_count=len(pend),
                trace_id=started[0].get("trace_id") if started else None,
            )
        finally:
            h.close()

    def start_run(self, on_node: Any = None) -> str:
        if self.hosted and self.profile != "replay":
            raise ServiceError("The hosted demo only runs the replay profile.")
        run_id, _ = runner.start(
            self.snapshot_path, self.profile, self.state_dir, cassette=self.cassette,
            on_node=on_node,
        )  # fmt: skip
        return run_id

    def recommendations(self, run_id: str | None) -> list[Recommendation]:
        if run_id is None:
            return []
        h = runner.open_existing(self.state_dir, run_id)
        try:
            vals = h.graph.get_state(h.config).values
            return [Recommendation(**r) for r in vals.get("recommendations", [])]
        finally:
            h.close()

    def pending(self, run_id: str | None) -> list[dict[str, Any]]:
        if run_id is None:
            return []
        h = runner.open_existing(self.state_dir, run_id)
        try:
            return runner.pending(h)
        finally:
            h.close()

    def staged(self, run_id: str) -> dict[str, Decision]:
        return runner.load_decisions(self.state_dir, run_id)

    def ledger(self, run_id: str) -> list[dict[str, Any]]:
        path = runner.run_dir(self.state_dir, run_id) / "ledger.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]

    def tool_log(self, run_id: str, exception_id: str) -> dict[str, Any]:
        """tool_call_id -> full tool output for one exception (from the ledger)."""
        return {
            e["id"]: e["output"]
            for e in self.ledger(run_id)
            if e["event"] == "tool_call" and e.get("exception_id") == exception_id and "output" in e
        }

    def spans(self, run_id: str, exception_id: str | None = None) -> list[dict[str, Any]]:
        rows = tracing.read_spans(runner.run_dir(self.state_dir, run_id) / "spans.jsonl")
        if exception_id is None:
            return rows
        keep = {
            r["span_id"]
            for r in rows
            if r["attributes"].get("sentinel.exception_id") == exception_id
        }
        return [r for r in rows if r["span_id"] in keep]

    def trace_link(self, run_id: str) -> str | None:
        """Phoenix URL when the run exported there, else None (the spans file is always there)."""
        if os.environ.get("SENTINEL_OTEL_EXPORTER", self.settings["otel"]["exporter"]) != "phoenix":
            return None
        info = self.run_info(run_id)
        return None if info.trace_id is None else f"{PHOENIX_UI}/projects"

    # ------------------------------------------------------------------ decisions

    def _rec_status(self, rec_id: str, run_id: str | None) -> str:
        conn = sqlite3.connect(self.snap.state_db)
        try:
            row = conn.execute(
                "SELECT status FROM decision_log WHERE snapshot_id = ? AND rec_id = ?",
                (self.snap.snapshot_id, rec_id),
            ).fetchone()
        finally:
            conn.close()
        staged = self.staged(run_id).get(rec_id) if run_id else None
        if row is not None and row[0] != "PENDING":
            return str(row[0])
        if staged is not None:
            return f"STAGED {staged.decision}"
        return "AWAITING APPROVAL" if row is None else "PENDING"

    def decision_status(self, run_id: str | None) -> dict[str, str]:
        if run_id is None:
            return {}
        return {r.rec_id: self._rec_status(r.rec_id, run_id) for r in self.recommendations(run_id)}

    def spend_usd(self, rec: Recommendation) -> float | None:
        """Quantity x unit cost, with the same harness function the validator uses."""
        return action_value_usd(rec.primary_action, {}, self.snap.part(rec.part_id).unit_cost_usd)

    def needs_second_approver(self, rec: Recommendation) -> bool:
        value = self.spend_usd(rec)
        return value is not None and value > self.policy.second_approver_over_usd

    def check_edit(self, rec: Recommendation, run_id: str, quantity: float) -> Checked:
        """Re-validate an edited quantity before it can be staged."""
        from supply_sentinel.agent.models import RecommendationDraft

        out = Checked()
        action = rec.primary_action
        if action.quantity is None:
            out.errors.append(f"{action.action_type.value} has no quantity to edit.")
            return out
        if quantity <= 0 and action.action_type.value in NEEDS_QTY:
            out.errors.append("Quantity must be greater than 0.")
            return out
        card = self._card(rec.exception_id)
        if card is None:
            return out
        fields = set(RecommendationDraft.model_fields)
        draft = RecommendationDraft(**rec.model_dump(include=fields))
        draft.primary_action = draft.primary_action.model_copy(update={"quantity": quantity})
        out.warnings = [
            i.message for i in check_sanity(draft, card, self.tool_log(run_id, rec.exception_id))
        ]
        return out

    def _card(self, exception_id: str) -> Any:
        from supply_sentinel.agent.models import ExceptionCard
        from supply_sentinel.engine import rules_baseline

        for i, exc in enumerate(self.snap.exceptions, start=1):
            if exc.exception_id == exception_id:
                inp = self.snap.parts[exc.part_id]
                return ExceptionCard(
                    exception=exc,
                    baseline=rules_baseline.recommend(inp, exc, self.snap.cfg, seq=i),
                    platforms=[w.platform_id for w in inp.where_used],
                )
        return None

    def stage(
        self,
        run_id: str,
        rec_id: str,
        decision: str,
        reason: str = "",
        quantity: float | None = None,
        second_approver: bool = False,
    ) -> None:
        """Stage a human decision (final only after `resume`). Raises ServiceError if refused."""
        if decision not in ("APPROVED", "EDITED", "REJECTED"):
            raise ServiceError(f"Unknown decision '{decision}'.")
        pending = {p["rec_id"]: p for p in self.pending(run_id)}
        if rec_id not in pending:
            raise ServiceError(f"{rec_id} is not waiting for approval in run {run_id}.")
        rec = next(r for r in self.recommendations(run_id) if r.rec_id == rec_id)
        reason = reason.strip()
        if decision == "REJECTED" and not reason:
            raise ServiceError("A rejection needs a reason.")
        if decision != "REJECTED":
            if rec.autonomy_tier == 2 and not reason:
                raise ServiceError("Tier 2 approvals need a reason.")
            if self.needs_second_approver(rec) and not second_approver:
                limit = self.policy.second_approver_over_usd
                raise ServiceError(f"Spend is over ${limit:,.0f}: a second approver must confirm.")
        if decision == "EDITED":
            if quantity is None:
                raise ServiceError("Approve with edit needs a quantity.")
            if not reason:
                raise ServiceError("Approve with edit needs a reason.")
            check = self.check_edit(rec, run_id, quantity)
            if check.errors:
                raise ServiceError(" ".join(check.errors))
        runner.save_decision(
            self.state_dir, run_id,
            Decision(rec_id=rec_id, decision=decision, reason=reason, quantity=quantity,  # type: ignore[arg-type]
                     decided_by=runner.decider()),
        )  # fmt: skip

    def resume(self, run_id: str) -> list[dict[str, Any]]:
        final = runner.resume(self.state_dir, run_id)
        return list(final.get("outcomes", []))

    def decisions_log(
        self, status: str | None = None, part: str | None = None
    ) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.snap.state_db)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT rec_id, snapshot_id, exception_id, part_id, action_type, quantity, status, "
                "decided_by, decided_at, rationale, run_id FROM decision_log "
                "ORDER BY COALESCE(decided_at, '') DESC, rec_id"
            ).fetchall()
        finally:
            conn.close()
        out = [dict(r) for r in rows]
        if status:
            out = [r for r in out if r["status"] == status]
        if part:
            out = [r for r in out if part.lower() in (r["part_id"] or "").lower()]
        return out

    # ------------------------------------------------------------------ exceptions

    def _impact(self, row: s.ExceptionSummary) -> float | None:
        return row.revenue_at_risk_usd if row.revenue_at_risk_usd is not None else row.exposure_usd

    def exceptions(self, run_id: str | None = None) -> list[ExceptionRow]:
        listing = tools.list_exceptions(self.snap)
        recs = {r.exception_id: r for r in self.recommendations(run_id)}
        status = self.decision_status(run_id)
        rows = []
        for e in listing.exceptions:
            rec = recs.get(e.exception_id)
            rows.append(
                ExceptionRow(
                    exception_id=e.exception_id, type=e.type, severity=e.severity,
                    part_id=e.part_id,
                    platforms=e.platforms, weeks_to_impact=e.weeks_to_impact,
                    impact_usd=self._impact(e), baseline_action=e.baseline_action,
                    agent_action=rec.primary_action.action_type.value if rec else "-",
                    status=status.get(rec.rec_id, "-") if rec else "NO RUN",
                )
            )  # fmt: skip
        return rows

    def evidence(self, part_id: str) -> Evidence:
        snap = self.snap
        return Evidence(
            position=tools.get_part_position(snap, part_id),
            pos=tools.get_open_pos(snap, part_id),
            commits=tools.get_supplier_commits(snap, part_id),
            where_used=tools.get_where_used(snap, part_id),
            history=tools.get_decision_history(snap, part_id),
        )

    def overview(self) -> Overview:
        snap = self.snap
        age = (today() - snap.as_of).days
        run_id = self.current_run_id()
        rows = self.exceptions()
        pending_recs = [
            r for r in self.recommendations(run_id)
            if self._rec_status(r.rec_id, run_id) in ("AWAITING APPROVAL", "PENDING")
            or self._rec_status(r.rec_id, run_id).startswith("STAGED")
        ]  # fmt: skip
        ltb = [
            self.spend_usd(r) or 0.0
            for r in pending_recs
            if r.primary_action.action_type.value in LTB_ACTIONS
        ]
        by_type = Counter(r.type for r in rows)
        shortage_rev = [
            e.revenue_at_risk_usd or 0.0
            for e in tools.list_exceptions(snap).exceptions
            if e.type == ExceptionType.SHORTAGE.value
        ]
        eo = [
            e.exposure_usd or 0.0
            for e in tools.list_exceptions(snap).exceptions
            if e.type in (ExceptionType.EXCESS.value, ExceptionType.OBSOLESCENCE_RISK.value)
        ]
        return Overview(
            snapshot_id=snap.snapshot_id, as_of=snap.as_of.isoformat(), age_days=age,
            stale=age > int(self.settings["staleness_days"]), profile=self.profile,
            by_type=dict(by_type), revenue_at_risk_usd=sum(shortage_rev),
            pending_ltb_spend_usd=sum(ltb), eo_exposure_usd=sum(eo),
            pending_approvals=len(pending_recs), run_id=run_id,
        )  # fmt: skip

    # ------------------------------------------------------------------ what-if

    def what_if(
        self,
        part_id: str,
        po_slip: dict[str, int] | None = None,
        demand_change_pct: dict[str, float] | None = None,
        expedite_qty: int | None = None,
        expedite_to_week: int | None = None,
        release_hold_units: int = 0,
    ) -> s.WhatIfOut:
        try:
            return tools.simulate_scenario(
                self.snap, part_id, po_slip=po_slip or None,
                demand_change_pct=demand_change_pct or None, expedite_qty=expedite_qty,
                expedite_to_week=expedite_to_week, release_hold_units=release_hold_units,
            )  # fmt: skip
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    def baseline_pab(self, part_id: str) -> list[float]:
        return [w.pab for w in tools.get_part_position(self.snap, part_id).weeks]

    def part_ids(self) -> list[str]:
        return sorted(self.snap.parts)

    # ------------------------------------------------------------------ evals

    def eval_files(self) -> list[Path]:
        return [p for p, _ in reversed(report.load_results(self.results_dir))]

    def eval_doc(self, path: Path) -> dict[str, Any]:
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def eval_summary(self, doc: dict[str, Any]) -> dict[str, Any]:
        return report.summarize(doc)

    def eval_by_category(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for cat in report.CATEGORIES:
            rows = [r for r in doc["results"] if r["category"] == cat]
            if rows:
                out.append({"category": cat, "passed": sum(r["passed"] for r in rows),
                            "total": len(rows)})  # fmt: skip
        return out

    def eval_failures(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"scenario": r["scenario_id"], "run": r["run_idx"], "error": r.get("error") or "",
             "failed": ", ".join(
                 n for n, x in r["scores"].items() if x["hard"] and x["passed"] is False
             ),
             "ledger": r.get("ledger") or ""}
            for r in doc["results"] if not r["passed"]
        ]  # fmt: skip
