"""验证失败图片资产可被非计费 Tool 重置为 planned，且不绕过生图确认。"""

from __future__ import annotations

import pytest

from pixelflow.agent_tools.catalog import runtime_video_tool_registry
from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolValidationError
from pixelflow.agent_tools.video.image_asset_retry import RetryFailedImageAssetsTool
from pixelflow.agent_tools.video.image_assets import GenerateImageAssetsTool
from pixelflow.generation_jobs.contracts import GenerationJobKind, GenerationJobStatus
from pixelflow.generation_jobs.service import GenerationJobSubmission
from pixelflow.video.contracts import VideoWorkspace


class _FakeGenerationJobService:
    image_available = True

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


def _context(payload):
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload=payload,
        ),
        run_id="hrun_test",
        tool_call_id="tool-call-retry",
    )


def _failed_asset(asset_id: str) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "kind": "character" if "character" in asset_id else "scene",
        "role": "女主" if "character" in asset_id else "厨房",
        "origin": "planned_generation",
        "state": "failed",
        "generation_prompt": "女主锁骨相" if "character" in asset_id else "空厨房主光",
        "failure_status": "failed",
        "failure_reason_code": "provider_business_failed",
        "failed_at": "2026-09-01T12:00:00+00:00",
        "generation_job_id": "generation-job-old",
        "image_url": "https://cdn.example/old.png",
        "usable_for_video": False,
    }


@pytest.mark.asyncio
async def test_retry_failed_image_assets_resets_failed_rows_to_planned() -> None:
    result = await RetryFailedImageAssetsTool().execute(
        _context({"asset_registry": [_failed_asset("asset_character_01"), _failed_asset("asset_scene_01")]}),
        {"asset_ids": ["asset_character_01", "asset_scene_01"]},
    )

    registry = result.workspace_patch["asset_registry"]
    assert [item["state"] for item in registry] == ["planned", "planned"]
    assert all(item.get("generation_prompt") for item in registry)
    assert all(item.get("failure_reason_code") is None for item in registry)
    assert all("image_url" not in item for item in registry)
    assert result.model_observation["reset_count"] == 2
    assert "generate_image_assets" in result.public_summary


@pytest.mark.asyncio
async def test_retry_failed_image_assets_rejects_ready_and_existing_material() -> None:
    tool = RetryFailedImageAssetsTool()
    ready = {
        "asset_id": "asset_character_01",
        "origin": "planned_generation",
        "state": "ready",
        "generation_prompt": "女主",
        "provider_artifact_ref": "artifact:image:asset_character_01",
        "usable_for_video": True,
    }
    uploaded = {
        "asset_id": "asset_product_01",
        "origin": "existing_material",
        "state": "ready",
        "source_material_id": "material-1",
        "usable_for_video": True,
    }
    with pytest.raises(VideoToolValidationError, match="不是可重试的失败资产"):
        await tool.execute(_context({"asset_registry": [ready]}), {"asset_ids": ["asset_character_01"]})
    with pytest.raises(VideoToolValidationError, match="不是可重试的待生成资产"):
        await tool.execute(_context({"asset_registry": [uploaded]}), {"asset_ids": ["asset_product_01"]})


@pytest.mark.asyncio
async def test_retry_failed_image_assets_is_idempotent_for_already_planned() -> None:
    planned = {
        "asset_id": "asset_character_01",
        "origin": "planned_generation",
        "state": "planned",
        "generation_prompt": "女主锁骨相",
    }
    result = await RetryFailedImageAssetsTool().execute(
        _context({"asset_registry": [planned]}),
        {"asset_ids": ["asset_character_01"]},
    )
    assert result.model_observation["reset_count"] == 0
    assert result.model_observation["already_planned_count"] == 1
    assert result.workspace_patch["asset_registry"][0]["state"] == "planned"


@pytest.mark.asyncio
async def test_generate_image_assets_accepts_assets_after_retry() -> None:
    retry = await RetryFailedImageAssetsTool().execute(
        _context({"asset_registry": [_failed_asset("asset_character_01")]}),
        {"asset_ids": ["asset_character_01"]},
    )
    generated = await GenerateImageAssetsTool(
        generation_job_service=_FakeGenerationJobService()
    ).execute(
        _context({"asset_registry": retry.workspace_patch["asset_registry"]}),
        {"asset_ids": ["asset_character_01"]},
    )
    assert generated.model_observation["status"] == "submitted"
    assert generated.workspace_patch["asset_registry"][0]["generation_job_status"] == "queued"
    assert generated.workspace_patch["asset_registry"][0]["state"] == "generating"


def test_retry_failed_image_assets_is_published_as_non_billing_tool() -> None:
    tool = runtime_video_tool_registry().resolve("retry_failed_image_assets")
    assert tool is not None
    spec = tool.spec
    assert spec.cost_level.value == "none"
    assert spec.confirmation_required is False
    assert spec.workspace_mutations == ("asset_registry",)
