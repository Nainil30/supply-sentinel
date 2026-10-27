# ADR 0006 — Designed natural exceptions in the base snapshot

- Status: accepted
- Date: 2026-10-26
- Phase: 2

## Context

Spec 01 and the Phase 2 acceptance need 2 to 6 exceptions on the base snapshot. The Phase 1 generator only placed POs out to lead time plus a few weeks, so the engine saw a shortage past that point in 32 of 47 parts. That is the edge of the PO book, not a real gap.

## Decision

1. Open POs now cover the 26-week netting horizon (`PO_COVER_WEEKS = 25`). With that, the base had zero exceptions.
2. Three issues are designed on non-anchor parts in `catalog.py`: `P-PHY-100G` starts with 2 weeks of cover and its first PO slips 3 weeks (SHORTAGE); `P-HSINK-L` has a first commit 40% below its PO (COMMIT_GAP); `P-HSINK-S` starts with 72 weeks of cover (EXCESS). Seed 42 gives 3 exceptions; seeds 7 and 123 give 4 and 5.
3. Anchor parts are untouched. Two Phase 1 tests were relaxed for exactly these designed parts.

## Alternatives considered

| Option | Why not |
|---|---|
| Rely on random slips | Count would vary from 0 to 30 by seed |
| Make the engine only flag shortages inside lead time | Contradicts spec 02 (weeks 0 to 25) |

## Consequences

The demo has one clean example per main type. The excess part has stock but no open PO, so the baseline says PUSH_OUT_PO with nothing to push; the agent should notice (an eval signal). Revisit if scenario authors want more natural noise.
