# AUTOPILOT.md — unattended build rules

Active when a prompt starts with `AUTOPILOT` (the runner `scripts/autopilot.sh` sends that) or the environment has `SENTINEL_AUTOPILOT=1`. In autopilot the owner is not watching. You build each phase end to end, make the decisions the owner would have made, and leave a clear review trail. Everything in `AGENTS.md` still applies; autopilot never weakens a non-negotiable.

## 1. How a phase runs in autopilot

1. Read `CLAUDE.md`, `AGENTS.md`, this file, the phase section of `docs/BUILD_PLAN.md`, and the specs it names.
2. Write your plan to `_private/phase-plans/phase-NN.md`: files to create or change, tests, risks. Check it against the spec yourself. Do not wait for approval.
3. Implement in small steps. Commit after each coherent step (Conventional Commits). A later session may have to resume from `git log`, so commit often.
4. Wherever the phase prompt says "for me", "I will", "show me" or "wait for approval", follow the delegation table in §3 instead.
5. Run the `phase-gate` skill. Fix failures and run it again until it passes or you hit a true blocker.
6. Append this phase's section to `_private/OWNER_REVIEW.md` (format in §4).
7. Your final message must end with exactly one line:
   - `PHASE_STATUS: DONE` when the phase gate passed and every acceptance item is met or explicitly delegated, or
   - `PHASE_STATUS: BLOCKED - <one-line reason>` when you cannot finish.

If the branch already has commits when you start, you are resuming: read `git log`, the plan file and the last phase-gate output, then continue. Do not restart from scratch.

## 2. Decide-and-record instead of ask

| Normally "ask before" | In autopilot |
|---|---|
| Adding a dependency | Allowed if it is in the AGENTS.md stack table or the spec needs it. Anything else: choose the smallest well-known option and write an ADR |
| Changing a formula, autonomy threshold or eval gate | **Not allowed.** Keep the spec value. If the spec is wrong, implement the spec, write an ADR and an OWNER_REVIEW entry explaining the issue |
| Changing a scenario `expected:` block | Draft, review with `domain-reviewer`, commit, and list it in OWNER_REVIEW. Never edit labels to match agent output |
| Secrets, pushing, tags, PRs, releases | Never. The runner script does pushes, PRs, merges and tags. `.env` is never read or written |

## 3. Owner checkpoints, delegated

| Phase | Owner checkpoint in BUILD_PLAN | What you do instead |
|---|---|---|
| 1 | Sanity-check the data | Run the `domain-reviewer` subagent on realism; fix blockers; log findings |
| 2 | Recompute the LTB example by hand | Write `scripts/check_worked_examples.py` that recomputes Examples A–C with only the standard library (`statistics.NormalDist`) and compares to the engine; run it in tests |
| 3 | Try the server from Claude Code and the Inspector; screenshots | Contract tests through the SDK `Client`; if Node is installed, run the Inspector in CLI mode against the server if available; put screenshot placeholders in `docs/HOSTS.md` |
| 5 | Rewrite the runtime skills | Keep the kit's skills; have `domain-reviewer` review them; apply only fixes that make them consistent with `config/autonomy.yaml` and the engine; log suggested personal edits for the owner |
| 6 | Approve every expected block | Draft all 40, run `domain-reviewer` on the full set, fix issues, commit; put the review table in OWNER_REVIEW |
| 6 | Label 15 messages by hand | Create `evals/reference_labels/comms_labels.yaml`, labeled by the `eval-analyst` subagent working only from the rubric (not the judge model). Mark the file `labeler: ai-reference`. The calibration report must say "provisional — AI reference labels, owner relabel pending". Leave `evals/human_labels/` empty with a README |
| 6 | Lock the baseline file | Do nothing; the runner copies the newest smoke result to `evals/results/baseline_main.json` |
| 8 | Write "What I learned" | Leave the heading with the placeholder `<!-- owner writes this -->` |
| 9 | Write the README "problem" paragraph | Draft it in first person from `docs/PROJECT_BRIEF.md`, then flag it in OWNER_REVIEW for personalization |
| 9 | GIF, hosted demo, push and tag | Prepare the demo config and a `docs/DEPLOY.md` with click-by-click steps; the runner pushes and tags; GIF and deploy stay with the owner |

## 4. `_private/OWNER_REVIEW.md` format

```
## Phase NN — <name> (YYYY-MM-DD)
Decisions made for you:
- <decision> — <why> — <file/ADR>
Verify (about 10 minutes):
- [ ] <specific thing to open or run>
Explain-back questions:
1. ...
2. ...
3. ...
```

## 5. Models, quotas and honesty

- Live LLM runs: prefer `local` (Ollama) if `ollama list` works, because it has no daily cap. If `config/models.local.yaml` exists, use its model ids for the `local` profile (it records what is installed on this machine) and merge them into `config/models.yaml` in Phase 0. Otherwise use `free-cloud` (the app reads `GITHUB_TOKEN` from `.env` itself; you never open `.env`). CI uses the Actions `GITHUB_TOKEN`, so no local token is needed for CI.
- Free-tier quotas are small. If a quota runs out mid-run, the eval runner is resumable: keep what completed, record the gap in OWNER_REVIEW with the exact command to resume, and finish the phase with what you have.
- Never fabricate results. Every eval number in docs or the README must come from a results file produced by an actual run. If a live run could not happen, say so.

## 6. Token efficiency

Every phase is a fresh session with a usage budget. Spend it on building, not reading.

- Read only the specs the phase names, once. Use Grep and ranged Reads for code you already wrote instead of re-reading whole files.
- Run tests quietly (`pytest -q`, `-x` while fixing) and read only the failure output.
- Delegate reviews to the `domain-reviewer` / `eval-analyst` subagents; their working context is discarded and only the summary comes back.
- Never paste large data, logs or generated files into your reasoning; inspect with `head`, `wc`, or targeted queries.
- Generated artifacts (snapshots, cassettes, results) are produced by commands, never typed out by you.
- Keep the phase report and OWNER_REVIEW entries short (tables, one line per item).
- LLM-heavy steps in the product (cassette recording, evals) run against Ollama when available, which costs no Claude usage.

## 7. Hard stops

End with `PHASE_STATUS: BLOCKED - …` instead of improvising when:
- a non-negotiable would have to be broken to continue,
- the same test keeps failing after three distinct fix attempts,
- a required tool or credential is missing (say which).
