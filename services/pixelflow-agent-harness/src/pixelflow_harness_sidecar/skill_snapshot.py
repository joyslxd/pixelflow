"""提供 M0 所需的隔离文件系统 Skill 快照能力。"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class SkillSnapshotEntry:
    """表示单个冻结 Skill 的公开目录和完整正文。"""

    name: str
    description: str
    content_sha256: str
    body: str


@dataclass(frozen=True, slots=True)
class SkillCatalogSnapshot:
    """表示一个 Run 接受时不可变的共享 Skill 目录快照。"""

    catalog_digest: str
    entries: Mapping[str, SkillSnapshotEntry]

    def load(self, name: str) -> SkillSnapshotEntry:
        """按名称读取冻结正文，拒绝未知或运行中新增的 Skill。"""

        try:
            return self.entries[name]
        except KeyError as error:
            raise KeyError("冻结 Skill 快照中不存在该名称") from error

    def materialize(self, root: Path) -> None:
        """把冻结正文原子写入本 Run 专属根，禁止复用管理员可修改的源目录。"""

        if root.exists():
            raise ValueError("Run Skill 快照根已存在，拒绝覆盖或混入旧文件")
        root.mkdir(parents=True, mode=0o700)
        skills_root = root / "skills"
        skills_root.mkdir(mode=0o700)
        try:
            for entry in self.entries.values():
                skill_dir = skills_root / entry.name
                skill_dir.mkdir(mode=0o700)
                skill_file = skill_dir / "SKILL.md"
                temporary_file = skill_dir / ".SKILL.md.tmp"
                temporary_file.write_text(entry.body, encoding="utf-8")
                os.chmod(temporary_file, 0o400)
                temporary_file.replace(skill_file)
                os.chmod(skill_file, 0o400)
                os.chmod(skill_dir, 0o500)
            os.chmod(skills_root, 0o500)
        except Exception:
            # 失败时留下的半文件必须使该 Run 失败关闭，不能回退到源目录读取。
            raise


def snapshot_skill_root(root: Path, *, max_body_bytes: int = 32_768) -> SkillCatalogSnapshot:
    """从受控根读取 Skill，并在读取完成后返回独立内存快照。"""

    if max_body_bytes <= 0:
        raise ValueError("Skill 正文预算必须为正数")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Skill 根目录不存在或不是目录")

    entries: dict[str, SkillSnapshotEntry] = {}
    for skill_dir in sorted(root.iterdir(), key=lambda item: item.name):
        if skill_dir.is_symlink() or not skill_dir.is_dir():
            raise ValueError("Skill 根目录只能包含非链接的 Skill 目录")
        if not _SKILL_NAME.fullmatch(skill_dir.name):
            raise ValueError("Skill 名称必须使用 kebab-case")
        skill_file = skill_dir / "SKILL.md"
        if skill_file.is_symlink() or not skill_file.is_file():
            raise ValueError("Skill 目录缺少完整的 SKILL.md")
        raw = skill_file.read_bytes()
        if len(raw) > max_body_bytes:
            raise ValueError("Skill 正文超过 M0 预算")
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Skill 正文必须使用 UTF-8 编码") from error
        description = _extract_description(body, expected_name=skill_dir.name)
        entries[skill_dir.name] = SkillSnapshotEntry(
            name=skill_dir.name,
            description=description,
            content_sha256=f"sha256:{hashlib.sha256(raw).hexdigest()}",
            body=body,
        )

    digest_source = "\n".join(
        f"{entry.name}:{entry.content_sha256}" for entry in entries.values()
    ).encode("utf-8")
    return SkillCatalogSnapshot(
        catalog_digest=f"sha256:{hashlib.sha256(digest_source).hexdigest()}",
        entries=MappingProxyType(entries),
    )


def _extract_description(body: str, *, expected_name: str) -> str:
    """从可选 frontmatter 读取描述，缺失时拒绝不完整的 Skill。"""

    lines = body.splitlines()
    if len(lines) < 3 or lines[0] != "---":
        raise ValueError("Skill 必须使用包含 description 的 frontmatter")
    name: str | None = None
    description: str | None = None
    closed = False
    for line in lines[1:]:
        if line == "---":
            closed = True
            break
        if line.startswith("name:"):
            name = line.removeprefix("name:").strip().strip('"')
        if line.startswith("description:"):
            description = line.removeprefix("description:").strip().strip('"')
    if not closed:
        raise ValueError("Skill frontmatter 缺少结束分隔符")
    if name is not None and name != expected_name:
        raise ValueError("Skill frontmatter 名称与目录不一致")
    if not description:
        raise ValueError("Skill frontmatter 缺少 description")
    if len(description) > 512:
        raise ValueError("Skill description 超过 M0 预算")
    return description
