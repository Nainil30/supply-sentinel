"""action (hard): primary action in `any_of`; secondary in `any_of` when required."""

from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext


def score(sc: ScoreContext) -> Score:
    exp = sc.scenario.expected
    rec = sc.rec
    if exp.primary_action is None:
        ok = rec is None
        return Score(
            name="action",
            passed=ok,
            hard=True,
            detail="no recommendation, as expected" if ok else f"unexpected {rec and rec.rec_id}",
        )
    if rec is None:
        return Score(name="action", passed=False, hard=True, detail="no recommendation produced")
    primary = rec.primary_action.action_type.value
    problems: list[str] = []
    if primary not in exp.primary_action.any_of:
        problems.append(f"primary {primary} not in {exp.primary_action.any_of}")
    sec = rec.secondary_action.action_type.value if rec.secondary_action else None
    want = exp.secondary_action
    if want is not None:
        if sec is None and not want.optional:
            problems.append(f"secondary missing (wanted one of {want.any_of})")
        elif sec is not None and sec not in want.any_of:
            problems.append(f"secondary {sec} not in {want.any_of}")
    return Score(
        name="action",
        passed=not problems,
        hard=True,
        detail="; ".join(problems) or primary + (f" + {sec}" if sec else ""),
    )
