---
name: add-eval-scenario
description: Procedure for drafting or changing a Supply Sentinel eval scenario YAML (perturbations, target, expected labels with oracle quantities), verifying it builds and behaves as intended, and presenting it for owner sign-off. Use whenever a scenario in evals/scenarios/ is added or changed.
---

# Add or change an eval scenario

Scenarios are ground truth. The owner (a supply planner) decides what "right" is. Your job is to draft carefully and make review easy.

## Steps

1. **Pick the slot.** Use the ID and intent from the catalog in `docs/specs/05_EVALS.md` §3. Category and difficulty must match.
2. **Write perturbations** using only ops registered in `datagen/scenarios.py` (see `docs/specs/01_DATA.md` §5). Prefer resolver tokens (`{first_open_po:<part>}`) over hard-coded ids.
3. **Write the target and expected block.**
   - Quantities: use `{oracle: <engine key>}` whenever the right quantity is something the engine computes. Use exact numbers only for hand-built cases like EO-06.
   - Actions: `any_of` lists only actions a competent planner would accept.
   - `must_not`: the specific wrong moves this scenario exists to catch.
   - `notes`: 1–3 sentences of planner rationale.
4. **Build and verify.**
   - `uv run sentinel scenario build <ID>`
   - `uv run sentinel detect --snapshot <ID>` → the target exception fires (or does not, for negatives) with the expected type.
   - `uv run sentinel eval --system rules --scenarios <ID>` → record whether the rules baseline passes. Note it in the review table; some scenarios exist precisely because the baseline fails.
5. **Never tune labels to agent output.** Do not run the agent and then edit `expected:` to match it.
6. **Present for sign-off** in a table: ID | setup in one line | expected primary (secondary) | quantity source | must_not | baseline passes? | your confidence in the label. Wait for owner approval before committing.
7. **After approval**, commit with `test(evals): add <ID>` and append to `evals/CHANGELOG.md`: date, ID, change, approved by owner.

## Changing an existing scenario

Same steps, plus: explain what was wrong with the old label, show the evidence, and list which past results files are no longer comparable.
