"""VideoWorkspace 稳定身份：升级与 Entrypoint 必须共用同一派生规则。"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5


def video_workspace_id_for_conversation(conversation_id: str) -> str:
    """由 conversation_id 派生唯一 workspace_id。

    历史 `legacy_upgrade` 曾用另一套 uuid5 命名，导致同会话双 Workspace，
    Snapshot 以 409 失败。此后一律走本函数。
    """

    cid = conversation_id.strip()
    if not cid:
        raise ValueError("conversation_id 不能为空")
    value = ":".join(("pixelflow-video-agent", "video_workspace", cid))
    return f"video_workspace_{uuid5(NAMESPACE_URL, value).hex}"
