"""定义可替换的 AgentEngine Port 与无副作用 M0 实现。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from .contracts import HarnessRunEvent, HarnessRunHandle, HarnessRunRequest, RunStatus


class AgentEngine(Protocol):
    """隔离具体 Harness 的 Agent loop 引擎端口。"""

    async def create_run(self, request: HarnessRunRequest) -> HarnessRunHandle:
        """创建或按稳定请求身份回读一个 Run。"""

    async def stream_events(self, run_id: str, after_sequence: int = 0) -> AsyncIterator[HarnessRunEvent]:
        """按序输出指定 Run 在游标之后的稳定事件。"""

    async def cancel_run(self, run_id: str) -> None:
        """取消模型 loop，不声明取消任何外部 Provider。"""

    async def discover_skills(self) -> dict[str, str]:
        """返回当前冻结 Skill 目录的最小目录信息。"""

    async def register_tools(self, manifest_digest: str) -> None:
        """登记启动期已经冻结的 Capability Tool Manifest。"""


@dataclass(slots=True)
class _FakeRun:
    """保存 Fake Engine 的最小 Run 状态，绝不承载真实业务真相。"""

    request_digest: str
    handle: HarnessRunHandle
    events: list[HarnessRunEvent] = field(default_factory=list)


class FakeAgentEngine:
    """用于 M0/M2 合同测试的确定性无副作用 Engine。"""

    def __init__(self, *, engine_id: str = "fake-agent-engine", engine_version: str = "m0") -> None:
        self._engine_id = engine_id
        self._engine_version = engine_version
        self._runs_by_key: dict[str, _FakeRun] = {}
        self._runs_by_id: dict[str, _FakeRun] = {}
        self._manifest_digest: str | None = None

    async def create_run(self, request: HarnessRunRequest) -> HarnessRunHandle:
        """按请求身份创建 Fake Run，并拒绝同身份输入漂移。"""

        existing = self._runs_by_key.get(request.run_request_key)
        if existing is not None:
            if existing.request_digest != request.request_digest:
                raise ValueError("同一 Run 身份的请求摘要不一致")
            return existing.handle

        run_id = f"hrun_{len(self._runs_by_key) + 1:04d}"
        handle = HarnessRunHandle(
            run_id=run_id,
            status=RunStatus.ACCEPTED,
            engine_id=self._engine_id,
            engine_version=self._engine_version,
            skill_catalog_digest="sha256:fake-skill-catalog",
        )
        event = HarnessRunEvent(
            protocol_version="v1",
            run_id=run_id,
            event_id=f"hevt_{run_id}_0001",
            sequence=1,
            type="run.accepted",
            occurred_at=datetime.now(UTC).isoformat(),
            payload={"public_summary": "Fake Engine 已接受 Run"},
        )
        fake_run = _FakeRun(request_digest=request.request_digest, handle=handle, events=[event])
        self._runs_by_key[request.run_request_key] = fake_run
        self._runs_by_id[run_id] = fake_run
        return handle

    async def stream_events(self, run_id: str, after_sequence: int = 0) -> AsyncIterator[HarnessRunEvent]:
        """以严格递增 sequence 返回已记录事件。"""

        fake_run = self._runs_by_id.get(run_id)
        if fake_run is None:
            raise KeyError("未找到指定 Run")
        for event in fake_run.events:
            if event.sequence > after_sequence:
                yield event

    async def cancel_run(self, run_id: str) -> None:
        """记录取消事件，但不模拟或取消外部 Operation。"""

        fake_run = self._runs_by_id.get(run_id)
        if fake_run is None:
            raise KeyError("未找到指定 Run")
        if fake_run.handle.status is RunStatus.CANCELLED:
            return
        sequence = len(fake_run.events) + 1
        fake_run.events.append(
            HarnessRunEvent(
                protocol_version="v1",
                run_id=run_id,
                event_id=f"hevt_{run_id}_{sequence:04d}",
                sequence=sequence,
                type="run.cancelled",
                occurred_at=datetime.now(UTC).isoformat(),
                payload={"public_summary": "Fake Engine 已取消模型循环"},
            )
        )
        fake_run.handle = fake_run.handle.model_copy(update={"status": RunStatus.CANCELLED})

    async def discover_skills(self) -> dict[str, str]:
        """返回 M0 固定的空目录，真实发现由 Skill Snapshot 模块验证。"""

        return {}

    async def register_tools(self, manifest_digest: str) -> None:
        """记录 Manifest 摘要并拒绝同一进程内的漂移。"""

        if self._manifest_digest is not None and self._manifest_digest != manifest_digest:
            raise ValueError("Fake Engine 不允许在运行期间替换 Tool Manifest")
        self._manifest_digest = manifest_digest
