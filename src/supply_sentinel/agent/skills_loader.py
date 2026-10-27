"""Agent Skills loader (agentskills.io layout: skills/<name>/SKILL.md with YAML frontmatter).

Progressive disclosure: the index (name + description) is cheap and always in the prompt; the
body is read from disk only when a specialist loads that skill.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    path: Path


def _split(text: str, path: Path) -> tuple[dict[str, object], str]:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.DOTALL)
    if not m:
        raise SkillError(f"{path}: missing YAML frontmatter between '---' lines.")
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2).strip()


class SkillLibrary:
    def __init__(self, skills_dir: Path = Path("skills")) -> None:
        self.skills: dict[str, SkillMeta] = {}
        for path in sorted(skills_dir.glob("*/SKILL.md")):
            meta, _ = _split(path.read_text(encoding="utf-8"), path)
            name, desc = str(meta.get("name", "")), str(meta.get("description", ""))
            if not NAME_RE.match(name) or len(name) > 64 or name != path.parent.name:
                raise SkillError(f"{path}: name '{name}' must match its folder, lowercase-hyphen.")
            if not desc or len(desc) > 1024:
                raise SkillError(f"{path}: description is required and at most 1024 characters.")
            tools = tuple(str(meta.get("allowed-tools", "")).split())
            self.skills[name] = SkillMeta(name, desc, tools, path)

    def index_text(self) -> str:
        """Name and description of every skill (~100 tokens each)."""
        return "\n".join(f"- {s.name}: {s.description}" for s in self.skills.values())

    def get(self, name: str) -> SkillMeta:
        try:
            return self.skills[name]
        except KeyError:
            raise SkillError(f"Unknown skill '{name}'. Known: {sorted(self.skills)}.") from None

    def body(self, name: str) -> str:
        """Read the full instructions now (this is the 'load' step)."""
        meta = self.get(name)
        return _split(meta.path.read_text(encoding="utf-8"), meta.path)[1]

    def allowed_tools(self, name: str) -> tuple[str, ...]:
        return self.get(name).allowed_tools
