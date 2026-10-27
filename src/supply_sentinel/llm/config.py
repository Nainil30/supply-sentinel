"""Load config/models.yaml into small typed profile objects."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from supply_sentinel.llm.types import Role


class RoleConfig(BaseModel):
    model: str
    temperature: float = 0.0


class ProfileConfig(BaseModel):
    name: str
    status: str = "active"
    provider: str = ""
    base_url: str | None = None
    api_key_env: str | None = None
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    # replay only: the profile whose models and cassette folder we replay
    source_profile: str | None = None
    cassette_dir: str = "evals/cassettes"

    def role(self, role: Role) -> RoleConfig:
        try:
            return self.roles[role]
        except KeyError:
            raise KeyError(f"Profile '{self.name}' has no model for role '{role}'.") from None


def load_profile(name: str, config_dir: Path = Path("config")) -> ProfileConfig:
    raw = yaml.safe_load((config_dir / "models.yaml").read_text(encoding="utf-8"))
    profiles = raw.get("profiles", {})
    if name not in profiles:
        raise KeyError(f"Unknown profile '{name}'. Choose one of {sorted(profiles)}.")
    data = dict(profiles[name])
    data["name"] = name
    data["roles"] = {k: RoleConfig(**v) for k, v in data.get("roles", {}).items()}
    return ProfileConfig(**data)
