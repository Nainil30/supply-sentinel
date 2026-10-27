# Threat model

Scope: the agent, MCP server and console running on synthetic data. Each row names the control, where it lives, and the test or scenario that exercises it.

| Threat | Control | Code | Tested by |
|---|---|---|---|
| Prompt injection via supplier notes or EOL notice text | Free text is wrapped as `UntrustedText`, scanned against `config/untrusted_patterns.yaml`, and flagged; skills tell the agent to treat it as data | `mcp_server/untrusted.py` | `tests/unit/test_mcp_untrusted.py`; scenarios ROB-01, ROB-02, ROB-03 |
| Excessive agency (agent acts on its own) | The agent can only call `record_recommendation`; approval is a human step staged in the console or CLI and applied on resume; tier 2 needs a reason | `agent/policy.py`, `agent/nodes/`, `mcp_server/tools.py` | `tests/integration/test_agent_graph.py`; `must_not` scorer |
| Tool misuse | Per-skill tool allowlists in the MCP bridge; read-only tools; per-specialist call and wall budgets | `agent/mcp_bridge.py`, `config/settings.yaml` | `tests/unit/test_specialist_budget.py` |
| Hallucinated numbers | Validator checks every number in a recommendation against tool outputs in the run ledger | `agent/validate.py`, `evals/` grounding scorer | `tests/unit/test_agent_validate.py`; grounding score in the leaderboard |
| Data exfiltration via drafted messages | Messages are drafts for a human to review; no send capability exists; the validator rejects ungrounded content | `agent/validate.py` | approval gate tests |
| Dependency supply chain | Versions pinned in `uv.lock`; Dependabot weekly; CodeQL and secret scanning enabled in repo settings | `pyproject.toml`, `.github/dependabot.yml` | CI |
| Secret leakage | Keys only from environment; `.env` gitignored and blocked by a hook; cassettes keep messages only | `.claude/hooks/guard_paths.py`, `llm/` | `tests/unit/test_ship_files.py` (cassette scan) |
| Stale data | The run compares the snapshot as-of date with the run date and raises `STALE_DATA` | `agent/` run setup, `settings.yaml` `staleness_days` | scenario ROB-07 |
| Conflicting specialist outputs | Reconcile node raises `CONFLICT` and raises the tier | `agent/nodes/` | scenario ROB-06 |

## Known gaps

- The injection scanner is pattern based. A novel phrasing can pass it; the structural controls (no send, human approval, allowlists) are the real backstop.
- Content capture of prompts is on by default because data is synthetic. Set `otel.capture_content: false` in `config/settings.yaml` before using real data.
- The rules baseline does not flag injection (it ignores text), so ROB-01, ROB-03 fail for it by design.
