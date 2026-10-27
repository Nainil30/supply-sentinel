# ADR 0004 — Service spares as whole units, and other engine simplifications

- Status: accepted
- Date: 2026-10-26
- Phase: 2

## Context

Spec 02 asks for service spares to be modeled as whole-unit replacements exploded through the BOM. Writing the engine surfaced four more places where the spec leaves a choice.

## Decision

1. Service demand per platform is `spares_units(year) / 52` per week until EOSL, multiplied by `qty_per` like production. No part-level failure rates.
2. A monthly forecast bucket is spread evenly over the Mondays inside it (up to last ship), so the engine works on one weekly series.
3. Commits override PO quantity week by week (spec). Counterfactuals that move a PO (supplier slip, what-if) also move the matching commit quantity, otherwise the commit would hide the move. The slip counterfactual moves a PO to its need week only when it is promised later than needed.
4. Allocation carries the PAB at the start of the shortage window into the available supply, instead of the full week-0 on-hand. Same result when the shortage starts in week 0.
5. `qty_units` of a PARTIAL PO is treated as the open (remaining) quantity; the schema has no received column. EOL platforms use the EOL_ANNOUNCED allocation class. A commit-cut PO that also slips moves only its committed quantity.
6. What-if `allocation` means "share of this platform's demand that is served" (0 to 1).

## Alternatives considered

| Option | Why not |
|---|---|
| Per-part spares failure rates | No data for them; adds invented parameters |
| Use raw week-0 on-hand in allocation | Counts stock the platforms already consumed before the window |

## Consequences

Netting is easy to explain and to recompute by hand. Watch: scenario authors who slip a PO must also set the commit (`set_commit`), or the old commit week still delivers. Phase 6 scenarios should check this.
