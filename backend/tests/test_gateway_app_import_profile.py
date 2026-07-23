from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_gateway_app_import_loads_profile_before_router_side_effects() -> None:
    """导入 Gateway app 时不应让 DeerFlow 先去查找旧 config.yaml。"""
    backend_dir = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "-c", "import app.gateway.app; import time; time.sleep(0.5)"],
        cwd=backend_dir,
        text=True,
        capture_output=True,
        timeout=10,
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
        timeout=10,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "Agent Runtime 配置无效" in output
