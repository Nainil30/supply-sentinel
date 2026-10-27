"""Shared fixture: a base snapshot built once per module, plus helpers to start replay runs."""

import shutil

import pytest

from supply_sentinel.agent import runner
from supply_sentinel.datagen import generator


@pytest.fixture(scope="module")
def base_env(tmp_path_factory):
    d = tmp_path_factory.mktemp("console")
    db = generator.build_base(seed=42, out_dir=d, state_db=d / "decisions.sqlite")
    shutil.copyfile(d / "decisions.sqlite", d / "pristine.sqlite")
    return db, d


@pytest.fixture
def replay_run(base_env, monkeypatch):
    """A fresh replay run paused at the approval gate. Returns (db, state_dir, run_id, pending)."""
    db, d = base_env
    # Earlier decisions change the prompt (decision history), which would miss the cassette.
    shutil.copyfile(d / "pristine.sqlite", d / "decisions.sqlite")
    monkeypatch.setenv("SENTINEL_TODAY", "2026-11-02")
    run_id, pending = runner.start(db, "replay", d, cassette="base")
    return db, d, run_id, pending
