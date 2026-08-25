from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_gateway_app_import_loads_profile_before_router_side_effects() -> None:
    """导入 Gateway app 时只加载 PixelFlow profile，不依赖已删除的旧配置。"""
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
    assert "config.yaml" not in output
