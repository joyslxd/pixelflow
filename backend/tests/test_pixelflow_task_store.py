from __future__ import annotations

import pytest

from pixelflow.tasks import MemoryPixelFlowTaskStore, PixelFlowAssetRecord, PixelFlowTaskRecord


@pytest.mark.asyncio
async def test_memory_task_store_create_update_and_events():
    store = MemoryPixelFlowTaskStore()
    task = await store.create(
        PixelFlowTaskRecord(
            task_id="t1",
            user_id="u1",
            task_type="ecom_video",
            status="created",
            phase="intake",
            thread_id="th1",
            product_info={"product_name": "杯子"},
        )
    )

    assert task.task_id == "t1"
    assert (await store.get("t1", user_id="u1")).product_info["product_name"] == "杯子"
    assert await store.get("t1", user_id="other") is None

    updated = await store.update("t1", user_id="u1", status="running", phase="creative", brief={"brief_id": "b1"})
    assert updated.status == "running"
    assert updated.phase == "creative"
    assert updated.brief["brief_id"] == "b1"

    first = await store.append_event("t1", "task_created", {"phase": "intake"}, user_id="u1")
    second = await store.append_event("t1", "phase_change", {"phase": "creative"}, user_id="u1")

    assert first["id"] < second["id"]
    rows = await store.list_events("t1", user_id="u1", after_id=first["id"])
    assert [r["event"] for r in rows] == ["phase_change"]

    asset = await store.upsert_asset(
        PixelFlowAssetRecord(
            asset_id="a1",
            task_id="t1",
            user_id="u1",
            asset_type="generated_video",
            status="ready",
            phase="generate",
            shot_id="shot_001",
            url="https://x/clip.mp4",
            vendor="borgrise",
            vendor_task_id="bt1",
        )
    )
    assert asset.url == "https://x/clip.mp4"
    assets = await store.list_assets("t1", user_id="u1")
    assert len(assets) == 1
    assert assets[0].asset_type == "generated_video"


def test_pixelflow_router_imports():
    from app.gateway.routers import pixelflow_tasks

    paths = {route.path for route in pixelflow_tasks.router.routes}
    assert "/api/tasks" in paths
    assert "/api/tasks/{task_id}/events" in paths
    assert "/api/tasks/{task_id}/assets" in paths
