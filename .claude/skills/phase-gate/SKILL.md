---
name: phase-gate
description: End-of-phase quality gate for Supply Sentinel. Runs lint, types, tests, the replay smoke eval when available, confidentiality and docs checks, then writes the phase report. Use at the end of every build phase before declaring it done.
---

# Phase gate

Run every step. If a step fails, fix it or report it as a blocker; do not skip it.

1. `uv run ruff check .` and `uv run ruff format --check .`
2. `uv run mypy` (strict on `engine/` and `evals/`).
3. `uv run pytest -m "not live" -q --cov=supply_sentinel --cov-report=term-missing`. Engine coverage should be at least 90%; report the overall number.
4. From Phase 6 on: `uv run sentinel eval --suite smoke --profile replay`. Pass rate must match the cassette run.
5. Confidentiality: no real company, product or supplier names in the diff. If `_private/denylist.txt` exists, grep the diff for every term in it. Check no `.env`, keys or tokens are staged (`git diff --cached`).
6. Docs: every deviation from a spec has an ADR; `CHANGELOG.md` has an entry for this phase; new commands are in the README or AGENTS.md command list.
7. Acceptance: re-read the phase's acceptance list in `docs/BUILD_PLAN.md` and check each item explicitly.
8. Commits: Conventional Commit messages, no "WIP", no giant mixed commits.

## Phase report (print this at the end)

```
Phase <N> — <name>: DONE | BLOCKED
Built: <3–6 bullets>
Tests: <passed>/<total>, coverage <x>% (engine <y>%)
Evals (if applicable): <suite/profile/pass rate>
Deviations: <ADR links or "none">
Open issues: <list or "none">
Explain-back questions for the owner:
  1. <question about a design choice in this phase>
  2. <question about a formula or control>
  3. <question an interviewer might ask>
```
