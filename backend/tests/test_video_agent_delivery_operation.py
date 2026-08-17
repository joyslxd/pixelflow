"""Task 11 VideoAgent交付M06 Adapter测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pixelflow.agent_runtime.jobs import ProviderJobAdapter
from pixelflow.agent_runtime.persistence.repositories import (
    MemoryAgentRuntimeRepository,
)
from pixelflow.video_agent.adapters.delivery_operation import (
    M06DeliveryOperationPort,
)
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.credentials import TransientVideoAgentCredential
from pixelflow.video_agent.tools import (
    ComposeOrExportVideoTool,
    VideoToolContext,
    VideoToolExecutionError,
)

NOW = datetime(2026, 8, 6, 16, 0, tzinfo=UTC)
AUTHORIZATION = "Bearer delivery-operation-test"


class SynchronousMergeService:
    def __init__(self, *, clock: list[datetime] | None = None, advance: timedelta | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._clock = clock
        self._advance = advance

    async def start(
        self,
        request,
        *,
        authorization: str,
        idempotency_key: str,
    ):
        self.calls.append(
            {
                "request": dict(request),
                "authorization": authorization,
                "idempotency_key": idempotency_key,
            }
        )
        # 模拟 content-app 同步合并耗时超过默认 30s start 租约
        if self._clock is not None and self._advance is not None:
            self._clock[0] = self._clock[0] + self._advance
        return {
            "job_id": "provider-merge-1",
            "status": "succeeded",
            "result": {
                "video_url": "https://cdn.example.invalid/final.mp4",
                "raw": {},
            },
        }

    async def status(self, provider_job_id: str):
        raise AssertionError(f"同步任务不得轮询：{provider_job_id}")


class UnusedJianyingService:
    async def start(
        self,
        request,
        *,
        authorization: str,
        idempotency_key: str,
    ):
        del request, authorization, idempotency_key
        raise AssertionError("MP4交付不得调用剪映Provider")

    async def status(self, provider_job_id: str):
        raise AssertionError(f"MP4交付不得查询剪映Provider：{provider_job_id}")


def _context(
    *,
    credential: TransientVideoAgentCredential | None,
) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-delivery",
        plan_id="plan-delivery",
        step_id="step-delivery",
        credential=credential,
        workspace=VideoWorkspace(
            workspace_id="workspace-delivery",
            conversation_id="conversation-delivery",
            payload={
                "video_params": {
                    "model": "seedance-2.0",
                    "size": "1080x1920",
                    "duration_sec": 10,
                },
                "dirty_scene_ids": [],
                "qc": {
                    "scene-1": {"status": "resolved"},
                    "scene-2": {"status": "resolved"},
                },
                "scenes": [
                    _scene("scene-1", 1),
                    _scene("scene-2", 2),
                ],
            },
        ),
    )


def _scene(scene_id: str, scene_index: int) -> dict[str, object]:
    variant_id = f"{scene_id}-v1"
    return {
        "scene_id": scene_id,
        "scene_index": scene_index,
        "approved_variant_id": variant_id,
        "variants": [
            {
                "variant_id": variant_id,
                "artifact_ref": f"artifact:{variant_id}",
                "video_url": f"https://cdn.example.invalid/{variant_id}.mp4",
                "source_job_id": f"job-{variant_id}",
                "review_status": "approved",
                "selected": True,
            }
        ],
    }


@pytest.mark.asyncio
async def test_delivery_operation_uses_context_credential_and_persists_terminal_event() -> None:
    """同步合并也必须经M06身份、完成事件和执行期凭据边界。"""

    repository = MemoryAgentRuntimeRepository()
    merge_service = SynchronousMergeService()
    port = M06DeliveryOperationPort(
        repository=repository,
        merge_adapter=ProviderJobAdapter(merge_service),
        jianying_adapter=ProviderJobAdapter(UnusedJianyingService()),
        lease_owner="delivery-start-worker",
        clock=lambda: NOW,
        job_id_factory=lambda: "operation-delivery-1",
    )
    tool = ComposeOrExportVideoTool(operation_port=port)

    result = await tool.execute(
        _context(
            credential=TransientVideoAgentCredential(AUTHORIZATION),
        ),
        {"output_type": "mp4"},
    )
    operation = await repository.get_operation(
        "user-delivery",
        "operation-delivery-1",
    )
    events = await repository.list_events(
        "user-delivery",
        "conversation-delivery",
    )

    assert result.pending_operation_job_ids == ()
    assert result.artifact_refs[0].startswith("artifact:video-delivery-")
    assert merge_service.calls[0]["authorization"] == AUTHORIZATION
    assert merge_service.calls[0]["request"]["video_urls"] == [
        "https://cdn.example.invalid/scene-1-v1.mp4",
        "https://cdn.example.invalid/scene-2-v1.mp4",
    ]
    assert operation is not None
    assert operation.status.value == "succeeded"
    assert len(events) == 1
    assert AUTHORIZATION not in operation.model_dump_json()
    assert AUTHORIZATION not in events[0].model_dump_json()


@pytest.mark.asyncio
async def test_delivery_operation_without_request_credential_fails_before_provider() -> None:
    """Gateway未传请求期凭据时必须在Provider调用前失败关闭。"""

    repository = MemoryAgentRuntimeRepository()
    merge_service = SynchronousMergeService()
    port = M06DeliveryOperationPort(
        repository=repository,
        merge_adapter=ProviderJobAdapter(merge_service),
        jianying_adapter=ProviderJobAdapter(UnusedJianyingService()),
        lease_owner="delivery-start-worker",
        clock=lambda: NOW,
    )

    with pytest.raises(VideoToolExecutionError, match="启动失败"):
        await ComposeOrExportVideoTool(operation_port=port).execute(
            _context(credential=None),
            {"output_type": "mp4"},
        )

    assert merge_service.calls == []


@pytest.mark.asyncio
async def test_delivery_merges_using_creation_contract_when_video_params_missing() -> None:
    repository = MemoryAgentRuntimeRepository()
    merge_service = SynchronousMergeService()
    port = M06DeliveryOperationPort(
        repository=repository,
        merge_adapter=ProviderJobAdapter(merge_service),
        jianying_adapter=ProviderJobAdapter(UnusedJianyingService()),
        lease_owner="delivery-start-worker",
        clock=lambda: NOW,
        job_id_factory=lambda: "operation-delivery-contract-1",
    )
    context = _context(
        credential=TransientVideoAgentCredential(authorization=AUTHORIZATION)
    )
    payload = dict(context.workspace.payload)
    payload.pop("video_params", None)
    payload["creation_contract"] = {
        "video_model": "seedance-2.0",
        "video_size": "1080x1920",
        "video_duration_sec": 20,
    }
    context = VideoToolContext(
        user_id=context.user_id,
        plan_id=context.plan_id,
        step_id=context.step_id,
        credential=context.credential,
        workspace=VideoWorkspace(
            workspace_id=context.workspace.workspace_id,
            conversation_id=context.workspace.conversation_id,
            payload=payload,
        ),
    )
    result = await ComposeOrExportVideoTool(operation_port=port).execute(
        context,
        {"output_type": "mp4"},
    )
    assert result.pending_operation_job_ids or result.artifact_refs
    assert merge_service.calls
    request = merge_service.calls[0]["request"]
    assert request["model"] == "seedance-2.0"
    assert request["size"] == "1080x1920"
    assert request["duration"] == 20


@pytest.mark.asyncio
async def test_delivery_sync_merge_survives_beyond_default_thirty_second_lease() -> None:
    """14 镜同步合并常 >30s；默认 1h start 租约必须能落 SUCCEEDED。"""

    clock = [NOW]
    merge_service = SynchronousMergeService(
        clock=clock,
        advance=timedelta(seconds=65),
    )
    port = M06DeliveryOperationPort(
        repository=MemoryAgentRuntimeRepository(),
        merge_adapter=ProviderJobAdapter(merge_service),
        jianying_adapter=ProviderJobAdapter(UnusedJianyingService()),
        lease_owner="delivery-start-worker",
        clock=lambda: clock[0],
        job_id_factory=lambda: "operation-delivery-long-merge",
    )

    result = await ComposeOrExportVideoTool(operation_port=port).execute(
        _context(credential=TransientVideoAgentCredential(AUTHORIZATION)),
        {"output_type": "mp4"},
    )

    assert result.artifact_refs
    assert merge_service.calls


@pytest.mark.asyncio
async def test_delivery_short_start_lease_surfaces_conflict_detail() -> None:
    """租约过短时 finalize 失败，公开错误须带上「租约无效」细节。"""

    clock = [NOW]
    merge_service = SynchronousMergeService(
        clock=clock,
        advance=timedelta(seconds=65),
    )
    port = M06DeliveryOperationPort(
        repository=MemoryAgentRuntimeRepository(),
        merge_adapter=ProviderJobAdapter(merge_service),
        jianying_adapter=ProviderJobAdapter(UnusedJianyingService()),
        lease_owner="delivery-start-worker",
        clock=lambda: clock[0],
        job_id_factory=lambda: "operation-delivery-lease-expired",
        start_lease_duration=timedelta(seconds=30),
    )

    with pytest.raises(VideoToolExecutionError, match="租约无效"):
        await ComposeOrExportVideoTool(operation_port=port).execute(
            _context(credential=TransientVideoAgentCredential(AUTHORIZATION)),
            {"output_type": "mp4"},
        )

    assert merge_service.calls
