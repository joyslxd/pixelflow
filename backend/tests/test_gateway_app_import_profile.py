from __future__ import annotations

import subprocess
from pathlib import Path


def test_gateway_app_import_loads_profile_before_router_side_effects() -> None:
    """导入 Gateway app 时不应让 DeerFlow 先去查找旧 config.yaml。"""
    backend_dir = Path(__file__).resolve().parents[1]
    python_bin = backend_dir / ".venv" / "bin" / "python"

    completed = subprocess.run(
        [str(python_bin), "-c", "import app.gateway.app; import time; time.sleep(0.5)"],
        cwd=backend_dir,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0
    assert "`config.yaml` file not found" not in output
