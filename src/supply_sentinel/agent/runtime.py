"""Everything a node needs that is not state: snapshot, tools, model router, skills, config.

Built once per process (`sentinel run` or `sentinel resume`) and captured by the graph's nodes.
State holds only plain data, so a paused run can resume in a new process.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from supply_sentinel.agent.mcp_bridge import McpBridge
from supply_sentinel.agent.policy import Policy
from supply_sentinel.agent.skills_loader import SkillLibrary
from supply_sentinel.llm.router import Router
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

    @property
    def run_dir(self) -> Path:
        return self.state_dir / "runs" / self.run_id
