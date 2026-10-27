from typer.testing import CliRunner

from supply_sentinel.cli import app

COMMANDS = [
    "datagen", "detect", "run", "approvals", "approve",
    "reject", "resume", "eval", "serve-mcp", "ui",
]  # fmt: skip


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in COMMANDS:
        assert name in result.output


def test_stub_exits_nonzero() -> None:
    result = CliRunner().invoke(app, ["detect"])
    assert result.exit_code == 1


def test_datagen_and_scenario_build_end_to_end(tmp_path):
    snaps, state = tmp_path / "snaps", tmp_path / "state.sqlite"
    summary = tmp_path / "summary.md"
    result = CliRunner().invoke(
        app,
        ["datagen", "--out-dir", str(snaps), "--state-db", str(state), "--summary", str(summary)],
    )
    assert result.exit_code == 0, result.output
    assert (snaps / "base.duckdb").exists()
    assert "Row counts" in summary.read_text(encoding="utf-8")

    scen_dir = tmp_path / "scen"
    scen_dir.mkdir()
    (scen_dir / "s1.yaml").write_text(
        "perturbations:\n  - {op: set_meta, as_of_date: '2026-10-05'}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        ["scenario", "build", "s1", "--scenario-dir", str(scen_dir),
         "--snapshot-dir", str(snaps), "--state-db", str(state)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert (snaps / "s1.duckdb").exists()


def test_scenario_build_reports_missing_file(tmp_path):
    result = CliRunner().invoke(app, ["scenario", "build", "nope", "--scenario-dir", str(tmp_path)])
    assert result.exit_code != 0
