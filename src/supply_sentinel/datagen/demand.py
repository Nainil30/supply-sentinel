"""Noise-free weekly demand curves per platform (pure functions, no randomness).

`t` is `week_idx`: 0 is the as-of week (Monday 2026-11-02), negative is history.
The generator multiplies history by lognormal noise; the forecast is these curves times
the platform's forecast bias.
"""

from datetime import date, timedelta

from supply_sentinel.datagen.catalog import (
    AS_OF,
    SERVICE_DECLINE_PER_YEAR,
    SERVICE_GROWTH_PER_YEAR,
    WEEKLY_BUCKETS,
    Platform,
)

TAPER_WEEKS = 13  # production winds down over the last 13 weeks before last ship
TAPER_FLOOR = 0.4


def week_start(t: int) -> date:
    return AS_OF + timedelta(weeks=t)


def week_idx_of(d: date) -> int:
    """Week index of the Monday-start week containing `d` (floor division)."""
    return (d - AS_OF).days // 7


def _taper(t: int, t_last: int) -> float:
    if t > t_last:
        return 0.0
    weeks_left = t_last - t
    if weeks_left >= TAPER_WEEKS:
        return 1.0
    return TAPER_FLOOR + (1 - TAPER_FLOOR) * weeks_left / TAPER_WEEKS


def _is_quarter_end_window(monday: date) -> bool:
    """True for the last two weeks of a calendar quarter."""
    quarter_end_month = ((monday.month - 1) // 3 + 1) * 3
    next_month_first = (
        date(monday.year + 1, 1, 1)
        if quarter_end_month == 12
        else date(monday.year, quarter_end_month + 1, 1)
    )
    quarter_end = next_month_first - timedelta(days=1)
    return (quarter_end - monday).days <= 13


def mean_level(p: Platform, t: int) -> float:
    """Expected production units in week `t` (before noise and forecast bias)."""
    t_last = week_idx_of(p.last_ship_date)
    pid = p.platform_id
    if pid == "ORB-S16":
        launch_t = week_idx_of(p.launch_date)
        if t < launch_t:
            level = 0.0
        elif t < 0:
            level = 2.0 + 0.3 * (t - launch_t)  # pilot builds before the ramp
        elif t < WEEKLY_BUCKETS - 1:
            level = 5.0 + 55.0 * t / (WEEKLY_BUCKETS - 1)  # 5 -> 60 over 26 weeks
        else:
            level = 60.0
        return level * _taper(t, t_last)
    if pid == "ORB-S8":
        return 80.0 * 1.005 ** min(t, 52) * _taper(t, t_last)  # +0.5%/wk for a year, then flat
    if pid == "ORB-S4":
        if t > t_last:
            return 0.0
        return 40.0 + 0.3 * (-t) if t < 0 else 40.0 - 20.0 * t / t_last  # 40 -> 20 at last ship
    if pid == "ORB-L4":
        lift = 1.30 if _is_quarter_end_window(week_start(t)) else 1.0
        return 120.0 * lift * _taper(t, t_last)
    if pid == "ORB-M1":
        return 60.0 * _taper(t, t_last)
    if pid == "ORB-L1":  # production stopped at last ship; only history exists
        return 25.0 * _taper(t, t_last)
    return 0.0


def forecast_level(p: Platform, t: int) -> float:
    return mean_level(p, t) * p.forecast_bias


def service_annual_units(p: Platform, year: int) -> int:
    """Annual spares requirement for `year`: grows until last-ship year, then declines."""
    base = p.service_per_week * 52
    last_year = p.last_ship_date.year
    peak = 1 + SERVICE_GROWTH_PER_YEAR * (min(year, last_year) - AS_OF.year)
    decline = (1 - SERVICE_DECLINE_PER_YEAR) ** max(0, year - last_year)
    return round(base * peak * decline)


def service_weekly_rate(p: Platform, t: int) -> float:
    return service_annual_units(p, week_start(t).year) / 52


def forecast_buckets(p: Platform) -> list[tuple[date, str, float]]:
    """(bucket_start, bucket_type, units): 26 weekly buckets, then monthly to last ship.

    The first monthly bucket starts on the Monday of week 26 and runs to the end of that month.
    Monthly units are the sum of the weekly curve over the Mondays inside the bucket.
    """
    out: list[tuple[date, str, float]] = []
    t_last = week_idx_of(p.last_ship_date)
    for t in range(WEEKLY_BUCKETS):
        units = forecast_level(p, t)
        if t <= t_last and units > 0:
            out.append((week_start(t), "WEEK", round(units, 2)))
    start = week_start(WEEKLY_BUCKETS)
    while start <= p.last_ship_date:
        next_start = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        total = 0.0
        t = week_idx_of(start)
        while week_start(t) < next_start:
            if week_start(t) <= p.last_ship_date:
                total += forecast_level(p, t)
            t += 1
        if total > 0:
            out.append((start, "MONTH", round(total, 2)))
        start = next_start
    return out
