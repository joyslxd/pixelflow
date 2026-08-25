"""验证 Python M00 本地门禁的固定命令矩阵与中文 fail-closed 规则。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def _load_gate_module():
    """直接加载仓库脚本，避免为门禁工具引入运行时业务包依赖。"""

    script = Path(__file__).parents[2] / "scripts/agentization/m00_local_gate.py"
    specification = importlib.util.spec_from_file_location("m00_local_gate", script)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> None:
    """在隔离临时仓库执行 Git 命令，所有提交只用于门禁规则验证。"""

    subprocess.run(("git", "-C", str(repository), *arguments), check=True, capture_output=True)


def _commit(repository: Path, message: str) -> str:
    """提交临时测试文件并返回稳定 SHA。"""

    _git(repository, "add", ".")
    _git(repository, "-c", "user.name=M00 测试", "-c", "user.email=m00@example.test", "commit", "-m", message)
    completed = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_m00_command_matrix_covers_sidecar_without_powershell() -> None:
    """M00 只依赖 Python、Node 与原生 Shell，不再要求 PowerShell/Pester。"""

    gate = _load_gate_module()
    root = Path(__file__).parents[2]
    commands = gate.build_m00_commands(root, require_environment=False)
    rendered = "\n".join(f"{item.executable} {' '.join(item.arguments)}" for item in commands)

    assert "powershell" not in rendered.lower()
    assert "Invoke-Pester" not in rendered
    assert "not m0_real and not mem0_real" in rendered
    assert "pixelflow/agent_runtime" not in rendered
    assert "npm run build" in rendered
    assert "pytest tests" in rendered


def test_chinese_policy_rejects_new_english_comment(tmp_path: Path) -> None:
    """新增英文人工注释必须失败关闭，避免 Python 迁移放宽原 M00 规则。"""

    gate = _load_gate_module()
    _git(tmp_path, "init")
    source = tmp_path / "sample.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    base = _commit(tmp_path, "测试：建立中文门禁基线")
    source.write_text("# English explanation\ndef value():\n    return 1\n", encoding="utf-8")
    _commit(tmp_path, "测试：加入待拒绝注释")

    with pytest.raises(gate.GateViolation, match="人工注释缺少中文说明"):
        gate.check_chinese_engineering_policy(tmp_path, base)


def test_chinese_policy_accepts_chinese_comment_and_schema(tmp_path: Path) -> None:
    """中文注释及 JSON schema 逐项说明满足门禁后可以通过。"""

    gate = _load_gate_module()
    _git(tmp_path, "init")
    source = tmp_path / "sample.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    base = _commit(tmp_path, "测试：建立配置门禁基线")
    source.write_text("# 用途：返回稳定值；影响：仅用于测试。\ndef value():\n    return 1\n", encoding="utf-8")
    (tmp_path / "plugin.json").write_text('{"enabled": true}', encoding="utf-8")
    (tmp_path / "plugin.schema.json").write_text(
        '{"properties":{"enabled":{"description":"用途：控制测试开关；影响：开启后执行测试。"}}}',
        encoding="utf-8",
    )
    _commit(tmp_path, "测试：补充中文配置说明")

    gate.check_chinese_engineering_policy(tmp_path, base)
