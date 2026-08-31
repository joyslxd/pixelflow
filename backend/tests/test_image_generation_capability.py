from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.image_assets import GenerateImageAssetsTool
from pixelflow.capabilities.image_generation import (
    ContentAppImageGenerationAdapter,
    ContentAppImageProviderSettings,
)
from pixelflow.operations.jobs.batch_repository import MemoryOperationBatchRepository
from pixelflow.video.adapters.operations.images import M06ImageGenerationBatchOperationPort
from pixelflow.video.adapters.operations.projector import build_image_asset_success_patch
from pixelflow.video.contracts import VideoWorkspace


@pytest.mark.asyncio
async def test_content_app_image_adapter_maps_task_and_image_result() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = json_load(request.content)
        return httpx.Response(200, json={"success": True, "data": {"taskId": "img-task-1", "status": "processing"}})

    adapter = ContentAppImageGenerationAdapter(
        ContentAppImageProviderSettings("https://content.example/api", "image", "v1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    snapshot = await adapter.start(
        {"generation_mode": "text_to_image", "prompt": "厨房", "model": "seeddream-5.0", "ratio": "9:16", "size": "1080p"},
        authorization="Bearer transient",
        idempotency_key="operation-key",
    )

    assert snapshot.provider_job_id == "img-task-1"
    assert snapshot.outcome.value == "polling"
    assert seen["url"] == "https://content.example/api/picture/text_to_image"
    assert seen["json"] == {"prompt": "厨房", "model": "seeddream-5.0", "size": "1080p", "ratio": "9:16", "num_images": 1}
    assert seen["headers"]["modeltype"] == "seeddream-5.0"
    assert seen["headers"]["billtype"] == "2"


@pytest.mark.asyncio
async def test_generate_image_assets_creates_batch_for_planned_assets() -> None:
    repository = MemoryOperationBatchRepository()
    workspace = VideoWorkspace(
        workspace_id="workspace-images",
        conversation_id="conversation-images",
        revision=3,
        payload={
            "asset_registry": [
                {
                    "asset_id": "asset-character-host",
                    "kind": "character",
                    "role": "女主角",
                    "origin": "planned_generation",
                    "generation_prompt": "年轻女主人设定图",
                    "state": "planned",
                    "usable_for_video": False,
                }
            ]
        },
    )
    port = M06ImageGenerationBatchOperationPort(batch_repository=repository)
    result = await GenerateImageAssetsTool(batch_operation_port=port).execute(
        VideoToolContext(
            user_id="user",
            workspace=workspace,
            run_id="hrun_1234567890abcdef",
            tool_call_id="call_image_1",
        ),
        {"asset_ids": ["asset-character-host"]},
    )

    assert result.model_observation["asset_ids"] == ["asset-character-host"]
    assert result.model_observation["batch_ids"]
    batch = await repository.list_dispatchable_batches(limit=1)
    assert batch[0].children[0].scene_id == "asset-character-host"


def json_load(value: bytes) -> object:
    import json

    return json.loads(value)


def test_image_asset_success_projects_ready_and_keeps_provider_reference() -> None:
    patch = build_image_asset_success_patch(
        {
            "asset_registry": [{
                "asset_id": "asset-kitchen",
                "kind": "scene",
                "role": "厨房",
                "origin": "planned_generation",
                "generation_prompt": "现代厨房",
                "state": "planned",
                "usable_for_video": False,
            }]
        },
        asset_id="asset-kitchen",
        result={
            "image_url": "https://cdn.example/kitchen.png",
            "artifact_ref": "artifact:image:kitchen.png",
        },
        now=datetime.now(UTC),
    )

    assert patch is not None
    asset = patch["asset_registry"][0]
    assert asset["state"] == "ready"
    assert asset["usable_for_video"] is True
    assert asset["provider_artifact_ref"] == "artifact:image:kitchen.png"
    assert asset["image_url"] == "https://cdn.example/kitchen.png"
