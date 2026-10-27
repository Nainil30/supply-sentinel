"""The LangGraph application (spec 04 section 2), Phase 4 shape: only the shortage specialist.

load_snapshot -> detect_exceptions -> ingest_documents -> prioritize -> shortage_specialist (fan-out)
-> validate -> approval_gate (interrupt) -> finalize -> weekly_brief
"""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from supply_sentinel.agent.models import ExceptionCard
from supply_sentinel.agent.nodes import brief, intake, review
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists import shortage
from supply_sentinel.agent.state import AgentState


def _bind(fn: Callable[[RunContext, AgentState], dict[str, Any]], ctx: RunContext) -> Any:
    def node(state: AgentState) -> dict[str, Any]:
        return fn(ctx, state)

    node.__name__ = fn.__name__
    return node


def build_graph(ctx: RunContext, checkpointer: BaseCheckpointSaver[Any]) -> Any:
    g = StateGraph(AgentState)

    def shortage_specialist(payload: dict[str, Any]) -> dict[str, Any]:
        card = ExceptionCard(**payload["card"])
        cand = shortage.investigate(ctx, card)
        return {"candidates": [cand.model_dump(mode="json")]}

    def fan_out(state: AgentState) -> list[Send] | str:
        queue = state.get("queue", [])
        if not queue:
            return "validate"
        return [Send("shortage_specialist", {"card": c}) for c in queue]

    g.add_node("load_snapshot", _bind(intake.load_snapshot, ctx))
    g.add_node("detect_exceptions", _bind(intake.detect_exceptions, ctx))
    g.add_node("ingest_documents", _bind(intake.ingest_documents, ctx))
    g.add_node("prioritize", _bind(intake.prioritize, ctx))
    g.add_node("shortage_specialist", shortage_specialist)  # type: ignore[call-overload]
    g.add_node("validate", _bind(review.validate, ctx))
    g.add_node("approval_gate", _bind(review.approval_gate, ctx))
    g.add_node("finalize", _bind(review.finalize, ctx))
    g.add_node("weekly_brief", _bind(brief.weekly_brief, ctx))

    # Spec order is load -> ingest -> detect; ingest needs the detected parts, so detect runs first.
    g.add_edge(START, "load_snapshot")
    g.add_edge("load_snapshot", "detect_exceptions")
    g.add_edge("detect_exceptions", "ingest_documents")
    g.add_edge("ingest_documents", "prioritize")
    g.add_conditional_edges("prioritize", fan_out, ["shortage_specialist", "validate"])
    g.add_edge("shortage_specialist", "validate")
    g.add_edge("validate", "approval_gate")
    g.add_edge("approval_gate", "finalize")
    g.add_edge("finalize", "weekly_brief")
    g.add_edge("weekly_brief", END)
    return g.compile(checkpointer=checkpointer)
