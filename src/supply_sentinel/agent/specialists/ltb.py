"""LTB specialist: handles EOL_NOTICE with the ltb-sizing skill."""

from supply_sentinel.agent.models import Candidate, ExceptionCard
from supply_sentinel.agent.runtime import RunContext
from supply_sentinel.agent.specialists.base import run_specialist

SKILL = "ltb-sizing"
HANDLES = ("EOL_NOTICE",)


def investigate(ctx: RunContext, card: ExceptionCard) -> Candidate:
    return run_specialist(ctx, card, SKILL, "last-time-buy")
