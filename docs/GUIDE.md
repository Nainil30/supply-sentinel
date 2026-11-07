# Supply Sentinel: the complete guide

One place to look things up: what each part does, where it lives, how to run it, and what is left to do.
The README is the pitch; this file is the manual.

## 1. What it is, in one paragraph

Every week a deterministic **planning engine** nets demand against supply for a fictional networking OEM
("Orbis Networks", synthetic data) and finds exceptions: shortages, commit gaps, end-of-life notices and
excess/obsolete inventory. An **agent** (one orchestrator, three specialists) investigates each exception
through **MCP tools**, picks an action with exact quantities, drafts a stakeholder message, and stops at a
**human approval gate**. The LLM never does arithmetic: every number comes from an engine call and is
re-checked by a grounding scorer. Quality is measured by **40 labeled eval scenarios** against a plain
**rules baseline**.

## 2. How one run flows

| Step | What happens | Code |
|---|---|---|
| 1. Intake | Load the snapshot, run detection, cap at 12 exceptions | `agent/nodes/intake.py`, `engine/` |
| 2. Extract | Read untrusted supplier/EOL text into structured facts; flag injection | `agent/nodes/extract.py`, `mcp_server/untrusted.py` |
| 3. Specialists | Shortage, LTB and E&O specialists call tools and draft a recommendation (JSON schema bound) | `agent/specialists/` |
| 4. Reconcile | If two recommendations on one part conflict, combine them (always tier 2) | `agent/nodes/reconcile.py` |
| 5. Validate | Schema, allowed actions, grounding of every number, policy tier | `agent/validate.py`, `agent/policy.py` |
| 6. Gate | `interrupt()` pauses the LangGraph thread; recommendations are PENDING | `agent/graph.py` |
| 7. Human | You approve or reject (CLI or console); decisions are staged in `decisions.json` | `cli.py`, `app/pages/approvals.py` |
| 8. Resume | Apply decisions, finalize, write the weekly brief | `agent/nodes/review.py`, `agent/nodes/brief.py` |

If a specialist fails (model error, timeout, budget), it falls back to the rules baseline and the
recommendation carries a visible `RULES_FALLBACK` flag. Every event goes to the run ledger (JSONL) and
OpenTelemetry spans.

## 3. Where everything lives

| Path | What |
|---|---|
| `src/supply_sentinel/datagen/` | Seeded synthetic data generator (DuckDB) and scenario injection |
| `src/supply_sentinel/engine/` | Netting, exceptions, root cause, LTB sizing, E&O, what-if, rules baseline. Pure functions |
| `src/supply_sentinel/mcp_server/` | 15 MCP tools: 14 read-only, 1 write (`record_recommendation`, PENDING only), resources, prompts |
| `src/supply_sentinel/agent/` | LangGraph graph, nodes, specialists, validation, policy, MCP bridge |
| `src/supply_sentinel/llm/` | Provider layer (Ollama, OpenAI-compatible/Gemini, Anthropic), replay cassettes |
| `src/supply_sentinel/evals/` | Eval runner, oracle, scorers, LLM judge, calibration, leaderboard report |
| `src/supply_sentinel/observability/` | Run ledger and tracing |
| `src/supply_sentinel/app/` | Streamlit Planner Console (8 pages) |
| `src/supply_sentinel/cli.py` | The `sentinel` command |
| `skills/` | Domain playbooks the agent loads: shortage triage, LTB sizing, E&O disposition, stakeholder comms |
| `config/` | Settings, model profiles, autonomy tiers, injection patterns (section 6) |
| `evals/scenarios/` | 40 scenario files (SHT, LTB, EO, NEG, ROB) |
| `evals/results/` | Timestamped results, `leaderboard.md`, `calibration.md`, locked `baseline_main.json` |
| `evals/cassettes/` | Recorded model responses for the `replay` profile |
| `evals/human_labels/`, `evals/reference_labels/` | Judge calibration labels (human, AI reference) |
| `docs/` | Brief, build plan, specs, ADRs, architecture, threat model, error analysis, deploy, demo script |
| `tests/` | `unit/`, `integration/`, fixtures; network tests are marked `live` |
| `hosted_app.py` | Entry point for the hosted demo (replay only, no secrets) |

## 4. Command cheat sheet

```bash
uv sync                                        # install everything
uv run sentinel datagen --seed 42              # build data/snapshots/base.duckdb
uv run sentinel datagen --check-determinism    # same seed, same data
uv run sentinel detect                         # exceptions from the engine, no LLM
uv run sentinel run --profile replay           # agent run up to the approval gate
uv run sentinel approvals                      # what is waiting for you
uv run sentinel approve REC-... --reason "..." --thread <thread>
uv run sentinel reject  REC-... --reason "..." --thread <thread>
uv run sentinel resume --thread <thread>       # apply decisions, write the brief
uv run sentinel ui                             # Planner Console on http://localhost:8501
uv run sentinel serve-mcp --transport stdio    # engine as an MCP server
uv run sentinel eval --suite smoke --profile replay
uv run sentinel eval --suite full --system rules        # the baseline
uv run sentinel eval report                    # rebuild evals/results/leaderboard.md
uv run sentinel eval calibrate --labels evals/human_labels/comms_labels.yaml
uv run pytest -m "not live"                    # all offline tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
docker compose up                              # console in Docker (replay)
```

Useful `eval` flags: `--scenarios SHT-01,EO-01`, `--k 3` (pass^k), `--judge`, `--record`, `--resume <file>`,
`--gate replay|live|nightly`.

## 5. Run profiles and keys

| Profile | Needs | Use it for |
|---|---|---|
| `replay` | nothing | CI, the demo, quick checks. Replays recorded responses; proves plumbing, not model quality |
| `local` | Ollama with `qwen3:8b` and `gemma4:e4b` (`config/models.local.yaml`) | Real runs for free. On this laptop's CPU, about 20 to 28 minutes per scenario |
| `free-cloud` | `GEMINI_API_KEY` in `.env` | Gemini free tier: 20 requests per day, demos only (ADR-0014) |
| `premium` | your own key (e.g. `ANTHROPIC_API_KEY`) | Optional, never required |

Keys go in `.env` (copy `.env.example`; it is gitignored). Claude Code is blocked from reading or editing it.

## 6. Config files

| File | Controls |
|---|---|
| `config/settings.yaml` | As-of date, exception cap, run limits (80 LLM calls, 160 tool calls, 7200 s), context budget (7,000 tokens), LTB/E&O parameters, allocation priority, tracing exporter |
| `config/models.yaml` | Model per role (specialist, extractor, writer, judge) for each profile, rate limits, wall limits |
| `config/models.local.yaml` | The Ollama models installed on this machine |
| `config/autonomy.yaml` | Tier per action (0 auto-record, 1 approve, 2 approve with reason), escalation rules (over $50k needs a second approver; security flag, failed grounding or conflict raise the tier), allowed actions per exception type |
| `config/untrusted_patterns.yaml` | Prompt-injection patterns for supplier and EOL text |

Changing a threshold here is a policy change: review it, and run the evals.

## 7. Evals

- **Scenarios:** 40 YAML files. Each one injects a situation into the base snapshot and states the
  expected action, quantities (as oracle tokens like `{oracle:eo.cancel_qty}`, computed by the engine),
  root cause, tier and things that must not happen.
- **Suites:** `smoke` (12, CI), `safety` (7 robustness/injection), `full` (40).
- **Scorers** (`evals/scorers/`): detection, action, quantity, root cause, mentions, tier, safety,
  grounding (re-runs the ledger's tool calls) and efficiency. Optional LLM judge for the message.
- **Results:** each run writes `evals/results/<timestamp>_<suite>_<system>_<profile>.json` and `.md`;
  `sentinel eval report` rebuilds the leaderboard.
- **Rule:** never edit an `expected:` block, human labels or a locked baseline to make a test pass
  (`evals/CHANGELOG.md` records any intentional change).

Current numbers (from committed results files):

| System | Suite | Pass |
|---|---|---|
| rules | full 40 | 57% |
| rules | smoke 12 | 42% |
| agent, replay (recorded before ADR-0015) | smoke 12 | 25% |
| agent, local, after ADR-0015 | 3 smoke scenarios | 2 of 3 |

The agent does not yet beat the rules baseline. Why, and what fixed the main failure: `docs/ERROR_ANALYSIS.md`.

## 8. Guardrails

| Control | Where |
|---|---|
| Engine computes, LLM chooses | ADR-0005, grounding scorer |
| Only write is a PENDING recommendation | `mcp_server/tools.py` (`record_recommendation`) |
| Tier set by policy, not by the model | `config/autonomy.yaml`, `agent/policy.py` |
| Untrusted text wrapped, scanned, flagged | `mcp_server/untrusted.py`, `config/untrusted_patterns.yaml` |
| Snapshot bound per run, not a tool argument | `mcp_server/` |
| Loud fallback (`RULES_FALLBACK`) | `agent/specialists/base.py` |
| Threat model | `docs/THREAT_MODEL.md` |

## 9. CI and GitHub

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | every push and PR | ruff, mypy, offline tests, replay smoke eval with gate |
| `evals-live.yml` | manual, or PR label `run-live-evals` | smoke suite on a live profile with the judge |
| `nightly-evals.yml` | daily 03:17 UTC, or manual | safety suite on `free-cloud`; **skipped with a notice until the `GEMINI_API_KEY` repo secret is set** |
| `release.yml` | tag `v*` | builds and pushes the Docker image to GHCR, creates a GitHub release |

Dependabot opens weekly PRs for Python packages and GitHub Actions. Merge them when CI is green.

Repository guardrails on GitHub:

| Control | What it does |
|---|---|
| Ruleset `protect-main` | `main` only changes through a pull request whose `test` check (from `ci.yml`) passes; no force pushes or deletion |
| CodeQL default setup | Scans Python and the workflows on every push and PR; findings appear under Security > Code scanning |
| `.github/CODEOWNERS` | Marks eval labels, scenarios, baselines, `config/autonomy.yaml`, injection patterns and agent instructions as owner-reviewed; the GitHub side of the local path-guard hook |

## 10. Claude Code harness

| Piece | Where | Purpose |
|---|---|---|
| Instructions | `AGENTS.md`, `CLAUDE.md` | Non-negotiables, stack, conventions |
| Hooks | `.claude/settings.json`, `.claude/hooks/` | Auto-format Python; block edits to `.env`, human labels, baselines, snapshots |
| Subagents | `.claude/agents/` | `domain-reviewer`, `eval-analyst` |
| Dev skills | `.claude/skills/` | `phase-gate`, `add-eval-scenario` |
| Autopilot | `scripts/autopilot.sh`, `AUTOPILOT.md` | Runs build phases headless with decide-and-record rules |

## 11. Docs index

| Doc | Read it for |
|---|---|
| `docs/PROJECT_BRIEF.md` | Why the project exists |
| `docs/ARCHITECTURE.md` | Components and data flow |
| `docs/specs/01..07` | Detailed specs per block |
| `docs/adr/` | 15 design decisions (README table) |
| `docs/ERROR_ANALYSIS.md` | Failure clusters, the fix, what I learned |
| `docs/THREAT_MODEL.md` | Attacks and controls |
| `docs/HOSTS.md` | Using the MCP server from Claude Code, VS Code, Desktop |
| `docs/DEPLOY.md` | Hosted demo steps |
| `docs/DEMO_SCRIPT.md` | What to show in 90 seconds |
| `docs/BUILD_PLAN.md` | Phase-by-phase plan and acceptance checks |

## 12. What is left (owner only)

These need your judgment or your accounts, so they were not done for you.

1. **Defend it.** Read your local, gitignored `_private/OWNER_REVIEW.md` and `_private/OWNER_BRIEFING.md`.
2. **Approve the answer keys.** `_private/SCENARIO_REVIEW.md`, starting with the 12 flagged rows.
3. **Label the judge.** Fill `_private/human_labels_TEMPLATE.yaml`, copy it to
   `evals/human_labels/comms_labels.yaml`, run `uv run sentinel eval calibrate --labels evals/human_labels/comms_labels.yaml`,
   and update the calibration line in the README.
4. **Deploy.** `docs/DEPLOY.md`: share.streamlit.io, repo `main`, main file `hosted_app.py`, no secrets.
   Paste the URL into the README.
5. **GIF.** Record 60 to 90 seconds following `docs/DEMO_SCRIPT.md` and save as `docs/demo.gif`.
6. **Optional:** add `GEMINI_API_KEY` as a repo secret to turn the nightly safety evals on (20 requests/day
   limits it to a few scenarios).

Next engineering steps, when you have a GPU or more quota: re-record the replay cassettes with the fixed
specialist, run `full` at k=3, the safety suite and the four ablations listed in `docs/ERROR_ANALYSIS.md`.

## 13. Troubleshooting (Windows)

| Symptom | Fix |
|---|---|
| Git Bash turns `origin/main:path` or `/flags` into Windows paths | Prefix the command with `MSYS_NO_PATHCONV=1` |
| `local` runs end in `RULES_FALLBACK` | Check the ledger timings; CPU inference is slow. ADR-0015 reserves time for the final call |
| Gemini HTTP 429 | Free tier is 20 requests/day; wait for the reset or use `local` |
| Gemini HTTP 400 about `thought_signature` | Use the built-in provider layer, which echoes signatures (ADR-0014) |
| `StreamlitPageNotFoundError` | Page paths must be absolute when run through `hosted_app.py` (fixed) |
| `.env` saved as `.env.txt` | Windows hides extensions; rename it to `.env` |
