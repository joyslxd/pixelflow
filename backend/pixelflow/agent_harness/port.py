"""定义 Gateway 依赖的 Agent Harness Port，隔离具体 Engine 与 HTTP Client。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pixelflow.agent_tools.repository import RunBinding

from .contracts import HarnessRunEvent, HarnessRunHandle, HarnessRunRequest


@runtime_checkable
class AgentHarnessPort(Protocol):
    """类似 Java Client 接口：业务层只依赖 Run 与事件合同。"""

    async def create_and_bind(self, request: HarnessRunRequest) -> HarnessRunHandle:
        """创建 Run、写入 Gateway binding 后再激活 Sidecar。"""

    async def stream_sidecar_events(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        after_sequence: int,
    ) -> AsyncIterator[HarnessRunEvent]:
        """按归属和 sequence 读取仅供 Gateway 投影的事件流。"""

    async def get_owned_binding(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
    ) -> RunBinding:
        """回读 Gateway 权威 binding，并校验用户和会话归属。"""

    async def aclose(self) -> None:
        """关闭 Port 自己持有的网络资源。"""


__all__ = ["AgentHarnessPort"]
