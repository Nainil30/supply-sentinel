"""Graph state. Plain JSON-friendly data only, so the SQLite checkpointer can store it and a
paused run can resume in a different process. Nodes turn dicts back into models when needed.
"""

from operator import add
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    snapshot_id: str
    as_of_date: str
    profile: str
    stale: bool
    cards: list[dict[str, Any]]  # ExceptionCard dumps, all detected exceptions
    queue: list[dict[str, Any]]  # cards routed to a specialist this run, in priority order
    deferred: list[dict[str, Any]]  # cards with no specialist wired yet (baseline only)
    security_flags: Annotated[
        list[dict[str, Any]], add
    ]  # from ingestion; field starts with part id
    candidates: Annotated[list[dict[str, Any]], add]  # Candidate dumps from the fan-out
    recommendations: list[dict[str, Any]]  # validated Recommendation dumps
    decisions: list[dict[str, Any]]  # Decision dumps from the approval gate
    outcomes: list[dict[str, Any]]  # {rec_id, status} after finalize
    budget: dict[str, Any]  # RunBudget dump
    errors: Annotated[list[dict[str, Any]], add]  # NodeError dumps
    brief_path: str | None
