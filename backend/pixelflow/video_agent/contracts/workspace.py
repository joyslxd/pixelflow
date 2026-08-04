"""VideoAgent 可恢复工作区合同。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, JsonValue

from .plan import VideoAgentContract


class VideoWorkspace(VideoAgentContract):
    workspace_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(default=1, ge=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
