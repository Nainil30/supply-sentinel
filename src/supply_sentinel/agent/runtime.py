"""Everything a node needs that is not state: snapshot, tools, model router, skills, config.

Built once per process (`sentinel run` or `sentinel resume`) and captured by the graph's nodes.
State holds only plain data, so a paused run can resume in a new process.
"""

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from supply_sentinel.agent.mcp_bridge import McpBridge
from supply_sentinel.agent.policy import Policy
from supply_sentinel.agent.skills_loader import SkillLibrary
from supply_sentinel.llm.router import Router
from supply_sentinel.llm.types import ProviderError
from supply_sentinel.mcp_server.context import Snapshot
from supply_sentinel.observability.ledger import Ledger


def load_settings(config_dir: Path = Path("config")) -> dict[str, Any]:
    return dict(yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8")))


@dataclass
class RunContext:
    run_id: str
    snap: Snapshot
    bridge: McpBridge
    router: Router
    skills: SkillLibrary
    policy: Policy
    settings: dict[str, Any]
    ledger: Ledger
    state_dir: Path
    profile: str
    wall_seconds: float  # per-specialist wall-clock limit
    _used: dict[str, int] = field(default_factory=lambda: {"llm": 0, "tool": 0})
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _t0: float = field(default_factory=time.monotonic)

    def charge(self, kind: str) -> None:
        """Count one LLM or tool call against the run budget (settings `run_limits`).

        Raises ProviderError when a limit is hit, so the specialist falls back to the rules
        baseline exactly as it does for a provider outage.
        """
        limits = self.settings["run_limits"]
        with self._lock:
            if time.monotonic() - self._t0 > float(limits["wall_seconds"]):
                raise ProviderError(f"run budget exceeded: over {limits['wall_seconds']}s")
            self._used[kind] += 1
            cap = int(limits[f"max_{kind}_calls"])
            if self._used[kind] > cap:
                raise ProviderError(f"run budget exceeded: more than {cap} {kind} calls")

    @property
    def run_dir(self) -> Path:
        return self.state_dir / "runs" / self.run_id
