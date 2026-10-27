"""`sentinel` command line. Every command is a stub until its phase lands."""

import typer

app = typer.Typer(help="Supply Sentinel: supply-execution copilot.", no_args_is_help=True)


def _stub(name: str, phase: int) -> None:
    typer.echo(f"'{name}' is not implemented yet (planned for Phase {phase}).")
    raise typer.Exit(code=1)


@app.command()
def datagen(seed: int = 42) -> None:
    """Build the base synthetic snapshot."""
    _stub("datagen", 1)


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
