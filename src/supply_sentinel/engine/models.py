"""Pydantic models for the planning engine (spec 02).

Inputs (`PartInputs` and friends) are plain data: the loader fills them from DuckDB, tests fill them
by hand. Every engine function takes inputs and returns one of the result models below.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- inputs


class PurchaseOrder(BaseModel):
    po_id: str
    qty_units: int
    need_week_idx: int
    promise_week_idx: int
    cancel_window_weeks: int = 0


class WhereUsed(BaseModel):
    """One platform that uses the part, with its weekly demand curves (index = week_idx).

    `production` is the current forecast, `production_prev` the previous forecast version, and
    `service` the weekly spares demand. All are per platform unit; multiply by `qty_per`.
    """

    platform_id: str
    qty_per: int
    lifecycle_phase: str  # NPI | SUSTAIN | EOL_ANNOUNCED | EOL
    asp_usd: float
    production: list[float]
    production_prev: list[float]
    service: list[float]
    mape: float | None = None  # forecast MAPE at the lag nearest the part's lead time


class Alternate(BaseModel):
    alt_part_id: str
    qualification_status: str  # QUALIFIED | IN_QUAL | NOT_STARTED
    qual_lead_time_weeks: int


class EolNotice(BaseModel):
    notice_id: str
    ltb_date: date | None
    last_ship_date: date | None
    replacement_part_id: str | None = None


class PartInputs(BaseModel):
    part_id: str
    description: str = ""
    commodity: str = ""
    as_of: date = date(2026, 11, 2)
    unit_cost_usd: float
    lead_time_weeks: int
    moq: int = 1
    order_multiple: int = 1
    lifecycle_status: str = "ACTIVE"
    on_hand_units: int  # all locations, including quality hold
    quality_hold_units: int = 0
    pos: list[PurchaseOrder] = Field(default_factory=list)
    commits: dict[int, int] = Field(default_factory=dict)  # week_idx -> commit_units
    where_used: list[WhereUsed] = Field(default_factory=list)
    alternates: list[Alternate] = Field(default_factory=list)
    eol_notice: EolNotice | None = None

    @property
    def usable_on_hand(self) -> int:
        return self.on_hand_units - self.quality_hold_units


# ---------------------------------------------------------------- netting


class Netting(BaseModel):
    horizon_weeks: int
    gross_requirements: list[float]
    receipts: list[float]
    pab: list[float]
    first_shortage_week: int | None
    max_shortfall_units: float
    recovery_week: int | None
    coverage_weeks: float | None


# ---------------------------------------------------------------- exceptions


class ExceptionType(StrEnum):
    SHORTAGE = "SHORTAGE"
    COMMIT_GAP = "COMMIT_GAP"
    EOL_NOTICE = "EOL_NOTICE"
    EXCESS = "EXCESS"
    OBSOLESCENCE_RISK = "OBSOLESCENCE_RISK"


class Severity(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RootCause(StrEnum):
    QUALITY_HOLD = "QUALITY_HOLD"
    COMMIT_GAP = "COMMIT_GAP"
    SUPPLIER_SLIP = "SUPPLIER_SLIP"
    DEMAND_SPIKE = "DEMAND_SPIKE"
    STRUCTURAL = "STRUCTURAL"


class ExceptionRecord(BaseModel):
    exception_id: str
    type: ExceptionType
    part_id: str
    severity: Severity
    # SHORTAGE
    first_shortage_week: int | None = None
    max_shortfall_units: float | None = None
    recovery_week: int | None = None
    revenue_at_risk_usd: float | None = None
    # COMMIT_GAP
    gap_weeks: list[int] | None = None
    gap_units: int | None = None
    # EOL_NOTICE
    days_to_ltb: int | None = None
    replacement_part_id: str | None = None
    where_used: list[str] | None = None
    # EXCESS / OBSOLESCENCE_RISK
    excess_units: float | None = None
    excess_usd: float | None = None
    cancellable_po_qty: int | None = None
    obsolete_units: float | None = None
    obsolete_usd: float | None = None


# ---------------------------------------------------------------- root cause


class Counterfactual(BaseModel):
    cause: RootCause
    pab: list[float]
    max_shortfall_units: float
    removes_shortage: bool
    shortfall_reduction_pct: float


class RootCauseResult(BaseModel):
    part_id: str
    primary: RootCause
    contributing: list[RootCause]
    baseline_pab: list[float]
    counterfactuals: list[Counterfactual]


# ---------------------------------------------------------------- LTB


class NewsvendorResult(BaseModel):
    mu_units: float
    sigma_units: float
    unit_cost_usd: float
    overage_cost_usd: float
    underage_cost_usd: float
    critical_ratio: float
    z: float
    q_star_units: float
    gross_units: int
    usable_on_hand_units: int
    open_po_units: int
    net_units: int
    order_qty_units: int
    spend_usd: float


class BridgeOption(BaseModel):
    alt_part_id: str
    qualification_status: str
    qual_lead_time_weeks: int
    window_weeks: int
    sizing: NewsvendorResult


class LtbResult(BaseModel):
    part_id: str
    cv: float
    avg_years_held: float
    sizing: NewsvendorResult
    requires_second_approver: bool
    bridge: BridgeOption | None = None


# ---------------------------------------------------------------- E&O


class EoResult(BaseModel):
    part_id: str
    supply_units: int
    need_52w_units: float
    safety_stock_units: float
    excess_units: float
    excess_usd: float
    cancellable_po_qty: int
    cancel_qty_recommended: float
    avoidable_by_cancel_usd: float
    residual_excess_units: float
    residual_excess_usd: float
    lifetime_need_units: float
    all_platforms_ending: bool
    obsolete_units: float
    obsolete_usd: float


# ---------------------------------------------------------------- allocation / what-if


class PlatformAllocation(BaseModel):
    platform_id: str
    demand_units: float
    allocated_units: float
    unmet_units: float
    share: float  # allocated / total available


class AllocationResult(BaseModel):
    part_id: str
    window_start_week: int
    window_end_week: int
    available_units: float
    total_demand_units: float
    allocations: list[PlatformAllocation]


class WhatIfResult(BaseModel):
    part_id: str
    pab: list[float]
    first_shortage_week: int | None
    max_shortfall_units: float
    baseline_first_shortage_week: int | None
    baseline_max_shortfall_units: float
    shortfall_delta_units: float  # scenario minus baseline; negative is better


# ------------------------------------------------------- recommendation (shared with agent)


class ActionType(StrEnum):
    PLACE_PO = "PLACE_PO"
    EXPEDITE = "EXPEDITE"
    PULL_IN_PO = "PULL_IN_PO"
    ALLOCATE = "ALLOCATE"
    ESCALATE_SUPPLIER = "ESCALATE_SUPPLIER"
    QUALIFY_ALTERNATE = "QUALIFY_ALTERNATE"
    REVIEW_QUALITY_HOLD = "REVIEW_QUALITY_HOLD"
    LTB_BUY = "LTB_BUY"
    BRIDGE_BUY = "BRIDGE_BUY"
    PUSH_OUT_PO = "PUSH_OUT_PO"
    CANCEL_PO = "CANCEL_PO"
    DISPOSITION_REVIEW = "DISPOSITION_REVIEW"
    MONITOR = "MONITOR"
    DATA_ISSUE_FLAG = "DATA_ISSUE_FLAG"


class RecSource(StrEnum):
    AGENT = "AGENT"
    RULES_FALLBACK = "RULES_FALLBACK"
    RECONCILED = "RECONCILED"
    RULES_BASELINE = "RULES_BASELINE"


class RecRootCause(StrEnum):
    SUPPLIER_SLIP = "SUPPLIER_SLIP"
    COMMIT_GAP = "COMMIT_GAP"
    DEMAND_SPIKE = "DEMAND_SPIKE"
    QUALITY_HOLD = "QUALITY_HOLD"
    STRUCTURAL = "STRUCTURAL"
    EOL = "EOL"
    EXCESS_FORECAST_DROP = "EXCESS_FORECAST_DROP"
    EXCESS_OVERBUY = "EXCESS_OVERBUY"
    LIFECYCLE_END = "LIFECYCLE_END"
    DATA_ISSUE = "DATA_ISSUE"
    NONE = "NONE"


class Agreement(StrEnum):
    AGREE = "AGREE"
    DEVIATE = "DEVIATE"


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Stakeholder(StrEnum):
    GSM = "GSM"
    GCM = "GCM"
    PM = "PM"
    FINANCE = "FINANCE"
    QUALITY = "QUALITY"


class ActionItem(BaseModel):
    action_type: ActionType
    quantity: float | None = None
    po_ids: list[str] | None = None
    target_week_idx: int | None = None
    platform_allocations: dict[str, float] | None = None


class EvidenceRef(BaseModel):
    tool_call_id: str
    field_path: str
    value: float | int | str


class AlternativeConsidered(BaseModel):
    action_type: ActionType
    why_not: str


class Grounding(BaseModel):
    status: str = "NOT_CHECKED"  # NOT_CHECKED | PASSED | FAILED
    checked: int = 0
    ungrounded_values: list[str] = Field(default_factory=list)


class SecurityFlag(BaseModel):
    kind: str
    field: str
    detail: str = ""


class Recommendation(BaseModel):
    rec_id: str
    exception_id: str
    part_id: str
    source: RecSource
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
    autonomy_tier: int
    security_flags: list[SecurityFlag] = Field(default_factory=list)
    grounding: Grounding = Field(default_factory=Grounding)
