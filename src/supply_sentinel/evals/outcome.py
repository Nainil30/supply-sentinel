"""What one scenario run produced, and what a scorer says about it."""

from typing import Any

from pydantic import BaseModel, Field

from supply_sentinel.engine.models import ExceptionRecord, Recommendation, SecurityFlag


class Usage(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0  # free profiles report 0; paid profiles need a price table


class RunOutcome(BaseModel):
    scenario_id: str
    system: str  # "rules" | "agent"
    profile: str
    run_idx: int = 0
    crashed: bool = False
    error: str | None = None
    exceptions: list[ExceptionRecord] = Field(default_factory=list)  # everything detected
    recs: list[Recommendation] = Field(default_factory=list)  # proposals captured at the gate
    run_flags: list[str] = Field(default_factory=list)  # run-level, e.g. STALE_DATA
    security_flags: list[SecurityFlag] = Field(default_factory=list)  # run-level, from ingestion
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)  # ledger tool_call events
    usage: Usage = Field(default_factory=Usage)
    ledger_path: str | None = None


class Score(BaseModel):
    """`passed` is None when the scorer does not apply (for example comms on the rules baseline)."""

    name: str
    passed: bool | None
    hard: bool
    detail: str = ""
    num: int = 0  # for ratio scorers (grounding): traceable claims
    den: int = 0  # ... and all claims
