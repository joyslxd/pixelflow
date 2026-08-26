"""统一视频 Workspace 修改的应用服务。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRecordConflictError
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace.repository import VideoWorkspaceRepository


class VideoWorkspaceMutationService:
    """让浏览器 Command 与 Tool Handler 通过同一乐观锁写入边界修改工作区。"""

    def __init__(self, repository: VideoWorkspaceRepository) -> None:
        self._repository = repository

    async def apply_patch(
        self,
        *,
        user_id: str,
        workspace_id: str,
        patch: Mapping[str, Any],
        expected_revision: int,
        now: datetime | None = None,
    ) -> VideoWorkspace:
        """按调用方显式 revision 修改权威 Workspace，不静默覆盖更新。"""

        return await self._repository.apply_workspace_patch(
            user_id,
            workspace_id,
            dict(patch),
            expected_revision=expected_revision,
            now=now or datetime.now(UTC),
        )

    async def apply_tool_patch(
        self,
        *,
        user_id: str,
        workspace: VideoWorkspace,
        patch: Mapping[str, Any],
        now: datetime,
        max_attempts: int = 3,
    ) -> VideoWorkspace:
        """Tool 在长耗时计算后可基于最新 revision 重试写入，但不改变业务补丁。"""

        current = workspace
        last_error: AgentRuntimeRecordConflictError | None = None
        for _ in range(max_attempts):
            try:
                return await self.apply_patch(
                    user_id=user_id,
                    workspace_id=current.workspace_id,
                    patch=patch,
                    expected_revision=current.revision,
                    now=now,
                )
            except AgentRuntimeRecordConflictError as error:
                last_error = error
                refreshed = await self._repository.get_workspace(user_id, current.workspace_id)
                if refreshed is None:
                    raise
                current = refreshed
        assert last_error is not None
        raise last_error
