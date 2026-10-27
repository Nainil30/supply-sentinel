# ADR 0002 — Verified versions and model providers (Phase 0)

- Status: accepted
- Date: 2026-10-26
- Phase: 0

## Context

Spec 06 assumed the GitHub Models free tier. Phase 0 verifies libraries and providers against live sources.

## Decision

- **mcp 2.3.0** (PyPI): `MCPServer` and `Client` both import from the package (checked in an isolated env). Added in Phase 3.
- **langchain-mcp-adapters 0.3.2** requires `mcp<2.0.0`, so it cannot be used with mcp v2. Plan: a thin `agent/mcp_bridge.py` using the SDK `Client` and wrapping tools as LangChain tools.
- Latest at check time: langgraph 1.2.18, langchain 1.4.7, typer 0.27.3, duckdb 2.0.0, pydantic 2.14.1.
- **GitHub Models is retired.** docs.github.com states: "As of July 30, 2026, GitHub Models has been fully retired." The `free-cloud` profile stays in `config/models.yaml` marked `status: retired` with no live model ids. Free paths are now `local` (Ollama: qwen3:8b for specialist/extractor/writer, gemma4:e4b as judge, a different family) and `replay`.
- CI consequence: `evals-live.yml` and `nightly-evals.yml` cannot use free hosted inference. CI gates run on `replay`; live evals run locally on Ollama. Specs 05/06 mention GitHub Models and need an owner revision.
- Hooks are invoked as `python3`; the path guard was run directly and blocks `.env`. They are stdlib-only, so any Python 3 works.

## Alternatives considered

| Option | Why not |
|---|---|
| Azure AI Foundry / Copilot models | Not free or not an open API; violates non-negotiable 3 |

## Consequences

Premium (BYO key) stays optional. Watch for a new free hosted tier; adopting one needs a new ADR.

## Phase 3 addendum: MCP transport and auth

- Server built with `mcp.server.MCPServer` (mcp 2.3.0). Tools are plain functions bound to one `Snapshot`; the `snap` parameter is hidden from the tool schema so a model cannot pick its data source.
- Transports: `stdio` (default, local hosts) and Streamable HTTP via `streamable_http_app()` served by uvicorn at `/mcp`, bound to `127.0.0.1`.
- Auth: no OAuth in v1. Optional static bearer token from `SENTINEL_MCP_TOKEN`, checked by a small Starlette middleware (401 otherwise). Not meant for the open internet.
- Errors: `ToolInputError` subclasses the SDK `ToolError` and `ResourceError`, so its message (with a fix hint) reaches the model. Other exceptions are reported as a generic failure.
- SDK note: `ToolAnnotations` fields are snake_case (`read_only_hint`), not the camelCase names in the spec text. `langchain-mcp-adapters` still cannot be used (needs `mcp<2`).
- The Inspector (Node, via npx) was not run by the autopilot because it downloads a package; the command is in `docs/HOSTS.md`.
