"""must_mention (soft): every string (or `re:` regex) appears in the message or rationale."""

import re

from supply_sentinel.evals import oracle
from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext, rec_text


def resolved_mentions(sc: ScoreContext) -> list[str]:
    out = oracle.resolve(
        sc.scenario.expected.must_mention,
        {sc.part.part_id: sc.part},
        oracle_part=sc.part,
        cfg=sc.cfg,
    )
    return [str(x) for x in out]


def _present(wanted: str, text: str) -> bool:
    if wanted.startswith("re:"):
        return re.search(wanted[3:], text, re.IGNORECASE) is not None
    return wanted.lower() in text.lower()


def score(sc: ScoreContext) -> Score:
    wanted = resolved_mentions(sc)
    if not wanted:
        return Score(name="must_mention", passed=None, hard=False, detail="nothing required")
    rec = sc.rec
    if rec is None:
        return Score(name="must_mention", passed=False, hard=False, detail="no recommendation")
    text = rec_text(rec)
    missing = [w for w in wanted if not _present(w, text)]
    return Score(
        name="must_mention",
        passed=not missing,
        hard=False,
        detail=f"missing: {missing}" if missing else f"all {len(wanted)} present",
    )
