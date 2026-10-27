"""Engine settings. Defaults mirror `config/settings.yaml` and `config/autonomy.yaml`.

`load_config` reads those files; tests use `EngineConfig()` directly so they need no files.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class EngineConfig(BaseModel):
    horizon_weeks: int = 26  # netting horizon: weeks 0-25
    commit_gap_weeks: int = 12  # commit gaps are checked for weeks 0-11
    need_52w_weeks: int = 52
    safety_lookback_weeks: int = 13  # average weekly GR over weeks 0-12
    # LTB (newsvendor)
    salvage_pct: float = 0.10
    holding_rate: float = 0.20
    cv_floor: float = 0.10
    shortage_penalty_multiple: dict[str, float] = Field(
        default_factory=lambda: {"default": 5.0, "ASIC": 8.0}
    )
    bridge_cutover_buffer_weeks: int = 8
    # E&O
    min_excess_usd: float = 5000.0
    safety_weeks: float = 2.0
    # allocation and approvals
    allocation_priority: list[str] = Field(
        default_factory=lambda: ["SERVICE", "NPI", "SUSTAIN", "EOL_ANNOUNCED"]
    )
    second_approver_over_usd: float = 50000.0
    action_tiers: dict[str, int] = Field(default_factory=dict)

    def penalty_multiple(self, commodity: str) -> float:
        return self.shortage_penalty_multiple.get(
            commodity, self.shortage_penalty_multiple.get("default", 5.0)
        )


def load_config(config_dir: Path = Path("config")) -> EngineConfig:
    settings = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
    autonomy = yaml.safe_load((config_dir / "autonomy.yaml").read_text(encoding="utf-8"))
    ltb = settings["ltb"]
    return EngineConfig(
        salvage_pct=ltb["salvage_pct"],
        holding_rate=ltb["holding_rate"],
        cv_floor=ltb["cv_floor"],
        shortage_penalty_multiple=ltb["shortage_penalty_multiple"],
        min_excess_usd=settings["eo"]["min_excess_usd"],
        safety_weeks=settings["eo"]["safety_weeks"],
        allocation_priority=settings["allocation_priority"],
        second_approver_over_usd=autonomy["escalation_rules"]["second_approver_over_usd"],
        action_tiers=autonomy["action_tiers"],
    )
