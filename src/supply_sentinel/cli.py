"""`sentinel` command line. Every command is a stub until its phase lands."""

import tempfile
from pathlib import Path
from typing import Annotated

import typer

from supply_sentinel.engine.models import ExceptionRecord

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
def detect(
    snapshot: Annotated[Path, typer.Option(help="Snapshot file to analyse.")] = Path(
        "data/snapshots/base.duckdb"
    ),
    config_dir: Path = Path("config"),
) -> None:
    """Run deterministic exception detection with the rules baseline (no LLM)."""
    from supply_sentinel.engine import loader, rules_baseline
    from supply_sentinel.engine.config import load_config
    from supply_sentinel.engine.exceptions import detect_exceptions

    cfg = load_config(config_dir)
    con = loader.open_snapshot(snapshot)
    try:
        parts = {p.part_id: p for p in loader.load_part_inputs(con)}
    finally:
        con.close()
    found = detect_exceptions(list(parts.values()), cfg)
    typer.echo(f"{snapshot.stem}: {len(found)} exception(s)")
    rows = [("ID", "SEV", "DETAIL", "BASELINE ACTION")]
    for i, exc in enumerate(found, start=1):
        rec = rules_baseline.recommend(parts[exc.part_id], exc, cfg, seq=i)
        act = rec.primary_action
        qty = f" x {act.quantity:,.0f}" if act.quantity is not None else ""
        rows.append((exc.exception_id, exc.severity.value, _detail(exc), f"{act.action_type}{qty}"))
    widths = [max(len(r[c]) for r in rows) for c in range(4)]
    for r in rows:
        typer.echo("  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip())


def _detail(exc: ExceptionRecord) -> str:
    if exc.type == "SHORTAGE":
        return (
            f"short {exc.max_shortfall_units:,.0f} from wk {exc.first_shortage_week}, "
            f"${exc.revenue_at_risk_usd:,.0f} at risk"
        )
    if exc.type == "COMMIT_GAP":
        return f"commits {exc.gap_units} below PO in weeks {exc.gap_weeks}"
    if exc.type == "EOL_NOTICE":
        return f"last-time-buy in {exc.days_to_ltb} days"
    if exc.type == "EXCESS":
        return f"excess {exc.excess_units:,.0f} units (${exc.excess_usd:,.0f})"
    return f"obsolete {exc.obsolete_units:,.0f} units (${exc.obsolete_usd:,.0f})"


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
def serve_mcp(
    transport: Annotated[str, typer.Option(help="stdio or http.")] = "stdio",
    port: int = 8765,
    host: str = "127.0.0.1",
) -> None:
    """Serve the planning engine over MCP (reads SENTINEL_DB and SENTINEL_STATE_DIR)."""
    from supply_sentinel.mcp_server.server import serve

    if transport not in ("stdio", "http"):
        raise typer.BadParameter("transport must be 'stdio' or 'http'")
    serve(transport, host=host, port=port)


@app.command()
def ui() -> None:
    """Launch the Planner Console."""
    _stub("ui", 7)
