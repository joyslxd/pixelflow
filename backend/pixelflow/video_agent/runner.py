"""VideoAgent 持久化计划的独立异步唤醒器。"""

from __future__ import annotations

from dataclasses import dataclass

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.video_agent.credentials import TransientVideoAgentCredential
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.workspace import VideoAgentRepository


@dataclass(frozen=True, slots=True)
class VideoAgentRunScope:
    """把一次Runner唤醒绑定到唯一用户、对话、Turn和Plan。"""

    user_id: str
    conversation_id: str
    turn_id: str
    plan_id: str


class VideoAgentRunner:
    """在HTTP响应之外执行已持久化计划，不持有长期用户凭据。"""

    def __init__(
        self,
        *,
        repository: VideoAgentRepository,
        executor: VideoAgentExecutor,
    ) -> None:
        self._repository = repository
        self._executor = executor

    async def notify_turn(
        self,
        scope: VideoAgentRunScope,
        credential: TransientVideoAgentCredential | None,
    ) -> None:
        """复核Plan归属后执行，结束时无条件清理一次性Authorization。"""

        try:
            plan = await self._repository.get_plan(scope.user_id, scope.plan_id)
            if (
                plan is None
                or plan.conversation_id != scope.conversation_id
            ):
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent Runner唤醒目标不存在或不属于当前会话"
                )
            await self._executor.run_plan(
                scope.user_id,
                scope.plan_id,
                credential=credential,
            )
        finally:
            if credential is not None:
                credential.discard()


__all__ = ["VideoAgentRunner", "VideoAgentRunScope"]
