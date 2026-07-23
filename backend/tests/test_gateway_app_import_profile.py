from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml


def test_gateway_app_import_loads_profile_before_router_side_effects() -> None:
    """导入 Gateway app 时不应让 DeerFlow 先去查找旧 config.yaml。"""
    backend_dir = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "-c", "import app.gateway.app; import time; time.sleep(0.5)"],
        cwd=backend_dir,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0
    assert "`config.yaml` file not found" not in output


def test_gateway_app_import_rejects_invalid_agent_runtime_config() -> None:
    """网关导入阶段必须拒绝非法 Agent Runtime 开关，避免带病启动。"""
    backend_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PIXELFLOW_AGENT_RUNTIME_MODE"] = "automatic"

    completed = subprocess.run(
        [sys.executable, "-c", "import app.gateway.app"],
        cwd=backend_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "Agent Runtime 配置无效" in output


def test_gateway_app_import_rejects_explicit_null_agent_runtime_value(tmp_path: Path) -> None:
    """profile 显式 null 不能退回默认值后继续启动。"""
    source_profile = Path(__file__).resolve().parents[1] / "config.dev.yml"
    profile_data = yaml.safe_load(source_profile.read_text(encoding="utf-8"))
    profile_data["pixelflow"]["agent_runtime"] = {
        "mode": "off",
        "enabled_intents": [],
        "new_conversation_rollout_percent": 0,
        "context_compaction_enabled": None,
    }
    config_file = tmp_path / "config.invalid.yml"
    config_file.write_text(
        yaml.safe_dump(profile_data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PIXELFLOW_CONFIG_FILE"] = str(config_file)
    for key in (
        "PIXELFLOW_AGENT_RUNTIME_MODE",
        "PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS",
        "PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT",
        "PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED",
    ):
        env.pop(key, None)

    completed = subprocess.run(
        [sys.executable, "-c", "import app.gateway.app"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "pixelflow.agent_runtime.context_compaction_enabled" in output
