# Spec 01 — Synthetic Data and Scenario Injection

Goal: a seeded, realistic, internally consistent supply dataset for a fictional data center networking OEM, plus a way to inject labeled perturbations for evals.

Code: `src/supply_sentinel/datagen/` (`catalog.py`, `generator.py`, `scenarios.py`, `validate.py`). CLI: `sentinel datagen`, `sentinel scenario build <id>`.

## 1. Fictional world

- Company: **Orbis Networks** (fictional). Not modeled on any real company's products or naming.
- As-of date of the base snapshot: **Monday 2026-11-02**. `week_idx = 0` is that week.
- History: 104 weeks of shipments. Forecast horizon: 26 weekly buckets, then monthly buckets out to each platform's last-ship date (needed for LTB).
- Seed: default `42`. Same seed → byte-identical DuckDB contents.

### 1.1 Platforms (exactly six)

| platform_id | Description | Lifecycle phase | Launch | Last ship | End of service (EOSL) | ASP (USD) | Weekly demand pattern |
|---|---|---|---|---|---|---|---|
| ORB-S16 | Spine, 64×1.6T, 102.4T switch ASIC | NPI | 2026-09 | 2031-12 | 2036-12 | 78,000 | Ramp 5 → 60/wk over 26 wks |
| ORB-S8 | Spine, 64×800G, 51.2T ASIC | SUSTAIN | 2024-03 | 2029-06 | 2034-06 | 52,000 | ~80/wk, +0.5%/wk trend, noise |
| ORB-S4 | Spine, 64×400G, 25.6T ASIC | EOL_ANNOUNCED | 2021-02 | 2027-06-30 | 2032-06-30 | 31,000 | ~40/wk declining to ~20 by last ship |
| ORB-L4 | Leaf, 48×100G + 8×400G, 12.8T ASIC | SUSTAIN | 2023-05 | 2029-12 | 2034-12 | 24,000 | ~120/wk, quarter-end spikes |
| ORB-L1 | Leaf, 48×25G + 8×100G, 3.2T ASIC | EOL (service only) | 2019-04 | 2026-06-30 | 2031-06-30 | 11,000 | 0 production; spares 2–4/wk |
| ORB-M1 | Management switch, 48×1G + 4×10G | SUSTAIN | 2022-01 | 2030-06 | 2035-06 | 4,500 | ~60/wk, stable |

### 1.2 Suppliers (twelve, all invented)

| supplier_id | Name | Commodity | Region | On-time delivery (OTD) | Risk tier |
|---|---|---|---|---|---|
| SUP-01 | Silicore Semiconductor | Switch ASICs | APAC | 0.88 | HIGH (single source) |
| SUP-02 | Arvane Micro | CPU modules | NA | 0.93 | MEDIUM |
| SUP-03 | Kestrel Logic | BMC, CPLD | APAC | 0.90 | MEDIUM |
| SUP-04 | Tervan Power | Power supplies | APAC | 0.91 | MEDIUM |
| SUP-05 | Kaldor Thermal | Fans | APAC | 0.95 | LOW |
| SUP-06 | Meridian Memory | DDR modules | APAC | 0.89 | HIGH (allocation-prone) |
| SUP-07 | Halvex Interconnect | Cages, connectors | APAC | 0.94 | LOW |
| SUP-08 | Lumora Timing | Clock/jitter ICs | EMEA | 0.92 | MEDIUM |
| SUP-09 | Brightpath Photonics | Retimers, PHYs | NA | 0.87 | HIGH |
| SUP-10 | Pacifica Contract Mfg | PCBA, final assembly | APAC | 0.96 | LOW |
| SUP-11 | Norrland Metals | Chassis, sheet metal | EMEA | 0.97 | LOW |
| SUP-12 | Quillon Storage | Boot SSD/eMMC | APAC | 0.93 | MEDIUM |

### 1.3 Parts and BOM

Generate 45–60 parts. Each platform has a single-level BOM of 10–14 component parts (the PCBA and final assembly are done at the contract manufacturer, so model them as one `P-PCBA-<platform>` part each). At least 10 parts must be shared across two or more platforms.

**Anchor parts** (must exist exactly as below; scenarios and tests depend on them):

| part_id | Description | Supplier | Unit cost (USD) | Lead time (wks) | MOQ / multiple | Used on (qty_per) | Notes |
|---|---|---|---|---|---|---|---|
| P-ASIC-102T | 102.4T switch ASIC | SUP-01 | 9,500 | 40 | 10 / 10 | S16 (1) | NPI ramp risk |
| P-ASIC-51T | 51.2T switch ASIC | SUP-01 | 5,200 | 36 | 10 / 10 | S8 (1) | |
| P-CPU-8C | 8-core x86 CPU module | SUP-02 | 410 | 18 | 25 / 25 | S16, S8, S4 (1 each) | Shared |
| P-CPU-4C | 4-core x86 CPU module | SUP-02 | 165 | 16 | 50 / 50 | L4, L1, M1 (1 each) | Shared |
| P-BMC-A | Baseboard mgmt controller | SUP-03 | 22 | 20 | 100 / 100 | all six (1 each) | Shared by all; EOL target |
| P-BMC-B | Next-gen BMC (replacement for P-BMC-A) | SUP-03 | 24 | 20 | 100 / 100 | none yet | Alternate, qualification 12 wks |
| P-CPLD-S4 | Board CPLD unique to S4 | SUP-03 | 38 | 16 | 50 / 50 | S4 (1) | Canonical LTB example |
| P-PSU-3000AC | 3000W AC PSU | SUP-04 | 260 | 14 | 20 / 20 | S16, S8 (2 each) | Shared |
| P-PSU-2000AC | 2000W AC PSU | SUP-04 | 180 | 14 | 20 / 20 | S4, L4 (2 each) | Shared |
| P-PSU-650AC | 650W AC PSU | SUP-04 | 70 | 12 | 50 / 50 | L1, M1 (1 each) | |
| P-FAN-80 | 80mm fan module | SUP-05 | 31 | 9 | 50 / 50 | S16 (6), S8 (6), S4 (5) | Canonical E&O example cost |
| P-FAN-40 | 40mm fan module | SUP-05 | 14 | 8 | 100 / 100 | L4 (4), L1 (3), M1 (2) | E&O scenarios (L1 service tail) |
| P-DDR5-16G | DDR5 16GB module | SUP-06 | 95 | 16 | 50 / 50 | S16, S8 (2 each) | |
| P-DDR4-8G | DDR4 8GB module | SUP-06 | 41 | 18 | 50 / 50 | S4, L4, L1, M1 (1 each) | Obsolescence risk |
| P-RTM-224G | 224G retimer | SUP-09 | 120 | 24 | 100 / 100 | S16 (8) | |
| P-CLK-J1 | Jitter-cleaner clock IC | SUP-08 | 12 | 16 | 250 / 250 | S16, S8, S4, L4 (1 each) | Shared |
| P-SSD-240 | 240GB boot SSD | SUP-12 | 28 | 10 | 100 / 100 | S16, S8, S4, L4 (1 each) | |
| P-PCBA-<platform> | Main board assembly, one per platform | SUP-10 | 300–2,400 | 6–8 | 10 / 10 | own platform (1) | |

Fill the rest of each BOM with realistic generic parts (connector cages, chassis, heatsinks, PHYs, power sequencing ICs, eMMC) so each platform has 10–14 lines.

## 2. Tables (DuckDB)

One DuckDB file per snapshot: `data/snapshots/<snapshot_id>.duckdb`. The base snapshot id is `base`. Snapshots are written once by the generator and then opened **read-only** by every other process (DuckDB allows many read-only readers but only one writer process). Mutable state (decisions, agent checkpoints) lives in SQLite under `data/state/`.

| Table | Columns (type) | Notes |
|---|---|---|
| `meta` | snapshot_id VARCHAR, as_of_date DATE, seed INT, generator_version VARCHAR, created_at TIMESTAMP | One row |
| `platforms` | platform_id PK, description, lifecycle_phase ENUM(NPI, SUSTAIN, EOL_ANNOUNCED, EOL), launch_date, last_ship_date, eosl_date, asp_usd DOUBLE | |
| `suppliers` | supplier_id PK, name, commodity, region, otd_rate DOUBLE, risk_tier ENUM(LOW, MEDIUM, HIGH) | |
| `parts` | part_id PK, description, commodity, supplier_id FK, unit_cost_usd, lead_time_weeks INT, moq INT, order_multiple INT, lifecycle_status ENUM(ACTIVE, NRND, EOL_NOTICE, OBSOLETE), shelf_life_months INT NULL | NRND = not recommended for new designs |
| `bom` | platform_id FK, part_id FK, qty_per INT, effective_from DATE, effective_to DATE NULL | Single level |
| `alternates` | part_id FK, alt_part_id FK, qualification_status ENUM(QUALIFIED, IN_QUAL, NOT_STARTED), qual_lead_time_weeks INT, notes | |
| `shipments_history` | platform_id, week_start DATE, units INT | 104 weeks; production + service combined, with a `channel` column ENUM(PRODUCTION, SERVICE) |
| `demand_forecast` | platform_id, bucket_start DATE, bucket_type ENUM(WEEK, MONTH), units DOUBLE, forecast_version DATE | Keep current version and the previous week's version (for "what changed") |
| `service_demand` | platform_id, year INT, spares_units INT | Annual spares requirement through EOSL |
| `inventory` | part_id, location ENUM(CM_HUB, SERVICE_DEPOT), on_hand_units INT, quality_hold_units INT | |
| `purchase_orders` | po_id PK, part_id, supplier_id, qty_units INT, need_week_idx INT, promise_week_idx INT, status ENUM(OPEN, PARTIAL, CLOSED), cancel_window_weeks INT, unit_cost_usd | A PO is cancellable only if `need_week_idx > cancel_window_weeks` |
| `supplier_commits` | part_id, supplier_id, week_idx INT, commit_units INT, note TEXT | `note` is free text and is **untrusted** |
| `eol_notices` | notice_id PK, part_id, supplier_id, notice_date, ltb_date, last_ship_date, replacement_part_id NULL, raw_text TEXT | `raw_text` is **untrusted** |
| `decision_log` (**SQLite**, not DuckDB) | rec_id PK, snapshot_id, exception_id, part_id, action_type, quantity INT NULL, status ENUM(PENDING, APPROVED, EDITED, REJECTED), decided_by, decided_at, rationale, run_id | Lives in `data/state/decisions.sqlite` because the MCP server, agent and UI all write to it from different processes; DuckDB snapshots are opened read-only by everyone. Seed 6–10 historical decisions per snapshot so memory matters |
| `forecast_accuracy` | platform_id, lag_weeks INT, mape DOUBLE, bias DOUBLE, sigma_units DOUBLE | Computed by the generator from history vs archived forecasts |

## 3. Generation rules

- **Demand.** Build each platform's weekly history from its pattern in 1.1 with multiplicative noise (lognormal, sigma 0.12–0.20 by platform) and quarter-end lift for L4. Forecast = smoothed history projected forward, with deliberate bias for one platform (S8 over-forecasts ~6%) so forecast accuracy is non-trivial.
- **Gross requirements** are not stored; the engine computes them from forecast × qty_per.
- **Inventory.** Set on-hand so most parts have 3–10 weeks of coverage. A few parts (listed in scenarios) start tight.
- **Purchase orders.** For each part, create open POs that roughly cover lead-time demand, staggered weekly or bi-weekly. About 85% have `promise_week_idx == need_week_idx`; the rest slip 1–3 weeks, weighted by supplier OTD.
- **Supplier commits.** Mirror the open POs over the next 12 weeks. About 10% of commit rows have short natural-language notes (for example "wafer allocation tight, partial ship", "carrier capacity constrained"). Notes are plain business text in the base snapshot.
- **EOL notices.** The base snapshot contains exactly one existing notice (P-DDR4-8G, NRND status, no LTB date yet). Other notices come from scenarios.
- **Decision log.** Seed 6–10 plausible historical decisions within the last 8 weeks.
- **Consistency.** No negative on-hand. BOM effective dates are consistent with platform launch and last ship dates. ORB-L1 has zero production forecast. All costs are positive. Every part in a BOM exists in `parts`.

## 4. Validation (`datagen/validate.py`)

Run after every generation and every scenario build. Fail loudly with a readable report.

| Check | Rule |
|---|---|
| Referential integrity | All FKs resolve |
| Determinism | Re-running with the same seed yields identical table hashes |
| Coverage sanity | Median weeks of coverage across parts between 3 and 12 |
| Lifecycle sanity | No production forecast after a platform's last ship date |
| Anchor parts | Every anchor part exists with the exact attributes in 1.3 |
| Natural exceptions | Base snapshot has between 2 and 6 natural exceptions so the demo is neither empty nor noisy (enable this check in Phase 2, once the engine exists) |

## 5. Scenario injection (`datagen/scenarios.py`)

A scenario is the base snapshot plus a list of perturbations, materialized as `data/snapshots/<scenario_id>.duckdb`. The scenario YAML format and labels are defined in `05_EVALS.md`; this section defines the perturbation operations.

| op | Parameters | Effect |
|---|---|---|
| `slip_po` | po_id, weeks | Moves `promise_week_idx` later |
| `scale_forecast` | platform_id, from_week, to_week, pct | Multiplies current forecast; writes the old values as the previous version |
| `set_quality_hold` | part_id, location, units | Moves on-hand units into quality hold |
| `set_commit` | part_id, week_idx, commit_units, note | Overrides a commit row and its note |
| `add_eol_notice` | part_id, notice_date, ltb_date, last_ship_date, replacement_part_id, raw_text | Inserts a notice; sets part status to EOL_NOTICE |
| `set_alternate_status` | part_id, alt_part_id, status | Changes qualification status |
| `set_platform_phase` | platform_id, phase, last_ship_date | Changes lifecycle |
| `add_decision` | part_id, action_type, quantity, status, weeks_ago | Inserts history for memory scenarios |
| `drop_rows` | table, where | Removes data (missing-data scenarios) |
| `set_meta` | as_of_date | Makes a snapshot stale |
| `inject_text` | table, row_key, field, text | Writes adversarial text into an untrusted field |

Each op is a small pure function `apply(con, **params) -> None` plus an entry in a registry, with a unit test.

## 6. Committed sample

Commit `data/sample/base_summary.md` (row counts, coverage histogram as a table, list of natural exceptions) so a reader can see the data without running anything. Do not commit `.duckdb` files.
