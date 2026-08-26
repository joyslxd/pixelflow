"""把权威 VideoWorkspace 投影为浏览器可消费的安全摘要。"""

from __future__ import annotations

from pixelflow.agent_control_plane.public_contracts import VideoWorkspaceProjectionV1
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace.digest import build_workspace_digest
from pixelflow.video.workspace.ids import video_workspace_id_for_conversation
from pixelflow.video.workspace.repository import VideoWorkspaceRepository


async def ensure_conversation_video_workspace(
    repository: VideoWorkspaceRepository,
    *,
    user_id: str,
    conversation_id: str,
) -> VideoWorkspaceProjectionV1:
    """读取或创建当前会话的视频工作区，并只返回公开摘要。"""

    loaded = await repository.load_conversation_state(user_id, conversation_id)
    if loaded is None:
        workspace = await repository.create_workspace(
            user_id,
            VideoWorkspace(
                workspace_id=video_workspace_id_for_conversation(conversation_id),
                conversation_id=conversation_id,
                revision=1,
                payload={},
            ),
        )
    else:
        workspace, _plan = loaded
    return VideoWorkspaceProjectionV1(
        workspace_id=workspace.workspace_id,
        revision=workspace.revision,
        summary=build_workspace_digest(workspace),
    )
