# CLAUDE.md

@AGENTS.md

## How to work in this repo (Claude Code)

You are building Supply Sentinel phase by phase from `docs/BUILD_PLAN.md`. The human (the repo owner) is a supply chain planner and data/AI engineer. He must be able to explain every module in an interview, so clarity beats cleverness.

### Phase discipline

1. Work on **one phase per session**. The phase prompt tells you which spec(s) to read.
2. Start in **plan mode**. Read the spec(s), list the files you will create or change, the tests you will write, and any open questions. Wait for approval.
3. Implement in small steps. Commit after each coherent step with a Conventional Commit message.
4. Before declaring the phase done, run the `phase-gate` skill (`.claude/skills/phase-gate/SKILL.md`). Do not skip failing checks.
5. End the session with a short report: what was built, test results, deviations from spec (with ADR links), and 3 questions the owner should be able to answer about this phase.
6. Never start the next phase on your own.

### Verification habits

- Run the code you write. A phase is not done until `uv run pytest -m "not live"` passes and the phase's acceptance checks in `docs/BUILD_PLAN.md` are met.
- For engine formulas, test against the worked examples in `docs/specs/02_ENGINE.md` before anything else.
- For library APIs that may have changed (MCP SDK v2, LangGraph, LangChain, OpenTelemetry, Streamlit, GitHub Models endpoints and model IDs), check current docs or the installed package's source before writing code. Do not rely on memory for these.

### Subagents and skills available

| Name | Type | Use it when |
|---|---|---|
| `domain-reviewer` | subagent | After engine, specialist or skill changes: checks supply chain logic against the specs |
| `eval-analyst` | subagent | After eval runs: clusters failures, proposes fixes, updates `docs/ERROR_ANALYSIS.md` |
| `phase-gate` | skill | End of every phase |
| `add-eval-scenario` | skill | Adding or changing an eval scenario |

### Hooks in place (see `.claude/settings.json`)

- After any Python file edit, ruff formats and lint-fixes it.
- Edits to `.env`, `evals/human_labels/**`, `evals/results/baseline_*.json` and `data/snapshots/**` are blocked. If you need one of these changed, ask the owner.

### Ask before

- Adding any dependency not in the AGENTS.md stack table.
- Changing a formula, a threshold in `config/autonomy.yaml`, or an eval gate.
- Changing any scenario's `expected:` block.
- Anything involving secrets, pushing to GitHub, or creating releases.

### ADRs

When you make a non-obvious design decision or deviate from a spec, add `docs/adr/NNNN-short-title.md` using `docs/adr/0000-template.md`. Keep it under 30 lines.

### Autopilot

If a prompt starts with `AUTOPILOT` (sent by `scripts/autopilot.sh`), follow `AUTOPILOT.md`. It replaces waiting for plan approval, the "Ask before" list, and the owner checkpoints with decide-and-record rules and a delegation table. It never relaxes the non-negotiables, the protected paths, or the ban on pushing and tagging.

### Commit trailer

Keep the default co-author trailer. The owner is open about using Claude Code as a pair programmer under harness controls; that is part of the story this repo tells.
