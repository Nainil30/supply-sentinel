"""The agent's only door to the planning engine: the project's own MCP server, in memory.

Each skill declares `allowed-tools`. The bridge shows the model only those tool schemas and
refuses any other call, so least privilege holds even if the model invents a tool name.
"""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from mcp import Client

from supply_sentinel.engine.models import Recommendation
from supply_sentinel.mcp_server.context import Snapshot
from supply_sentinel.mcp_server.server import build_server


class ToolNotAllowed(PermissionError):
    pass


def _slim_schema(node: Any) -> Any:
    """Drop titles and per-property descriptions: they cost tokens the model can do without."""
    if isinstance(node, dict):
        return {k: _slim_schema(v) for k, v in node.items() if k not in ("title",)}
    if isinstance(node, list):
        return [_slim_schema(x) for x in node]
    return node


class McpBridge:
    def __init__(self, snap: Snapshot) -> None:
        self.snap = snap
        self._server = build_server(snap)
        self._specs: dict[str, dict[str, object]] | None = None

    def _run(self, coro_fn: Any) -> Any:
        async def go() -> Any:
            async with Client(self._server) as client:
                return await coro_fn(client)

        return asyncio.run(go())

    def _all_specs(self) -> dict[str, dict[str, object]]:
        if self._specs is None:

            async def fetch(client: Client) -> Any:
                return await client.list_tools()

            listed = self._run(fetch)
            self._specs = {
                t.name: {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": _slim_schema(t.input_schema),
                    },
                }
                for t in listed.tools
            }
        return self._specs

    def tool_schemas(self, allowed: Sequence[str]) -> list[dict[str, object]]:
        """OpenAI-style function schemas for the allowed tools only, in allowlist order."""
        specs = self._all_specs()
        return [specs[n] for n in allowed if n in specs]

    def call(self, name: str, args: dict[str, Any], allowed: Sequence[str]) -> dict[str, Any]:
        """Run an allowed tool. Tool errors come back as {"error": text} for the model to read."""
        if name not in allowed:
            raise ToolNotAllowed(f"Tool '{name}' is not allowed for this skill.")
        return self._call(name, args)

    def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        async def go(client: Client) -> Any:
            return await client.call_tool(name, args)

        try:
            result = self._run(go)
        except (
            Exception
        ) as exc:  # a tool crashed (e.g. explain_root_cause on a part with no shortage)
            return {
                "error": f"Tool '{name}' failed: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}"
            }
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            return {"error": text}
        data = result.structured_content
        if data is None:
            return dict(json.loads(result.content[0].text))
        return dict(data)

    def record_recommendation(self, rec: Recommendation) -> str | None:
        """Harness-only: write the PENDING row. Never reachable from a skill allowlist.

        Returns an error text, or None on success (or if this rec_id was already recorded).
        """
        out = self._call("record_recommendation", {"recommendation": rec.model_dump(mode="json")})
        err = out.get("error")
        if err and "already recorded" not in str(err):
            return str(err)
        return None
