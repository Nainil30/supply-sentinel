# ADR 0010 — Memory without a vector store

- Status: accepted
- Date: 2026-10-26
- Phase: 5

## Context

Spec 04 §7 asks for an ADR on agent memory. The build plan called it ADR-0005, but 0005 was used for the deterministic engine, so it lives here. The agent needs to avoid repeating last week's action and to learn from rejections. All the data is structured (parts, POs, decisions).

## Decision

Four kinds of memory, none of them semantic search: working memory is graph state; durable execution is the SQLite checkpointer; long-term memory is the `decision_log` table, read through `get_decision_history`; procedural memory is the Agent Skills in `skills/`. The harness pre-fetches the last 3 decisions for the part and shows them to each specialist as evidence id `history`.

## Alternatives considered

| Option | Why not |
|---|---|
| Vector store of past decisions | Decisions are rows with part id, action and status; a SQL lookup is exact, a similarity search is not. Adds a dependency (non-negotiable 7) |
| Let the model call the history tool itself | It sometimes skips it. A harness pre-fetch is deterministic and costs one tool call |

## Consequences

History is only as good as the `decision_log`. If a part has no history the prompt line is omitted, so cassettes stay small. Watch the scenarios where a planner rejected an action last week.
