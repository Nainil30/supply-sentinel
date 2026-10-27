"""Autonomy tiers (config/autonomy.yaml). The LLM never sets a tier; this module does."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from supply_sentinel.engine.models import ActionItem, ActionType, SecurityFlag


@dataclass(frozen=True)
class Policy:
    action_tiers: dict[str, int]
    allowed_actions: dict[str, list[str]]
    second_approver_over_usd: float
    security_flag_min_tier: int
    grounding_failed_min_tier: int
    conflict_min_tier: int

    @classmethod
    def load(cls, config_dir: Path = Path("config")) -> "Policy":
        raw = yaml.safe_load((config_dir / "autonomy.yaml").read_text(encoding="utf-8"))
        esc = raw["escalation_rules"]
        return cls(
            action_tiers=dict(raw["action_tiers"]),
            allowed_actions={k: list(v) for k, v in raw["allowed_actions"].items()},
            second_approver_over_usd=float(esc["second_approver_over_usd"]),
            security_flag_min_tier=int(esc["security_flag_min_tier"]),
            grounding_failed_min_tier=int(esc["grounding_failed_min_tier"]),
            conflict_min_tier=int(esc["conflict_min_tier"]),
        )

    def is_allowed(self, exception_type: str, action: ActionType) -> bool:
        return action.value in self.allowed_actions.get(exception_type, [])

    def assign_tier(
        self,
        primary: ActionItem,
        secondary: ActionItem | None = None,
        *,
        value_usd: float | None = None,
        flags: list[SecurityFlag] | None = None,
        grounding_failed: bool = False,
        conflict: bool = False,
    ) -> int:
        """Highest tier among the actions, then raised by each escalation rule that applies."""
        tier = self.action_tiers[primary.action_type.value]
        if secondary is not None:
            tier = max(tier, self.action_tiers[secondary.action_type.value])
        if flags:
            tier = max(tier, self.security_flag_min_tier)
        if grounding_failed:
            tier = max(tier, self.grounding_failed_min_tier)
        if conflict:
            tier = max(tier, self.conflict_min_tier)
        if value_usd is not None and value_usd > self.second_approver_over_usd:
            tier = 2
        return tier


def action_value_usd(
    action: ActionItem, tool_log: dict[str, Any], baseline_unit_cost: float | None = None
) -> float | None:
    """Quantity x unit cost, computed by the harness (not the LLM) from a tool result."""
    if action.quantity is None:
        return None
    cost = baseline_unit_cost
    for out in tool_log.values():
        if isinstance(out, dict) and isinstance(out.get("unit_cost_usd"), int | float):
            cost = float(out["unit_cost_usd"])
            break
    return None if cost is None else float(action.quantity) * cost
