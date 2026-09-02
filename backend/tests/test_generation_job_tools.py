"""验证生成 Tool 只创建 GenerationJob，不再创建旧批次或子任务编排。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential
from pixelflow.agent_tools.video.image_assets import GenerateImageAssetsTool
from pixelflow.agent_tools.video.scene import GenerateScenesTool
from pixelflow.generation_jobs.contracts import GenerationJobKind, GenerationJobStatus
from pixelflow.generation_jobs.credentials import TransientGenerationJobCredentialStore
from pixelflow.generation_jobs.providers import ProviderJobOutcome, ProviderJobSnapshot
from pixelflow.generation_jobs.repository import MemoryGenerationJobRepository
from pixelflow.generation_jobs.service import GenerationJobService, GenerationJobSubmission
from pixelflow.video.contracts import VideoWorkspace


class _FakeGenerationJobService:
    image_available = True
    video_available = True

    async def submit_images(self, context, *, assets, attempt):
        del context, attempt
        return tuple(
            GenerationJobSubmission(
                job_id=f"generation-job-image-{asset['asset_id']}",
                item_id=asset["asset_id"],
                kind=GenerationJobKind.IMAGE,
                status=GenerationJobStatus.QUEUED,
            )
            for asset in assets
        )

    async def submit_videos(self, context, *, scenes, variant_count, attempt):
        del context, attempt
        return tuple(
            GenerationJobSubmission(
                job_id=f"generation-job-video-{scene['scene_id']}-{variant}",
                item_id=scene["scene_id"],
                kind=GenerationJobKind.VIDEO,
                status=GenerationJobStatus.QUEUED,
                variant_index=variant,
            )
            for scene in scenes
            for variant in range(1, variant_count + 1)
        )


class _FakeImageProvider:
    """只验证 GenerationJob 提交阶段，不发起真实 Provider 请求。"""

    provider_id = "fake-image"
    profile_version = "test-v1"

    def __init__(self) -> None:
        self.start_calls = 0

    def prepare_operation_request(self, request):
        return {**dict(request), "provider_id": self.provider_id, "provider_profile_version": self.profile_version}

    async def start(self, request, *, authorization, idempotency_key):
        self.start_calls += 1
        del request, authorization, idempotency_key
        return ProviderJobSnapshot(
            provider_job_id="provider-image-test",
            outcome=ProviderJobOutcome.POLLING,
            reason_code="provider_polling",
            message="测试 Provider 未调用。",
        )

    async def status(self, provider_job_id, *, user_id, conversation_id, authorization=""):
        del user_id, conversation_id, authorization
        return ProviderJobSnapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.POLLING,
            reason_code="provider_polling",
            message="测试 Provider 未调用。",
        )

    def as_operation_adapter(self):
        return object()

def _context(payload):
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload=payload,
        ),
        run_id="hrun_test",
        tool_call_id="tool-call-test",
    )


@pytest.mark.asyncio
async def test_image_tool_writes_generation_job_id_to_asset_registry() -> None:
    result = await GenerateImageAssetsTool(
        generation_job_service=_FakeGenerationJobService()
    ).execute(
        _context({
            "asset_registry": [{
                "asset_id": "asset-host",
                "origin": "planned_generation",
                "state": "planned",
                "generation_prompt": "女主人",
            }]
        }),
        {"asset_ids": ["asset-host"]},
    )

    assert result.model_observation["generation_job_ids"] == [
        "generation-job-image-asset-host"
    ]
    assert result.workspace_patch["asset_registry"][0]["generation_job_status"] == "queued"
    assert result.workspace_patch["asset_registry"][0]["state"] == "generating"
    assert not any("batch" in key for key in result.model_observation)


@pytest.mark.asyncio
async def test_image_tool_with_real_service_creates_generation_job_before_provider_start() -> None:
    """真实图片 Tool Service 必须先落一条 GenerationJob，且提交阶段不调用 Provider。"""

    repository = MemoryGenerationJobRepository()
    provider = _FakeImageProvider()
    service = GenerationJobService(
        repository=repository,
        credential_store=TransientGenerationJobCredentialStore(),
        image_provider=provider,
    )
    context = _context(
        {
            "asset_registry": [
                {
                    "asset_id": "asset-host",
                    "origin": "planned_generation",
                    "state": "planned",
                    "generation_prompt": "现代厨房中的年轻女主人",
                }
            ]
        }
    )
    context = replace(context, credential=TransientVideoAgentCredential("test-only"))

    result = await GenerateImageAssetsTool(generation_job_service=service).execute(
        context,
        {"asset_ids": ["asset-host"]},
    )

    job_id = result.model_observation["generation_job_ids"][0]
    record = await repository.get(job_id)
    assert record is not None
    assert record.item_id == "asset-host"
    assert record.status is GenerationJobStatus.QUEUED
    assert provider.start_calls == 0


@pytest.mark.asyncio
async def test_scene_tool_writes_one_generation_job_per_variant() -> None:
    result = await GenerateScenesTool(
        generation_job_service=_FakeGenerationJobService()
    ).execute(
        _context({
            "creation_contract": {
                "video_model": "seedance-2.0",
                "video_ratio": "9:16",
                "video_size": "1080p",
                "video_sound": "on",
            },
            "scenes": [{
                "scene_id": "scene-1",
                "prompt": "产品展示",
                "duration_sec": 8,
            }],
            "dirty_scene_ids": ["scene-1"],
        }),
        {"scene_ids": ["scene-1"], "variant_count": 2},
    )

    assert len(result.model_observation["generation_job_ids"]) == 2
    jobs = result.workspace_patch["scenes"][0]["generation_jobs"]
    assert [job["variant_index"] for job in jobs] == [1, 2]
