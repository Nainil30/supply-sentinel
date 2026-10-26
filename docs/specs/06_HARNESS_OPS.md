# Spec 06 — Harness, Observability, CI/CD, Packaging, Security

"Harness" in this repo means three things, all documented in `docs/ARCHITECTURE.md`:

| Harness | What it controls | Where |
|---|---|---|
| Runtime harness | How the agent runs: tool allowlists, budgets, retries, validation, approval gates, checkpointing, tracing | `src/supply_sentinel/agent/`, `config/` |
| Eval harness | How we know it works: scenarios, oracle, scorers, judge, reports, gates | `src/supply_sentinel/evals/`, `evals/` |
| Development harness | How coding agents build it safely: AGENTS.md, CLAUDE.md, hooks, subagents, skills, Copilot custom agent, CI | `.claude/`, `.github/`, root files |

## 1. Configuration files

### `config/autonomy.yaml`

```yaml
version: 1
tiers:
  0: {label: auto_record, approval: none}
  1: {label: approve, approval: single}
  2: {label: approve_with_reason, approval: single_with_reason}
action_tiers:
  MONITOR: 0
  DATA_ISSUE_FLAG: 0
  ESCALATE_SUPPLIER: 1
  PULL_IN_PO: 1
  PUSH_OUT_PO: 1
  PLACE_PO: 1
  REVIEW_QUALITY_HOLD: 1
  QUALIFY_ALTERNATE: 1
  EXPEDITE: 2
  ALLOCATE: 2
  LTB_BUY: 2
  BRIDGE_BUY: 2
  CANCEL_PO: 2
  DISPOSITION_REVIEW: 2
escalation_rules:
  second_approver_over_usd: 50000
  security_flag_min_tier: 1
  grounding_failed_min_tier: 2
  conflict_min_tier: 2
allowed_actions:
  SHORTAGE: [PLACE_PO, EXPEDITE, PULL_IN_PO, ALLOCATE, ESCALATE_SUPPLIER, QUALIFY_ALTERNATE, REVIEW_QUALITY_HOLD, MONITOR, DATA_ISSUE_FLAG]
  COMMIT_GAP: [ESCALATE_SUPPLIER, PULL_IN_PO, MONITOR, DATA_ISSUE_FLAG]
  EOL_NOTICE: [LTB_BUY, BRIDGE_BUY, QUALIFY_ALTERNATE, MONITOR, DATA_ISSUE_FLAG]
  EXCESS: [CANCEL_PO, PUSH_OUT_PO, DISPOSITION_REVIEW, MONITOR, DATA_ISSUE_FLAG]
  OBSOLESCENCE_RISK: [DISPOSITION_REVIEW, CANCEL_PO, PUSH_OUT_PO, MONITOR, DATA_ISSUE_FLAG]
```

### `config/settings.yaml` (key entries)

```yaml
as_of_default: 2026-11-02
staleness_days: 7
max_exceptions_per_run: 12
max_parallel_specialists: 2
specialist_limits: {max_tool_calls: 8, max_idle_turns: 2, wall_seconds: 120}
context_budget: {max_input_tokens: 7000, per_tool_result: 800, skill_body: 2000}
allocation_priority: [SERVICE, NPI, SUSTAIN, EOL_ANNOUNCED]   # planner-editable
ltb: {salvage_pct: 0.10, holding_rate: 0.20, cv_floor: 0.10, shortage_penalty_multiple: {default: 5, ASIC: 8}}
eo: {min_excess_usd: 5000, safety_weeks: 2}
untrusted_patterns_file: config/untrusted_patterns.yaml
otel: {exporter: file}   # file | phoenix | none
```

### `config/models.yaml`

```yaml
profiles:
  free-cloud:
    provider: openai          # OpenAI-compatible
    base_url: https://models.github.ai/inference   # verify in GitHub Models docs (Phase 0)
    api_key_env: GITHUB_TOKEN                       # PAT with models:read, or Actions GITHUB_TOKEN
    roles:
      specialist: {model: "<high-tier tool-calling model id>", temperature: 0.1}
      extractor:  {model: "<low-tier model id>", temperature: 0}
      writer:     {model: "<low-tier model id>", temperature: 0.3}
      judge:      {model: "<different family from specialist>", temperature: 0}
    limits: {rpm: 10, rpd: 50, concurrency: 2, max_input_tokens: 7000, max_output_tokens: 1500}
  local:
    provider: ollama
    base_url: http://localhost:11434
    roles:
      specialist: {model: "gpt-oss:20b"}
      extractor:  {model: "gpt-oss:20b"}
      writer:     {model: "gpt-oss:20b"}
      judge:      {model: "<second local model, different family>"}
    limits: {concurrency: 1, max_input_tokens: 7000, max_output_tokens: 1500}
  premium:       # optional, never required
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    roles: {specialist: {model: "<current model id>"}, writer: {model: "<current small model id>"}}
  replay:
    cassette_dir: evals/cassettes
price_table_usd_per_mtok: {}   # fill only for paid profiles; free profiles report $0
```

Rate limits above reflect the GitHub Models free tier at the time of writing (high-tier: 10 requests/min, 50/day, 8k input / 4k output tokens per request, 2 concurrent; low-tier: 15/min, 150/day). Re-check in Phase 0.

## 2. Observability

- **Run ledger (always on).** `runs/<run_id>/ledger.jsonl` with events: `run_start`, `llm_call` {role, provider, model, input_tokens, output_tokens, latency_ms, cassette_key, status}, `tool_call` {tool_call_id, tool, args, output (full JSON), latency_ms, error}, `validation` {rec_id, checks}, `approval` {rec_id, decision, by, reason}, `run_end` {totals}. The ledger is the system of record; the grounding scorer reads it. Add `runs/` to `.gitignore` except `runs/demo/`.
- **OpenTelemetry.** Use OpenTelemetry GenAI semantic conventions (`invoke_agent`, `execute_tool`, `chat` spans with `gen_ai.*` attributes). Instrument LangGraph/LangChain with the OpenInference instrumentation, add custom spans for specialists and tool calls, and enable the MCP SDK's own tracing if it is on by default in v2. Propagate trace context into MCP requests when the SDK supports it.
- **Exporters.** `otel.exporter: file` writes `runs/<run_id>/spans.jsonl`. `phoenix` exports OTLP to a local Arize Phoenix container (`docker run -p 6006:6006 arizephoenix/phoenix`). The UI links each recommendation to its trace.
- **Content capture.** Prompt/response capture is on by default because data is synthetic; document the flag that turns it off.

## 3. CI/CD (`.github/workflows/`)

| Workflow | Trigger | Steps |
|---|---|---|
| `ci.yml` | push, pull_request | setup uv (cache) → `uv sync` → ruff → mypy → `pytest -m "not live" --cov` → `sentinel datagen` → `sentinel eval --suite smoke --profile replay` → upload results artifact |
| `evals-live.yml` | pull_request `labeled` (`run-live-evals`), `workflow_dispatch` | `permissions: {contents: read, models: read, pull-requests: write}` → smoke on `free-cloud` using `GITHUB_TOKEN` → job summary table → PR comment → enforce gates from `05_EVALS.md` §8 |
| `nightly-evals.yml` | `schedule` (off-the-hour minute) + manual | safety suite on `free-cloud` → artifact + summary |
| `release.yml` | tag `v*` | build Docker image → push to GHCR → GitHub Release with leaderboard and changelog |

Also: `dependabot.yml` (uv/pip + github-actions, weekly), CodeQL default setup (enable in repo settings), secret scanning with push protection (repo settings), branch protection on `main` requiring `ci.yml`.

Git workflow: branch per phase (`phase/03-mcp-server`), PR into `main`, merge after CI passes, tag `v0.<phase>.0`. `v1.0.0` at Phase 9.

## 4. Packaging

- `Dockerfile` (multi-stage, uv, non-root user). `docker-compose.yml` services: `mcp` (Streamable HTTP, 8765), `ui` (Streamlit, 8501), `phoenix` (6006), `ollama` (profile `local`, 11434). Default compose run uses the `replay` profile so it works with no keys.
- `.devcontainer/devcontainer.json`: Python 3.12 image, Node feature (MCP Inspector), uv; `postCreateCommand`: `uv sync && uv run sentinel datagen && uv run sentinel eval --suite smoke --profile replay`; forward ports 8501, 6006, 8765.
- Hosted demo: Streamlit Community Cloud or a Hugging Face Space, `replay` profile, base snapshot built at startup. Verify the free tier at deploy time; document the URL in the README.

## 5. Development harness files

| File | Purpose |
|---|---|
| `AGENTS.md` | Shared rules for any coding agent |
| `CLAUDE.md` | Imports AGENTS.md; Claude Code workflow |
| `.claude/settings.json` | Permissions and hooks |
| `.claude/hooks/format_changed.py` | PostToolUse: ruff format + fix on edited `.py` |
| `.claude/hooks/guard_paths.py` | PreToolUse: blocks edits to protected paths |
| `.claude/agents/domain-reviewer.md` | Read-only reviewer for supply chain logic |
| `.claude/agents/eval-analyst.md` | Runs evals, clusters failures, writes ERROR_ANALYSIS.md |
| `.claude/skills/phase-gate/SKILL.md` | End-of-phase checklist |
| `.claude/skills/add-eval-scenario/SKILL.md` | Procedure for new scenarios |
| `.mcp.json` | Claude Code uses the project's own MCP server |
| `.github/copilot-instructions.md` | Short pointer to AGENTS.md for Copilot |
| `.github/agents/supply-reviewer.agent.md` | Copilot custom agent: reviews PRs that touch `engine/`, `skills/`, `evals/scenarios/`, with the project MCP server configured |
| `.vscode/mcp.json` | VS Code agent mode uses the same MCP server |

Write `.github/agents/supply-reviewer.agent.md` in Phase 9 following the current Copilot custom-agent format (YAML frontmatter with `name`, `description`, `tools`, optional `mcp-servers`). Check the current docs before writing it.

## 6. Security

- `SECURITY.md`: how to report issues; scope (synthetic data, local tools).
- `docs/THREAT_MODEL.md`: a table of threats for an agentic system and the control for each — prompt injection via data, excessive agency, tool misuse, hallucinated numbers, data exfiltration via messages, dependency supply chain, secret leakage, stale data. Link each control to code and to the eval scenario that tests it.
- Secrets only via environment variables; `.env.example` committed, `.env` gitignored and blocked by hook. Cassettes store messages, never headers or keys; add a test that scans cassettes for token-like strings.

## 7. Documentation set

| Doc | Content |
|---|---|
| `README.md` | See §8 |
| `docs/ARCHITECTURE.md` | The three harnesses, the graph diagram, a sequence diagram for one exception from detection to approval, data flow |
| `docs/HOSTS.md` | Connecting the MCP server to Claude Code, Claude Desktop, VS Code, MCP Inspector |
| `docs/THREAT_MODEL.md` | §6 |
| `docs/ERROR_ANALYSIS.md` | Phase 8 output |
| `docs/DEMO_SCRIPT.md` | A 5-minute live demo, click by click |
| `docs/adr/*` | Decisions 0001–0008 (list in `BUILD_PLAN.md`) |

## 8. README structure

1. Title, one-line pitch, badges (CI, license, Python), hosted-demo link, 60–90 s GIF.
2. "The problem" — one paragraph in the planner's voice (the owner writes this himself).
3. Architecture diagram (mermaid) and the 5-block table.
4. "Why an agent and not just rules" — the comparison table, filled from the latest leaderboard file (linked).
5. Eval results — leaderboard and per-category table, each linked to its results file; known weaknesses stated plainly.
6. Quickstart — three commands, replay mode, no keys.
7. Run modes — free-cloud, local, premium, replay.
8. Use the MCP server from your own tools — Claude Code, VS Code, Claude Desktop.
9. Design decisions — ADR list with one-line summaries.
10. Agentic engineering practices — table mapping practices (tool design, memory/state, evals, multi-agent orchestration, guardrails, SDLC) to code locations.
11. Disclaimer — fictional company, synthetic data, not affiliated with any employer, any resemblance is coincidental.
12. Roadmap and license (MIT).
