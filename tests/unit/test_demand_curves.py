"""Hand-checked values for the noise-free demand curves."""

from datetime import date

import pytest

from supply_sentinel.datagen.catalog import PLATFORMS
from supply_sentinel.datagen.demand import (
    _is_quarter_end_window,
    forecast_buckets,
    mean_level,
    service_annual_units,
    week_idx_of,
    week_start,
)

P = {p.platform_id: p for p in PLATFORMS}


def test_week_index_round_trip():
    assert week_start(0) == date(2026, 11, 2)
    assert week_idx_of(date(2026, 11, 8)) == 0
    assert week_idx_of(date(2026, 10, 31)) == -1


def test_s16_ramps_five_to_sixty_over_26_weeks():
    assert mean_level(P["ORB-S16"], 0) == 5.0
    assert mean_level(P["ORB-S16"], 25) == 60.0
    assert mean_level(P["ORB-S16"], -10) == 0.0  # before launch


def test_s8_trend_and_flat_after_a_year():
    assert mean_level(P["ORB-S8"], 0) == 80.0
    assert mean_level(P["ORB-S8"], 10) == pytest.approx(80 * 1.005**10)
    assert mean_level(P["ORB-S8"], 60) == mean_level(P["ORB-S8"], 52)


def test_s4_declines_to_twenty_at_last_ship():
    t_last = week_idx_of(P["ORB-S4"].last_ship_date)
    assert mean_level(P["ORB-S4"], 0) == 40.0
    assert mean_level(P["ORB-S4"], t_last) == pytest.approx(20.0)
    assert mean_level(P["ORB-S4"], t_last + 1) == 0.0


def test_l1_has_no_production_and_taper_applies_before_last_ship():
    assert mean_level(P["ORB-L1"], 0) == 0.0
    t_last = week_idx_of(P["ORB-M1"].last_ship_date)
    assert mean_level(P["ORB-M1"], t_last - 20) == 60.0
    assert mean_level(P["ORB-M1"], t_last) == pytest.approx(60 * 0.4)


def test_quarter_end_window():
    assert not _is_quarter_end_window(date(2026, 12, 14))  # 17 days before 31 Dec
    assert _is_quarter_end_window(date(2026, 12, 21))
    assert _is_quarter_end_window(date(2026, 12, 28))
    assert not _is_quarter_end_window(date(2026, 11, 2))


def test_l4_quarter_end_lift_is_thirty_percent():
    p = P["ORB-L4"]
    assert mean_level(p, week_idx_of(date(2026, 12, 21))) == pytest.approx(120 * 1.3)
    assert mean_level(p, 0) == 120.0


def test_forecast_buckets_shape():
    buckets = forecast_buckets(P["ORB-S8"])
    weekly = [b for b in buckets if b[1] == "WEEK"]
    monthly = [b for b in buckets if b[1] == "MONTH"]
    assert len(weekly) == 26
    assert weekly[0][2] == pytest.approx(84.8)  # 80 x 1.06 bias
    assert monthly[0][0] == date(2027, 5, 3)  # Monday of week 26
    assert monthly[1][0] == date(2027, 6, 1)
    assert monthly[-1][0] <= P["ORB-S8"].last_ship_date
    assert forecast_buckets(P["ORB-L1"]) == []


def test_service_demand_growth_and_decline():
    s4 = P["ORB-S4"]  # 3/wk -> 156 base; last ship 2027
    assert service_annual_units(s4, 2026) == 156
    assert service_annual_units(s4, 2027) == round(156 * 1.1)
    assert service_annual_units(s4, 2028) == round(156 * 1.1 * 0.92)
    l1 = P["ORB-L1"]
    assert service_annual_units(l1, 2027) == round(156 * 0.92)
