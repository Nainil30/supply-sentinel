# Spec 03 — MCP Server

Goal: one MCP server that exposes the planning engine and data. The same server is used by our LangGraph agent, by Claude Code and Claude Desktop, and by VS Code Copilot agent mode. This is the "build once, use from any host" proof.

Code: `src/supply_sentinel/mcp_server/` — `server.py`, `tools.py`, `resources.py`, `prompts.py`, `untrusted.py`, `schemas.py`. CLI: `sentinel serve-mcp --transport stdio|http [--port 8765]`.

## 1. SDK and protocol

- Official `mcp` Python SDK **v2** (`from mcp.server import MCPServer`), which speaks protocol revision **2026-07-28** (stateless requests, `server/discover`) and still serves 2025-era clients. Confirm the installed version and API in Phase 0 and record it in ADR-0002.
- Transports: **stdio** (for local hosts) and **Streamable HTTP** (for Docker and the UI). HTTP binds to `127.0.0.1` by default. No OAuth in v1; optional static bearer token via `SENTINEL_MCP_TOKEN` when running in Docker. Write the auth decision into ADR-0002.
- The server is bound to one snapshot per process via `SENTINEL_DB=data/snapshots/<id>.duckdb` (opened read-only) and `SENTINEL_STATE_DIR=data/state` (SQLite for decisions). Tools never accept a snapshot argument; the agent must not choose its data source.

## 2. Tool design rules

1. Every tool returns a Pydantic model (structured output). Numbers are named fields, never buried in prose.
2. Outputs are **compact**: at most 26 weekly rows or 20 list rows; longer lists are truncated with `truncated: true` and a count. This keeps every LLM call under the free-tier input limit (see `04_AGENT.md`, context budget).
3. Every response includes `as_of_date` and `snapshot_id`.
4. Untrusted free text (supplier notes, EOL notice text) is never returned raw. It is returned as an `UntrustedText` object: `{source, text, injection_suspected: bool, matched_patterns: list[str]}` built by `untrusted.py`.
5. Tool annotations are set honestly: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint=false`.
6. Descriptions are written for a model: what it returns, when to use it, when not to, units.
7. Errors are returned as structured tool errors with a fix hint (for example "unknown part_id; call list_exceptions or check the BOM resource").

## 3. Tools

### Read and calculate (read-only, idempotent)

| Tool | Inputs | Returns (key fields) |
|---|---|---|
| `list_exceptions` | type?, platform_id?, min_severity? | exceptions with id, type, part_id, platforms, severity, weeks_to_impact, revenue_at_risk_usd or exposure_usd, baseline_action |
| `get_part_position` | part_id, horizon_weeks = 26 | usable_on_hand, quality_hold_units, coverage_weeks, lead_time_weeks, moq, order_multiple, unit_cost_usd, per-week rows {week_idx, gross_req, receipts, pab}, first_shortage_week, max_shortfall_units, shortfall_rounded_to_multiple, recovery_week |
| `compute_allocation` | part_id | for the shortage window: per-platform demand, available supply, priority order (from `allocation_priority`), allocated units, share, unmet units |
| `get_where_used` | part_id | platforms {platform_id, qty_per, lifecycle_phase, last_ship_date, eosl_date, share_of_gross_req_pct} |
| `get_open_pos` | part_id | POs {po_id, qty_units, need_week_idx, promise_week_idx, slip_weeks, cancellable, unit_cost_usd} |
| `get_supplier_commits` | part_id, weeks = 12 | rows {week_idx, commit_units, po_promised_units, gap_units, note: UntrustedText or null} |
| `get_demand_profile` | platform_id | last 13 weeks actuals, next 26 weeks forecast (current and previous version), forecast_change_pct, mape, bias |
| `get_eol_notice` | part_id | notice_id, notice_date, ltb_date, last_ship_date, replacement_part_id, days_to_ltb, raw_text: UntrustedText |
| `get_alternates` | part_id | alternates {alt_part_id, qualification_status, qual_lead_time_weeks, unit_cost_usd} |
| `get_decision_history` | part_id, limit = 5 | prior recommendations and human decisions {rec_id, action_type, quantity, status, decided_at, rationale} |
| `explain_root_cause` | part_id | primary cause, contributing causes, counterfactual summaries {cause, max_shortfall_after, first_shortage_week_after} |
| `compute_ltb_quantity` | part_id, shortage_penalty_multiple?, cv_floor? | all newsvendor intermediates (μ, σ, Co, Cu, CR, z, Q*, gross, net, order_qty, spend_usd, requires_second_approver) + bridge option with the same fields if an alternate exists |
| `compute_eo_exposure` | part_id | supply, need_52w, safety_stock, excess_units, excess_usd, cancellable_qty, cancel_qty_recommended (= min(excess, cancellable)), avoidable_usd, residual_units, residual_usd, obsolete_units, obsolete_usd |
| `simulate_scenario` | part_id + what-if params from `02_ENGINE.md` §6 | new PAB rows, first_shortage_week, max_shortfall_units, deltas vs baseline |

### Write (not read-only, not destructive)

| Tool | Inputs | Returns | Notes |
|---|---|---|---|
| `record_recommendation` | a validated `Recommendation` | rec_id, status = PENDING | Writes only to `decision_log` with status PENDING. Called by the agent **harness** after validation, never offered to the LLM. Also usable by external hosts. |

There is **no** approve, send, or modify-data tool. Approval exists only in the UI/CLI.

## 4. Resources

| URI | Content |
|---|---|
| `supply://catalog/platforms` | Platform table (markdown table + JSON) |
| `supply://catalog/platform/{platform_id}/bom` | BOM with part descriptions, suppliers, lead times |
| `supply://policy/autonomy` | Contents of `config/autonomy.yaml` |
| `supply://snapshot/current` | Snapshot id, as-of date, row counts, staleness in days |
| `supply://glossary` | Supply chain terms used in this project (LTB, EOSL, PAB, NRND, E&O, OTD, MOQ, GSM, GCM) |

## 5. Prompts

| Name | Arguments | Purpose |
|---|---|---|
| `weekly_supply_review` | none | Walks a host model through list_exceptions → top 5 by severity → summary table |
| `review_ltb` | part_id | Structured LTB review using where-used, alternates and compute_ltb_quantity |
| `draft_escalation` | exception_id, audience (GSM, GCM, PM, FINANCE) | Drafts a message using only tool-sourced numbers |

These make the server useful in Claude Desktop or Copilot Chat without our agent.

## 6. Untrusted text handling (`untrusted.py`)

- Patterns flagged (case-insensitive, extendable list in config): instruction overrides ("ignore previous", "disregard", "new instructions"), role claims ("system:", "assistant:", "you are now"), tool or action coercion ("approve", "call the tool", "record_recommendation", "set quantity"), exfiltration cues ("send to", "http://", "https://"), and hidden-text tricks (zero-width characters, long base64 runs).
- Flagging never removes text silently; it marks it. The agent treats any flagged text as evidence of a security issue and raises a DATA_ISSUE_FLAG with `security_flags`.
- Unit tests include benign notes that must **not** be flagged (for example "approved vendor list updated" should not trigger on "approve" alone — use word-boundary rules plus context patterns and test false positives).

## 7. Host configuration files (commit these)

`.mcp.json` (Claude Code, project scope):
```json
{
  "mcpServers": {
    "supply-sentinel": {
      "command": "uv",
      "args": ["run", "sentinel", "serve-mcp", "--transport", "stdio"],
      "env": { "SENTINEL_DB": "data/snapshots/base.duckdb", "SENTINEL_STATE_DIR": "data/state" }
    }
  }
}
```

`.vscode/mcp.json` (VS Code / Copilot agent mode): same server definition in the VS Code format (check current schema; it uses a `servers` key with `type: "stdio"`).

`docs/HOSTS.md`: how to connect from Claude Desktop, Claude Code, VS Code, and the MCP Inspector (`npx @modelcontextprotocol/inspector`), with a screenshot placeholder for each.

## 8. Tests

- Unit: each tool function called directly on the toy fixtures and on `base`.
- Contract: start the server in-process (or via stdio) with the SDK's `Client`, list tools/resources/prompts, call each tool once, validate the schema.
- Snapshot test of tool descriptions (changes to descriptions show up in review, because they change model behavior).
- Injection tests for `untrusted.py`, including false-positive cases.
