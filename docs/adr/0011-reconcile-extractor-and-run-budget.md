# ADR 0011 — Reconcile node, extractor data issues and run budget

- Status: accepted
- Date: 2026-10-26
- Phase: 5

## Context

Spec 04 names a reconcile node, an extractor in `ingest_documents` and budget fallbacks, but not the exact rules. The build plan called the fallback ADR "0008"; that number is the context budget ADR, so this is 0011.

## Decision

- **Conflict** = on one part, one recommendation increases supply (PLACE_PO, EXPEDITE, PULL_IN_PO, LTB_BUY, BRIDGE_BUY) and another reduces it (CANCEL_PO, PUSH_OUT_PO). Other pairs are not conflicts.
- The reconciler may use an action allowed for either exception type. A valid result replaces both (source RECONCILED); otherwise both stay with a CONFLICT flag. Both outcomes are at least tier 2 (`conflict_min_tier`).
- Extractor mismatches (notice text vs `eol_notices`) become tier-0 DATA_ISSUE_FLAG recommendations: they record, they never act.
- `settings.yaml` `run_limits` caps LLM calls, tool calls and wall time per run. A breach raises the same error as a provider outage, so the specialist falls back to the rules baseline (RULES_FALLBACK, visible flag).

## Alternatives considered

| Option | Why not |
|---|---|
| Always escalate conflicts without an LLM | Loses the chance to find a plan that does both (pull in one PO, push out another) |
| Make data issues tier 1 | Would need a new policy rule; `autonomy.yaml` is owner-controlled |

## Consequences

The limits (80 LLM, 160 tool, 1800 s) are first guesses for the free tier; Phase 8 should tune them from ledgers. A run with more than two recommendations on one part is flagged, not merged.
