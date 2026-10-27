"""Record and replay chat responses (spec 04 section 8).

Key = SHA-256 of (role, model id, messages, tool schemas), so a replayed run must send exactly
the prompts that were recorded. A miss names the key and the last message so drift is easy to see.
"""

import hashlib
import json
import threading
from pathlib import Path

from supply_sentinel.llm.types import ChatClient, LLMResponse, Msg, ProviderError, Role


class CassetteMiss(ProviderError):
    """No recorded response for this request."""


def request_key(role: str, model: str, messages: list[Msg], tools: list[dict[str, object]]) -> str:
    payload = {
        "role": role,
        "model": model,
        "messages": [
            m.model_dump(exclude_defaults=True, exclude={"tool_calls": {"__all__": {"extra"}}})
            for m in messages
        ],
        "tools": tools,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CassetteClient:
    """With `inner` it records (calls the model, appends); without, it only replays."""

    def __init__(
        self, path: Path, model_ids: dict[str, str], inner: ChatClient | None = None
    ) -> None:
        self.path = path
        self.inner = inner
        self._model_ids = model_ids
        self._lock = threading.Lock()
        self._entries: dict[str, LLMResponse] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line:
                    row = json.loads(line)
                    self._entries[row["key"]] = LLMResponse(**row["response"])

    @property
    def mode(self) -> str:
        return "record" if self.inner is not None else "replay"

    def model_id(self, role: Role) -> str:
        return self._model_ids[role]

    def complete(
        self,
        role: Role,
        messages: list[Msg],
        tools: list[dict[str, object]],
        json_schema: dict[str, object] | None = None,
    ) -> LLMResponse:
        key = request_key(role, self.model_id(role), messages, tools)
        if key in self._entries:
            return self._entries[key].model_copy(update={"cached": True})
        if self.inner is None:
            last = (messages[-1].content if messages else "")[:160].replace("\n", " ")
            raise CassetteMiss(
                f"No cassette entry {key[:12]} in {self.path} (role={role}). "
                f"Prompts changed since recording? Last message: {last!r}. "
                "Re-record with `sentinel run --profile local --record`."
            )
        resp = self.inner.complete(role, messages, tools, json_schema)
        with self._lock:
            self._entries[key] = resp
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                row = {"key": key, "role": role, "response": resp.model_dump()}
                f.write(json.dumps(row) + "\n")
        return resp
