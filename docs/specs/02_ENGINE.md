# Spec 02 — Planning Engine and Rules Baseline

Goal: every number the product shows is computed here, deterministically, with tests. The agent never computes; it calls these functions through MCP tools.

Code: `src/supply_sentinel/engine/` — `netting.py`, `exceptions.py`, `root_cause.py`, `ltb.py`, `eo.py`, `allocation.py`, `whatif.py`, `severity.py`, `rules_baseline.py`, `models.py`. All functions are pure: data in, Pydantic model out.

## 1. Definitions

| Term | Definition |
|---|---|
| `week_idx` | 0 = as-of week (Mon 2026-11-02 in base). Horizon for netting: weeks 0–25 |
| Gross requirement GR[p,w] | Σ over platforms i using part p: forecast_units[i,w] × qty_per[i,p] + weekly service demand[i] × qty_per[i,p] (service = annual spares / 52) |
| Usable on-hand | Σ locations (on_hand − quality_hold) at week 0 |
| Receipts R[p,w] | If a supplier commit row exists for (p,w): commit_units. Otherwise: open PO remaining qty with `promise_week_idx = w` |
| Projected available balance PAB[w] | PAB[−1] = usable on-hand; PAB[w] = PAB[w−1] + R[p,w] − GR[p,w] |
| Coverage (weeks) | usable on-hand ÷ mean(GR[p, 0..12]) |
| Shared part | used on ≥ 2 platforms with effective BOM lines |

Simplification (write it in ADR-0004): service spares are modeled as whole-unit replacements exploded through the BOM.

## 2. Exception detection (`exceptions.py`)

One exception per (part, type). Types and triggers:

| Type | Trigger | Key fields |
|---|---|---|
| SHORTAGE | min PAB over weeks 0–25 < 0 | first_shortage_week, max_shortfall_units, recovery_week (first week after shortage with PAB ≥ 0, or null), revenue_at_risk_usd |
| COMMIT_GAP | For weeks 0–11, commit_units < promised PO qty in that week, and the part has **no** SHORTAGE (otherwise it is evaluated as a root cause) | gap_weeks, gap_units |
| EOL_NOTICE | An `eol_notices` row with ltb_date ≥ as-of date | days_to_ltb, replacement_part_id, where_used |
| EXCESS | excess_units > max(MOQ, 4 × avg weekly GR) and excess value ≥ $5,000 (see §5) | excess_units, excess_usd, cancellable_po_qty |
| OBSOLESCENCE_RISK | All where-used platforms are EOL_ANNOUNCED or EOL, and supply exceeds lifetime need (see §5). Supersedes EXCESS for that part | obsolete_units, obsolete_usd |

Exception id format: `EXC-<TYPE3>-<part_id>` (for example `EXC-SHT-P-PSU-2000AC`).

### Severity (`severity.py`)

| Type | P1 | P2 | P3 |
|---|---|---|---|
| SHORTAGE | revenue_at_risk ≥ $250k and first_shortage_week ≤ 4 | revenue_at_risk ≥ $50k or first_shortage_week ≤ 8 | otherwise |
| COMMIT_GAP | gap in weeks 0–4 | gap in weeks 5–8 | otherwise |
| EOL_NOTICE | days_to_ltb ≤ 30 | ≤ 90 | otherwise |
| EXCESS / OBSOLESCENCE_RISK | exposure ≥ $100k | ≥ $20k | otherwise |

Revenue at risk (shortage): split max_shortfall across where-used platforms by their share of GR in the shortage window; for each platform, units impacted = shortfall share ÷ qty_per; revenue = units × ASP; sum.

## 3. Root cause by counterfactual replay (`root_cause.py`)

For a SHORTAGE, recompute PAB under single-factor counterfactuals, in this order. The first one that removes the shortage entirely is the **primary** root cause. Any other that reduces max shortfall by ≥ 25% is **contributing**.

| Order | Root cause | Counterfactual |
|---|---|---|
| 1 | QUALITY_HOLD | Release all quality-hold units into usable on-hand |
| 2 | COMMIT_GAP | Replace commit_units with the PO promised qty |
| 3 | SUPPLIER_SLIP | Move every open PO from promise week to need week |
| 4 | DEMAND_SPIKE | Use the previous forecast version for all where-used platforms |
| — | STRUCTURAL | None of the above removes the shortage |

Return the primary cause, contributing causes, and the counterfactual PAB series for each (the agent cites these).

## 4. LTB sizing (`ltb.py`) — a newsvendor problem

For a part with an EOL notice:

| Step | Formula |
|---|---|
| Lifetime need μ | Σ over where-used platforms: (production forecast from week 0 to the platform's last ship date, weekly then monthly buckets + service spares from as-of to EOSL, pro-rated) × qty_per |
| Uncertainty σ | cv × μ, where cv = max(need-weighted MAPE of where-used platforms, `cv_floor` = 0.10) |
| Overage cost Co | c − salvage + holding; salvage = `salvage_pct` (0.10) × c; holding = `holding_rate` (0.20/yr) × c × avg_years_held; avg_years_held = (years from LTB receipt to end of need) ÷ 2 |
| Underage cost Cu | (`shortage_penalty_multiple` − 1) × c; default multiple 5 (broker premium / redesign proxy), configurable per commodity |
| Critical ratio CR | Cu ÷ (Cu + Co) |
| Target Q* | μ + z·σ, z = Φ⁻¹(CR) |
| Gross LTB | ceil(Q*) |
| Net LTB | max(0, gross − usable on-hand − open PO qty) |
| Order qty | Round net up to `order_multiple`; if > 0, at least MOQ |
| Spend | order qty × c |

Return every intermediate value, so every number the agent cites is traceable.

**Bridge buy** (`compute_bridge_quantity`): when an alternate or replacement exists with status IN_QUAL or NOT_STARTED, size a buy that covers demand from week 0 through (qual_lead_time_weeks + 8 weeks cutover buffer), using the same newsvendor math on that window. If the alternate is QUALIFIED, the bridge window is 8 weeks only. `compute_ltb_quantity` returns both the full LTB and the bridge option when an alternate exists. The engine does not choose; the LTB specialist chooses and must justify.

## 5. E&O exposure (`eo.py`)

| Quantity | Formula |
|---|---|
| Supply | usable on-hand + all open PO qty |
| 52-week need | Σ GR[p, 0..51] (weekly, then monthly buckets) |
| Safety stock | 2 × average weekly GR over weeks 0–12 |
| Excess units | max(0, supply − 52-week need − safety stock) |
| Cancellable PO qty | Σ open PO qty where need_week_idx > cancel_window_weeks |
| Avoidable by cancel | min(excess units, cancellable qty) × c |
| Residual excess | excess units − min(excess units, cancellable qty) |
| Lifetime need (obsolescence) | production to last ship + service to EOSL, all where-used platforms |
| Obsolete units | max(0, supply − lifetime need) when all where-used platforms are EOL_ANNOUNCED/EOL |

Disposition hierarchy the E&O skill follows (engine reports facts for each): cancel cancellable POs → push out non-cancellable POs → disposition review (return to vendor, broker, scrap) for the residual.

## 5b. Allocation (`allocation.py`)

For a shortage on a shared part: over the shortage window (first_shortage_week to recovery_week, or to the horizon end), compute each platform's demand for the part, the available supply (usable on-hand + receipts in the window), and allocate supply in `allocation_priority` order from `config/settings.yaml` (default: SERVICE, NPI, SUSTAIN, EOL_ANNOUNCED; ties broken pro-rata by demand). Return per-platform allocated units, share and unmet units. Pure function.

`get_part_position` also returns `shortfall_rounded_to_multiple` = ceil(max_shortfall ÷ order_multiple) × order_multiple (and ≥ MOQ). `compute_eo_exposure` returns `cancel_qty_recommended` = min(excess units, cancellable qty). `compute_ltb_quantity` returns `requires_second_approver` from the autonomy config threshold.

## 6. What-if (`whatif.py`)

`simulate(part_id, *, po_slip: dict[po_id, weeks] | None, slip_all_weeks: int = 0, demand_change_pct: dict[platform_id, pct] | None, expedite: {qty, to_week} | None, release_hold_units: int = 0, allocation: dict[platform_id, share] | None)` → new PAB series, first shortage week, max shortfall, and deltas vs baseline. Pure; never writes.

## 7. Rules baseline (`rules_baseline.py`)

The baseline produces the same `Recommendation` model as the agent (see `04_AGENT.md`), with a templated message. It must be good enough to be a fair opponent.

| Exception | Baseline action | Quantity |
|---|---|---|
| SHORTAGE, primary QUALITY_HOLD | REVIEW_QUALITY_HOLD | held units needed to clear shortfall |
| SHORTAGE, primary COMMIT_GAP | ESCALATE_SUPPLIER | gap units |
| SHORTAGE, primary SUPPLIER_SLIP | PULL_IN_PO (slipped PO with the earliest need) | max shortfall |
| SHORTAGE, DEMAND_SPIKE or STRUCTURAL, first_shortage_week < lead time | EXPEDITE | max shortfall |
| SHORTAGE, DEMAND_SPIKE or STRUCTURAL, first_shortage_week ≥ lead time | PLACE_PO | max shortfall rounded up to order multiple |
| COMMIT_GAP | ESCALATE_SUPPLIER | gap units |
| EOL_NOTICE, net LTB > 0 | LTB_BUY | full LTB order qty (ignores alternates) |
| EOL_NOTICE, net LTB = 0 | MONITOR | — |
| EXCESS with cancellable qty | CANCEL_PO | min(excess, cancellable) |
| EXCESS without | PUSH_OUT_PO | residual |
| OBSOLESCENCE_RISK | DISPOSITION_REVIEW | obsolete units |

Known blind spots (by design; the evals measure them): no decision history, no alternates, no cross-exception reasoning on the same part, no allocation, no reading of text.

## 8. Worked examples (write these as tests first)

Toy fixtures live in `tests/fixtures/`; they do not depend on generated data.

**Example A — supplier slip (netting + root cause).** One platform, qty_per 1, ASP $24,000. Usable on-hand 80. GR weeks 1–6 = [30, 30, 40, 40, 30, 30] (use week_idx 1–6 for readability; week 0 has GR 0). PO-A1: 60 units, need week 3, promise week 5. PO-A2: 60 units, need = promise = week 5. No commits.

| Week | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| PAB as promised | 50 | 20 | −20 | −60 | 30 | 0 |
| PAB if PO-A1 on time | 50 | 20 | 40 | 0 | 30 | 0 |

Expect: SHORTAGE, first_shortage_week 3, max_shortfall 60, recovery_week 5, primary root cause SUPPLIER_SLIP, revenue_at_risk $1,440,000, severity P1, baseline action PULL_IN_PO for PO-A1, qty 60.

**Example B — LTB sizing.** Part P-CPLD-S4, c = $38, order multiple 50. μ = 1,200, σ = 150 (cv 0.125), avg_years_held = 2, usable on-hand 180, open PO 100.

| Value | Expected |
|---|---|
| Co = 38 − 3.80 + (0.20 × 38 × 2) | 49.40 |
| Cu = (5 − 1) × 38 | 152.00 |
| CR | 0.7547 (±0.0001) |
| z | 0.6894 (±0.001) |
| Q* | 1,303.41 (±0.5) |
| Gross LTB | 1,304 |
| Net LTB | 1,024 |
| Order qty | 1,050 |
| Spend | $39,900 |

Sensitivity check for the test: at service-level framing, Q* at CR 0.90 = 1,392.2 and at CR 0.95 = 1,446.7.

**Example C — E&O.** Part priced at c = $31 (P-FAN-80 cost). Usable on-hand 900; open POs 600, of which 200 are cancellable; 52-week need 1,100; safety stock 80.

| Value | Expected |
|---|---|
| Supply | 1,500 |
| Excess units = 1,500 − 1,100 − 80 | 320 |
| Excess value | $9,920 |
| Avoidable by cancel = min(320, 200) × 31 | $6,200 |
| Residual excess | 120 units / $3,720 |
| Severity | P3 |
| Baseline action | CANCEL_PO, qty 200 |

## 9. Property tests (hypothesis)

- Conservation: PAB[last] = usable on-hand + Σ receipts − Σ GR.
- Monotonicity: adding a receipt never lowers any PAB value; increasing Cu never lowers Q*; when CR > 0.5, increasing σ raises Q*.
- Root cause: if a fixture's only perturbation is a slip, the primary cause is SUPPLIER_SLIP.
- Determinism: same inputs → identical outputs (no randomness in the engine).
