"""Scenario files (spec 05 section 2): load, validate, resolve tokens, build the snapshot.

A scenario is ground truth written by a planner. Quantities are not typed in: they are oracle
tokens that the engine computes on the scenario's own snapshot, so labels survive regeneration.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict, Field

from supply_sentinel.datagen import scenarios as datagen_scenarios
from supply_sentinel.engine import loader
from supply_sentinel.engine.models import PartInputs
from supply_sentinel.evals import oracle

SCENARIO_DIR = Path("evals/scenarios")
SNAPSHOT_DIR = Path("data/snapshots")
SUITES = ("smoke", "safety", "full")
RUN_DATE = "2026-11-02"  # pinned run date for evals (SENTINEL_TODAY); ROB-07 ages the snapshot


class AnyOf(BaseModel):
    """`any_of` lists what a competent planner accepts. `optional` means "may be absent"."""

    model_config = ConfigDict(extra="forbid")
    any_of: list[str] = Field(default_factory=list)
    optional: bool = False


class QuantityRule(BaseModel):
    """Exactly one of `oracle`, `exact` or `range`. `tolerance_pct` applies to oracle/exact."""

    model_config = ConfigDict(extra="forbid")
    oracle: str | None = None
    exact: float | None = None
    range: tuple[float, float] | None = None
    tolerance_pct: float = 0.0


class MustNot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_cause: AnyOf | None = None
    primary_action: AnyOf | None = None  # None: no recommendation is expected for the target
    secondary_action: AnyOf | None = None
    quantity: QuantityRule | None = None
    stakeholder: AnyOf | None = None
    must_mention: list[str] = Field(default_factory=list)
    must_not: MustNot = Field(default_factory=MustNot)
    security_flag_expected: bool = False
    run_flags: list[str] = Field(default_factory=list)  # e.g. STALE_DATA
    tier: int | None = None  # minimum autonomy tier the planner expects (optional)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_id: str
    expect_exception: str | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    category: Literal["shortage", "ltb", "eo", "negative", "robustness"]
    difficulty: Literal["easy", "medium", "hard"]
    suites: list[Literal["smoke", "safety", "full"]]
    gate: Literal["hard", "soft"] | None = None  # robustness scenarios only (spec table)
    perturbations: list[dict[str, Any]] = Field(default_factory=list)
    target: Target
    expected: Expected
    notes: str = ""


def scenario_files(scenario_dir: Path = SCENARIO_DIR) -> list[Path]:
    return sorted(scenario_dir.glob("*.yaml"))


def load_scenario(path: Path) -> Scenario:
    return Scenario(**yaml.safe_load(path.read_text(encoding="utf-8")))


def load_all(scenario_dir: Path = SCENARIO_DIR) -> dict[str, Scenario]:
    out: dict[str, Scenario] = {}
    for f in scenario_files(scenario_dir):
        s = load_scenario(f)
        if s.id != f.stem:
            raise ValueError(f"{f.name}: id {s.id!r} must match the file name")
        out[s.id] = s
    return out


def select(
    scenarios: dict[str, Scenario], suite: str | None = None, ids: list[str] | None = None
) -> list[Scenario]:
    if ids:
        missing = [i for i in ids if i not in scenarios]
        if missing:
            raise KeyError(f"unknown scenario(s) {missing}; known: {sorted(scenarios)}")
        return [scenarios[i] for i in ids]
    if suite not in SUITES:
        raise KeyError(f"unknown suite {suite!r}; choose one of {SUITES}")
    return [s for s in scenarios.values() if suite in s.suites]


# ---------------------------------------------------------------- building


def base_parts(base_path: Path) -> dict[str, PartInputs]:
    con = loader.open_snapshot(base_path)
    try:
        return {p.part_id: p for p in loader.load_part_inputs(con)}
    finally:
        con.close()


def resolved_perturbations(
    scenario: Scenario, snapshot_dir: Path = SNAPSHOT_DIR
) -> list[dict[str, Any]]:
    """Perturbations with `{first_open_po:...}` style tokens replaced (from the BASE snapshot)."""
    parts = base_parts(snapshot_dir / "base.duckdb")
    resolved = oracle.resolve(scenario.perturbations, parts)
    assert isinstance(resolved, list)
    return resolved


def _fingerprint(base_path: Path, steps: list[dict[str, Any]]) -> str:
    con = duckdb.connect(str(base_path), read_only=True)
    try:
        meta = con.execute("SELECT seed, generator_version, as_of_date FROM meta").fetchone()
    finally:
        con.close()
    blob = json.dumps([list(meta or ()), steps], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_snapshot(
    scenario: Scenario,
    snapshot_dir: Path = SNAPSHOT_DIR,
    state_db: Path = Path("data/state/decisions.sqlite"),
    force: bool = False,
) -> Path:
    """Build (or reuse) data/snapshots/<id>.duckdb. Cached by base seed + perturbations."""
    base_path = snapshot_dir / "base.duckdb"
    steps = resolved_perturbations(scenario, snapshot_dir)
    key = _fingerprint(base_path, steps)
    path = snapshot_dir / f"{scenario.id}.duckdb"
    stamp = snapshot_dir / f"{scenario.id}.fingerprint"
    if not force and path.exists() and stamp.exists() and stamp.read_text().strip() == key:
        return path
    path, _ = datagen_scenarios.build_scenario(
        scenario.id, steps, snapshot_dir=snapshot_dir, state_db=state_db
    )
    stamp.write_text(key, encoding="utf-8")
    return path
