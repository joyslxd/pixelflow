from __future__ import annotations

import pytest

from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowAssetRecord,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
    PixelFlowTaskRecord,
)


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


@pytest.mark.asyncio
async def test_memory_conversation_store_paginates_and_restores_context():
    store = MemoryPixelFlowTaskStore()
    for i in range(7):
        await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=f"c{i}",
                user_id="u1",
                title=f"对话 {i}",
                current_task_id=f"t{i}",
                last_phase="intake",
                context={"index": i},
            )
        )

    first_page, next_cursor = await store.list_conversations(user_id="u1", limit=5)
    assert [r.conversation_id for r in first_page] == ["c6", "c5", "c4", "c3", "c2"]
    assert next_cursor

    second_page, final_cursor = await store.list_conversations(user_id="u1", limit=5, cursor=next_cursor)
    assert [r.conversation_id for r in second_page] == ["c1", "c0"]
    assert final_cursor is None

    other = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="other",
            user_id="u2",
            title="其他用户对话",
            current_task_id="t-other",
            last_phase="done",
        )
    )
    assert other.conversation_id == "other"
    assert await store.get_conversation("other", user_id="u1") is None

    message = await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="m1",
            conversation_id="c6",
            user_id="u1",
            role="user",
            content="生成一条口红短视频",
            payload={"time": "10:00"},
        )
    )
    assert message.message_id == "m1"

    restored = await store.get_conversation("c6", user_id="u1")
    assert restored is not None
    assert restored.current_task_id == "t6"
    assert restored.context["index"] == 6
    assert [m.content for m in await store.list_conversation_messages("c6", user_id="u1")] == ["生成一条口红短视频"]


def test_pixelflow_router_imports():
    from app.gateway.routers import pixelflow_tasks

    paths = {route.path for route in pixelflow_tasks.router.routes}
    assert pixelflow_tasks.router.prefix == "/agent/flows"
    assert "/agent/flows" in paths
    assert "/agent/flows/{task_id}/events" in paths
    assert "/agent/flows/{task_id}/assets" in paths


def test_mysql_task_store_initializes_conversation_tables():
    from pixelflow.tasks.model import PixelFlowConversationMessageRow, PixelFlowConversationRow
    from pixelflow.tasks.mysql import PIXELFLOW_TASK_TABLES

    assert PixelFlowConversationRow.__table__ in PIXELFLOW_TASK_TABLES
    assert PixelFlowConversationMessageRow.__table__ in PIXELFLOW_TASK_TABLES


def test_explainable_event_contract_for_generate_phase():
    """生成阶段必须返回可解释事件，而不是返回大模型原始思维链。"""
    from app.gateway.routers import pixelflow_tasks

    events = pixelflow_tasks._build_phase_transition_events(
        previous_phase="brief_review",
        phase="generate",
        state={
            "task_id": "t1",
            "brief": {"brief_id": "brief-1", "shots": [{"shot_id": "s1"}, {"shot_id": "s2"}]},
            "generated_assets": [{"ok": True, "url": "https://cdn.example/1.mp4"}],
        },
        run_id="run-1",
    )

    event_names = [name for name, _payload in events]
    assert event_names == ["step_finished", "step_started", "vendor_call_started", "vendor_call_finished"]
    assert events[1][1]["phase"] == "generate"
    assert events[1][1]["summary"]
    assert "chain_of_thought" not in events[1][1]
    assert "raw_thought" not in events[1][1]


def test_asset_ready_events_only_expose_safe_asset_fields():
    from app.gateway.routers import pixelflow_tasks

    events = pixelflow_tasks._build_asset_ready_events(
        [
            PixelFlowAssetRecord(
                asset_id="a1",
                task_id="t1",
                user_id="u1",
                asset_type="final_video",
                status="ready",
                phase="done",
                url="https://cdn.example/final.mp4",
                local_path="/tmp/private/final.mp4",
                metadata={"secret": "hidden", "duration_sec": 12},
            )
        ],
        run_id="run-1",
    )

    assert events == [
        (
            "asset_ready",
            {
                "asset_id": "a1",
                "asset_type": "final_video",
                "phase": "done",
                "status": "ready",
                "url": "https://cdn.example/final.mp4",
                "vendor": "",
                "summary": "最终成片已准备好，可以在前端预览或下载。",
                "run_id": "run-1",
            },
        )
    ]
