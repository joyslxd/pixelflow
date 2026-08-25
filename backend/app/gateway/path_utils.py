"""thread 虚拟路径解析工具，例如 mnt/user-data/outputs/...。"""

from pathlib import Path

from fastapi import HTTPException

from pixelflow.platform import PixelFlowPaths, get_effective_user_id


def resolve_thread_virtual_path(thread_id: str, virtual_path: str) -> Path:
    """把 sandbox 中看到的虚拟路径解析为 thread user-data 下的真实文件路径。

    参数：
        thread_id: thread ID。
        virtual_path: sandbox 内看到的虚拟路径，例如 /mnt/user-data/outputs/file.txt。

    返回解析后的真实文件系统路径。

    路径非法或越界时抛 HTTPException。
    """
    try:
        return PixelFlowPaths().resolve_thread_virtual_path(
            user_id=get_effective_user_id(),
            thread_id=thread_id,
            virtual_path=virtual_path,
        )
    except ValueError as e:
        status = 403 if "traversal" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))
