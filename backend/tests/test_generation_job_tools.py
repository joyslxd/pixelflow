"""验证生成 Tool 只创建 GenerationJob，不再创建 Batch 或 Operation。"""

from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.image_assets import GenerateImageAssetsTool
from pixelflow.agent_tools.video.scene import GenerateScenesTool
from pixelflow.generation_jobs.contracts import GenerationJobKind, GenerationJobStatus
from pixelflow.generation_jobs.service import GenerationJobSubmission
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
    assert not any("batch" in key for key in result.model_observation)


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
