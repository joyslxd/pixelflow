"""原生 Video Agent 的 LangGraph 状态 schema。"""

from __future__ import annotations

from typing import NotRequired

from deerflow.agents.thread_state import ThreadState


class VideoAgentState(ThreadState):
    """在 DeerFlow ThreadState 上叠加视频控制面标识。

    业务真相仍在 VideoWorkspace / Operation / Confirmation；
    这里只保存本轮 Agent 循环需要的轻量引用。
    """

    workspace_id: NotRequired[str | None]
    conversation_id: NotRequired[str | None]
    turn_id: NotRequired[str | None]
    plan_id: NotRequired[str | None]
    active_step_id: NotRequired[str | None]
