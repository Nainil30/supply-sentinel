"""JSONL run ledger: the system of record for LLM calls, tool calls and decisions."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Ledger:
    def __init__(self, path: Path | None, run_id: str = "") -> None:
        self.path = path
        self.run_id = run_id
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        row = {"ts": datetime.now(UTC).isoformat(), "run_id": self.run_id, "event": event}
        row.update(fields)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def read(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x]
