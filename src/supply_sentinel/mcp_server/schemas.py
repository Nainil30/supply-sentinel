"""Pydantic output models for the MCP tools (spec 03 section 2).

Every response carries `as_of_date` and `snapshot_id`. Lists are capped (`MAX_ROWS`,
`MAX_WEEKS`); when a cap bites, `truncated` is true and `total_count` has the real size.
"""

from datetime import date

from pydantic import BaseModel, Field

from supply_sentinel.engine.models import (
    AllocationResult,
    EoResult,
    LtbResult,
    Recommendation,
    WhatIfResult,
)
from supply_sentinel.mcp_server.untrusted import UntrustedText

MAX_ROWS = 20
MAX_WEEKS = 26


class Base(BaseModel):
    as_of_date: date
    snapshot_id: str


# ---------------------------------------------------------------- list_exceptions


class ExceptionSummary(BaseModel):
    exception_id: str
    type: str
    part_id: str
    platforms: list[str]
    severity: str
    weeks_to_impact: int | None
    revenue_at_risk_usd: float | None = None
    exposure_usd: float | None = None
    baseline_action: str
    baseline_quantity: float | None = None


class ExceptionList(Base):
    exceptions: list[ExceptionSummary]
    total_count: int
    truncated: bool


# ---------------------------------------------------------------- part position


class WeekRow(BaseModel):
    week_idx: int
    gross_req: float
    receipts: float
    pab: float


class PartPosition(Base):
    part_id: str
    description: str
    usable_on_hand: int
    quality_hold_units: int
    coverage_weeks: float | None
    lead_time_weeks: int
    moq: int
    order_multiple: int
    unit_cost_usd: float
    weeks: list[WeekRow]
    first_shortage_week: int | None
    max_shortfall_units: float
    shortfall_rounded_to_multiple: int
    recovery_week: int | None
    truncated: bool


class Allocation(Base):
    part_id: str
    priority_order: list[str]
    result: AllocationResult


class PlatformUse(BaseModel):
    platform_id: str
    qty_per: int
    lifecycle_phase: str
    last_ship_date: date
    eosl_date: date
    share_of_gross_req_pct: float


class WhereUsed(Base):
    part_id: str
    platforms: list[PlatformUse]


class OpenPo(BaseModel):
    po_id: str
    qty_units: int
    need_week_idx: int
    promise_week_idx: int
    slip_weeks: int
    cancellable: bool
    unit_cost_usd: float


class OpenPos(Base):
    part_id: str
    pos: list[OpenPo]
    total_count: int
    truncated: bool


class CommitRow(BaseModel):
    week_idx: int
    commit_units: int | None
    po_promised_units: int
    gap_units: int
    note: UntrustedText | None = None


class SupplierCommits(Base):
    part_id: str
    rows: list[CommitRow]
    truncated: bool


class ForecastRow(BaseModel):
    week_idx: int
    units: float


class ActualRow(BaseModel):
    week_start: date
    units: int


class DemandProfile(Base):
    platform_id: str
    actuals_last_13_weeks: list[ActualRow]
    forecast_current: list[ForecastRow]
    forecast_previous: list[ForecastRow]
    forecast_change_pct: float
    mape: float | None
    bias: float | None
    truncated: bool = Field(description="True when forecast rows were capped at 26 weeks.")


class EolNoticeOut(Base):
    part_id: str
    notice_id: str
    notice_date: date
    ltb_date: date | None
    last_ship_date: date | None
    replacement_part_id: str | None
    days_to_ltb: int | None
    raw_text: UntrustedText


class AlternateOut(BaseModel):
    alt_part_id: str
    qualification_status: str
    qual_lead_time_weeks: int
    unit_cost_usd: float


class Alternates(Base):
    part_id: str
    alternates: list[AlternateOut]


class DecisionRow(BaseModel):
    rec_id: str
    action_type: str
    quantity: int | None
    status: str
    decided_at: str | None
    rationale: str | None


class DecisionHistory(Base):
    part_id: str
    decisions: list[DecisionRow]


class CounterfactualOut(BaseModel):
    cause: str
    max_shortfall_after: float
    first_shortage_week_after: int | None
    removes_shortage: bool
    shortfall_reduction_pct: float


class RootCauseOut(Base):
    part_id: str
    primary_cause: str
    contributing_causes: list[str]
    counterfactuals: list[CounterfactualOut]


class LtbOut(Base):
    result: LtbResult


class EoOut(Base):
    result: EoResult


class WhatIfOut(Base):
    result: WhatIfResult
    first_shortage_week_pab_truncated: bool = False


class RecordedRecommendation(Base):
    rec_id: str
    status: str


__all__ = ["Recommendation"]  # re-exported for the server's record_recommendation tool
