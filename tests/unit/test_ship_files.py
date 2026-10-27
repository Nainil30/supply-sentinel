"""Phase 9 shipping files exist, parse, and cassettes hold no secrets."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TOKEN_LIKE = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_-]{30,}|Bearer\s+[A-Za-z0-9._-]{20,})"
)


def test_cassettes_contain_no_token_like_strings():
    files = list((ROOT / "evals" / "cassettes").rglob("*.jsonl"))
    assert files
    for path in files:
        assert not TOKEN_LIKE.search(path.read_text(encoding="utf-8")), path


def test_compose_defaults_to_replay_ui_without_keys():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "profiles" not in compose["services"]["ui"]
    assert "environment" not in compose["services"]["ui"]
    entry = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "--profile replay --hosted" in entry


def test_devcontainer_and_release_workflow_parse():
    json.loads((ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))
    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    assert wf[True]["push"]["tags"] == ["v*"]


def test_copilot_agent_has_frontmatter():
    text = (ROOT / ".github" / "agents" / "supply-reviewer.agent.md").read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    assert front["name"] == "supply-reviewer"
    assert front["description"]
    assert "supply-sentinel" in front["mcp-servers"]
