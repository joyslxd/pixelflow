"""PixelFlow 会话级 Agent Runtime 的稳定合同入口。"""

from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Any, Final

_PUBLIC_MODULES: Final[dict[str, str]] = {
    "SupervisorReplayDisposition": "replay",
    "SupervisorReplayResult": "replay",
    "SupervisorReplayRuntime": "replay",
    "WorkflowCommandPreview": "replay",
}
_PUBLIC_IMPORT_LOCK: Final[RLock] = RLock()
_MISSING: Final[object] = object()

__all__ = [
    "SupervisorReplayDisposition",
    "SupervisorReplayResult",
    "SupervisorReplayRuntime",
    "WorkflowCommandPreview",
]


def __getattr__(name: str) -> Any:
    """按公开符号所在模块惰性加载，并保留真实导入异常。"""

    module_name = _PUBLIC_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"模块 {__name__!r} 没有属性 {name!r}")
    with _PUBLIC_IMPORT_LOCK:
        existing = globals().get(name, _MISSING)
        if existing is not _MISSING:
            return existing
        module = import_module(f"{__name__}.{module_name}")
        value = getattr(module, name)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """让交互式检查继续展示全部稳定公开符号。"""

    return sorted({*globals(), *__all__})
