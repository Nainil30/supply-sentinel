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
