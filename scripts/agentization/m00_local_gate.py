#!/usr/bin/env python3
"""提供 macOS/Linux 可直接执行的 M00 中文工程与本地质量门禁。"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_CHINESE = re.compile(r"[\u3400-\u9fff]")
_GRANDFATHERED_COMMITS = {
    "0af72ff6993e9e67636f21e8e16d641411702d67",
}
_CODE_EXTENSIONS = {".py", ".sh", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_CONFIG_EXTENSIONS = {".yml", ".yaml", ".toml", ".ini", ".conf", ".properties"}


class GateViolation(RuntimeError):
    """表示本地门禁发现了必须失败关闭的工程规范问题。"""


@dataclass(frozen=True)
class GateCommand:
    """表示一条由 M00 统一调度的本地检查命令。"""

    working_directory: str
    executable: str
    arguments: tuple[str, ...]


def _run_git(repository: Path, *arguments: str, allow_failure: bool = False) -> str:
    """调用 Git 并在失败时返回不含环境秘密的固定诊断。"""

    result = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0 and not allow_failure:
        raise GateViolation(f"Git 命令失败：git {' '.join(arguments)}")
    return result.stdout


def resolve_repository_root(repository_path: Path) -> Path:
    """解析 Git 顶层目录，避免门禁错误扫描调用者当前目录。"""

    root = _run_git(repository_path, "rev-parse", "--show-toplevel").strip()
    if not root:
        raise GateViolation("无法解析 Git 仓库根目录")
    return Path(root).resolve()


def _require_executable(path: Path, description: str) -> Path:
    """要求项目受控可执行文件存在，禁止回退到 PATH 中的任意解释器。"""

    if not path.is_file():
        raise GateViolation(f"缺少{description}：{path}")
    return path


def _backend_python(root: Path) -> Path:
    """定位后端受控 Python 环境。"""

    return _require_executable(root / "backend/.venv/bin/python", "backend/.venv/bin/python")


def _sidecar_python(root: Path) -> Path:
    """定位 Sidecar 受控 Python 环境。"""

    return _require_executable(
        root / "services/pixelflow-agent-harness/.venv/bin/python",
        "services/pixelflow-agent-harness/.venv/bin/python",
    )


def _sidecar_python_command(root: Path, *arguments: str) -> GateCommand:
    """生成与部署架构一致的 Sidecar Python 命令。"""

    sidecar_root = root / "services/pixelflow-agent-harness"
    executable = _sidecar_python(root)
    if platform.system() == "Darwin":
        arch = _require_executable(Path("/usr/bin/arch"), "macOS /usr/bin/arch")
        return GateCommand(str(sidecar_root), str(arch), ("-arm64", str(executable), *arguments))
    return GateCommand(str(sidecar_root), str(executable), tuple(arguments))


def build_m00_commands(root: Path, *, require_environment: bool) -> list[GateCommand]:
    """构造 M00 固定命令矩阵；真实模型测试不在默认门禁中执行。"""

    backend_python = _backend_python(root) if require_environment else root / "backend/.venv/bin/python"
    if require_environment:
        _sidecar_python(root)
    backend = root / "backend"
    web = root / "web"
    sidecar_plugin = root / "services/pixelflow-agent-harness/engines/deepseek/packages/dsh-plugin-capability-tools"
    commands = [
        GateCommand(str(root), "git", ("diff", "--check")),
        GateCommand(
            str(backend),
            str(backend_python),
            ("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"),
        ),
        GateCommand(
            str(backend),
            str(backend_python),
            (
                "-m", "pytest",
                "tests",
                "-m", "not m0_real and not mem0_real",
                "-q",
            ),
        ),
        GateCommand(
            str(backend), str(backend_python),
            (
                "-m", "ruff", "check",
                "../scripts/agentization/m00_local_gate.py",
                "app",
                "pixelflow",
                "tests",
            ),
        ),
        GateCommand(str(web), "corepack", ("pnpm", "test:agent-runtime-contracts")),
        GateCommand(str(web), "corepack", ("pnpm", "test")),
        GateCommand(str(web), "corepack", ("pnpm", "lint")),
        GateCommand(str(web), "corepack", ("pnpm", "build-prod")),
        _sidecar_python_command(root, "-m", "ruff", "check", "src", "tests"),
        _sidecar_python_command(root, "-m", "pytest", "tests", "-m", "not m0_real", "-q"),
        GateCommand(str(sidecar_plugin), "npm", ("run", "build")),
    ]
    return commands


def _contains_chinese(value: str | None) -> bool:
    """判断文本是否包含中文主体语义。"""

    return bool(value and _CHINESE.search(value))


def _added_lines(diff: str) -> list[tuple[int, str]]:
    """解析零上下文 Git diff 的新增行及其目标行号。"""

    entries: list[tuple[int, str]] = []
    line_number = 0
    in_hunk = False
    for line in diff.splitlines():
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            line_number = int(match.group(1))
            in_hunk = True
            continue
        if not in_hunk or line.startswith("+++"):
            continue
        if line.startswith("+"):
            entries.append((line_number, line[1:]))
            line_number += 1
        elif line.startswith("-"):
            continue
        elif not line.startswith("\\"):
            line_number += 1
    return entries


def _comment_text(line: str, extension: str) -> str | None:
    """提取新增行中的人工注释，机器指令后续由白名单排除。"""

    stripped = line.lstrip("\ufeff").lstrip()
    match = re.match(r"^(?:#|//|/\*+|<!--)\s*(.*)$", stripped)
    if match:
        return match.group(1).strip()
    block_comment = re.match(r"^\*+\s+(.*)$", stripped)
    if block_comment:
        return block_comment.group(1).strip()
    if extension in {".py", ".sh", ".yml", ".yaml", ".toml"}:
        match = re.search(r"\s+#\s*(.+)$", line)
    elif extension in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        match = re.search(r"\s+//\s*(.+)$", line)
    else:
        match = None
    return match.group(1).strip() if match else None


def _is_machine_directive(comment: str) -> bool:
    """识别允许保留英文的最小机器指令白名单。"""

    return bool(re.match(
        r"^(?:!|noqa|type:\s*ignore|pyright:|mypy:|ruff:|eslint|prettier|istanbul|c8\b|SPDX-|https?://|[-=_*#/.]+$)",
        comment.strip(),
    ))


def _read_git_file(root: Path, head_ref: str, relative_path: str) -> str:
    """从待检查提交读取完整文件，避免检查工作区中未提交的偶然内容。"""

    return _run_git(root, "show", f"{head_ref}:{relative_path}")


def _adjacent_config_comment(lines: list[str], index: int) -> bool:
    """检查配置叶子项是否紧邻中文用途说明，影响语义由独立 reviewer 复核。"""

    inline = re.search(r"#\s*(.+)$", lines[index])
    if inline and _contains_chinese(inline.group(1)) and "用途" in inline.group(1):
        return True
    for cursor in range(index - 1, -1, -1):
        candidate = lines[cursor].strip()
        if not candidate:
            continue
        return candidate.startswith("#") and _contains_chinese(candidate) and "用途" in candidate
    return False


def _json_leaf_paths(value: Any, prefix: str = "") -> Iterable[str]:
    """产出 JSON 的叶子键路径，用于逐键寻找 schema description。"""

    if isinstance(value, dict):
        if not value:
            yield prefix
        for key, child in value.items():
            yield from _json_leaf_paths(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, list):
        if not value:
            yield prefix
        for index, child in enumerate(value):
            yield from _json_leaf_paths(child, f"{prefix}[{index}]")
    else:
        yield prefix


def _schema_description(schema: dict[str, Any], leaf_path: str) -> str | None:
    """按叶子路径读取 schema 中对应字段的中文说明。"""

    node: Any = schema
    for raw_segment in leaf_path.split("."):
        segment = re.sub(r"\[\d+\]$", "", raw_segment)
        properties = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(properties, dict) or segment not in properties:
            return None
        node = properties[segment]
        if re.search(r"\[\d+\]$", raw_segment):
            node = node.get("items") if isinstance(node, dict) else None
    return node.get("description") if isinstance(node, dict) else None


def check_chinese_engineering_policy(root: Path, base_ref: str, head_ref: str = "HEAD") -> None:
    """执行提交、注释、docstring 与配置逐项中文说明的 fail-closed 检查。"""

    violations: list[str] = []
    commits = [item for item in _run_git(root, "rev-list", "--reverse", f"{base_ref}..{head_ref}").splitlines() if item]
    for commit in commits:
        if commit in _GRANDFATHERED_COMMITS:
            continue
        subject = _run_git(root, "show", "-s", "--format=%s", commit).strip()
        body = _run_git(root, "show", "-s", "--format=%b", commit).strip()
        if not _contains_chinese(subject):
            violations.append(f"提交标题缺少中文主体语义：{commit}")
        if body and not _contains_chinese(body):
            violations.append(f"提交正文缺少中文主体语义：{commit}")

    changed_paths = [item for item in _run_git(root, "diff", "--name-only", "--diff-filter=ACMR", base_ref, head_ref).splitlines() if item]
    for relative_path in changed_paths:
        extension = Path(relative_path).suffix.lower()
        diff = _run_git(root, "diff", "--unified=0", "--no-color", base_ref, head_ref, "--", relative_path)
        if extension in _CODE_EXTENSIONS:
            for line_number, line in _added_lines(diff):
                comment = _comment_text(line, extension)
                if comment and not _is_machine_directive(comment) and re.search(r"[A-Za-z]{2,}", comment) and not _contains_chinese(comment):
                    violations.append(f"人工注释缺少中文说明：{relative_path} 第 {line_number} 行")
                if extension == ".py" and re.match(r"^\s*(?:\"\"\"|''')", line) and re.search(r"[A-Za-z]{2,}", line) and not _contains_chinese(line):
                    violations.append(f"docstring 缺少中文说明：{relative_path} 第 {line_number} 行")
        if extension in _CONFIG_EXTENSIONS:
            lines = _read_git_file(root, head_ref, relative_path).splitlines()
            for index, line in enumerate(lines):
                yaml_leaf = extension in {".yml", ".yaml"} and bool(re.match(r"^\s*(?:-\s*)?[A-Za-z0-9_.-]+\s*:\s*[^#\s].*", line))
                equals_leaf = extension in {".toml", ".ini", ".conf", ".properties"} and bool(re.match(r"^\s*[A-Za-z0-9_.-]+\s*=\s*[^#;\s].*", line))
                if (yaml_leaf or equals_leaf) and not _adjacent_config_comment(lines, index):
                    violations.append(f"叶子配置缺少紧邻中文用途和影响说明：{relative_path} 第 {index + 1} 行")
        has_config_name = re.search(
            r"(?i)(config|settings|manifest)[^/]*\.json$|(^|/)(package|plugin|langgraph|tsconfig[^/]*)\.json$",
            relative_path,
        )
        is_fixture_or_schema = re.search(r"(?i)\.schema\.json$|(^|/)(tests?|fixtures?)/", relative_path)
        is_json_config = extension == ".json" and has_config_name and not is_fixture_or_schema
        if is_json_config:
            config = json.loads(_read_git_file(root, head_ref, relative_path))
            schema_path = str(Path(relative_path).with_suffix(".schema.json"))
            try:
                schema = json.loads(_read_git_file(root, head_ref, schema_path))
            except GateViolation:
                violations.append(f"JSON 配置缺少同目录 schema：{relative_path} -> {schema_path}")
                continue
            for leaf_path in _json_leaf_paths(config):
                description = _schema_description(schema, leaf_path)
                if not (_contains_chinese(description) and description and "用途" in description and "影响" in description):
                    violations.append(f"JSON 配置键缺少中文用途和影响 description：{relative_path} -> {leaf_path}")
    if violations:
        raise GateViolation("中文工程规范检查失败：\n- " + "\n- ".join(violations))


def _execute(command: GateCommand) -> None:
    """执行一条门禁命令并隐藏不必要的子进程实现细节。"""

    result = subprocess.run(
        (command.executable, *command.arguments),
        cwd=command.working_directory,
        check=False,
    )
    if result.returncode:
        raise GateViolation(f"M00 门禁命令失败：{command.executable} {' '.join(command.arguments)}")


def main(argv: list[str] | None = None) -> int:
    """解析终端参数，先做中文检查再执行固定 M00 命令矩阵。"""

    parser = argparse.ArgumentParser(description="执行 PixelFlow M00 本地门禁")
    parser.add_argument("--repository-path", default=".", help="Git 仓库路径")
    parser.add_argument("--base-ref", required=True, help="中文工程检查的基线提交或引用")
    parser.add_argument("--head-ref", default="HEAD", help="待检查提交或引用")
    parser.add_argument("--plan-only", action="store_true", help="只输出命令矩阵，不执行检查")
    arguments = parser.parse_args(argv)
    root = resolve_repository_root(Path(arguments.repository_path))
    commands = build_m00_commands(root, require_environment=not arguments.plan_only)
    if arguments.plan_only:
        print(json.dumps([asdict(command) for command in commands], ensure_ascii=False, indent=2))
        return 0
    check_chinese_engineering_policy(root, arguments.base_ref, arguments.head_ref)
    for command in commands:
        _execute(command)
    print("M00 Python 本地门禁通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateViolation as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
