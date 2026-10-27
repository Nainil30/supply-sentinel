"""The LangGraph application (spec 04 section 2).

load_snapshot -> detect_exceptions -> ingest_documents -> prioritize -> one specialist per
exception (fan-out: shortage, ltb, eo) -> validate -> reconcile -> approval_gate (interrupt)
-> finalize -> weekly_brief
"""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from supply_sentinel.agent.models import ExceptionCard
from supply_sentinel.agent.nodes import brief, intake, reconcile, review
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists import eo, ltb, shortage
from supply_sentinel.agent.state import AgentState


def _bind(fn: Callable[[RunContext, AgentState], dict[str, Any]], ctx: RunContext) -> Any:
    def node(state: AgentState) -> dict[str, Any]:
        return fn(ctx, state)

    node.__name__ = fn.__name__
    return node


def build_graph(ctx: RunContext, checkpointer: BaseCheckpointSaver[Any]) -> Any:
    g = StateGraph(AgentState)

    def specialist(investigate: Callable[[RunContext, ExceptionCard], Any]) -> Any:
        def node(payload: dict[str, Any]) -> dict[str, Any]:
            cand = investigate(ctx, ExceptionCard(**payload["card"]))
            return {"candidates": [cand.model_dump(mode="json")]}

        return node

    routes = {
        **dict.fromkeys(shortage.HANDLES, "shortage_specialist"),
        **dict.fromkeys(ltb.HANDLES, "ltb_specialist"),
        **dict.fromkeys(eo.HANDLES, "eo_specialist"),
    }

    def fan_out(state: AgentState) -> list[Send] | str:
        queue = state.get("queue", [])
        if not queue:
            return "validate"
        return [Send(routes[c["exception"]["type"]], {"card": c}) for c in queue]

    g.add_node("load_snapshot", _bind(intake.load_snapshot, ctx))
    g.add_node("detect_exceptions", _bind(intake.detect_exceptions, ctx))
    g.add_node("ingest_documents", _bind(intake.ingest_documents, ctx))
    g.add_node("prioritize", _bind(intake.prioritize, ctx))
    g.add_node("shortage_specialist", specialist(shortage.investigate))
    g.add_node("ltb_specialist", specialist(ltb.investigate))
    g.add_node("eo_specialist", specialist(eo.investigate))
    g.add_node("validate", _bind(review.validate, ctx))
    g.add_node("reconcile", _bind(reconcile.reconcile, ctx))
    g.add_node("approval_gate", _bind(review.approval_gate, ctx))
    g.add_node("finalize", _bind(review.finalize, ctx))
    g.add_node("weekly_brief", _bind(brief.weekly_brief, ctx))

    # Spec order is load -> ingest -> detect; ingest needs the detected parts, so detect runs first.
    g.add_edge(START, "load_snapshot")
    g.add_edge("load_snapshot", "detect_exceptions")
    g.add_edge("detect_exceptions", "ingest_documents")
    g.add_edge("ingest_documents", "prioritize")
    g.add_conditional_edges("prioritize", fan_out, [*sorted(set(routes.values())), "validate"])
    for name in sorted(set(routes.values())):
        g.add_edge(name, "validate")
    g.add_edge("validate", "reconcile")
    g.add_edge("reconcile", "approval_gate")
    g.add_edge("approval_gate", "finalize")
    g.add_edge("finalize", "weekly_brief")
    g.add_edge("weekly_brief", END)
    return g.compile(checkpointer=checkpointer)
