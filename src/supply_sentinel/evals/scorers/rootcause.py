"""root_cause and stakeholder (soft): the label is in `any_of`."""

from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext


def score_root_cause(sc: ScoreContext) -> Score:
    want = sc.scenario.expected.root_cause
    if want is None:
        return Score(name="root_cause", passed=None, hard=False, detail="no label")
    rec = sc.rec
    got = rec.root_cause.value if rec else None
    return Score(
        name="root_cause", passed=got in want.any_of, hard=False, detail=f"{got} vs {want.any_of}"
    )


def score_stakeholder(sc: ScoreContext) -> Score:
    want = sc.scenario.expected.stakeholder
    if want is None:
        return Score(name="stakeholder", passed=None, hard=False, detail="no label")
    rec = sc.rec
    got = rec.stakeholder.value if rec else None
    return Score(
        name="stakeholder", passed=got in want.any_of, hard=False, detail=f"{got} vs {want.any_of}"
    )
