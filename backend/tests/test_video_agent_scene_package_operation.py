"""场景包 Operation Port：长 LLM start 不得被 30s lease 掐断。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pixelflow.agent_runtime.jobs.providers import (
    ProviderJobAdapter,
)
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.adapters.scene_package_operation import (
    M06ScenePackageOperationPort,
)
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolExecutionError,
)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class _SlowPrepareService:
    def __init__(self, clock: _Clock, *, delay_sec: float) -> None:
        self._clock = clock
        self._delay_sec = delay_sec

    async def start(self, request, *, authorization: str, idempotency_key: str):  # noqa: ANN001, ARG002
        self._clock.advance(self._delay_sec)
        return {
            "job_id": f"prepare-scene-packages-{idempotency_key[-16:]}",
            "status": "succeeded",
            "ok": True,
            "result": {
                "ok": True,
                "global_assets": {},
                "scene_packages": [],
                "message": "场景包已准备",
            },
        }

    async def status(self, provider_job_id: str) -> dict:  # noqa: ARG002
        return {"job_id": provider_job_id, "status": "succeeded", "ok": True}


def _context() -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="ws-1",
            conversation_id="conversation-1",
            payload={},
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
            updated_at=datetime(2026, 8, 12, tzinfo=UTC),
        ),
        plan_id="plan-1",
        step_id="step-1",
        credential=None,
    )


@pytest.mark.asyncio
async def test_prepare_start_survives_llm_longer_than_default_30s_lease() -> None:
    """回归：同步 prepare LLM >30s 时，旧默认 lease 会报「启动失败」。"""

    clock = _Clock(datetime(2026, 8, 12, tzinfo=UTC))
    repository = MemoryAgentRuntimeRepository()
    prepare_adapter = ProviderJobAdapter(_SlowPrepareService(clock, delay_sec=65))
    assets_adapter = ProviderJobAdapter(_SlowPrepareService(clock, delay_sec=1))
    port = M06ScenePackageOperationPort(
        repository=repository,
        prepare_adapter=prepare_adapter,
        assets_adapter=assets_adapter,
        lease_owner="worker-1",
        clock=clock,
        job_id_factory=lambda: "operation-prepare-long",
    )

    job = await port.start_prepare_scene_packages(
        _context(),
        plan_markdown="0-10秒｜开场\n【剧情】展示产品。",
        form_values={"video_ratio": "9:16", "ending_cta": "absent"},
        selected_direction={},
        materials=[],
        target_duration_ms=30_000,
        attempt=1,
    )

    assert job.status == "succeeded"
    assert job.result.get("message") == "场景包已准备"


@pytest.mark.asyncio
async def test_prepare_start_fails_when_lease_too_short_for_llm() -> None:
    """对照：lease 过短时仍应失败，证明根因是租约而非领域 Job。"""

    clock = _Clock(datetime(2026, 8, 12, tzinfo=UTC))
    repository = MemoryAgentRuntimeRepository()
    prepare_adapter = ProviderJobAdapter(_SlowPrepareService(clock, delay_sec=65))
    assets_adapter = ProviderJobAdapter(_SlowPrepareService(clock, delay_sec=1))
    port = M06ScenePackageOperationPort(
        repository=repository,
        prepare_adapter=prepare_adapter,
        assets_adapter=assets_adapter,
        lease_owner="worker-1",
        clock=clock,
        job_id_factory=lambda: "operation-prepare-short",
        start_lease_duration=timedelta(seconds=30),
    )

    with pytest.raises(VideoToolExecutionError, match="启动失败"):
        await port.start_prepare_scene_packages(
            _context(),
            plan_markdown="0-10秒｜开场\n【剧情】展示产品。",
            form_values={"video_ratio": "9:16"},
            selected_direction={},
            materials=[],
            target_duration_ms=30_000,
            attempt=1,
        )
