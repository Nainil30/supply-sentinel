"""Eval harness end to end: the 40 committed scenarios, the rules system, and the agent in eval mode."""

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from supply_sentinel.cli import app
from supply_sentinel.datagen import generator
from supply_sentinel.engine.config import load_config
from supply_sentinel.evals import oracle, report, runner
from supply_sentinel.evals import scenario as scn
from supply_sentinel.llm.types import LLMResponse, Msg, ProviderError, Role
from tests.fixtures.scripted import ScriptedClient, text, tool_call

SCENARIO_DIR = Path("evals/scenarios")
SMOKE = {"SHT-01", "SHT-05", "SHT-11", "LTB-01", "LTB-03", "EO-01", "EO-04", "NEG-01", "NEG-04",
         "ROB-01", "ROB-03", "ROB-06"}  # fmt: skip


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("evals")
    generator.build_base(seed=42, out_dir=d, state_db=d / "decisions.sqlite")
    return d


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setenv("SENTINEL_TODAY", scn.RUN_DATE)


def test_catalog_has_the_forty_scenarios_in_the_right_suites():
    all_ = scn.load_all(SCENARIO_DIR)
    assert len(all_) == 40
    counts = {
        c: sum(s.category == c for s in all_.values())
        for c in ("shortage", "ltb", "eo", "negative", "robustness")
    }
    assert counts == {"shortage": 12, "ltb": 8, "eo": 8, "negative": 5, "robustness": 7}
    assert {s.id for s in scn.select(all_, "smoke")} == SMOKE
    assert {s.id for s in scn.select(all_, "safety")} == {f"ROB-0{i}" for i in range(1, 8)}
    assert len(scn.select(all_, "full")) == 40
    for s in all_.values():
        assert s.id.startswith(
            {"shortage": "SHT", "ltb": "LTB", "eo": "EO", "negative": "NEG", "robustness": "ROB"}[
                s.category
            ]
        )
        assert s.notes, f"{s.id} needs planner rationale"
    with pytest.raises(KeyError):
        scn.select(all_, "nonsense")


def test_every_scenario_builds_and_raises_the_exception_it_names(env):
    """Acceptance: all 40 build valid snapshots, and detection matches `expect_exception`."""
    state_db = env / "decisions.sqlite"
    path = runner.run_suite(
        scn.select(scn.load_all(SCENARIO_DIR), "full"), suite="full", system="rules",
        results_dir=env / "results", snapshot_dir=env, state_db=state_db, log=lambda _m: None,
    )  # fmt: skip
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["results"]) == 40 and not any(r["crashed"] for r in doc["results"])
    wrong = {r["scenario_id"]: r["scores"]["detection"]["detail"] for r in doc["results"]
             if r["scores"]["detection"]["passed"] is not True}  # fmt: skip
    assert wrong == {}
    by_id = {r["scenario_id"]: r for r in doc["results"]}
    # The baseline is meant to pass the easy cases and fail the ones that need judgement.
    assert by_id["SHT-01"]["passed"] and by_id["EO-01"]["passed"] and by_id["NEG-01"]["passed"]
    assert (
        not by_id["SHT-05"]["passed"]
        and not by_id["SHT-11"]["passed"]
        and not by_id["ROB-01"]["passed"]
    )
    assert report.summarize(doc)["grounding"] is None  # the baseline makes no claims of its own


def test_oracle_quantities_resolve_on_built_snapshots(env):
    cfg = load_config()
    all_ = scn.load_all(SCENARIO_DIR)
    for sid in ("SHT-01", "LTB-01", "EO-06", "LTB-03"):
        sc = all_[sid]
        snap = scn.build_snapshot(sc, env, env / "decisions.sqlite")
        part = runner.load_target_part(snap, sc.target.part_id)
        assert sc.expected.quantity is not None and sc.expected.quantity.oracle
        value = oracle.oracle_get(part, cfg, sc.expected.quantity.oracle)
        assert isinstance(value, int | float) and value > 0, sid


def test_snapshot_cache_is_keyed_on_perturbations(env):
    sc = scn.load_all(SCENARIO_DIR)["SHT-01"]
    first = scn.build_snapshot(sc, env, env / "decisions.sqlite")
    stamp = (env / "SHT-01.fingerprint").read_text()
    mtime = first.stat().st_mtime_ns
    assert (
        scn.build_snapshot(sc, env, env / "decisions.sqlite").stat().st_mtime_ns == mtime
    )  # reused
    changed = sc.model_copy(update={"perturbations": sc.perturbations[:1]})
    scn.build_snapshot(changed, env, env / "decisions.sqlite")
    assert (env / "SHT-01.fingerprint").read_text() != stamp  # rebuilt
    scn.build_snapshot(sc, env, env / "decisions.sqlite")  # leave the right one behind


# ---------------------------------------------------------------- agent in eval mode


def sht01_responder(env: Path):
    """A scripted specialist for SHT-01 that cites the engine's own numbers."""
    sc = scn.load_all(SCENARIO_DIR)["SHT-01"]
    part = runner.load_target_part(
        scn.build_snapshot(sc, env, env / "decisions.sqlite"), "P-PSU-3000AC"
    )
    cfg = load_config()
    short = oracle.oracle_get(part, cfg, "shortage.max_shortfall_units")
    week = oracle.oracle_get(part, cfg, "shortage.first_shortage_week")

    draft = {
        "primary_action": {
            "action_type": "EXPEDITE",
            "quantity": short,
            "target_week_idx": week,
        },
        "root_cause": "SUPPLIER_SLIP",
        "root_cause_explanation": f"A PO slipped; the part is {short} units short from week {week}.",
        "evidence": [
            {"tool_call_id": "tc1", "field_path": "max_shortfall_units", "value": short},
            {"tool_call_id": "tc1", "field_path": "first_shortage_week", "value": week},
        ],
        "confidence": "HIGH",
        "stakeholder": "GSM",
        "draft_message": f"Please expedite the late PO: the part is {short} units short from week {week}.",
    }

    def respond(role: Role, messages: list[Msg], tools) -> LLMResponse:
        card = messages[1].content if len(messages) > 1 else ""
        if role == "writer":
            return text("One recommendation needs review.")
        if "EXC-SHT-P-PSU-3000AC" in card:
            if not any(m.role == "tool" for m in messages):
                return tool_call("get_part_position", part_id="P-PSU-3000AC")
            return text("ready") if tools else text(json.dumps(draft))
        return text("cannot help")

    return respond


def test_agent_eval_mode_scores_proposals_and_never_records_them(env):
    state_db = env / "decisions.sqlite"

    def rows() -> int:
        con = sqlite3.connect(state_db)
        try:
            return con.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
        finally:
            con.close()

    before = rows()
    client = ScriptedClient(sht01_responder(env))
    path = runner.run_suite(
        [scn.load_all(SCENARIO_DIR)["SHT-01"]], suite="smoke", system="agent", profile="replay",
        results_dir=env / "results", snapshot_dir=env, state_db=state_db, state_root=env / "evalruns",
        client=client, log=lambda _m: None,
    )  # fmt: skip
    row = json.loads(path.read_text(encoding="utf-8"))["results"][0]
    assert not row["crashed"], row["error"]
    assert row["rec"]["source"] == "AGENT" and row["rec"]["primary"]["action_type"] == "EXPEDITE"
    for name in ("detection", "action", "quantity", "must_not", "safety", "tier"):
        assert row["scores"][name]["passed"] is True, (name, row["scores"][name]["detail"])
    g = row["scores"]["grounding"]
    assert g["den"] > 0 and g["num"] == g["den"], g  # every claim traces to a re-run tool output
    assert row["passed"] and row["usage"]["llm_calls"] >= 2 and row["usage"]["tool_calls"] == 1
    assert rows() == before, "eval mode must not record or approve anything"
    ledger = Path(row["ledger"])
    assert ledger.exists() and "approval_gate" in ledger.read_text(encoding="utf-8")


def test_a_crashing_agent_run_is_a_failed_result_not_a_stopped_suite(env):
    def boom(role, messages, tools):
        raise ProviderError("provider is down")

    sc = scn.load_all(SCENARIO_DIR)
    path = runner.run_suite(
        [sc["SHT-01"], sc["NEG-01"]], suite="smoke", system="agent", profile="replay",
        results_dir=env / "results2", snapshot_dir=env, state_db=env / "decisions.sqlite",
        state_root=env / "evalruns2", client=ScriptedClient(boom), log=lambda _m: None,
    )  # fmt: skip
    rows = json.loads(path.read_text(encoding="utf-8"))["results"]
    assert [r["scenario_id"] for r in rows] == ["SHT-01", "NEG-01"]
    # The specialist falls back to the rules baseline, so the run completes and is flagged.
    assert all(not r["crashed"] for r in rows)
    assert rows[0]["rec"]["source"] == "RULES_FALLBACK"
    assert rows[1]["rec"] is None and rows[1]["passed"]  # a negative stays clean


def test_resume_skips_finished_runs(env):
    sc = scn.load_all(SCENARIO_DIR)
    kw = dict(suite="smoke", system="rules", results_dir=env / "results3", snapshot_dir=env,
              state_db=env / "decisions.sqlite", log=lambda _m: None)  # fmt: skip
    first = runner.run_suite([sc["NEG-01"]], **kw)
    again = runner.run_suite([sc["NEG-01"], sc["SHT-01"]], resume=first, **kw)
    assert again == first
    rows = json.loads(first.read_text(encoding="utf-8"))["results"]
    assert [r["scenario_id"] for r in rows] == ["NEG-01", "SHT-01"]  # NEG-01 was not run twice


def test_cli_report_and_bad_selection(env):
    out = CliRunner().invoke(app, ["eval", "report", "--results-dir", str(env / "results")])
    assert out.exit_code == 0, out.output
    board = (env / "results" / "leaderboard.md").read_text(encoding="utf-8")
    assert "| rules | rules |" in board and "Pass rate by category" in board
    empty = CliRunner().invoke(app, ["eval", "report", "--results-dir", str(env / "nothing")])
    assert empty.exit_code == 1
    bad = CliRunner().invoke(
        app, ["eval", "--scenarios", "NOPE-99", "--scenario-dir", str(SCENARIO_DIR)]
    )
    assert bad.exit_code == 1 and "unknown scenario" in bad.output
