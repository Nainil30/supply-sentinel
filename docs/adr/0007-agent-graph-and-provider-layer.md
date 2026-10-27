# ADR-0007: Agent graph shape and provider layer (Phase 4)

Status: accepted. Replaces the "ADR-0003" the build plan names for this decision, because 0003 is taken.

## Context
Spec 04 describes a LangGraph hub-and-spoke agent, a router, and a replay wrapper. Phase 4 wires one specialist.

## Decisions
- **Thin `ChatClient`, not a `BaseChatModel` subclass.** Our own `Msg`/`LLMResponse` types give a canonical cassette key (role, model id, messages, tool schemas). LangChain `init_chat_model` is used only inside `LiveClient`.
- **Layering:** `Router` (rate limit, concurrency, backoff, ledger) wraps `CassetteClient` (record or replay) wraps `LiveClient`. Replay never imports a provider.
- **Deterministic prompts.** Tool-call ids are rewritten to `tc1, tc2, ...`, no timestamps or run ids enter a prompt, so replay keys are stable.
- **State is plain dicts.** The SQLite checkpointer then never needs custom type registration, and a run can resume in a new process.
- **Tools go through the project's own MCP server** (in-memory SDK `Client`), filtered by each skill's `allowed-tools`. `record_recommendation` is a harness-only method, not on any allowlist.
- **Repair lives in the `validate` node**, not as a loop edge back to the specialist. Same behavior as the spec (one repair), simpler graph.
- **Node order:** `detect_exceptions` runs before `ingest_documents` because ingestion scans the notes of the detected parts.
- **Non-shortage exceptions** are listed in the weekly brief with their baseline action until Phase 5 wires the LTB and E&O specialists.
- **Stale snapshot** (> 7 days) is flagged in the brief; the DATA_ISSUE_FLAG recommendation is left to Phase 5.
- **Approval flow:** `approve`/`reject` stage decisions in `runs/<run_id>/decisions.json`; `resume` feeds them to `interrupt()`. Missing decisions stay PENDING.

## Consequences
Cassettes break if any prompt text changes; re-record with `sentinel run --profile local --record`.
