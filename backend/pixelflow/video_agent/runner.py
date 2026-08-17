"""VideoAgent 持久化计划的独立异步唤醒器。"""

from __future__ import annotations

from dataclasses import dataclass

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.video_agent.contracts import AgentPlanStatus
from pixelflow.video_agent.credentials import TransientVideoAgentCredential
from pixelflow.video_agent.native_invoke import (
    NativeVideoAgentInvokeRequest,
    NativeVideoAgentInvoker,
)
from pixelflow.video_agent.workspace import VideoAgentRepository


@dataclass(frozen=True, slots=True)
class VideoAgentRunScope:
    """把一次 Runner 唤醒绑定到唯一用户、对话、Turn 和 Plan。"""

    user_id: str
    conversation_id: str
    turn_id: str
    plan_id: str


class VideoAgentRunner:
    """在 HTTP 响应之外执行已持久化 Turn；只走原生 Agent invoke。"""

    def __init__(
        self,
        *,
        repository: VideoAgentRepository,
        native_invoker: NativeVideoAgentInvoker,
    ) -> None:
        self._repository = repository
        self._native_invoker = native_invoker

    async def notify_turn(
        self,
        scope: VideoAgentRunScope,
        credential: TransientVideoAgentCredential | None,
    ) -> None:
        """复核 Plan 归属后执行，结束时无条件清理一次性 Authorization。"""

        try:
            plan = await self._repository.get_plan(scope.user_id, scope.plan_id)
            if plan is None or plan.conversation_id != scope.conversation_id:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent Runner唤醒目标不存在或不属于当前会话"
                )
            workspace = await self._repository.get_workspace(
                scope.user_id,
                plan.workspace_id,
            )
            if workspace is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent Runner 缺少对应 workspace"
                )
            latest_input = workspace.payload.get("latest_input")
            content = (
                str(latest_input).strip()
                if isinstance(latest_input, str) and latest_input.strip()
                else (plan.public_goal or "").strip()
            )
            if not content:
                content = "请根据当前视频工作区继续处理"

            await self._native_invoker.invoke(
                NativeVideoAgentInvokeRequest(
                    user_id=scope.user_id,
                    conversation_id=scope.conversation_id,
                    turn_id=scope.turn_id,
                    plan_id=scope.plan_id,
                    content=content,
                    workspace=workspace,
                    credential=credential,
                )
            )
            # 观察 Plan：原生循环结束后收口，不由 Plan 步骤表驱动。
            current = await self._repository.get_plan(scope.user_id, scope.plan_id)
            if current is not None and current.status is AgentPlanStatus.RUNNING:
                from datetime import UTC, datetime

                await self._repository.update_plan_status(
                    scope.user_id,
                    scope.plan_id,
                    AgentPlanStatus.COMPLETED,
                    now=datetime.now(UTC),
                )
        finally:
            if credential is not None:
                credential.discard()
