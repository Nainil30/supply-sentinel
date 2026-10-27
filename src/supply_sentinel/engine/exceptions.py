"""Exception detection (spec 02 section 2): one exception per (part, type)."""

from supply_sentinel.engine import severity
from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.eo import avg_weekly_gross, compute_eo_exposure
from supply_sentinel.engine.models import (
    ExceptionRecord,
    ExceptionType,
    Netting,
    PartInputs,
)
from supply_sentinel.engine.netting import EPS, commit_gaps, net_position

TYPE_CODES = {
    ExceptionType.SHORTAGE: "SHT",
    ExceptionType.COMMIT_GAP: "GAP",
    ExceptionType.EOL_NOTICE: "EOL",
    ExceptionType.EXCESS: "EXS",
    ExceptionType.OBSOLESCENCE_RISK: "OBS",
}


def exception_id(exc_type: ExceptionType, part_id: str) -> str:
    return f"EXC-{TYPE_CODES[exc_type]}-{part_id}"


def revenue_at_risk(inp: PartInputs, net: Netting) -> float:
    """Split the max shortfall over platforms by their share of GR in the shortage window.

    Per platform: units impacted = its shortfall share / qty_per; revenue = units x ASP.
    """
    if net.first_shortage_week is None:
        return 0.0
    start = net.first_shortage_week
    end = net.recovery_week if net.recovery_week is not None else net.horizon_weeks
    demand = {}
    for wu in inp.where_used:
        prod = wu.production[start:end]
        serv = wu.service[start:end]
        demand[wu.platform_id] = (sum(prod) + sum(serv)) * wu.qty_per
    total = sum(demand.values())
    if total <= EPS:
        return 0.0
    revenue = 0.0
    for wu in inp.where_used:
        share_units = net.max_shortfall_units * demand[wu.platform_id] / total
        revenue += share_units / wu.qty_per * wu.asp_usd
    return round(revenue, 2)


def detect_part_exceptions(inp: PartInputs, cfg: EngineConfig) -> list[ExceptionRecord]:
    net = net_position(inp, cfg.horizon_weeks)
    found: list[ExceptionRecord] = []

    if net.first_shortage_week is not None:
        revenue = revenue_at_risk(inp, net)
        found.append(
            ExceptionRecord(
                exception_id=exception_id(ExceptionType.SHORTAGE, inp.part_id),
                type=ExceptionType.SHORTAGE,
                part_id=inp.part_id,
                severity=severity.shortage_severity(revenue, net.first_shortage_week),
                first_shortage_week=net.first_shortage_week,
                max_shortfall_units=net.max_shortfall_units,
                recovery_week=net.recovery_week,
                revenue_at_risk_usd=revenue,
            )
        )
    else:
        gaps = commit_gaps(inp, cfg.commit_gap_weeks)
        if gaps:
            found.append(
                ExceptionRecord(
                    exception_id=exception_id(ExceptionType.COMMIT_GAP, inp.part_id),
                    type=ExceptionType.COMMIT_GAP,
                    part_id=inp.part_id,
                    severity=severity.commit_gap_severity(list(gaps)),
                    gap_weeks=list(gaps),
                    gap_units=sum(gaps.values()),
                )
            )

    eol = inp.eol_notice
    if eol is not None and eol.ltb_date is not None and eol.ltb_date >= inp.as_of:
        days = (eol.ltb_date - inp.as_of).days
        found.append(
            ExceptionRecord(
                exception_id=exception_id(ExceptionType.EOL_NOTICE, inp.part_id),
                type=ExceptionType.EOL_NOTICE,
                part_id=inp.part_id,
                severity=severity.eol_severity(days),
                days_to_ltb=days,
                replacement_part_id=eol.replacement_part_id,
                where_used=[wu.platform_id for wu in inp.where_used],
            )
        )

    eo = compute_eo_exposure(inp, cfg)
    if eo.obsolete_units > 0:
        found.append(
            ExceptionRecord(
                exception_id=exception_id(ExceptionType.OBSOLESCENCE_RISK, inp.part_id),
                type=ExceptionType.OBSOLESCENCE_RISK,
                part_id=inp.part_id,
                severity=severity.exposure_severity(eo.obsolete_usd),
                obsolete_units=eo.obsolete_units,
                obsolete_usd=eo.obsolete_usd,
            )
        )
    elif (
        eo.excess_units > max(inp.moq, 4 * avg_weekly_gross(inp, cfg))
        and eo.excess_usd >= cfg.min_excess_usd
    ):
        found.append(
            ExceptionRecord(
                exception_id=exception_id(ExceptionType.EXCESS, inp.part_id),
                type=ExceptionType.EXCESS,
                part_id=inp.part_id,
                severity=severity.exposure_severity(eo.excess_usd),
                excess_units=eo.excess_units,
                excess_usd=eo.excess_usd,
                cancellable_po_qty=eo.cancellable_po_qty,
            )
        )
    return found


SEVERITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}


def detect_exceptions(parts: list[PartInputs], cfg: EngineConfig) -> list[ExceptionRecord]:
    """All exceptions for a snapshot, most severe first (ties by id, so output is stable)."""
    found = [exc for inp in parts for exc in detect_part_exceptions(inp, cfg)]
    return sorted(found, key=lambda e: (SEVERITY_ORDER[e.severity.value], e.exception_id))
