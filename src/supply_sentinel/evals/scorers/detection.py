"""detection (hard): the target exception is raised iff `expect_exception` is set."""

from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext


def score(sc: ScoreContext) -> Score:
    part = sc.scenario.target.part_id
    want = sc.scenario.target.expect_exception
    found = sorted(e.type.value for e in sc.outcome.exceptions if e.part_id == part)
    if want is None:
        ok = not found
        detail = "no exception on the target part" if ok else f"unexpected exception(s): {found}"
    else:
        ok = want in found
        detail = f"{want} raised" if ok else f"{want} not raised (found: {found or 'none'})"
    return Score(name="detection", passed=ok, hard=True, detail=detail)
