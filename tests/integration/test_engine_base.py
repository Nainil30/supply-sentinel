"""The engine on the generated base snapshot (seed 42): natural exceptions and `sentinel detect`."""

import pytest
from typer.testing import CliRunner

from supply_sentinel.cli import app
from supply_sentinel.datagen import generator
from supply_sentinel.engine import loader, rules_baseline
from supply_sentinel.engine.config import load_config
from supply_sentinel.engine.exceptions import detect_exceptions
from supply_sentinel.engine.models import ActionType, ExceptionType


@pytest.fixture(scope="module")
def base_path(tmp_path_factory):
    d = tmp_path_factory.mktemp("engine_base")
    return generator.build_base(seed=42, out_dir=d, state_db=d / "decisions.sqlite")


@pytest.fixture(scope="module")
def parts(base_path):
    con = loader.open_snapshot(base_path)
    try:
        return {p.part_id: p for p in loader.load_part_inputs(con)}
    finally:
        con.close()


def test_base_has_two_to_six_natural_exceptions(parts):
    found = detect_exceptions(list(parts.values()), load_config())
    assert 2 <= len(found) <= 6
    assert {e.type for e in found} >= {ExceptionType.SHORTAGE}


def test_anchor_parts_have_no_natural_shortage(parts):
    """Scenarios build on the anchors, so the base must leave them clean."""
    found = detect_exceptions(list(parts.values()), load_config())
    anchors = {p for p in parts if p.startswith(("P-ASIC-", "P-CPLD", "P-BMC", "P-FAN-80"))}
    assert not [e for e in found if e.part_id in anchors and e.type is ExceptionType.SHORTAGE]


def test_every_natural_exception_gets_a_baseline_recommendation(parts):
    cfg = load_config()
    for i, exc in enumerate(detect_exceptions(list(parts.values()), cfg), start=1):
        rec = rules_baseline.recommend(parts[exc.part_id], exc, cfg, seq=i)
        assert rec.exception_id == exc.exception_id
        assert rec.primary_action.action_type in ActionType


def test_detection_is_repeatable(parts):
    cfg = load_config()
    first = detect_exceptions(list(parts.values()), cfg)
    second = detect_exceptions(list(parts.values()), cfg)
    assert [e.model_dump() for e in first] == [e.model_dump() for e in second]


def test_sentinel_detect_prints_table(base_path):
    result = CliRunner().invoke(app, ["detect", "--snapshot", str(base_path)])
    assert result.exit_code == 0, result.output
    assert "exception(s)" in result.output
    assert "BASELINE ACTION" in result.output
    assert "EXC-SHT-" in result.output
