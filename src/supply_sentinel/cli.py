"""`sentinel` command line. Every command is a stub until its phase lands."""

import tempfile
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Supply Sentinel: supply-execution copilot.", no_args_is_help=True)


def _stub(name: str, phase: int) -> None:
    typer.echo(f"'{name}' is not implemented yet (planned for Phase {phase}).")
    raise typer.Exit(code=1)


@app.command()
def datagen(
    seed: int = 42,
    out_dir: Path = Path("data/snapshots"),
    state_db: Path = Path("data/state/decisions.sqlite"),
    summary: Annotated[Path | None, typer.Option(help="Write the markdown summary here.")] = None,
    check_determinism: Annotated[
        bool, typer.Option(help="Build twice and compare table hashes.")
    ] = False,
) -> None:
    """Build the base synthetic snapshot."""
    from supply_sentinel.datagen import generator

    path = generator.build_base(seed=seed, out_dir=out_dir, state_db=state_db)
    typer.echo(f"Built {path} (seed {seed}); validation passed.")
    if check_determinism:
        first = generator.hashes_for(path)
        with tempfile.TemporaryDirectory() as tmp:
            second_path = generator.build_base(
                seed=seed, out_dir=Path(tmp), state_db=Path(tmp) / "decisions.sqlite"
            )
            second = generator.hashes_for(second_path)
        if first != second:
            diff = [t for t in first if first[t] != second.get(t)]
            typer.echo(f"Determinism check FAILED for tables: {diff}")
            raise typer.Exit(code=1)
        typer.echo("Determinism check passed: identical table hashes.")
    if summary is not None:
        typer.echo(f"Wrote {generator.write_summary(path, summary)}")


scenario_app = typer.Typer(help="Build labeled scenario snapshots.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")


@scenario_app.command("build")
def scenario_build(
    scenario_id: str,
    scenario_dir: Path = Path("evals/scenarios"),
    snapshot_dir: Path = Path("data/snapshots"),
    state_db: Path = Path("data/state/decisions.sqlite"),
) -> None:
    """Apply a scenario's perturbations to the base snapshot."""
    from supply_sentinel.datagen import scenarios

    steps = scenarios.load_perturbations(scenario_id, scenario_dir)
    path, report = scenarios.build_scenario(
        scenario_id, steps, snapshot_dir=snapshot_dir, state_db=state_db
    )
    typer.echo(f"Built {path} with {len(steps)} perturbation(s).")
    typer.echo(report.render())


@app.command()
def detect() -> None:
    """Run deterministic exception detection (no LLM)."""
    _stub("detect", 2)


@app.command()
def run(profile: str = "replay") -> None:
    """Run the full agent."""
    _stub("run", 4)


@app.command()
def approvals() -> None:
    """List recommendations waiting for approval."""
    _stub("approvals", 4)


@app.command()
def approve(rec_id: str) -> None:
    """Approve a recommendation."""
    _stub("approve", 4)


@app.command()
def reject(rec_id: str) -> None:
    """Reject a recommendation."""
    _stub("reject", 4)


@app.command()
def resume(run_id: str) -> None:
    """Resume a paused run."""
    _stub("resume", 4)


@app.command(name="eval")
def eval_(suite: str = "smoke", profile: str = "replay") -> None:
    """Run an eval suite."""
    _stub("eval", 6)


@app.command(name="serve-mcp")
def serve_mcp(transport: str = "stdio") -> None:
    """Serve the planning engine over MCP."""
    _stub("serve-mcp", 3)


@app.command()
def ui() -> None:
    """Launch the Planner Console."""
    _stub("ui", 7)
