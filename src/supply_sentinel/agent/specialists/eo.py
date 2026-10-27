"""E&O specialist: handles EXCESS and OBSOLESCENCE_RISK with the eo-disposition skill."""

from supply_sentinel.agent.models import Candidate, ExceptionCard
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists.base import run_specialist

SKILL = "eo-disposition"
HANDLES = ("EXCESS", "OBSOLESCENCE_RISK")


def investigate(ctx: RunContext, card: ExceptionCard) -> Candidate:
    return run_specialist(ctx, card, SKILL, "excess-and-obsolete")
