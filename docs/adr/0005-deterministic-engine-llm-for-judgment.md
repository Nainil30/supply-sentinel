# ADR 0005 — Deterministic engine; the LLM never does arithmetic

- Status: accepted
- Date: 2026-10-26
- Phase: 2

## Context

Planning decisions turn on quantities (shortfall, LTB size, excess). LLMs make arithmetic slips, and a wrong quantity in a purchase recommendation is expensive. The build plan reserved "ADR-0001" for this decision, but ADR-0001 was used for the stack in Phase 0, so it lives here.

## Decision

All numbers come from pure functions in `engine/` (data in, Pydantic model out, no I/O). The only I/O is `engine/loader.py`. The agent chooses and explains; the grounding check in Phase 4 verifies every number against a tool result. The engine also defines the shared `Recommendation` model and a rules baseline that produces one, so the agent and the baseline are scored on the same type.

## Alternatives considered

| Option | Why not |
|---|---|
| Let the LLM compute with a calculator tool | Still lets it pick wrong inputs; harder to test |
| Put the `Recommendation` model in `agent/` | The baseline in `engine/` would import from the agent and invert the dependency order |

## Consequences

Every formula has a hand-computed test (`scripts/check_worked_examples.py` recomputes Examples A to C with the standard library). mypy runs in strict mode on `engine/`. The model carries a few agent-only fields (grounding, security flags) with neutral defaults.
