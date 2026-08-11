"""V2.1 批次 E：生产路径不得再 import 已隔离的 agent_workflows.video。"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = (
    BACKEND_ROOT / "pixelflow" / "video_agent",
    BACKEND_ROOT / "pixelflow" / "agent_runtime",
    BACKEND_ROOT / "app" / "gateway",
)
BANNED_PREFIXES = (
    "pixelflow.agent_workflows.video",
)


def _collect_imports(filepath: Path) -> list[tuple[int, str]]:
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            results.append((node.lineno, node.module))
    return results


def _is_banned(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in BANNED_PREFIXES
    )


def test_production_paths_do_not_import_agent_workflows_video() -> None:
    violations: list[str] = []
    for root in SCAN_ROOTS:
        for py_file in sorted(root.rglob("*.py")):
            for lineno, module in _collect_imports(py_file):
                if _is_banned(module):
                    rel = py_file.relative_to(BACKEND_ROOT)
                    violations.append(f"  {rel}:{lineno}  imports {module}")

    assert not violations, (
        "Gateway / video_agent / agent_runtime must not import "
        "pixelflow.agent_workflows.video (Batch E quarantine):\n"
        + "\n".join(violations)
    )
