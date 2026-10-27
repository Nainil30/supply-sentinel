"""Shared scorer context and helpers."""

import re
from dataclasses import dataclass

from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.exceptions import TYPE_CODES
from supply_sentinel.engine.models import ExceptionType, PartInputs, Recommendation
from supply_sentinel.evals import oracle
from supply_sentinel.evals.outcome import RunOutcome
from supply_sentinel.evals.scenario import Scenario


@dataclass
class ScoreContext:
    scenario: Scenario
    outcome: RunOutcome
    part: PartInputs  # the target part on the scenario snapshot
    cfg: EngineConfig
    snapshot_path: str = ""
    config_dir: str = "config"

    def oracle_value(self, key: str) -> oracle.Scalar:
        return oracle.oracle_get(self.part, self.cfg, key)

    @property
    def target_recs(self) -> list[Recommendation]:
        return [r for r in self.outcome.recs if r.part_id == self.scenario.target.part_id]

    @property
    def rec(self) -> Recommendation | None:
        """The recommendation for the expected exception (else the first for the part)."""
        recs = self.target_recs
        want = self.scenario.target.expect_exception
        if want:
            code = TYPE_CODES[ExceptionType(want)]
            for r in recs:
                if f"-{code}-" in r.exception_id:
                    return r
        return recs[0] if recs else None


NUM = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")
ID = re.compile(r"\b(?:P|PO|ORB|SUP|EXC|REC|EOL)-[A-Za-z0-9-]+\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def numbers_in(text: str) -> list[float]:
    """Numbers in free text, ignoring ids (P-PSU-3000AC, PO-10611) and ISO dates."""
    cleaned = DATE.sub(" ", ID.sub(" ", text))
    out: list[float] = []
    for m in NUM.findall(cleaned):
        try:
            out.append(float(m.replace("$", "").replace(",", "")))
        except ValueError:
            continue
    return out


def rec_text(rec: Recommendation) -> str:
    return f"{rec.draft_message}\n{rec.root_cause_explanation}"
