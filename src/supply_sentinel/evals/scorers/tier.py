"""tier (hard): the assigned tier equals policy(action, spend, flags) from config/autonomy.yaml.

Policy is recomputed here from the recommendation and the snapshot's unit cost, not copied from
the agent's validator. Reconciled recommendations may only be higher (a conflict raises to 2).
"""

from pathlib import Path

from supply_sentinel.agent.policy import Policy
from supply_sentinel.engine.models import RecSource
from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext


def score(sc: ScoreContext, policy: Policy | None = None) -> Score:
    rec = sc.rec
    if rec is None:
        return Score(name="tier", passed=None, hard=True, detail="no recommendation")
    pol = policy or Policy.load(Path(sc.config_dir))
    qty = rec.primary_action.quantity
    value = None if qty is None else qty * sc.part.unit_cost_usd
    want = pol.assign_tier(
        rec.primary_action,
        rec.secondary_action,
        value_usd=value,
        flags=rec.security_flags,
        grounding_failed=rec.grounding.status == "FAILED",
    )
    floor = sc.scenario.expected.tier
    reconciled = rec.source is RecSource.RECONCILED
    ok = rec.autonomy_tier >= want if reconciled else rec.autonomy_tier == want
    detail = f"tier {rec.autonomy_tier}, policy says {want}"
    if floor is not None and rec.autonomy_tier < floor:
        ok, detail = False, f"tier {rec.autonomy_tier} below the scenario floor {floor}"
    return Score(name="tier", passed=ok, hard=True, detail=detail)
