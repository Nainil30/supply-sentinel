"""Skills loader, context budget, router, cassette record/replay."""

import json
from pathlib import Path

import pytest

from supply_sentinel.agent import context
from supply_sentinel.agent.skills_loader import SkillError, SkillLibrary
from supply_sentinel.llm.replay import CassetteClient, CassetteMiss, request_key
from supply_sentinel.llm.router import Router, TokenBucket
from supply_sentinel.llm.types import LLMResponse, Msg, ProviderError
from supply_sentinel.observability.ledger import Ledger
from tests.fixtures.scripted import ScriptedClient, text

# ------------------------------------------------------------------ skills


def test_real_skills_load_and_expose_allowed_tools():
    lib = SkillLibrary(Path("skills"))
    assert {"shortage-triage", "stakeholder-comms"} <= set(lib.skills)
    assert "get_part_position" in lib.allowed_tools("shortage-triage")
    assert lib.allowed_tools("stakeholder-comms") == ()
    assert "shortage-triage:" in lib.index_text()
    assert lib.body("shortage-triage").startswith("# Shortage triage")


def test_skill_name_must_match_folder(tmp_path):
    d = tmp_path / "good-name"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\nbody", encoding="utf-8")
    with pytest.raises(SkillError):
        SkillLibrary(tmp_path)


def test_unknown_skill():
    with pytest.raises(SkillError):
        SkillLibrary(Path("skills")).get("nope")


# ------------------------------------------------------------------ bridge allowlist


def test_bridge_only_shows_and_runs_allowed_tools(tmp_path):
    from supply_sentinel.agent.mcp_bridge import McpBridge, ToolNotAllowed
    from supply_sentinel.datagen import generator
    from supply_sentinel.mcp_server.context import Snapshot

    db = generator.build_base(seed=42, out_dir=tmp_path, state_db=tmp_path / "decisions.sqlite")
    bridge = McpBridge(Snapshot(db, state_dir=tmp_path))
    allowed = ("get_part_position", "get_open_pos")
    names = [t["function"]["name"] for t in bridge.tool_schemas(allowed)]  # type: ignore[index]
    assert names == list(allowed)
    with pytest.raises(ToolNotAllowed):
        bridge.call("record_recommendation", {}, allowed)
    with pytest.raises(ToolNotAllowed):
        bridge.call("simulate_scenario", {"part_id": "P-PHY-100G"}, allowed)
    assert bridge.call("get_open_pos", {"part_id": "P-PHY-100G"}, allowed)["pos"]
    assert "error" in bridge.call("get_open_pos", {"part_id": "nope"}, allowed)


# ------------------------------------------------------------------ context budget


def test_compact_result_cuts_long_lists_to_fit():
    big = {"part_id": "P-X", "weeks": [{"week": i, "pab": float(i) * 1.5} for i in range(200)]}
    out = context.compact_result(big, max_tokens=300)
    assert context.count_tokens(out) <= 300
    assert '"part_id":"P-X"' in out and "more" in out


def test_forced_overflow_triggers_summarization():
    results = [
        Msg(role="tool", name=f"t{i}", tool_call_id=f"tc{i}",
            content=json.dumps({"id": f"tc{i}", "qty": 100 + i, "rows": ["x" * 90] * 40}))
        for i in range(8)
    ]  # fmt: skip
    msgs = [Msg(role="system", content="s" * 6000), Msg(role="user", content="card"), *results]
    before = context.messages_tokens(msgs)
    assert before > context.MAX_INPUT_TOKENS - context.HEADROOM_TOKENS
    after = context.fit(msgs, [])
    assert after <= context.MAX_INPUT_TOKENS - context.HEADROOM_TOKENS
    assert msgs[2].content.startswith("[summary of t0]") and '"qty":100' in msgs[2].content
    assert not msgs[-1].content.startswith("[summary")  # newest result stays whole


# ------------------------------------------------------------------ router


def test_token_bucket_waits_when_empty():
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    bucket = TokenBucket(2, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        bucket.acquire()
    assert slept and slept[0] == pytest.approx(30.0)  # 2 per minute -> 30 s for the third


def test_router_retries_then_gives_up(tmp_path):
    attempts = []

    def flaky(role, messages, tools):
        attempts.append(1)
        raise RuntimeError("429 too many requests")

    sleeps: list[float] = []
    ledger = Ledger(tmp_path / "l.jsonl", "r1")
    router = Router(ScriptedClient(flaky), ledger=ledger, max_retries=2, sleep=sleeps.append)
    with pytest.raises(ProviderError):
        router.complete("specialist", [Msg(role="user", content="hi")], [])
    assert len(attempts) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0] - 0.5
    assert [e["event"] for e in ledger.read()].count("llm_error") == 3


def test_router_recovers_and_logs_tokens(tmp_path):
    state = {"n": 0}

    def sometimes(role, messages, tools):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return text("ok")

    ledger = Ledger(tmp_path / "l.jsonl", "r1")
    router = Router(ScriptedClient(sometimes), ledger=ledger, sleep=lambda s: None)
    assert router.complete("writer", [Msg(role="user", content="hello world")], []).content == "ok"
    call = next(e for e in ledger.read() if e["event"] == "llm_call")
    assert call["input_tokens"] > 0 and "output_tokens" in call


# ------------------------------------------------------------------ cassettes


def test_record_then_replay(tmp_path):
    path = tmp_path / "c.jsonl"
    inner = ScriptedClient(lambda r, m, t: LLMResponse(content="hello", input_tokens=7, model="m"))
    msgs = [Msg(role="user", content="q")]
    rec = CassetteClient(path, {"specialist": "m"}, inner=inner)
    assert rec.mode == "record"
    recorded = rec.complete("specialist", msgs, [])
    assert recorded.content == "hello"

    rep = CassetteClient(path, {"specialist": "m"})
    out = rep.complete("specialist", msgs, [])
    assert out.content == "hello" and out.cached and out.input_tokens == recorded.input_tokens
    assert len(inner.calls) == 1  # replay never touched the model


def test_replay_miss_names_the_key(tmp_path):
    rep = CassetteClient(tmp_path / "none.jsonl", {"specialist": "m"})
    with pytest.raises(CassetteMiss, match="No cassette entry"):
        rep.complete("specialist", [Msg(role="user", content="q")], [])


def test_key_depends_on_every_input():
    m = [Msg(role="user", content="q")]
    base = request_key("specialist", "m1", m, [])
    assert base != request_key("writer", "m1", m, [])
    assert base != request_key("specialist", "m2", m, [])
    assert base != request_key("specialist", "m1", [Msg(role="user", content="r")], [])
    assert base != request_key("specialist", "m1", m, [{"type": "function"}])
    assert base == request_key("specialist", "m1", [Msg(role="user", content="q")], [])
