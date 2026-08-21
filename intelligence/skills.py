from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    agents: tuple[str, ...]
    keywords: tuple[str, ...]
    priority: int
    content: str
    path: str


class SkillManager:
    """Load small, auditable prompt policies from ``skills/*/SKILL.md``."""

    def __init__(self, root: Path, max_prompt_chars: int = 4000):
        self.root = root
        self.max_prompt_chars = max(500, max_prompt_chars)
        self.skills: list[Skill] = []
        self.errors: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.skills = []
        self.errors = []
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*/SKILL.md")):
            try:
                self.skills.append(self._read(path))
            except (OSError, ValueError) as exc:
                self.errors.append(f"{path}: {exc}")
        self.skills.sort(key=lambda item: item.priority, reverse=True)

    def match(self, agents: list[str], message: str) -> list[Skill]:
        normalized = (message or "").lower()
        agent_set = set(agents)
        matched = []
        for skill in self.skills:
            agent_match = not skill.agents or bool(agent_set.intersection(skill.agents))
            keyword_match = not skill.keywords or any(item.lower() in normalized for item in skill.keywords)
            if agent_match and keyword_match:
                matched.append(skill)
        return matched

    def render(self, agents: list[str], message: str) -> tuple[str, list[str]]:
        chunks: list[str] = []
        names: list[str] = []
        remaining = self.max_prompt_chars
        for skill in self.match(agents, message):
            block = f"[{skill.name}]\n{skill.content.strip()}"
            if len(block) > remaining:
                block = block[:remaining]
            if not block:
                break
            chunks.append(block)
            names.append(skill.name)
            remaining -= len(block)
            if remaining <= 0:
                break
        return "\n\n".join(chunks), names

    @staticmethod
    def _read(path: Path) -> Skill:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError("缺少 YAML 风格头部")
        try:
            header, body = text[4:].split("\n---\n", 1)
        except ValueError as exc:
            raise ValueError("头部没有结束标记") from exc
        metadata: dict[str, str] = {}
        for line in header.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
        name = metadata.get("name") or path.parent.name
        agents = tuple(item.strip() for item in metadata.get("agents", "").split(",") if item.strip())
        keywords = tuple(item.strip() for item in metadata.get("keywords", "").split(",") if item.strip())
        return Skill(
            name=name,
            agents=agents,
            keywords=keywords,
            priority=int(metadata.get("priority", "50")),
            content=body.strip(),
            path=str(path),
        )
