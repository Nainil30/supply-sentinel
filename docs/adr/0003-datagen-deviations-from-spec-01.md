# ADR 0003 — Datagen: small deviations from spec 01

- Status: accepted
- Date: 2026-10-26
- Phase: 1

## Context

Spec 01 leaves a few details open or conflicts slightly with how DuckDB and the multi-process design behave. Each choice below keeps the spec's intent and stays easy to explain.

## Decision

1. **Primary keys only, FKs checked in `validate.py`.** DuckDB foreign keys block the `UPDATE`/`DELETE` statements that scenario ops use, so referential integrity is a validation check instead.
2. **`decision_log` key is `(snapshot_id, rec_id)`**, not `rec_id` alone, so a scenario snapshot can carry its own copy of the history (`REC-H001` exists in every snapshot).
3. **`add_decision` writes SQLite.** Ops are `apply(con, **params)`; only `add_decision` also receives `state_db`, supplied by the dispatcher. Decisions are stored under the scenario's `snapshot_id`.
4. **`set_platform_phase` also deletes forecast buckets that start after the new last-ship date**, so the lifecycle-sanity rule still holds.
5. **`scale_forecast` `pct` is a percent change** (30 means x1.30). Monthly buckets are scaled when they overlap the week range.
6. **BOM `effective_to` is NULL** for every line: ORB-L1 is past last ship but its BOM still drives spares through EOSL.
7. **Demand details the spec leaves open:** S8 grows +0.5%/wk for 52 weeks then flat; every platform tapers to 40% over its last 13 weeks; history noise is rescaled to mean one so forecast bias is the only systematic error; `created_at` is fixed to the as-of date for byte-stable output.
8. **Realism fixes from review:** ORB-L1 has production history until its last ship (forecast stays zero); ORB-S16 forecast accuracy is a designed NPI profile (MAPE 25/30/40% by lag) because it has under 26 weeks of history; PO slips are stratified per supplier so worse OTD means more slips; L4/L1 use a QSFP28 cage.
9. Scenario files may not use resolver tokens (`{first_open_po:...}`) until Phase 6; the loader raises a clear error.

## Alternatives considered

| Option | Why not |
|---|---|
| DuckDB FK constraints | Break UPDATE/DELETE in ops (over-eager constraint checking) |
| Store decisions in DuckDB | Single-writer limit; spec already says SQLite |
| Leave forecast after a moved last-ship date | Op output would fail its own validation |

## Consequences

Phase 2 may tune PO slips or cover to reach 2-6 natural exceptions; anchor parts must not change. If an eval needs the original `rec_id` uniqueness, revisit item 2.
