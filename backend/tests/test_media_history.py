from __future__ import annotations

import pytest

from pixelflow.generate.media_history import (
    build_scene_assets_artifact,
    media_result_client_message_id,
    persist_media_result_message,
    persist_scene_assets_progress,
    scene_asset_progress_client_message_id,
    scene_assets_should_persist,
)
from pixelflow.tasks import MemoryPixelFlowTaskStore
from pixelflow.tasks.store import PixelFlowConversationRecord


@pytest.mark.asyncio
async def test_persist_scene_assets_result_is_idempotent_and_updates_context():
    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="c-media",
            user_id="u1",
            title="media history",
            last_phase="scene_asset_generation_running",
            context={},
        )
    )
    job_id = "job-assets-1"
    result = {
        "ok": True,
        "message": "ok",
        "global_assets": {
            "characters": [{"asset_id": "c1", "images": ["https://cdn.example/c1.png"]}],
            "scenes": [],
            "props": [],
        },
        "scene_packages": [{"scene_id": "s1", "image_urls": ["https://cdn.example/s1.png"]}],
        "failed_assets": [],
        "target_duration_ms": 30_000,
        "creation_contract": {"image_model": "seedream"},
    }
    assert scene_assets_should_persist(result) is True

    first = await persist_media_result_message(
        store,
        conversation_id="c-media",
        user_id="u1",
        job_id=job_id,
        kind="scene_assets",
        content="参考图生成完成（本批）",
        artifact=build_scene_assets_artifact(result, job_id=job_id),
        last_phase="scene_package_ready",
        context_patch={
            "global_assets": result["global_assets"],
            "scene_packages": result["scene_packages"],
        },
    )
    second = await persist_media_result_message(
        store,
        conversation_id="c-media",
        user_id="u1",
        job_id=job_id,
        kind="scene_assets",
        content="参考图生成完成（本批）重复",
        artifact=build_scene_assets_artifact(result, job_id=job_id),
        last_phase="scene_package_ready",
        context_patch={
            "global_assets": result["global_assets"],
            "scene_packages": result["scene_packages"],
        },
    )
    assert first is True
    assert second is True

    messages = await store.list_conversation_messages("c-media", user_id="u1")
    media_messages = [
        item
        for item in messages
        if (item.payload or {}).get("client_message_id") == media_result_client_message_id("scene_assets", job_id)
    ]
    assert len(media_messages) == 1
    assert media_messages[0].payload["artifact"]["sceneAssetsGenerating"] is False
    assert media_messages[0].payload["artifact"]["type"] == "video_scene_packages"

    conversation = await store.get_conversation("c-media", user_id="u1")
    assert conversation is not None
    assert conversation.last_phase == "scene_package_ready"
    assert conversation.context["global_assets"]["characters"][0]["images"] == ["https://cdn.example/c1.png"]
    assert conversation.context["scene_packages"][0]["image_urls"] == ["https://cdn.example/s1.png"]


def test_scene_assets_without_urls_should_not_persist():
    assert scene_assets_should_persist({"ok": False, "global_assets": {}, "scene_packages": []}) is False


@pytest.mark.asyncio
async def test_persist_scene_assets_progress_upserts_partial_images():
    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="c-progress",
            user_id="u1",
            title="progress",
            last_phase="scene_asset_generation_running",
            context={},
        )
    )
    job_id = "job-progress-1"
    first = await persist_scene_assets_progress(
        store,
        conversation_id="c-progress",
        user_id="u1",
        job_id=job_id,
        global_assets={"scenes": [{"asset_id": "office", "name": "办公室", "images": ["https://cdn/office-1.png"]}]},
        scene_packages=[{"scene_id": "s1", "image_urls": ["https://cdn/office-1.png"]}],
        progress={"completed": 1, "total": 3, "asset_id": "office", "asset_name": "办公室", "asset_type": "scene_image", "ok": True},
    )
    second = await persist_scene_assets_progress(
        store,
        conversation_id="c-progress",
        user_id="u1",
        job_id=job_id,
        global_assets={
            "scenes": [
                {"asset_id": "office", "name": "办公室", "images": ["https://cdn/office-1.png"]},
                {"asset_id": "lab", "name": "实验室", "images": ["https://cdn/lab-1.png"]},
            ]
        },
        scene_packages=[
            {"scene_id": "s1", "image_urls": ["https://cdn/office-1.png"]},
            {"scene_id": "s2", "image_urls": ["https://cdn/lab-1.png"]},
        ],
        progress={"completed": 2, "total": 3, "asset_id": "lab", "asset_name": "实验室", "asset_type": "scene_image", "ok": True},
    )
    assert first is True
    assert second is True

    messages = await store.list_conversation_messages("c-progress", user_id="u1")
    progress_messages = [
        item
        for item in messages
        if (item.payload or {}).get("client_message_id") == scene_asset_progress_client_message_id(job_id)
    ]
    assert len(progress_messages) == 1
    artifact = progress_messages[0].payload["artifact"]
    assert artifact["sceneAssetsGenerating"] is True
    assert len(artifact["videoScenePackages"]["global_assets"]["scenes"]) == 2
    assert "2/3" in progress_messages[0].content

    conversation = await store.get_conversation("c-progress", user_id="u1")
    assert conversation is not None
    assert conversation.context["global_assets"]["scenes"][1]["images"] == ["https://cdn/lab-1.png"]
    assert conversation.last_phase == "scene_asset_generation_running"
