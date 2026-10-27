"""Shortage specialist: handles SHORTAGE and COMMIT_GAP with the shortage-triage skill."""

from supply_sentinel.agent.models import Candidate, ExceptionCard
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists.base import run_specialist

SKILL = "shortage-triage"
HANDLES = ("SHORTAGE", "COMMIT_GAP")


def investigate(ctx: RunContext, card: ExceptionCard) -> Candidate:
    return run_specialist(ctx, card, SKILL, "shortage")
