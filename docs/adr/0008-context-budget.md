# ADR-0008: Context budget of 7,000 input tokens per call (Phase 4)

Status: accepted. This is the "ADR-0006" of the build plan (number taken by an earlier ADR).

## Context
The GitHub Models free tier allowed about 8,000 input tokens per request. The same cap is used on every profile so eval results are comparable. That tier is retired (ADR-0002), but the cap also keeps local models fast.

## Decision
- Estimate tokens as characters / 4 (no tokenizer dependency); the ledger stores the provider's real count next to our estimate.
- Each tool result is compacted to at most 800 tokens by cutting long lists from the end and marking how many items were dropped. The full result stays in the tool log, so grounding is checked against the true values.
- Before every call, `context.fit` replaces the oldest tool results by a summary (scalar fields only, ids kept) until the call is under 6,500 tokens (7,000 minus 500 headroom) and tool results total at most 4,000.
- Tool schemas count toward the budget (about 1,300 tokens for the shortage allowlist), so the running tool-result allowance is whatever is left.
- The stakeholder-comms skill body is appended only to the final-answer message (progressive disclosure), not to every turn.

## Consequences
Older evidence can be summarized away before the model cites it; the repair call then cannot cite it either. The validator reports this as ungrounded and the number goes to a human at tier 2.
