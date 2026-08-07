"""只读取已启用 Skill 元数据的 VideoAgent 能力目录。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from deerflow.skills.types import Skill


class _SkillStorage(Protocol):
    def load_skills(self, *, enabled_only: bool = False) -> list[Skill]: ...


@dataclass(frozen=True)
class SkillManifest:
    """供规划器选择的最小 Skill 元数据，不包含完整指导正文。"""

    name: str
    description: str
    category: str
    allowed_tools: tuple[str, ...] | None
    guidance_ref: str


class SkillCatalog:
    """通过 DeerFlow SkillStorage 加载并筛选 VideoAgent 可用指导。"""

    def __init__(self, *, storage: _SkillStorage | None = None) -> None:
        if storage is None:
            from deerflow.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
        self._storage = storage

    def load_applicable(
        self,
        *,
        tool_names: Iterable[str],
        skill_names: Iterable[str] | None = None,
    ) -> tuple[SkillManifest, ...]:
        """返回与当前注册工具相容的已启用 Skill 元数据。"""

        available_tools = {name.strip() for name in tool_names if name.strip()}
        requested_skills = (
            {name.strip() for name in skill_names if name.strip()}
            if skill_names is not None
            else None
        )
        manifests: list[SkillManifest] = []
        skills: Sequence[Skill] = self._storage.load_skills(enabled_only=True)
        for skill in skills:
            if requested_skills is not None and skill.name not in requested_skills:
                continue
            allowed_tools = (
                tuple(dict.fromkeys(skill.allowed_tools))
                if skill.allowed_tools is not None
                else None
            )
            if allowed_tools is not None and not available_tools.intersection(allowed_tools):
                continue
            manifests.append(
                SkillManifest(
                    name=skill.name,
                    description=skill.description,
                    category=str(skill.category),
                    allowed_tools=allowed_tools,
                    guidance_ref=skill.get_container_file_path(),
                )
            )
        return tuple(sorted(manifests, key=lambda manifest: manifest.name))
