"""验证修订图片资产 Tool 可重置 ready/failed 并改 prompt，且不绕过生图确认。"""

from __future__ import annotations

import pytest

from pixelflow.agent_tools.catalog import runtime_video_tool_registry
from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolValidationError
from pixelflow.agent_tools.video.image_asset_revise import ReviseImageAssetsTool
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
        tool_call_id="tool-call-revise",
    )


def _ready_asset(asset_id: str) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "kind": "character",
        "role": "女主",
        "origin": "planned_generation",
        "state": "ready",
        "generation_prompt": "女主锁骨相",
        "image_url": "https://cdn.example/old.png",
        "provider_artifact_ref": "artifact:image:old.png",
        "usable_for_video": True,
        "generation_job_id": "generation-job-old",
        "generation_job_status": "succeeded",
        "completed_at": "2026-09-07T04:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_revise_image_assets_resets_ready_and_updates_prompt() -> None:
    untouched = {
        "asset_id": "asset_character_02",
        "origin": "planned_generation",
        "state": "ready",
        "generation_prompt": "闺蜜",
        "image_url": "https://cdn.example/keep.png",
        "usable_for_video": True,
    }
    result = await ReviseImageAssetsTool().execute(
        _context({"asset_registry": [_ready_asset("asset_character_01"), untouched]}),
        {
            "assets": [
                {
                    "asset_id": "asset_character_01",
                    "generation_prompt": "亚洲面孔女主锁骨相，素净自然妆",
                }
            ]
        },
    )

    registry = {item["asset_id"]: item for item in result.workspace_patch["asset_registry"]}
    revised = registry["asset_character_01"]
    assert revised["state"] == "planned"
    assert revised["generation_prompt"] == "亚洲面孔女主锁骨相，素净自然妆"
    assert revised["usable_for_video"] is False
    assert "image_url" not in revised
    assert "generation_job_id" not in revised
    assert registry["asset_character_02"]["state"] == "ready"
    assert registry["asset_character_02"]["image_url"] == "https://cdn.example/keep.png"
    assert result.model_observation["reset_count"] == 1
    assert result.model_observation["prompt_updated_count"] == 1
    assert "generate_image_assets" in result.public_summary


@pytest.mark.asyncio
async def test_revise_image_assets_resets_ready_without_prompt_change() -> None:
    result = await ReviseImageAssetsTool().execute(
        _context({"asset_registry": [_ready_asset("asset_character_01")]}),
        {"assets": [{"asset_id": "asset_character_01"}]},
    )

    revised = result.workspace_patch["asset_registry"][0]
    assert revised["state"] == "planned"
    assert revised["generation_prompt"] == "女主锁骨相"
    assert result.model_observation["prompt_updated_count"] == 0


@pytest.mark.asyncio
async def test_revise_image_assets_resets_failed_and_updates_prompt() -> None:
    failed = {
        "asset_id": "asset_character_01",
        "origin": "planned_generation",
        "state": "failed",
        "generation_prompt": "女主锁骨相",
        "failure_reason_code": "provider_business_failed",
        "generation_job_id": "generation-job-old",
        "usable_for_video": False,
    }
    result = await ReviseImageAssetsTool().execute(
        _context({"asset_registry": [failed]}),
        {
            "assets": [
                {
                    "asset_id": "asset_character_01",
                    "generation_prompt": "亚洲面孔女主锁骨相",
                }
            ]
        },
    )
    revised = result.workspace_patch["asset_registry"][0]
    assert revised["state"] == "planned"
    assert revised["generation_prompt"] == "亚洲面孔女主锁骨相"
    assert "failure_reason_code" not in revised
    assert result.model_observation["reset_count"] == 1


@pytest.mark.asyncio
async def test_revise_image_assets_rejects_duplicate_and_missing_ids() -> None:
    tool = ReviseImageAssetsTool()
    registry = [_ready_asset("asset_character_01")]
    with pytest.raises(VideoToolValidationError, match="重复"):
        await tool.execute(
            _context({"asset_registry": registry}),
            {
                "assets": [
                    {"asset_id": "asset_character_01"},
                    {"asset_id": "asset_character_01"},
                ]
            },
        )
    with pytest.raises(VideoToolValidationError, match="不在当前 Workspace"):
        await tool.execute(
            _context({"asset_registry": registry}),
            {"assets": [{"asset_id": "asset_missing"}]},
        )


@pytest.mark.asyncio
async def test_revise_image_assets_rejects_uploaded_and_generating() -> None:
    tool = ReviseImageAssetsTool()
    uploaded = {
        "asset_id": "asset_product_01",
        "origin": "existing_material",
        "state": "ready",
        "source_material_id": "material-1",
        "usable_for_video": True,
    }
    generating = {
        "asset_id": "asset_character_01",
        "origin": "planned_generation",
        "state": "generating",
        "generation_prompt": "女主",
        "generation_job_id": "generation-job-running",
    }
    with pytest.raises(VideoToolValidationError, match="不是可修订的待生成资产"):
        await tool.execute(_context({"asset_registry": [uploaded]}), {"assets": [{"asset_id": "asset_product_01"}]})
    with pytest.raises(VideoToolValidationError, match="正在生成"):
        await tool.execute(
            _context({"asset_registry": [generating]}),
            {"assets": [{"asset_id": "asset_character_01"}]},
        )


@pytest.mark.asyncio
async def test_generate_image_assets_accepts_assets_after_revise() -> None:
    revised = await ReviseImageAssetsTool().execute(
        _context({"asset_registry": [_ready_asset("asset_character_01")]}),
        {
            "assets": [
                {"asset_id": "asset_character_01", "generation_prompt": "亚洲面孔女主"},
            ]
        },
    )
    generated = await GenerateImageAssetsTool(
        generation_job_service=_FakeGenerationJobService()
    ).execute(
        _context({"asset_registry": revised.workspace_patch["asset_registry"]}),
        {"asset_ids": ["asset_character_01"]},
    )
    assert generated.model_observation["status"] == "submitted"
    assert generated.requires_confirmation is True
    assert generated.workspace_patch["asset_registry"][0]["state"] == "generating"


def test_revise_image_assets_is_published_as_non_billing_tool() -> None:
    tool = runtime_video_tool_registry().resolve("revise_image_assets")
    assert tool is not None
    spec = tool.spec
    assert spec.cost_level.value == "none"
    assert spec.confirmation_required is False
    assert spec.workspace_mutations == ("asset_registry",)
