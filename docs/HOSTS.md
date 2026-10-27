# Connecting hosts to the Supply Sentinel MCP server

One server, many hosts. Every host runs the same command; only the config file differs.

```bash
uv run sentinel datagen --seed 42          # once: builds data/snapshots/base.duckdb
uv run sentinel serve-mcp --transport stdio
```

The server is bound to one snapshot per process (`SENTINEL_DB`, opened read-only) and writes recommendations only to `SENTINEL_STATE_DIR/decisions.sqlite` with status `PENDING`. It has 14 read tools, 1 write tool (`record_recommendation`), 5 resources and 3 prompts. There is no approve or send tool.

## Claude Code

`.mcp.json` at the repo root is picked up automatically (approve the project server when prompted).

```bash
claude mcp list                      # supply-sentinel should show as connected
```

Try: "Use supply-sentinel to list P1 exceptions and explain the top one."

> Screenshot placeholder: `docs/img/hosts-claude-code.png`

## Claude Desktop

Add to `claude_desktop_config.json` (use an absolute path for `--directory`):

```json
{
  "mcpServers": {
    "supply-sentinel": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/supply-sentinel", "sentinel", "serve-mcp", "--transport", "stdio"],
      "env": {
        "SENTINEL_DB": "C:/path/to/supply-sentinel/data/snapshots/base.duckdb",
        "SENTINEL_STATE_DIR": "C:/path/to/supply-sentinel/data/state"
      }
    }
  }
}
```

Restart Claude Desktop, then pick the `weekly_supply_review` prompt from the attachments menu.

> Screenshot placeholder: `docs/img/hosts-claude-desktop.png`

## VS Code (Copilot agent mode)

`.vscode/mcp.json` is committed. Open the file and click **Start** above the server entry, then switch Copilot Chat to Agent mode and enable the tools.

> Screenshot placeholder: `docs/img/hosts-vscode.png`

## MCP Inspector

The Inspector is a Node tool. Run it against the stdio server (this downloads the package from npm, so it is not run in CI or by the autopilot):

```bash
npx @modelcontextprotocol/inspector uv run sentinel serve-mcp --transport stdio
```

CLI mode, for a quick check without the browser UI:

```bash
npx @modelcontextprotocol/inspector --cli uv run sentinel serve-mcp --transport stdio --method tools/list
```

For the HTTP transport, start `uv run sentinel serve-mcp --transport http --port 8765`, then in the Inspector choose **Streamable HTTP** and enter `http://127.0.0.1:8765/mcp`.

> Screenshot placeholder: `docs/img/hosts-inspector.png`

## Streamable HTTP and the optional token

```bash
SENTINEL_MCP_TOKEN=change-me uv run sentinel serve-mcp --transport http --port 8765
```

It binds to `127.0.0.1` by default. With `SENTINEL_MCP_TOKEN` set, every request needs `Authorization: Bearer <token>`; without it there is no auth, which is fine for localhost only. See ADR-0002.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Snapshot ... not found" | Run `uv run sentinel datagen --seed 42` or set `SENTINEL_DB`. |
| Host shows the server as failed | Run the command by hand; stdout must carry only MCP messages, so check stderr for the real error. |
| Tool returns an error with a hint | The hint says which tool to call first (for example `list_exceptions` for valid part ids). |
