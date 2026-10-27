"""Offline end to end: replay the committed cassette on `base`, approve, resume, read the brief."""

import json
import shutil
import sqlite3

import pytest
from typer.testing import CliRunner

from supply_sentinel.cli import app
from supply_sentinel.datagen import generator

# The cassette covers all three base exceptions. The local qwen3:8b model did not return a
# usable recommendation for any of them, so each one is a visible RULES_FALLBACK (see OWNER_REVIEW).


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("replay")
    db = generator.build_base(seed=42, out_dir=d, state_db=d / "decisions.sqlite")
    return db, d


def invoke(*args):
    res = CliRunner().invoke(app, list(args))
    assert res.exit_code == 0, res.output
    return res.output


def test_replay_pause_approve_resume_offline(env, monkeypatch):
    db, d = env
    monkeypatch.setenv("SENTINEL_TODAY", "2026-11-02")
    out = invoke("run", "--snapshot", str(db), "--cassette", "base",
                 "--state-dir", str(d))  # fmt: skip
    assert "paused at the approval gate" in out
    for part in ("P-PHY-100G", "P-HSINK-L", "P-HSINK-S"):  # one recommendation per exception type
        assert part in out
    assert out.count("RULES_FALLBACK") >= 3
    run_id = (d / "latest_run").read_text().strip()
    rec_id = f"REC-{run_id}-1"

    listing = invoke("approvals", "list", "--state-dir", str(d))
    assert rec_id in listing

    # Tier-1 approvals need no reason; the staged decision is not final until resume.
    invoke("approve", rec_id, "--state-dir", str(d))
    resumed = invoke("resume", "--state-dir", str(d))
    assert f"{rec_id}: APPROVED" in resumed

    brief = (d / "runs" / run_id / "weekly_brief.md").read_text(encoding="utf-8")
    assert "# Weekly supply brief - as of 2026-11-02" in brief
    assert rec_id in brief and "P-PHY-100G" in brief
    status = (
        sqlite3.connect(d / "decisions.sqlite")
        .execute("SELECT status, decided_by FROM decision_log WHERE rec_id = ?", (rec_id,))
        .fetchone()
    )
    assert status[0] == "APPROVED"

    events = [
        json.loads(x) for x in (d / "runs" / run_id / "ledger.jsonl").read_text().splitlines()
    ]
    calls = [e for e in events if e["event"] == "llm_call" and e["tag"].startswith("EXC-")]
    assert calls and all(e["cached"] for e in calls)
    assert all(0 < e["input_tokens"] <= 7000 for e in calls)


def test_reject_needs_a_reason(env, monkeypatch):
    db, d = env
    monkeypatch.setenv("SENTINEL_TODAY", "2026-11-02")
    invoke("run", "--snapshot", str(db), "--cassette", "base",
           "--state-dir", str(d))  # fmt: skip
    run_id = (d / "latest_run").read_text().strip()
    res = CliRunner().invoke(app, ["reject", f"REC-{run_id}-1", "--state-dir", str(d)])
    assert res.exit_code == 1 and "needs --reason" in res.output


def test_cassette_is_committed():
    assert shutil.which("git") is not None
    from pathlib import Path

    assert Path("evals/cassettes/local/base.jsonl").stat().st_size > 1000
