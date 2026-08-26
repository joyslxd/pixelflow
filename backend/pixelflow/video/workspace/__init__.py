"""视频工作区的稳定身份、摘要与持久化实现。"""

from .digest import (
    blocking_confirmation_from_plan,
    build_plan_digest,
    build_workspace_digest,
    summarize_operations,
    workspace_has_scene_asset_images,
)
from .ids import video_workspace_id_for_conversation
from .projection import ensure_conversation_video_workspace
from .memory_repository import MemoryVideoAgentRepository
from .repository import VideoWorkspaceRepository
from .sql_repository import SQLVideoAgentRepository

__all__ = [
    "blocking_confirmation_from_plan",
    "build_plan_digest",
    "build_workspace_digest",
    "ensure_conversation_video_workspace",
    "summarize_operations",
    "video_workspace_id_for_conversation",
    "VideoWorkspaceRepository",
    "MemoryVideoAgentRepository",
    "SQLVideoAgentRepository",
    "workspace_has_scene_asset_images",
]
