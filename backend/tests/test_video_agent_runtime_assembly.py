"""Task 11 VideoAgent核心Runtime受控装配测试。"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from pixelflow.agent_runtime.jobs import ProviderJobAdapter
from pixelflow.agent_runtime.persistence.repositories import (
    MemoryAgentRuntimeRepository,
)
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.video_agent.runtime import (
    VIDEO_AGENT_RUNTIME_NOT_READY,
    make_video_agent_runtime_assembly,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


class RecordingJobService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.start_calls = 0
        self.status_calls = 0

    async def start(
        self,
        request: Mapping[str, object],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        del request, authorization, idempotency_key
        self.start_calls += 1
        return {"job_id": f"{self.name}-job", "status": "polling"}

    async def status(self, provider_job_id: str) -> dict[str, object]:
        self.status_calls += 1
        return {"job_id": provider_job_id, "status": "polling"}


def _adapter(name: str) -> tuple[ProviderJobAdapter, RecordingJobService]:
    service = RecordingJobService(name)
    return ProviderJobAdapter(service), service


def test_core_runtime_assembly_registers_all_tools_without_jianying() -> None:
    """剪映未部署时核心工具、Executor和MP4恢复路由仍应就绪。"""

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    reference, reference_service = _adapter("reference")
    scene, scene_service = _adapter("scene")
    merge, merge_service = _adapter("merge")

    assembly = make_video_agent_runtime_assembly(
        operation_repository=runtime_repository,
        video_repository=video_repository,
        reference_adapter=reference,
        scene_adapter=scene,
        merge_adapter=merge,
        lease_owner="gateway-video-agent-test",
    )

    assert assembly.ready is True
    assert assembly.optional_capabilities == {"jianying_package": False}
    assert assembly.registry is not None
    assert assembly.registry.names() == (
        "analyze_reference_video",
        "brainstorm_script",
        "compose_or_export_video",
        "generate_scenes",
        "import_script",
        "inspect_scene",
        "inspect_video_workspace",
        "patch_scene",
        "replace_project_assets",
        "review_generated_scenes",
    )
    assert assembly.operation_resolver is not None
    assert assembly.operation_resolver.resolve("analyze_reference:0123456789abcdef") is reference
    assert assembly.operation_resolver.resolve("generate_scene:0123456789ab:v3") is scene
    assert assembly.operation_resolver.resolve("deliver:mp4") is merge
    with pytest.raises(OperationConflictError):
        assembly.operation_resolver.resolve("deliver:jianying_package")
    assert all(
        service.start_calls == 0 and service.status_calls == 0
        for service in (reference_service, scene_service, merge_service)
    )


def test_runtime_assembly_fails_closed_when_core_provider_is_missing() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    reference, _ = _adapter("reference")
    merge, _ = _adapter("merge")

    assembly = make_video_agent_runtime_assembly(
        operation_repository=runtime_repository,
        video_repository=MemoryVideoAgentRepository(
            event_repository=runtime_repository,
        ),
        reference_adapter=reference,
        scene_adapter=None,
        merge_adapter=merge,
        lease_owner="gateway-video-agent-test",
    )

    assert assembly.ready is False
    assert assembly.reason_code == VIDEO_AGENT_RUNTIME_NOT_READY
    assert assembly.registry is None
    assert assembly.executor is None


def test_runtime_assembly_registers_optional_jianying_route_when_available() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    reference, _ = _adapter("reference")
    scene, _ = _adapter("scene")
    merge, _ = _adapter("merge")
    jianying, _ = _adapter("jianying")

    assembly = make_video_agent_runtime_assembly(
        operation_repository=runtime_repository,
        video_repository=MemoryVideoAgentRepository(
            event_repository=runtime_repository,
        ),
        reference_adapter=reference,
        scene_adapter=scene,
        merge_adapter=merge,
        jianying_adapter=jianying,
        lease_owner="gateway-video-agent-test",
    )

    assert assembly.ready is True
    assert assembly.optional_capabilities == {"jianying_package": True}
    assert assembly.operation_resolver is not None
    assert assembly.operation_resolver.resolve("deliver:jianying_package") is jianying
