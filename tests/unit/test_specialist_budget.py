"""Slow-model controls: investigation yields time to the final call; the final call is schema-bound."""

import time
from types import SimpleNamespace

from supply_sentinel.agent.models import Candidate
from supply_sentinel.agent.specialists import base
from supply_sentinel.llm.router import Router
from supply_sentinel.llm.types import LLMResponse, Msg
from tests.fixtures.scripted import ScriptedClient, text


def test_investigation_stops_when_final_call_would_be_starved():
    cand = Candidate(exception_id="EXC-1", part_id="P-1")
    cand.usage.llm_calls = 1  # one slow call already made
    calls: list[str] = []

    def call(tag: str, with_tools: bool) -> LLMResponse:
        calls.append(tag)
        return text("")

    ctx = SimpleNamespace(wall_seconds=100.0)
    t0 = time.monotonic() - 50  # 50 s used; the slowest call took 30 s, so 2 x 30 s > 50 s left
    out = base._investigate(ctx, None, cand, [], call, (), 8, 2, t0, [30.0])  # type: ignore[arg-type]
    assert out is None
    assert calls == []  # no further tool-phase call was made


def test_router_passes_json_schema_to_client():
    seen: list[object] = []

    class Spy(ScriptedClient):
        def complete(self, role, messages, tools, json_schema=None):
            seen.append(json_schema)
            return super().complete(role, messages, tools, json_schema)

    router = Router(Spy(lambda *_: text("{}")))
    router.complete(
        "specialist", [Msg(role="user", content="x")], [], json_schema={"type": "object"}
    )
    assert seen == [{"type": "object"}]


def test_draft_schema_lists_required_fields():
    assert {"primary_action", "root_cause", "stakeholder", "draft_message"} <= set(
        base.DRAFT_SCHEMA["required"]  # type: ignore[arg-type]
    )
