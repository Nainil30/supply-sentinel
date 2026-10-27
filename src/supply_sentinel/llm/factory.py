"""Build the right `Router` for a profile name (live, recording, or replaying)."""

from pathlib import Path

from supply_sentinel.llm.config import ProfileConfig, load_profile
from supply_sentinel.llm.provider import LiveClient
from supply_sentinel.llm.replay import CassetteClient
from supply_sentinel.llm.router import Router
from supply_sentinel.llm.types import ChatClient
from supply_sentinel.observability.ledger import Ledger

ROLES = ("extractor", "specialist", "writer", "judge")


def cassette_path(profile: ProfileConfig, source: ProfileConfig, name: str) -> Path:
    return Path(profile.cassette_dir) / source.name / f"{name}.jsonl"


def build_router(
    profile_name: str,
    cassette_name: str,
    ledger: Ledger,
    record: bool = False,
    config_dir: Path = Path("config"),
    client: ChatClient | None = None,
) -> tuple[Router, ProfileConfig]:
    """`client` lets tests inject a scripted model. Returns the router and the model profile."""
    profile = load_profile(profile_name, config_dir)
    if client is not None:
        return Router(client, profile.limits, ledger), profile
    if profile.name == "replay":
        source = load_profile(profile.source_profile or "local", config_dir)
        ids = {r: source.role(r).model for r in ROLES if r in source.roles}  # type: ignore[arg-type]
        cassette = CassetteClient(cassette_path(profile, source, cassette_name), ids)
        return Router(cassette, {}, ledger), source
    live = LiveClient(profile)
    if record:
        ids = {r: profile.role(r).model for r in ROLES if r in profile.roles}  # type: ignore[arg-type]
        path = Path(load_profile("replay", config_dir).cassette_dir) / profile.name
        cassette = CassetteClient(path / f"{cassette_name}.jsonl", ids, inner=live)
        return Router(cassette, profile.limits, ledger), profile
    return Router(live, profile.limits, ledger), profile
