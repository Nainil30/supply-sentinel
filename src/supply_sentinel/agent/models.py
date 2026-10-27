"""Agent-side models. The final `Recommendation` is the engine's model, shared with the MCP server.

The LLM only fills a `RecommendationDraft`. The harness adds the id, source, tier and grounding.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from supply_sentinel.engine.models import (
    ActionItem,
    Agreement,
    AlternativeConsidered,
    Confidence,
    EvidenceRef,
    ExceptionRecord,
    Recommendation,
    RecRootCause,
    SecurityFlag,
    Stakeholder,
)


class RecommendationDraft(BaseModel):
    primary_action: ActionItem
    secondary_action: ActionItem | None = None
    root_cause: RecRootCause
    root_cause_explanation: str
    baseline_agreement: Agreement = Agreement.AGREE
    deviation_reason: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    alternatives_considered: list[AlternativeConsidered] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    stakeholder: Stakeholder
    draft_message: str


class ExceptionCard(BaseModel):
    """One exception plus the rules-engine suggestion, as the agent sees it."""

    exception: ExceptionRecord
    baseline: Recommendation
    platforms: list[str] = Field(default_factory=list)

    @property
    def exception_id(self) -> str:
        return self.exception.exception_id

    @property
    def part_id(self) -> str:
        return self.exception.part_id


class RunBudget(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    max_call_input_tokens: int = 0
    wall_seconds: float = 0.0


class Candidate(BaseModel):
    """A specialist's output before validation. `draft` is None when the specialist failed."""

    exception_id: str
    part_id: str
    draft: RecommendationDraft | None = None
    tool_log: dict[str, Any] = Field(default_factory=dict)  # tool_call_id -> raw tool output
    messages: list[dict[str, Any]] = Field(default_factory=list)  # kept for the repair call
    security_flags: list[SecurityFlag] = Field(default_factory=list)
    failure: str | None = None  # why there is no draft (provider error, budget, parse)
    usage: RunBudget = Field(default_factory=RunBudget)


class Decision(BaseModel):
    rec_id: str
    decision: Literal["APPROVED", "EDITED", "REJECTED", "DEFERRED"]
    quantity: float | None = None
    reason: str = ""
    decided_by: str = "planner"


class NodeError(BaseModel):
    node: str
    exception_id: str | None = None
    message: str
