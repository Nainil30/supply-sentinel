# Build Plan — Supply Sentinel

Ten phases. One phase per Claude Code session. Each phase ends with green tests, a phase-gate run, small commits, a PR into `main`, and a tag. Paste the prompt for the phase, review the plan Claude Code proposes, then let it build.

## How to run a phase

1. `git checkout -b phase/<NN>-<name>`
2. Start Claude Code in the repo root. Run `/clear` if the previous session is still open.
3. Paste the phase prompt below. Approve or adjust the plan.
4. Review diffs as they land. Prefer many small commits over one big one.
5. When Claude Code reports the phase done, run the acceptance checks yourself.
6. Write 5 lines in your private learning log: what was built, one design choice and why, one thing that surprised you.
7. Open a PR, let CI pass, merge, `git tag v0.<NN>.0`.

If a session drifts: stop it, say "Re-read CLAUDE.md and the phase spec, then continue from the last passing commit", or `/clear` and restart the phase with the same prompt.

## Time plan (part-time)

| Phase | Name | Effort |
|---|---|---|
| 0 | Bootstrap and harness | 0.5 day |
| 1 | Synthetic data | 1 day |
| 2 | Planning engine + rules baseline | 1.5 days |
| 3 | MCP server | 1 day |
| 4 | Agent core (one specialist end to end) | 1.5 days |
| 5 | All specialists + controls | 1.5 days |
| 6 | Eval harness + 40 scenarios + CI gates | 2 days |
| 7 | Observability + Planner Console | 1.5 days |
| 8 | Error analysis, tuning, ablations | 1 day |
| 9 | Ship: packaging, docs, demo, v1.0.0 | 1 day |

About 12–13 working days; 3–4 weeks of evenings and weekends.

## ADRs to produce along the way

| ADR | Title | Phase |
|---|---|---|
| 0001 | Deterministic engine; LLM for judgment only | 2 |
| 0002 | MCP SDK v2, agent-side MCP adapter, local HTTP auth | 0, updated in 3 |
| 0003 | LangGraph for orchestration | 4 |
| 0004 | Service spares modeled as whole units through the BOM | 2 |
| 0005 | No vector store: structured tools plus skills | 5 |
| 0006 | Free-tier context budget and tool-result summarization | 4 |
| 0007 | Autonomy tiers; only humans approve | 4 |
| 0008 | Agent Skills as planner-editable playbooks with tool allowlists | 5 |

---

## Phase 0 — Bootstrap and harness

**Prompt**

> Read CLAUDE.md, AGENTS.md, docs/PROJECT_BRIEF.md and docs/specs/06_HARNESS_OPS.md. This is Phase 0. In plan mode, propose:
> 1. `pyproject.toml` for Python 3.12 managed by uv, with the stack from AGENTS.md (only what Phase 0–2 need now; add the rest in later phases), ruff, mypy and pytest config, and a `sentinel` Typer entry point with stub commands: datagen, detect, run, approvals, approve, reject, resume, eval, serve-mcp, ui.
> 2. Package skeleton `src/supply_sentinel/` with empty modules for every block in AGENTS.md, and `tests/` with one placeholder test.
> 3. `config/` files from spec 06 §1 (placeholders for model ids), `.env.example`, `CHANGELOG.md`, `LICENSE` (MIT); `.gitignore`, `AUTOPILOT.md`, `scripts/autopilot.sh` and `docs/adr/0000-template.md` already exist — keep them.
> 4. `.github/workflows/ci.yml` (lint, type check, tests) and `.github/dependabot.yml`.
> Then verify against current docs or installed package source, not memory: (a) the latest stable `mcp` Python SDK version and its `MCPServer` and `Client` APIs; (b) whether LangChain/LangGraph's MCP integration supports mcp v2 — if not, plan a thin adapter in `agent/mcp_bridge.py`; (c) the GitHub Models inference endpoint and three current model ids (high-tier tool-calling, low-tier, a different family for the judge) plus current free-tier limits; (d) that `.claude/settings.json` matches the current Claude Code permissions and hooks schema — fix it if not. Record (a)–(c) in ADR-0002 and update `config/models.yaml`. No product code in this phase.

**Acceptance**
- `uv sync` succeeds; `uv run sentinel --help` lists all commands; `uv run pytest` passes.
- Editing a `.py` file triggers ruff formatting; attempting to edit `.env` is blocked by the hook.
- CI is green on GitHub. ADR-0002 exists with verified versions and model ids.

---

## Phase 1 — Synthetic data

**Prompt**

> Read docs/specs/01_DATA.md. Phase 1. Implement `datagen/` (catalog, generator, validate, scenarios op registry) exactly to spec, including all anchor parts and the SQLite decision log. Write tests first for determinism, referential integrity, anchor attributes, lifecycle sanity, and each scenario op on a small fixture. Implement `sentinel datagen --seed 42` and `sentinel scenario build` (ops only; scenario YAML files come in Phase 6). Generate `data/sample/base_summary.md`. Use the `domain-reviewer` subagent to review realism (lead times, costs, demand patterns, lifecycle phases) before finishing.

**Acceptance**
- Two runs with seed 42 produce identical table hashes.
- `validate.py` passes; anchor parts match the spec table exactly.
- Every scenario op has a passing unit test.
- `base_summary.md` is readable and committed; no `.duckdb` committed.

**Owner checkpoint:** open the DuckDB file (DuckDB CLI or a notebook) and sanity-check one platform's BOM, demand and POs like you would at work.

---

## Phase 2 — Planning engine and rules baseline

**Prompt**

> Read docs/specs/02_ENGINE.md. Phase 2. Write the worked examples A, B and C as failing tests first, then the property tests, then implement netting, exceptions, severity, root cause by counterfactual replay, LTB newsvendor with bridge option, E&O, what-if, and the rules baseline. All functions pure with Pydantic outputs; mypy strict on `engine/`. Implement `sentinel detect` to print an exceptions table for a snapshot. Enable the natural-exceptions check in `validate.py` (2–6 on base) and tune the generator if needed without touching anchor parts. Write ADR-0001 and ADR-0004. Finish with a `domain-reviewer` review.

**Acceptance**
- Examples A, B, C pass to the stated tolerances; property tests pass.
- `sentinel detect` on `base` shows 2–6 exceptions with severities and baseline actions.
- Rules baseline returns a `Recommendation` for every exception type.

**Owner checkpoint:** recompute Example B in a spreadsheet yourself. You will be asked about the newsvendor critical ratio in interviews.

---

## Phase 3 — MCP server

**Prompt**

> Read docs/specs/03_MCP.md. Phase 3. Implement the MCP server with the official SDK v2: all tools, resources and prompts, Pydantic structured outputs, compact outputs, honest annotations, `UntrustedText` with the injection pattern scanner (including false-positive tests), stdio and Streamable HTTP transports, and `sentinel serve-mcp`. Write contract tests using the SDK's Client. Commit `.mcp.json` and `.vscode/mcp.json` and write docs/HOSTS.md. Update ADR-0002 with the transport and auth decisions. Show me the exact command to open the MCP Inspector against the server.

**Acceptance**
- Contract tests list and call every tool, resource and prompt successfully.
- Injection scanner flags the adversarial set and does not flag the benign set.
- In Claude Code, "Use supply-sentinel to list P1 exceptions and explain the top one" works through the project's own MCP server.

**Owner checkpoint:** try the server from Claude Code and the MCP Inspector yourself; take screenshots for HOSTS.md.

---

## Phase 4 — Agent core (one specialist end to end)

**Prompt**

> Read docs/specs/04_AGENT.md and the runtime skills in `skills/`. Phase 4. Implement the provider layer (profiles from config/models.yaml, router with rate limits and backoff, replay wrapper with record/replay), `mcp_bridge.py` with per-skill tool allowlists from `allowed-tools`, `skills_loader.py` following the Agent Skills spec (frontmatter `name`, `description`; progressive disclosure of the body), `context.py` with the 7k input budget and summarization, the Recommendation and state models, `policy.py` tiering, `validate.py` (schema, grounding, policy, sanity, one repair), and the graph with only the Shortage specialist wired: load_snapshot → ingest_documents (scanner only, no extractor yet) → detect_exceptions → prioritize → shortage_specialist → validate → approval_gate → finalize → weekly_brief. Implement `sentinel run/approvals/approve/reject/resume`. Record cassettes for a run on `base` using the `local` or `free-cloud` profile, and add a replay end-to-end test. Write ADR-0003, ADR-0006, ADR-0007.

**Acceptance**
- `sentinel run --profile replay` on `base` pauses at the approval gate, `approve` + `resume` finalize and write the weekly brief, offline.
- Validator unit tests cover grounded, ungrounded, wrong PO id, disallowed action, tier assignment.
- Ledger shows token counts per LLM call, all under the budget.

---

## Phase 5 — All specialists and controls

**Prompt**

> Phase 5. Read docs/specs/04_AGENT.md §4, §6, §7, §10 again. Add the LTB and E&O specialists, the reconcile node and reconciler, the extractor in ingest_documents (EOL notice text vs table fields, mismatches become data-issue candidates), security-flag propagation and tier escalation, decision-history use, rules fallback on budget breach or provider failure, and budget enforcement tests. Use the `domain-reviewer` subagent on each specialist's behavior with three hand-made fixtures each. Write ADR-0005 and ADR-0008. Re-record cassettes for `base`.

**Acceptance**
- Each exception type in `base` gets a validated recommendation in replay mode.
- A forced provider failure produces a RULES_FALLBACK recommendation with a visible flag.
- A forced conflict on one part goes through reconcile.

**Owner checkpoint:** rewrite the four runtime skills in `skills/` in your own words and with your own heuristics. This is where your planning judgment lives. Re-record cassettes after.

---

## Phase 6 — Eval harness, 40 scenarios, CI gates

**Prompt**

> Read docs/specs/05_EVALS.md. Phase 6. Implement the scenario loader, resolver tokens and oracle, all scorers, the judge and calibration, the runner (eval mode, per-scenario MCP server, rate-limit aware, resumable) and `sentinel eval report`. Then use the `add-eval-scenario` skill to draft all 40 scenarios from the catalog — draft only; list every `expected:` block in a single review table for me to approve before you commit them. Prepare `evals/human_labels/comms_labels.yaml` as an empty template with 15 slots and the drafts to label (I will fill the labels). Add `evals-live.yml` and `nightly-evals.yml`. Run: rules baseline on `full`, agent on `smoke` with `free-cloud --record`, agent on `full` with `local` if available.

**Acceptance**
- 40 scenarios committed after owner approval; all build valid snapshots.
- Leaderboard and per-category tables generated from results files.
- Replay smoke runs in `ci.yml`; live smoke runs on the label; gates enforced.
- Calibration report produced after the owner fills the 15 labels.

**Owner checkpoint:** approve every expected block yourself; fill the 15 message labels; copy the first accepted live smoke result to `evals/results/baseline_main.json` (the hook stops Claude Code from doing it).

---

## Phase 7 — Observability and Planner Console

**Prompt**

> Read docs/specs/06_HARNESS_OPS.md §2 and docs/specs/07_UI.md. Phase 7. Complete the run ledger, add OpenTelemetry with GenAI semantic conventions and OpenInference instrumentation, the file and Phoenix exporters, then build the Streamlit Planner Console pages with a thin service layer and AppTest smoke tests. Write docs/ARCHITECTURE.md with the three harnesses, the graph diagram and a sequence diagram for one exception.

**Acceptance**
- A replay run shows full traces in Phoenix and in `spans.jsonl`.
- All console pages render on `base` in replay mode; approve/reject flows work from the UI.

---

## Phase 8 — Error analysis, tuning, ablations

**Prompt**

> Phase 8. Run the full suite with k=3 on the `local` profile (or the best free option available) and the safety suite on `free-cloud`. Use the `eval-analyst` subagent to cluster failures and propose fixes. Implement fixes for the top three failure modes (skills, tool descriptions, validator rules or context budget — not the labels), re-run, and record before/after numbers. Run the four ablations in docs/specs/05_EVALS.md §9. Write docs/ERROR_ANALYSIS.md, leaving a section titled "What I learned" for me.

**Acceptance**
- ERROR_ANALYSIS.md has failure clusters, changes made, before/after tables, ablation table.
- Leaderboard updated; no label changed without an evals/CHANGELOG.md entry.

**Owner checkpoint:** write "What I learned" yourself. This becomes your best interview story.

---

## Phase 9 — Ship v1.0.0

**Prompt**

> Phase 9. Read docs/specs/06_HARNESS_OPS.md §3–§8. Add the Dockerfile, docker-compose.yml (replay by default), devcontainer, release workflow, SECURITY.md, docs/THREAT_MODEL.md, docs/DEMO_SCRIPT.md, `.github/copilot-instructions.md`, and `.github/agents/supply-reviewer.agent.md` (check the current Copilot custom agent format first). Draft README.md to the §8 structure, leaving "The problem" paragraph for me, and fill all numbers from committed results files only. Prepare the hosted demo config for replay mode. Do not push or tag; give me the exact commands.

**Acceptance**
- `docker compose up` from a fresh clone serves the console in replay mode with no keys.
- Devcontainer builds and runs the smoke suite.
- README numbers all link to results files. Owner wrote "The problem".
- Owner pushes, tags `v1.0.0`, publishes the hosted demo, records the GIF.

---

## Stretch goals (only when asked, after v1.0.0)

| Idea | What it shows |
|---|---|
| MCP Apps UI for the exception queue inside an MCP host | Latest MCP UI extension |
| A2A "supplier agent" that answers commit requests | Agent-to-agent protocol across companies |
| GitHub Agentic Workflow that triages failed nightly evals into an issue (safe outputs) | GitHub-native agent automation |
| NPI transition specialist (cut-in/cut-out between platform generations) | Deeper domain coverage |
| Durable execution on Temporal for long approvals | Production-grade orchestration |
| Claude Agent SDK variant of one specialist, evaluated head to head | Framework comparison with data |
