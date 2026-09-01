from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.credential_store import TransientBatchCredentialStore
from pixelflow.agent_tools.video.image_asset_inspection import InspectImageAssetsTool
from pixelflow.agent_tools.video.image_assets import GenerateImageAssetsTool
from pixelflow.agent_tools.video.operation_batch import InspectOperationBatchTool
from pixelflow.capabilities.image_generation import (
    ContentAppImageGenerationAdapter,
    ContentAppImageProviderSettings,
)
from pixelflow.operations.jobs.batch import build_operation_batch_plan
from pixelflow.operations.jobs.batch_repository import MemoryOperationBatchRepository
from pixelflow.video.adapters.operations.images import (
    M06ImageGenerationBatchDispatcher,
    M06ImageGenerationBatchDispatcherWorker,
    M06ImageGenerationBatchOperationPort,
    _image_generation_request,
    _image_operation_stage,
)
from pixelflow.video.adapters.operations.projector import build_image_asset_success_patch
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace import MemoryVideoAgentRepository


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
    assert seen["url"] == "https://content.example/api/picture/text_to_image?projectId=1"
    assert seen["json"] == {
        "prompt": "厨房",
        "model": "seeddream-5.0",
        "model_version": "seeddream-5.0",
        "width": "9",
        "height": "16",
        "imageSize": "1080p",
        "num": 1,
        "oldFileOrderList": [],
    }
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


@pytest.mark.asyncio
async def test_generate_image_assets_unavailable_result_matches_manifest_observation_contract() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-images-unavailable",
        conversation_id="conversation-images-unavailable",
        revision=1,
        payload={"asset_registry": []},
    )
    tool = GenerateImageAssetsTool()

    result = await tool.execute(
        VideoToolContext(user_id="user", workspace=workspace),
        {"asset_ids": ["asset-character-host"]},
    )

    assert result.model_observation == {"status": "unavailable", "asset_ids": ["asset-character-host"]}
    assert set(result.model_observation).issubset(tool.spec.model_observation_keys)


def test_image_generation_request_uses_workspace_ratio_1080p_and_asset_prompt() -> None:
    request = _image_generation_request(
        {
            "creative_brief": {"aspect_ratio": "9:16"},
            "creation_contract": {"video_ratio": "16:9"},
        },
        {
            "asset_id": "asset-character-host",
            "generation_prompt": "资产注册表中的唯一图片提示词",
            "ratio": "1:1",
            "size": "1080p",
        },
    )

    assert request["prompt"] == "资产注册表中的唯一图片提示词"
    assert request["ratio"] == "16:9"
    assert request["size"] == "1080p"


def test_image_operation_stage_matches_batch_child_identity() -> None:
    """图片子项的 stage 必须与 M5 计划使用的版本后缀一致。"""

    asset_id = "asset-character-host"
    plan = build_operation_batch_plan(
        run_id="hrun_" + "b" * 32,
        tool_call_id="call_image_stage",
        scene_ids=(asset_id,),
        variant_count=1,
        attempt=1,
        stage_prefix="generate_image_asset",
    )
    from pixelflow.operations.jobs.identity import build_operation_idempotency_key

    assert plan.children[0].operation_idempotency_key == build_operation_idempotency_key(
        plan.batch_id,
        _image_operation_stage(asset_id),
        1,
        1,
    )


@pytest.mark.asyncio
async def test_image_dispatcher_marks_start_exception_as_failed(caplog: pytest.LogCaptureFixture) -> None:
    """Provider 启动异常不得让图片子项永久遗留在 starting。"""

    class FailingOperationPort:
        async def start_asset(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("provider unavailable")

    repository = MemoryOperationBatchRepository()
    plan = build_operation_batch_plan(
        run_id="hrun_" + "c" * 32,
        tool_call_id="call_image_failure",
        scene_ids=("asset-character-host",),
        variant_count=1,
        attempt=1,
        stage_prefix="generate_image_asset",
    )
    batch = await repository.create_or_read(
        user_id="user",
        conversation_id="conversation-images",
        workspace_id="workspace-images",
        plan=plan,
    )
    dispatcher = M06ImageGenerationBatchDispatcher(
        batch_repository=repository,
        operation_port=FailingOperationPort(),  # type: ignore[arg-type]
    )

    await dispatcher.dispatch_start_slots(
        batch=batch,
        context=VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-images",
                conversation_id="conversation-images",
                revision=1,
            ),
        ),
        assets_by_id={"asset-character-host": {"asset_id": "asset-character-host"}},
        attempt=1,
    )

    current = await repository.get_batch(
        user_id="user",
        conversation_id="conversation-images",
        workspace_id="workspace-images",
        batch_id=batch.batch_id,
    )
    assert current is not None
    assert current.children[0].status == "failed"
    assert "image_batch_child_start_failed" in caplog.text


@pytest.mark.asyncio
async def test_image_worker_marks_stale_starting_child_failed_when_credential_is_gone(caplog: pytest.LogCaptureFixture) -> None:
    """Gateway 重启丢失瞬时授权时，已领取子项必须收口而不是永久 starting。"""

    repository = MemoryOperationBatchRepository()
    plan = build_operation_batch_plan(
        run_id="hrun_" + "d" * 32,
        tool_call_id="call_image_stale",
        scene_ids=("asset-character-host",),
        variant_count=1,
        attempt=1,
        stage_prefix="generate_image_asset",
    )
    batch = await repository.create_or_read(
        user_id="user",
        conversation_id="conversation-images",
        workspace_id="workspace-images",
        plan=plan,
        run_id="hrun_" + "d" * 32,
        tool_call_id="call_image_stale",
        attempt=1,
    )
    await repository.claim_children(batch_id=batch.batch_id, max_concurrent=1)
    worker = M06ImageGenerationBatchDispatcherWorker(
        batch_repository=repository,
        video_repository=MemoryVideoAgentRepository(),
        dispatcher=M06ImageGenerationBatchDispatcher(
            batch_repository=repository,
            operation_port=object(),  # type: ignore[arg-type]
        ),
        credential_store=TransientBatchCredentialStore(),
        worker_id="test-image-worker",
        scan_interval=timedelta(seconds=1),
    )

    await worker.run_once()

    current = await repository.get_batch(
        user_id="user",
        conversation_id="conversation-images",
        workspace_id="workspace-images",
        batch_id=batch.batch_id,
    )
    assert current is not None
    assert current.children[0].status == "failed"
    assert "image_batch_starting_credential_missing" in caplog.text


@pytest.mark.asyncio
async def test_inspect_image_assets_reports_safe_per_asset_status() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-images-inspect",
        conversation_id="conversation-images-inspect",
        revision=1,
        payload={
            "asset_registry": [
                {"asset_id": "ready", "kind": "character", "state": "ready", "usable_for_video": True},
                {"asset_id": "running", "kind": "scene", "state": "polling", "usable_for_video": False},
                {"asset_id": "failed", "kind": "prop", "state": "failed", "usable_for_video": False},
            ]
        },
    )
    result = await InspectImageAssetsTool().execute(
        VideoToolContext(user_id="user", workspace=workspace),
        {},
    )

    assert result.model_observation["status"] == "running"
    assert result.model_observation["total"] == 3
    assert result.model_observation["ready"] == 1
    assert result.model_observation["running"] == 1
    assert result.model_observation["failed"] == 1
    assert result.model_observation["can_generate_scenes"] is False
    assert "image_url" not in result.model_observation


@pytest.mark.asyncio
async def test_inspect_operation_batch_only_reads_current_workspace_batch() -> None:
    repository = MemoryOperationBatchRepository()
    plan = build_operation_batch_plan(
        run_id="hrun_" + "a" * 32,
        tool_call_id="tool-batch-inspect",
        scene_ids=("asset-character-host", "asset-kitchen"),
        variant_count=1,
        attempt=1,
        stage_prefix="generate_image_asset",
    )
    batch = await repository.create_or_read(
        user_id="user",
        conversation_id="conversation-images",
        workspace_id="workspace-images",
        plan=plan,
    )
    first = (await repository.claim_children(batch_id=batch.batch_id, max_concurrent=1))[0]
    await repository.mark_child_polling(
        batch_id=batch.batch_id,
        child_key=first.operation_idempotency_key,
        job_id="operation:image:one",
    )
    result = await InspectOperationBatchTool(batch_repository=repository).execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-images",
                conversation_id="conversation-images",
                revision=1,
            ),
        ),
        {"batch_id": batch.batch_id},
    )

    assert result.model_observation["batch_id"] == batch.batch_id
    assert result.model_observation["total"] == 2
    assert result.model_observation["polling"] == 1
    assert any(
        child["job_id"] == "operation:image:one"
        for child in result.model_observation["children"]
    )


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
