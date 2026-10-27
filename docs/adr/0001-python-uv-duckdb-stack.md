# ADR 0001 — Python 3.12, uv and DuckDB as the base stack

- Status: accepted
- Date: 2026-10-26
- Phase: 0

## Context

The project must be free to run, reproducible, and explainable by one person. Data is structured and small (one file per snapshot).

## Decision

Python 3.12 pinned to `>=3.12,<3.13`, managed with uv (`uv.lock` committed). DuckDB holds each snapshot. Only Phase 0–2 dependencies are installed now (typer, pydantic, pyyaml, duckdb, numpy); MCP, LangGraph and UI packages are added in their phases.

## Alternatives considered

| Option | Why not |
|---|---|
| Postgres | Needs a server; breaks "free and offline" |
| pip + requirements.txt | No lockfile or env management |

## Consequences

Snapshots are single files that are easy to hash and diff. Watch: DuckDB 2.x API changes in Phase 1.
