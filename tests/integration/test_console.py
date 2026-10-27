"""Planner Console: every page renders on `base` in replay mode, and approve/reject reach the store."""

import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from supply_sentinel import app as app_pkg
from supply_sentinel.app.services import ServiceError, Services

PAGES = Path(app_pkg.__file__).parent / "pages"
PAGE_NAMES = ["overview", "queue", "detail", "approvals", "what_if", "decisions", "evals", "run"]


def page(name, env):
    db, d = env[0], env[1]
    import os

    os.environ.update(
        SENTINEL_DB=str(db), SENTINEL_STATE_DIR=str(d), SENTINEL_UI_PROFILE="replay",
        SENTINEL_UI_CASSETTE="base", SENTINEL_TODAY="2026-11-02",
    )  # fmt: skip
    return AppTest.from_file(str(PAGES / f"{name}.py"), default_timeout=60)


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    for k in ("SENTINEL_DB", "SENTINEL_STATE_DIR", "SENTINEL_UI_PROFILE", "SENTINEL_UI_CASSETTE"):
        monkeypatch.setenv(k, "x")  # registers the key so monkeypatch restores it afterwards


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_each_page_renders_on_base_in_replay(name, replay_run):
    at = page(name, replay_run).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value


def test_overview_shows_kpis_and_exceptions(replay_run):
    at = page("overview", replay_run).run()
    labels = [m.label for m in at.metric]
    for want in (
        "Revenue at risk (shortages)",
        "Pending LTB spend",
        "E&O exposure",
        "Pending approvals",
    ):
        assert want in labels
    assert at.dataframe[0].value.shape[0] == 3  # the three natural exceptions on base


def test_detail_shows_recommendation_and_trace(replay_run):
    at = page("detail", replay_run).run()
    assert not at.exception
    assert any(s.value == "Recommendation" for s in at.subheader)
    assert at.code  # the draft message
    run_id = replay_run[2]
    assert any(f"Run {run_id}" in m.value for m in at.expander[-1].markdown)
    assert any("Trace id" in m.value for m in at.expander[-1].markdown)


def test_approve_and_reject_flow_updates_decision_store(replay_run):
    db, d, run_id, pending = replay_run
    tier1 = [p for p in pending if p["tier"] == 1]
    assert len(pending) >= 2 and tier1
    approve_id = tier1[0]["rec_id"]
    reject_id = next(p["rec_id"] for p in pending if p["rec_id"] != approve_id)

    at = page("approvals", replay_run).run()
    at.button(key=f"APPROVED_{approve_id}").click().run()
    assert not at.exception
    at = page("approvals", replay_run).run()
    at.button(key=f"REJECTED_{reject_id}").click().run()
    assert at.error and "reason" in at.error[0].value  # a rejection without a reason is refused
    at.text_input(key=f"reason_{reject_id}").set_value("Supplier already confirmed the date.")
    at.button(key=f"REJECTED_{reject_id}").click().run()
    assert not at.error

    svc = Services(state_dir=d, snapshot_path=db)
    staged = svc.staged(run_id)
    assert staged[approve_id].decision == "APPROVED" and staged[reject_id].decision == "REJECTED"

    outcomes = {o["rec_id"]: o["status"] for o in svc.resume(run_id)}
    assert outcomes[approve_id] == "APPROVED" and outcomes[reject_id] == "REJECTED"
    rows = sqlite3.connect(d / "decisions.sqlite").execute(
        "SELECT rec_id, status FROM decision_log WHERE rec_id IN (?, ?)", (approve_id, reject_id)
    ).fetchall()  # fmt: skip
    assert dict(rows) == {approve_id: "APPROVED", reject_id: "REJECTED"}
    log = [e for e in svc.ledger(run_id) if e["event"] == "approval"]
    assert {e["rec_id"] for e in log} >= {approve_id, reject_id}
    assert any(e["event"] == "run_end" for e in svc.ledger(run_id))


def test_service_refuses_bad_decisions(replay_run):
    db, d, run_id, pending = replay_run
    svc = Services(state_dir=d, snapshot_path=db)
    rec_id = pending[0]["rec_id"]
    with pytest.raises(ServiceError, match="reason"):
        svc.stage(run_id, rec_id, "REJECTED")
    with pytest.raises(ServiceError, match="not waiting"):
        svc.stage(run_id, "REC-nope", "APPROVED")
    with pytest.raises(ServiceError, match="quantity"):
        svc.stage(run_id, rec_id, "EDITED", reason="x")


def test_edit_is_revalidated(replay_run):
    db, d, run_id, pending = replay_run
    svc = Services(state_dir=d, snapshot_path=db)
    rec = next(r for r in svc.recommendations(run_id) if r.primary_action.quantity)
    assert svc.check_edit(rec, run_id, 0).errors
    huge = svc.check_edit(rec, run_id, float(rec.primary_action.quantity) * 10)
    assert not huge.errors
    with pytest.raises(ServiceError, match="greater than 0"):
        svc.stage(run_id, rec.rec_id, "EDITED", reason="why", quantity=0)


def test_run_page_starts_a_replay_run(base_env, monkeypatch):
    monkeypatch.setenv("SENTINEL_TODAY", "2026-11-02")
    at = page("run", (base_env[0], base_env[1])).run()
    at.button[0].click().run()
    assert not at.exception
    assert any("paused at the approval gate" in s.value for s in at.success)


def test_hosted_demo_blocks_live_profiles(base_env):
    svc = Services(
        state_dir=base_env[1], snapshot_path=base_env[0], profile="free-cloud", hosted=True
    )
    with pytest.raises(ServiceError, match="replay"):
        svc.start_run()


def test_replay_run_writes_full_ledger_and_spans(replay_run):
    from supply_sentinel.observability import tracing

    db, d, run_id, pending = replay_run
    svc = Services(state_dir=d, snapshot_path=db)
    events = svc.ledger(run_id)
    kinds = {e["event"] for e in events}
    assert {"run_start", "llm_call", "tool_call", "validation", "approval_gate"} <= kinds
    tool = next(e for e in events if e["event"] == "tool_call")
    assert tool["output"] and tool["latency_ms"] >= 0  # full output is the system of record
    llm = next(e for e in events if e["event"] == "llm_call")
    assert llm["status"] == "ok" and "latency_ms" in llm and llm["provider"]
    spans = tracing.read_spans(d / "runs" / run_id / "spans.jsonl")
    names = {s["name"].split(" ")[0] for s in spans}
    assert {"invoke_agent", "execute_tool", "chat", "load_snapshot"} <= names  # GenAI + LangGraph
    assert len({s["trace_id"] for s in spans}) == 1
    start = next(e for e in events if e["event"] == "run_start")
    assert start["trace_id"] == spans[0]["trace_id"]
    # every tool_call event has a matching execute_tool span
    assert sum(s["name"].startswith("execute_tool") for s in spans) == sum(
        e["event"] == "tool_call" for e in events
    )


def test_entry_point_with_navigation_renders_overview(replay_run):
    entry = Path(app_pkg.__file__).parent / "streamlit_app.py"
    page("overview", replay_run)  # sets the SENTINEL_* environment
    at = AppTest.from_file(str(entry), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "Overview"
