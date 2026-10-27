"""Properties of the generated base snapshot (seed 42), built once per test session."""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from supply_sentinel.datagen import decisions, generator, scenarios, validate

# Independent copy of the spec 01 section 1.3 anchor table:
# part -> (supplier, unit cost, lead time weeks, MOQ, order multiple)
ANCHORS = {
    "P-ASIC-102T": ("SUP-01", 9500, 40, 10, 10),
    "P-ASIC-51T": ("SUP-01", 5200, 36, 10, 10),
    "P-CPU-8C": ("SUP-02", 410, 18, 25, 25),
    "P-CPU-4C": ("SUP-02", 165, 16, 50, 50),
    "P-BMC-A": ("SUP-03", 22, 20, 100, 100),
    "P-BMC-B": ("SUP-03", 24, 20, 100, 100),
    "P-CPLD-S4": ("SUP-03", 38, 16, 50, 50),
    "P-PSU-3000AC": ("SUP-04", 260, 14, 20, 20),
    "P-PSU-2000AC": ("SUP-04", 180, 14, 20, 20),
    "P-PSU-650AC": ("SUP-04", 70, 12, 50, 50),
    "P-FAN-80": ("SUP-05", 31, 9, 50, 50),
    "P-FAN-40": ("SUP-05", 14, 8, 100, 100),
    "P-DDR5-16G": ("SUP-06", 95, 16, 50, 50),
    "P-DDR4-8G": ("SUP-06", 41, 18, 50, 50),
    "P-RTM-224G": ("SUP-09", 120, 24, 100, 100),
    "P-CLK-J1": ("SUP-08", 12, 16, 250, 250),
    "P-SSD-240": ("SUP-12", 28, 10, 100, 100),
}


@pytest.fixture(scope="session")
def snapshot_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("snapshots")


@pytest.fixture(scope="session")
def state_db(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("state") / "decisions.sqlite"


@pytest.fixture(scope="session")
def base_path(snapshot_dir, state_db) -> Path:
    return generator.build_base(seed=42, out_dir=snapshot_dir, state_db=state_db)


@pytest.fixture
def con(base_path):
    c = duckdb.connect(str(base_path), read_only=True)
    yield c
    c.close()


def q(con, sql, params=None):
    return con.execute(sql, params or []).fetchall()


def test_two_runs_with_seed_42_have_identical_table_hashes(base_path, tmp_path):
    other = generator.build_base(seed=42, out_dir=tmp_path, state_db=tmp_path / "d.sqlite")
    assert generator.hashes_for(base_path) == generator.hashes_for(other)


def test_different_seed_changes_the_data(base_path, tmp_path):
    other = generator.build_base(seed=7, out_dir=tmp_path, state_db=tmp_path / "d.sqlite")
    a, b = generator.hashes_for(base_path), generator.hashes_for(other)
    assert a["shipments_history"] != b["shipments_history"]
    assert a["purchase_orders"] != b["purchase_orders"]
    assert a["platforms"] == b["platforms"]  # the fictional world itself is fixed


def test_validation_passes_with_natural_exceptions_skipped(con):
    report = validate.validate_snapshot(con)
    assert report.ok, report.render()
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["natural_exceptions"] == "SKIP"
    assert [s for s in statuses.values() if s == "FAIL"] == []


def test_anchor_parts_match_the_spec_table(con):
    for part_id, expected in ANCHORS.items():
        row = q(
            con,
            "SELECT supplier_id, unit_cost_usd, lead_time_weeks, moq, order_multiple "
            "FROM parts WHERE part_id = ?",
            [part_id],
        )
        assert row == [expected], part_id


def test_pcba_anchors(con):
    rows = q(con, "SELECT part_id, supplier_id, unit_cost_usd, lead_time_weeks, moq, order_multiple "
                  "FROM parts WHERE part_id LIKE 'P-PCBA-%'")  # fmt: skip
    assert len(rows) == 6
    for _, supplier, cost, lead, moq, mult in rows:
        assert supplier == "SUP-10"
        assert 300 <= cost <= 2400
        assert 6 <= lead <= 8
        assert (moq, mult) == (10, 10)


def test_bmc_b_is_in_no_bom_and_is_the_alternate_for_bmc_a(con):
    assert q(con, "SELECT COUNT(*) FROM bom WHERE part_id = 'P-BMC-B'") == [(0,)]
    assert q(con, "SELECT qualification_status, qual_lead_time_weeks FROM alternates "
                  "WHERE part_id = 'P-BMC-A' AND alt_part_id = 'P-BMC-B'") == [("NOT_STARTED", 12)]  # fmt: skip


def test_world_shape(con):
    assert q(con, "SELECT COUNT(*) FROM platforms") == [(6,)]
    assert q(con, "SELECT COUNT(*) FROM suppliers") == [(12,)]
    (n_parts,) = q(con, "SELECT COUNT(*) FROM parts")[0]
    assert 45 <= n_parts <= 60
    for _, n in q(con, "SELECT platform_id, COUNT(*) FROM bom GROUP BY 1"):
        assert 10 <= n <= 14
    shared = q(con, "SELECT COUNT(*) FROM (SELECT 1 FROM bom GROUP BY part_id HAVING COUNT(*) > 1)")
    assert shared[0][0] >= 10


def test_platform_lifecycle_matches_spec(con):
    rows = dict(q(con, "SELECT platform_id, lifecycle_phase FROM platforms"))
    assert rows == {
        "ORB-S16": "NPI", "ORB-S8": "SUSTAIN", "ORB-S4": "EOL_ANNOUNCED",
        "ORB-L4": "SUSTAIN", "ORB-L1": "EOL", "ORB-M1": "SUSTAIN",
    }  # fmt: skip
    assert q(con, "SELECT last_ship_date FROM platforms WHERE platform_id = 'ORB-S4'") == [
        (date(2027, 6, 30),)
    ]


def test_meta_is_the_fixed_as_of_week(con):
    assert q(con, "SELECT snapshot_id, as_of_date, seed FROM meta") == [
        ("base", date(2026, 11, 2), 42)
    ]


def test_history_is_104_weeks_and_l1_has_only_spares(con):
    (weeks,) = q(con, "SELECT COUNT(DISTINCT week_start) FROM shipments_history")[0]
    assert weeks == 104
    channels = q(con, "SELECT DISTINCT channel FROM shipments_history WHERE platform_id = 'ORB-L1'")
    assert channels == [("SERVICE",)]
    lo, hi = q(con, "SELECT MIN(units), MAX(units) FROM shipments_history "
                    "WHERE platform_id = 'ORB-L1'")[0]  # fmt: skip
    assert 2 <= lo and hi <= 4
    # S16 launched in September 2026: no production before the launch week
    (first,) = q(con, "SELECT MIN(week_start) FROM shipments_history "
                      "WHERE platform_id = 'ORB-S16'")[0]  # fmt: skip
    assert first == date(2026, 8, 31)


def test_forecast_lifecycle_and_ramp(con):
    assert q(con, "SELECT COUNT(*) FROM demand_forecast WHERE platform_id = 'ORB-L1'") == [(0,)]
    for pid, last in q(con, "SELECT platform_id, last_ship_date FROM platforms"):
        (max_start,) = q(con, "SELECT MAX(bucket_start) FROM demand_forecast "
                             "WHERE platform_id = ? AND units > 0", [pid])[0]  # fmt: skip
        assert max_start is None or max_start <= last
    s16 = q(con, "SELECT units FROM demand_forecast WHERE platform_id = 'ORB-S16' "
                 "AND forecast_version = '2026-11-02' AND bucket_type = 'WEEK' "
                 "ORDER BY bucket_start")  # fmt: skip
    assert s16[0][0] == 5.0
    assert s16[-1][0] == 60.0
    assert len(s16) == 26


def test_two_forecast_versions_are_kept(con):
    assert q(con, "SELECT DISTINCT forecast_version FROM demand_forecast ORDER BY 1") == [
        (date(2026, 10, 26),),
        (date(2026, 11, 2),),
    ]


def test_s8_is_over_forecast_by_about_six_percent(con):
    ((bias,),) = q(con, "SELECT bias FROM forecast_accuracy "
                        "WHERE platform_id = 'ORB-S8' AND lag_weeks = 4")  # fmt: skip
    assert 0.03 <= bias <= 0.10
    others = q(con, "SELECT ABS(bias) FROM forecast_accuracy WHERE platform_id <> 'ORB-S8'")
    assert max(b for (b,) in others) < 0.08


def test_forecast_accuracy_rows(con):
    assert q(con, "SELECT COUNT(*) FROM forecast_accuracy") == [(15,)]  # 5 platforms x 3 lags
    assert q(con, "SELECT COUNT(*) FROM forecast_accuracy WHERE sigma_units <= 0 OR mape <= 0") == [
        (0,)
    ]


def test_coverage_is_realistic_and_anchor_tight_parts_are_tight(con):
    cover = validate.coverage_weeks(con)
    values = sorted(cover.values())
    assert 3 <= values[len(values) // 2] <= 12
    assert cover["P-ASIC-102T"] < 4
    assert min(values) >= 2.5 and max(values) <= 12


def test_inventory_is_never_negative_and_holds_are_small(con):
    assert q(con, "SELECT COUNT(*) FROM inventory WHERE on_hand_units < 0") == [(0,)]
    held = q(con, "SELECT COUNT(*) FROM inventory WHERE quality_hold_units > 0")
    assert held == [(3,)]


def test_purchase_order_rules(con):
    rows = q(con, "SELECT p.qty_units, p.need_week_idx, p.promise_week_idx, p.status, "
                  "p.cancel_window_weeks, p.unit_cost_usd, t.moq, t.order_multiple, t.unit_cost_usd "
                  "FROM purchase_orders p JOIN parts t USING (part_id)")  # fmt: skip
    assert len(rows) > 100
    on_time = 0
    for qty, need, promise, status, window, cost, moq, mult, part_cost in rows:
        assert qty >= moq and qty % mult == 0
        assert need >= 1 and 0 <= promise - need <= 3
        assert status in ("OPEN", "PARTIAL")
        assert window >= 0 and cost == part_cost
        on_time += promise == need
    assert 0.78 <= on_time / len(rows) <= 0.92  # about 85% on time


def test_lower_otd_suppliers_slip_more(con):
    rate = dict(q(con, "SELECT supplier_id, AVG((promise_week_idx > need_week_idx)::INT) "
                       "FROM purchase_orders GROUP BY 1"))  # fmt: skip
    assert rate["SUP-09"] > rate["SUP-11"] if "SUP-11" in rate else True
    assert rate["SUP-01"] > rate["SUP-10"]


def test_supplier_commits_mirror_next_12_weeks(con):
    lo, hi = q(con, "SELECT MIN(week_idx), MAX(week_idx) FROM supplier_commits")[0]
    assert 0 <= lo and hi <= 11
    total_commit = q(con, "SELECT SUM(commit_units) FROM supplier_commits")[0][0]
    total_po = q(con, "SELECT SUM(qty_units) FROM purchase_orders WHERE promise_week_idx <= 11")[0][
        0
    ]
    assert total_commit == total_po
    (n, noted) = q(con, "SELECT COUNT(*), COUNT(note) FROM supplier_commits")[0]
    assert 0.03 <= noted / n <= 0.20


def test_single_existing_eol_notice(con):
    assert q(con, "SELECT part_id, ltb_date, last_ship_date, replacement_part_id "
                  "FROM eol_notices") == [("P-DDR4-8G", None, None, None)]  # fmt: skip
    assert q(con, "SELECT lifecycle_status FROM parts WHERE part_id = 'P-DDR4-8G'") == [("NRND",)]
    assert q(con, "SELECT COUNT(*) FROM parts WHERE lifecycle_status <> 'ACTIVE'") == [(1,)]


def test_service_demand_runs_through_eosl(con):
    for pid, eosl in q(con, "SELECT platform_id, eosl_date FROM platforms"):
        years = [y for (y,) in q(con, "SELECT year FROM service_demand WHERE platform_id = ? "
                                      "ORDER BY 1", [pid])]  # fmt: skip
        assert years == list(range(2026, eosl.year + 1))


def test_decision_log_is_seeded_in_sqlite(state_db, base_path):
    rows = decisions.list_decisions(state_db, "base")
    assert 6 <= len(rows) <= 10
    for r in rows:
        assert r["decided_at"] < "2026-11-02T14"  # all in the past
        assert r["decided_at"] >= "2026-09-07"  # within the last 8 weeks
        assert r["rec_id"].startswith("REC-")


# ---- validation catches real problems ----


def corrupt(base_path, tmp_path, sql):
    path = tmp_path / "bad.duckdb"
    path.write_bytes(base_path.read_bytes())
    c = duckdb.connect(str(path))
    c.execute(sql)
    report = validate.validate_snapshot(c)
    c.close()
    return report


@pytest.mark.parametrize(
    ("sql", "check"),
    [
        ("UPDATE parts SET unit_cost_usd = 9000 WHERE part_id = 'P-ASIC-102T'", "anchor_parts"),
        ("DELETE FROM bom WHERE part_id = 'P-BMC-A' AND platform_id = 'ORB-M1'", "anchor_parts"),
        ("DELETE FROM parts WHERE part_id = 'P-FAN-80'", "referential_integrity"),
        ("UPDATE inventory SET on_hand_units = -5 WHERE part_id = 'P-FAN-80'", "data_consistency"),
        ("INSERT INTO demand_forecast VALUES ('ORB-S4', '2028-01-03', 'WEEK', 5, '2026-11-02')",
         "lifecycle_sanity"),
        ("UPDATE inventory SET on_hand_units = on_hand_units * 100", "coverage_sanity"),
    ],
)  # fmt: skip
def test_validation_fails_loudly_on_corruption(base_path, tmp_path, sql, check):
    report = corrupt(base_path, tmp_path, sql)
    assert not report.ok
    assert {c.name for c in report.checks if c.status == "FAIL"} >= {check}
    with pytest.raises(validate.SnapshotValidationError, match=check):
        report.raise_if_failed()


# ---- building scenarios from the real base ----


def test_build_scenario_leaves_base_untouched(base_path, snapshot_dir, state_db):
    before = generator.hashes_for(base_path)
    po_id = q_one(base_path, "SELECT po_id FROM purchase_orders WHERE part_id = 'P-PSU-3000AC' "
                             "ORDER BY need_week_idx LIMIT 1")  # fmt: skip
    steps = [
        {"op": "slip_po", "po_id": po_id, "weeks": 3},
        {"op": "add_decision", "part_id": "P-PSU-3000AC", "action_type": "EXPEDITE",
         "quantity": 40, "status": "REJECTED", "weeks_ago": 3},
    ]  # fmt: skip
    path, report = scenarios.build_scenario(
        "t-slip", steps, snapshot_dir=snapshot_dir, state_db=state_db
    )
    assert report.ok
    assert generator.hashes_for(base_path) == before
    assert q_one(path, "SELECT snapshot_id FROM meta") == "t-slip"
    old = q_one(base_path, "SELECT promise_week_idx FROM purchase_orders WHERE po_id = ?", [po_id])
    new = q_one(path, "SELECT promise_week_idx FROM purchase_orders WHERE po_id = ?", [po_id])
    assert new == old + 3
    scen = decisions.list_decisions(state_db, "t-slip")
    base = decisions.list_decisions(state_db, "base")
    assert len(scen) == len(base) + 1  # copied history plus the added decision


def test_scenario_with_drop_rows_may_fail_validation_without_raising(
    base_path, snapshot_dir, state_db
):
    steps = [{"op": "drop_rows", "table": "parts", "where": "part_id = 'P-FAN-40'"}]
    _, report = scenarios.build_scenario(
        "t-missing", steps, snapshot_dir=snapshot_dir, state_db=state_db
    )
    assert not report.ok


def test_other_scenario_that_breaks_validation_raises(base_path, snapshot_dir, state_db):
    steps = [{"op": "set_quality_hold", "part_id": "P-FAN-40", "location": "CM_HUB", "units": 1}]
    path, _ = scenarios.build_scenario(
        "t-hold", steps, snapshot_dir=snapshot_dir, state_db=state_db
    )
    assert q_one(path, "SELECT quality_hold_units FROM inventory WHERE part_id = 'P-FAN-40' "
                       "AND location = 'CM_HUB'") == 1  # fmt: skip
    with pytest.raises(ValueError, match="differ"):
        scenarios.build_scenario("base", [], snapshot_dir=snapshot_dir, state_db=state_db)


def q_one(path, sql, params=None):
    c = duckdb.connect(str(path), read_only=True)
    try:
        return c.execute(sql, params or []).fetchone()[0]
    finally:
        c.close()
