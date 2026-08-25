"""提供受控的 PixelFlow 运行目录与用户隔离路径解析。"""

import os
import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_VIRTUAL_PREFIX = "/mnt/user-data/"


class PixelFlowPaths:
    """类似文件 Repository：只把合法用户与对话标识映射到受控根目录。"""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        """初始化运行根目录；缺省时由环境变量或项目默认目录决定。"""

        raw_base = base_dir or os.environ.get("PIXELFLOW_RUNTIME_HOME", ".pixelflow")
        self._base_dir = Path(raw_base).expanduser().resolve()

    @property
    def base_dir(self) -> Path:
        """返回所有 PixelFlow 本地运行数据的受控根目录。"""

        return self._base_dir

    def thread_user_data_dir(self, *, user_id: str, thread_id: str) -> Path:
        """返回指定用户对话的 user-data 根目录，拒绝路径穿越标识。"""

        return self.base_dir / "users" / self._safe_id(user_id, "user_id") / "threads" / self._safe_id(thread_id, "thread_id") / "user-data"

    def resolve_thread_virtual_path(self, *, user_id: str, thread_id: str, virtual_path: str) -> Path:
        """把 sandbox 虚拟路径映射到用户受控目录，并拒绝越界访问。"""

        if not virtual_path.startswith(_VIRTUAL_PREFIX):
            raise ValueError("虚拟路径必须位于 /mnt/user-data 下")
        relative = Path(virtual_path.removeprefix(_VIRTUAL_PREFIX))
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("虚拟路径包含非法片段")
        target = (self.thread_user_data_dir(user_id=user_id, thread_id=thread_id) / relative).resolve()
        root = self.thread_user_data_dir(user_id=user_id, thread_id=thread_id).resolve()
        if root != target and root not in target.parents:
            raise ValueError("虚拟路径越界")
        return target

    @staticmethod
    def _safe_id(value: str, field_name: str) -> str:
        """校验用于文件路径的公开标识符，禁止分隔符和空值。"""

        if not _SAFE_ID.fullmatch(value):
            raise ValueError(f"{field_name} 只能包含字母、数字、下划线和连字符")
        return value


__all__ = ["PixelFlowPaths"]
