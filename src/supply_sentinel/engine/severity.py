"""Severity tables (spec 02 section 2). Pure comparisons, no I/O."""

from supply_sentinel.engine.models import Severity


def shortage_severity(revenue_at_risk_usd: float, first_shortage_week: int) -> Severity:
    if revenue_at_risk_usd >= 250_000 and first_shortage_week <= 4:
        return Severity.P1
    if revenue_at_risk_usd >= 50_000 or first_shortage_week <= 8:
        return Severity.P2
    return Severity.P3


def commit_gap_severity(gap_weeks: list[int]) -> Severity:
    earliest = min(gap_weeks)
    if earliest <= 4:
        return Severity.P1
    if earliest <= 8:
        return Severity.P2
    return Severity.P3


def eol_severity(days_to_ltb: int) -> Severity:
    if days_to_ltb <= 30:
        return Severity.P1
    if days_to_ltb <= 90:
        return Severity.P2
    return Severity.P3


def exposure_severity(exposure_usd: float) -> Severity:
    """EXCESS and OBSOLESCENCE_RISK."""
    if exposure_usd >= 100_000:
        return Severity.P1
    if exposure_usd >= 20_000:
        return Severity.P2
    return Severity.P3
