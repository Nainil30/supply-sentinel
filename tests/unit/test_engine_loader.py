"""Loader: turns a DuckDB snapshot into engine inputs. Uses the hand-built mini snapshot."""

from datetime import date

import pytest

from supply_sentinel.engine import loader
from supply_sentinel.engine.config import EngineConfig, load_config
from tests.fixtures.mini_snapshot import AS_OF, make_mini


def test_weekly_and_monthly_buckets_become_one_weekly_series():
    rows = [
        (date(2026, 11, 2), "WEEK", 10.0),
        (date(2026, 11, 9), "WEEK", 12.0),
        (date(2026, 11, 23), "MONTH", 100.0),  # Mondays Nov 23 and Nov 30 -> weeks 3 and 4
        (date(2026, 12, 1), "MONTH", 90.0),  # Mondays Dec 7, 14, 21, 28 -> weeks 5-8
    ]
    series = loader.production_series(rows, 12, AS_OF, last_ship=date(2027, 6, 30))
    assert series[:2] == [10.0, 12.0]
    assert series[3] == pytest.approx(50.0)
    assert series[4] == pytest.approx(50.0)
    assert series[5:9] == pytest.approx([22.5] * 4)
    assert sum(series) == pytest.approx(10 + 12 + 100 + 90)


def test_monthly_bucket_stops_at_last_ship_date():
    rows = [(date(2026, 11, 23), "MONTH", 100.0)]
    series = loader.production_series(rows, 12, AS_OF, last_ship=date(2026, 11, 25))
    assert series[3] == pytest.approx(100.0)  # only the Nov 23 Monday is on or before last ship
    assert series[4] == 0.0


def test_service_series_is_annual_spares_over_52_until_eosl():
    series = loader.service_series({2026: 520, 2027: 1040}, 12, AS_OF, eosl=date(2026, 12, 7))
    assert series[0] == pytest.approx(10.0)  # 520 / 52
    assert series[5] == pytest.approx(10.0)  # Dec 7 is the last Monday on or before EOSL
    assert series[6] == 0.0
    assert loader.service_series({2027: 1040}, 60, AS_OF, eosl=date(2030, 1, 1))[9] == 20.0


def test_load_part_inputs_from_mini_snapshot():
    parts = {p.part_id: p for p in loader.load_part_inputs(make_mini())}
    a = parts["P-A"]
    assert a.as_of == AS_OF
    assert a.usable_on_hand == 100
    assert [(po.po_id, po.qty_units, po.promise_week_idx) for po in a.pos] == [("PO-1", 50, 3)]
    assert a.commits == {3: 50}
    (wu,) = a.where_used
    assert wu.platform_id == "ORB-S4"
    assert wu.production[:3] == [10.0, 10.0, 10.0]
    assert wu.production_prev[:3] == pytest.approx([9.0, 9.0, 9.0])  # previous version is x0.9
    assert wu.production[3] + wu.production[4] == pytest.approx(100.0)  # one month bucket
    assert [alt.alt_part_id for alt in a.alternates] == ["P-B"]
    assert parts["P-B"].where_used == []
    assert len(wu.production) >= 60


def test_load_config_matches_defaults():
    cfg = load_config()
    default = EngineConfig()
    assert cfg.salvage_pct == default.salvage_pct
    assert cfg.shortage_penalty_multiple == default.shortage_penalty_multiple
    assert cfg.allocation_priority == default.allocation_priority
    assert cfg.second_approver_over_usd == 50000
    assert cfg.action_tiers["LTB_BUY"] == 2
