# ADR-0009: Validation, grounding and tier assignment (Phase 4)

Status: accepted. This is the "ADR-0007" of the build plan (number taken by an earlier ADR).

## Decisions
- **Grounding is strict.** Every `EvidenceRef` must exist in the tool log at its path (integers and dates exact, floats within 0.5%). Every number in the action quantities, week targets, explanation, message, risks and why-not text must match a cited value. IDs and ISO dates are removed before the sweep; `$`, commas and `k`/`M` are normalized.
- **Failure ladder.** One repair call lists the problems. After that: policy or schema failure -> rules baseline with source `RULES_FALLBACK` and a visible flag; grounding failure -> keep the draft, mark `grounding.status = FAILED`, tier 2; sanity failure -> keep the draft, add a `SANITY_FLAG`.
- **Tiers come from `config/autonomy.yaml` only.** Highest tier of primary and secondary action, raised by security flags (min 1), failed grounding (2), conflict (2), and action value above `second_approver_over_usd` (2). The value is quantity x `unit_cost_usd` from a tool result, computed by the harness, never by the model.
- **Tier 2 approvals and every rejection need a reason**, enforced in the CLI and again in `finalize`.
- **`card` and `baseline`** are pseudo tool calls so the model can cite numbers from its own input.

## Deviation
The spec's "second approver" is not built; only the tier escalation is. It needs the UI (Phase 7).
