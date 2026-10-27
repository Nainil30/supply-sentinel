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
    from supply_sentinel.evals import oracle
    from supply_sentinel.evals import scenario as eval_scenario

    parts = eval_scenario.base_parts(snapshot_dir / "base.duckdb")
    steps = scenarios.load_perturbations(
        scenario_id, scenario_dir, resolver=lambda raw: oracle.resolve(raw, parts)
    )
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


STATE_DIR_HELP = "Where checkpoints, run folders and the decision log live."


def _snapshot_path(snapshot: str) -> Path:
    p = Path(snapshot)
    return p if p.suffix == ".duckdb" else Path("data/snapshots") / f"{snapshot}.duckdb"


def _thread(state_dir: Path, thread: str | None) -> str:
    from supply_sentinel.agent import runner

    try:
        return thread or runner.latest_run(state_dir)
    except FileNotFoundError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc


def _echo_pending(pending: list[dict[str, object]], staged: dict[str, object]) -> None:
    rows = [("REC", "PART", "ACTION", "QTY", "TIER", "SOURCE", "GROUNDING", "FLAGS", "STAGED")]
    for p in pending:
        qty = "" if p["quantity"] is None else f"{float(p['quantity']):,.0f}"  # type: ignore[arg-type]
        flags = ",".join(p["flags"]) or "-"  # type: ignore[arg-type]
        rows.append(
            (str(p["rec_id"]), str(p["part_id"]), str(p["action"]), qty, str(p["tier"]),
             str(p["source"]), str(p["grounding"]), flags, str(staged.get(str(p["rec_id"]), "-")))
        )  # fmt: skip
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    for r in rows:
        typer.echo("  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip())


@app.command()
def run(
    snapshot: Annotated[str, typer.Option(help="Snapshot name or .duckdb path.")] = "base",
    profile: Annotated[str, typer.Option(help="replay, local, free-cloud or premium.")] = "replay",
    record: Annotated[bool, typer.Option(help="Record model responses to a cassette.")] = False,
    cassette: Annotated[str | None, typer.Option(help="Cassette name (default: snapshot).")] = None,
    max_exceptions: Annotated[int | None, typer.Option(help="Cap exceptions this run.")] = None,
    state_dir: Annotated[Path, typer.Option(help=STATE_DIR_HELP)] = Path("data/state"),
) -> None:
    """Run the agent until the approval gate, then print what is waiting."""
    from supply_sentinel.agent import runner
    from supply_sentinel.llm.replay import CassetteMiss
    from supply_sentinel.llm.types import ProviderError

    try:
        run_id, pending = runner.start(
            _snapshot_path(snapshot), profile, state_dir, cassette=cassette, record=record,
            max_exceptions=max_exceptions,
        )  # fmt: skip
    except (ProviderError, CassetteMiss, FileNotFoundError, KeyError) as exc:
        typer.echo(f"Run failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Run {run_id} paused at the approval gate. Thread: {run_id}")
    if not pending:
        typer.echo("Nothing needs approval.")
    else:
        _echo_pending(pending, {})
    typer.echo(
        f"Next: sentinel approve REC-... --reason '...' --thread {run_id}; then sentinel resume."
    )


approvals_app = typer.Typer(
    help="Inspect recommendations waiting for a human.", no_args_is_help=True
)
app.add_typer(approvals_app, name="approvals")


@approvals_app.command("list")
def approvals_list(
    thread: Annotated[str | None, typer.Option(help="Run id (default: latest).")] = None,
    state_dir: Annotated[Path, typer.Option(help=STATE_DIR_HELP)] = Path("data/state"),
) -> None:
    """List recommendations waiting for approval and any decisions staged for them."""
    from supply_sentinel.agent import runner

    run_id = _thread(state_dir, thread)
    h = runner.open_existing(state_dir, run_id)
    try:
        pending = runner.pending(h)
    finally:
        h.close()
    staged = {k: v.decision for k, v in runner.load_decisions(state_dir, run_id).items()}
    typer.echo(f"Run {run_id}")
    _echo_pending(pending, staged)  # type: ignore[arg-type]


def _stage(
    rec_id: str, decision: str, reason: str, qty: float | None, thread: str | None, state_dir: Path
) -> None:
    from supply_sentinel.agent import runner
    from supply_sentinel.agent.models import Decision

    run_id = _thread(state_dir, thread)
    h = runner.open_existing(state_dir, run_id)
    try:
        by_id = {p["rec_id"]: p for p in runner.pending(h)}
    finally:
        h.close()
    if rec_id not in by_id:
        typer.echo(f"{rec_id} is not waiting for approval in run {run_id}. Known: {sorted(by_id)}")
        raise typer.Exit(code=1)
    needs_reason = decision == "REJECTED" or by_id[rec_id]["tier"] == 2
    if needs_reason and not reason:
        typer.echo(f"{rec_id} needs --reason (tier 2 approvals and all rejections).")
        raise typer.Exit(code=1)
    runner.save_decision(
        state_dir, run_id,
        Decision(rec_id=rec_id, decision=decision, reason=reason, quantity=qty,  # type: ignore[arg-type]
                 decided_by=runner.decider()),
    )  # fmt: skip
    typer.echo(f"Staged {decision} for {rec_id}. Run `sentinel resume --thread {run_id}` to apply.")


@app.command()
def approve(
    rec_id: str,
    reason: Annotated[str, typer.Option(help="Required for tier 2.")] = "",
    qty: Annotated[float | None, typer.Option(help="Edit the quantity (records EDITED).")] = None,
    thread: Annotated[str | None, typer.Option(help="Run id (default: latest).")] = None,
    state_dir: Annotated[Path, typer.Option(help=STATE_DIR_HELP)] = Path("data/state"),
) -> None:
    """Stage an approval for a recommendation. The agent cannot do this; only you can."""
    _stage(rec_id, "EDITED" if qty is not None else "APPROVED", reason, qty, thread, state_dir)


@app.command()
def reject(
    rec_id: str,
    reason: Annotated[str, typer.Option(help="Why you reject it (required).")] = "",
    thread: Annotated[str | None, typer.Option(help="Run id (default: latest).")] = None,
    state_dir: Annotated[Path, typer.Option(help=STATE_DIR_HELP)] = Path("data/state"),
) -> None:
    """Stage a rejection for a recommendation."""
    _stage(rec_id, "REJECTED", reason, None, thread, state_dir)


@app.command()
def resume(
    thread: Annotated[str | None, typer.Option(help="Run id (default: latest).")] = None,
    state_dir: Annotated[Path, typer.Option(help=STATE_DIR_HELP)] = Path("data/state"),
) -> None:
    """Apply staged decisions, finalize the run and write the weekly brief."""
    from supply_sentinel.agent import runner

    run_id = _thread(state_dir, thread)
    final = runner.resume(state_dir, run_id)
    for o in final.get("outcomes", []):
        typer.echo(f"{o['rec_id']}: {o['status']}")
    for e in final.get("errors", []):
        typer.echo(f"note: {e['message']}")
    typer.echo(f"Weekly brief: {final.get('brief_path')}")


eval_app = typer.Typer(
    help="Run eval suites and build reports.", invoke_without_command=True, no_args_is_help=False
)
app.add_typer(eval_app, name="eval")
RESULTS_HELP = "Where timestamped results files live."


@eval_app.callback()
def eval_(
    ctx: typer.Context,
    suite: Annotated[str, typer.Option(help="smoke, safety or full.")] = "smoke",
    profile: Annotated[str, typer.Option(help="replay, local, free-cloud or premium.")] = "replay",
    system: Annotated[str, typer.Option(help="agent or rules (the baseline).")] = "agent",
    scenarios: Annotated[
        str | None, typer.Option(help="Comma-separated ids; overrides --suite.")
    ] = None,
    k: Annotated[int, typer.Option(help="Runs per scenario (for pass^k).")] = 1,
    record: Annotated[bool, typer.Option(help="Record model responses to cassettes.")] = False,
    judge: Annotated[bool, typer.Option(help="Score messages with the LLM judge.")] = False,
    resume: Annotated[Path | None, typer.Option(help="Continue this results file.")] = None,
    gate: Annotated[
        str | None, typer.Option(help="Enforce CI gates: replay, live or nightly.")
    ] = None,
    scenario_dir: Path = Path("evals/scenarios"),
    results_dir: Annotated[Path, typer.Option(help=RESULTS_HELP)] = Path("evals/results"),
) -> None:
    """Run an eval suite (or a subcommand such as `report`)."""
    if ctx.invoked_subcommand is not None:
        return
    import json

    from supply_sentinel.evals import report, runner
    from supply_sentinel.evals import scenario as eval_scenario

    try:
        chosen = eval_scenario.select(
            eval_scenario.load_all(scenario_dir), suite, scenarios.split(",") if scenarios else None
        )
    except (KeyError, FileNotFoundError, ValueError) as exc:
        typer.echo(f"Cannot select scenarios: {exc}")
        raise typer.Exit(code=1) from exc
    if profile == "replay" and system == "agent":
        # Only scenarios with a recorded cassette can be replayed; say which ones are skipped.
        have = {p.stem for p in Path("evals/cassettes").glob("*/*.jsonl")}
        skipped = [s.id for s in chosen if s.id not in have]
        chosen = [s for s in chosen if s.id in have]
        if skipped:
            typer.echo(f"No cassette yet for {skipped}; record with `--profile local --record`.")
    if not chosen:
        typer.echo(f"No scenarios in suite '{suite}'.")
        raise typer.Exit(code=1)
    path = runner.run_suite(
        chosen, suite=suite, system=system, profile=profile, k=k, record=record, judge=judge,
        resume=resume, results_dir=results_dir, log=typer.echo,
    )  # fmt: skip
    doc = json.loads(path.read_text(encoding="utf-8"))
    path.with_suffix(".md").write_text(report.run_markdown(path, doc), encoding="utf-8")
    summary = report.summarize(doc)
    typer.echo(f"Wrote {path} (pass rate {summary['pass_rate']:.0%} over {summary['runs']} runs)")
    if gate:
        baseline_file = results_dir / "baseline_main.json"
        base = (
            json.loads(baseline_file.read_text(encoding="utf-8"))
            if baseline_file.exists()
            else None
        )
        hard, soft = report.check_gates(doc, gate, base)
        for msg in soft:
            typer.echo(f"warning: {msg}")
        for msg in hard:
            typer.echo(f"GATE FAILED: {msg}")
        if hard:
            raise typer.Exit(code=1)


@eval_app.command("report")
def eval_report(
    results_dir: Annotated[Path, typer.Option(help=RESULTS_HELP)] = Path("evals/results"),
) -> None:
    """Write evals/results/leaderboard.md from the results files."""
    from supply_sentinel.evals import report

    docs = report.load_results(results_dir)
    if not docs:
        typer.echo(f"No results files in {results_dir}. Run `sentinel eval` first.")
        raise typer.Exit(code=1)
    out = results_dir / "leaderboard.md"
    out.write_text(report.leaderboard(docs), encoding="utf-8")
    typer.echo(f"Wrote {out} from {len(docs)} results file(s).")


@eval_app.command("calibrate")
def eval_calibrate(
    labels: Annotated[Path, typer.Option(help="Labels YAML (human or ai-reference).")] = Path(
        "evals/reference_labels/comms_labels.yaml"
    ),
    profile: Annotated[str, typer.Option(help="Profile whose `judge` role is tested.")] = "local",
    record: bool = False,
    out: Path = Path("evals/results/calibration.md"),
) -> None:
    """Compare the judge with labelled messages (per-criterion agreement, Cohen's kappa)."""
    from supply_sentinel.evals import calibration
    from supply_sentinel.llm.config import load_profile
    from supply_sentinel.llm.factory import build_router
    from supply_sentinel.observability.ledger import Ledger

    data = calibration.load_labels(labels)
    router, model_profile = build_router(profile, "calibration", Ledger(None), record=record)
    try:
        result = calibration.calibrate(data, router)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    model = load_profile(profile).roles["judge"].model if profile != "replay" else "replay"
    out.write_text(calibration.render_report(result, model), encoding="utf-8")
    typer.echo(f"kappa {result['kappa']:.2f} over {result['items']} messages; wrote {out}")


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
