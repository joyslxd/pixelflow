"""Gateway 接入 Harness Run 的控制面应用服务。"""

from __future__ import annotations

from pixelflow.agent_harness import AgentHarnessPort, HarnessRunHandle, HarnessRunRequest
from pixelflow.agent_harness.projector import HarnessRunProjector


class AgentRunBridge:
    """统一创建、绑定并投影 Harness Run，Router 不感知 Sidecar 调用顺序。"""

    def __init__(
        self,
        *,
        harness: AgentHarnessPort,
        projector: HarnessRunProjector,
    ) -> None:
        self._harness = harness
        self._projector = projector

    async def start(self, request: HarnessRunRequest) -> HarnessRunHandle:
        """先完成 Gateway binding，再启动同一 Run 的公开事件投影。"""

        handle = await self._harness.create_and_bind(request)
        binding = await self._harness.get_owned_binding(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            run_id=handle.run_id,
        )
        await self._projector.start(harness=self._harness, binding=binding)
        return handle


__all__ = ["AgentRunBridge"]
