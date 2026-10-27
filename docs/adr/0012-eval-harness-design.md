# ADR-0012: Eval harness design and the data ops it needed

Status: accepted (Phase 6)

## Decisions
- **Eval mode.** The runner sets `eval_mode` and `only_parts` in settings. The approval gate then holds every proposal (tier 0 too), the graph stays paused, and the runner reads `cards`, `recommendations` and `security_flags` from the paused state. Nothing is recorded or approved. Each run gets its own copy of `decisions.sqlite`.
- **MCP in-process.** Spec says one stdio server per scenario. The agent already talks to the engine through an in-memory MCP client bound to one snapshot (ADR-0007), so a run is isolated the same way without a subprocess.
- **Independent grounding.** `scorers/grounding.py` re-runs the ledger's tool calls on the scenario snapshot and checks every number in the recommendation against those outputs and the engine's exception record. It does not import `agent/validate.py`.
- **Rec selection.** A part can carry several recommendations. The scorer takes the one for the expected exception whose primary action is accepted, else the first. `must_not` checks all of them.
- **New data ops** (`set_on_hand`, `update_po`, `add_po`, `set_cancel_window`, `add_alternate`, optional qual time on `set_alternate_status`). Base stock covers 3-10 weeks and open POs cover about 26, so the catalog situations cannot be built from the spec's 11 ops. Receipts for weeks 0-11 use supplier commits, so slips there need `set_commit` too.
- **Bug fixed.** `set_quality_hold` moved units out of `on_hand_units` and into the hold, but the engine computes usable stock as `on_hand - hold`, so a hold was counted twice. It now only raises the hold.
- **Whole-feed injection scan.** `ingest_documents` scans every part's notes; a flagged part with no exception gets a tier>=1 `DATA_ISSUE_FLAG` recommendation (needed for ROB-02).

## Consequences
Catalog deviations (e.g. EO-01 uses a 70% cut on two platforms, LTB-03 a 24-week qualification) are written in each scenario's `notes`.
