from __future__ import annotations

import asyncio

import pytest

from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowAssetRecord,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
    PixelFlowTaskRecord,
    SQLPixelFlowTaskStore,
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


@pytest.mark.asyncio
async def test_memory_conversation_store_updates_message_by_client_message_id():
    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(PixelFlowConversationRecord(conversation_id="c-ppt", user_id="u1", title="PPT"))
    await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="server-message-id",
            conversation_id="c-ppt",
            user_id="u1",
            role="assistant",
            content="PPT 图片生成中。",
            payload={
                "client_message_id": "client-message-id",
                "artifact": {
                    "type": "ppt_images",
                    "pptImages": {"pages": [{"page_index": 1, "status": "running", "image_url": None}]},
                },
            },
        )
    )

    updated = await store.update_conversation_message(
        "c-ppt",
        "client-message-id",
        user_id="u1",
        content="PPT 图片已生成。",
        payload={
            "client_message_id": "client-message-id",
            "artifact": {
                "type": "ppt_images",
                "pptImages": {"pages": [{"page_index": 1, "status": "completed", "image_url": "https://cdn.example/p1.png"}]},
            },
        },
    )

    assert updated is not None
    assert updated.message_id == "server-message-id"
    assert updated.content == "PPT 图片已生成。"
    assert updated.payload["artifact"]["pptImages"]["pages"][0]["image_url"] == "https://cdn.example/p1.png"
    assert await store.update_conversation_message("c-ppt", "client-message-id", user_id="other", content="x") is None


@pytest.mark.asyncio
async def test_memory_conversation_store_concurrent_duplicate_message_returns_existing():
    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(PixelFlowConversationRecord(conversation_id="c-plan", user_id="u1", title="Plan"))

    first, retried = await asyncio.gather(
        store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id="stable-plan-message",
                conversation_id="c-plan",
                user_id="u1",
                role="assistant",
                content="plan.md 首次写入",
                payload={"client_message_id": "plan-v1"},
            )
        ),
        store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id="stable-plan-message",
                conversation_id="c-plan",
                user_id="u1",
                role="assistant",
                content="重试不应重复插入",
                payload={"client_message_id": "plan-v1"},
            )
        ),
    )

    assert first.message_id == retried.message_id == "stable-plan-message"
    assert retried.content == "plan.md 首次写入"
    messages = await store.list_conversation_messages("c-plan", user_id="u1")
    assert [message.content for message in messages] == ["plan.md 首次写入"]


@pytest.mark.asyncio
async def test_memory_agent_runtime_response_write_commits_message_and_version_together():
    """专用写单元必须同时提交可见消息和响应后的上下文版本。"""

    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="c-live-response",
            user_id="u1",
            orchestration_mode="video_agent_v2",
            context={
                "__agent_runtime": {
                    "mode": "primary",
                    "context_version": 4,
                }
            },
        )
    )
    message = PixelFlowConversationMessageRecord(
        message_id="response-message-1",
        conversation_id="c-live-response",
        user_id="u1",
        role="user",
        content="同意方案",
        payload={"interrupt_id": "interrupt-1"},
        created_at="2026-08-01T08:00:00+00:00",
    )

    async with store.agent_runtime_interrupt_response_write(
        conversation_id="c-live-response",
        user_id="u1",
        message=message,
        occurred_at="2026-08-01T08:00:00+00:00",
    ) as write:
        assert write.pre_input_context_version == 4
        assert write.context_version == 5
        assert write.message == message

    conversation = await store.get_conversation(
        "c-live-response",
        user_id="u1",
    )
    assert conversation is not None
    assert conversation.context["__agent_runtime"]["context_version"] == 5
    assert conversation.revision == 2
    assert await store.list_conversation_messages(
        "c-live-response",
        user_id="u1",
    ) == [message]


@pytest.mark.asyncio
async def test_memory_agent_runtime_response_write_rolls_back_on_outer_failure():
    """Repository 后续写入失败时，专用写单元必须回滚消息、版本和 revision。"""

    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="c-live-rollback",
            user_id="u1",
            orchestration_mode="video_agent_v2",
            context={
                "__agent_runtime": {
                    "mode": "primary",
                    "context_version": 9,
                }
            },
        )
    )
    before = await store.get_conversation("c-live-rollback", user_id="u1")
    message = PixelFlowConversationMessageRecord(
        message_id="response-message-rollback",
        conversation_id="c-live-rollback",
        user_id="u1",
        role="user",
        content="同意方案",
        payload={"interrupt_id": "interrupt-1"},
        created_at="2026-08-01T08:00:00+00:00",
    )

    with pytest.raises(RuntimeError, match="注入写入失败"):
        async with store.agent_runtime_interrupt_response_write(
            conversation_id="c-live-rollback",
            user_id="u1",
            message=message,
            occurred_at="2026-08-01T08:00:00+00:00",
        ):
            raise RuntimeError("注入写入失败")

    assert await store.get_conversation(
        "c-live-rollback",
        user_id="u1",
    ) == before
    assert await store.list_conversation_messages(
        "c-live-rollback",
        user_id="u1",
    ) == []


def _jianying_pending(job_id: str, conversation_id: str, storyboard_version_id: str) -> dict[str, str]:
    return {
        "job_id": job_id,
        "conversation_id": conversation_id,
        "storyboard_version_id": storyboard_version_id,
    }


def _jianying_record(status: str, job_id: str, storyboard_version_id: str) -> dict[str, str]:
    record = {
        "status": status,
        "job_id": job_id,
        "storyboard_version_id": storyboard_version_id,
    }
    if status == "succeeded":
        record["download_url"] = "https://cdn.example.com/draft.zip"
    return record


@pytest.mark.asyncio
async def test_memory_conversation_store_atomically_merges_concurrent_jianying_draft_context():
    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="c-jianying-memory",
            user_id="u1",
            title="剪映草稿",
            context={
                "brand_name": "A 品牌",
                "concurrent_server_field": "保留",
                "jianying_draft_records": {"storyboard-0": {"status": "succeeded"}},
            },
        )
    )

    first, second, generic_update = await asyncio.gather(
        store.patch_jianying_draft_conversation_context(
            "c-jianying-memory",
            user_id="u1",
            expected_job_id="job-1",
            pending_job=_jianying_pending("job-1", "c-jianying-memory", "storyboard-1"),
            records={"storyboard-1": _jianying_record("running", "job-1", "storyboard-1")},
            last_phase="jianying_draft_running",
        ),
        store.patch_jianying_draft_conversation_context(
            "c-jianying-memory",
            user_id="u1",
            expected_job_id="job-2",
            pending_job=None,
            records={"storyboard-2": _jianying_record("failed", "job-2", "storyboard-2")},
            last_phase="jianying_draft_failed",
            resume_error="任务已过期",
        ),
        store.update_conversation(
            "c-jianying-memory",
            user_id="u1",
            context={
                "brand_name": "A 品牌",
                "concurrent_server_field": "保留",
                "generic_concurrent_field": "普通更新已保存",
                "pendingJianyingDraftJob": {"job_id": "stale-job"},
                "pending_jianying_draft_job": {"job_id": "stale-job"},
                "jianyingDraftRecords": {},
                "jianying_draft_records": {},
            },
        ),
    )

    assert first is not None
    assert second is not None
    assert generic_update is not None
    restored = await store.get_conversation("c-jianying-memory", user_id="u1")
    assert restored is not None
    assert restored.context["brand_name"] == "A 品牌"
    assert restored.context["concurrent_server_field"] == "保留"
    assert restored.context["generic_concurrent_field"] == "普通更新已保存"
    assert set(restored.context["jianyingDraftRecords"]) == {
        "storyboard-0",
        "storyboard-1",
        "storyboard-2",
    }
    assert restored.context["jianying_draft_records"] == restored.context["jianyingDraftRecords"]
    assert restored.context["pending_jianying_draft_job"] == restored.context["pendingJianyingDraftJob"]
    assert restored.context["jianying_draft_job_resume_error"] == "任务已过期"
    assert (
        await store.patch_jianying_draft_conversation_context(
            "c-jianying-memory",
            user_id="other",
            expected_job_id="forbidden-job",
            pending_job=None,
            records={},
            last_phase="forbidden",
        )
        is None
    )


@pytest.mark.asyncio
async def test_memory_jianying_draft_state_is_monotonic_for_stale_same_storyboard_writes():
    store = MemoryPixelFlowTaskStore()
    conversation_id = "c-jianying-memory-monotonic"
    storyboard_id = "storyboard-same"
    pending = _jianying_pending("job-1", conversation_id, storyboard_id)
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id="u1",
            title="剪映草稿单调状态",
            last_phase="jianying_draft_running",
            context={
                "pendingJianyingDraftJob": pending,
                "pending_jianying_draft_job": pending,
                "jianyingDraftRecords": {},
                "jianying_draft_records": {},
            },
        )
    )

    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-1",
        pending_job=None,
        records={storyboard_id: _jianying_record("succeeded", "job-1", storyboard_id)},
        last_phase="jianying_draft_succeeded",
    )
    await asyncio.gather(
        store.patch_jianying_draft_conversation_context(
            conversation_id,
            user_id="u1",
            expected_job_id="job-1",
            pending_job=None,
            records={storyboard_id: _jianying_record("failed", "job-1", storyboard_id)},
            last_phase="jianying_draft_failed",
            resume_error="旧标签页失败",
        ),
        store.patch_jianying_draft_conversation_context(
            conversation_id,
            user_id="u1",
            expected_job_id="job-1",
            pending_job=pending,
            records={},
            last_phase="jianying_draft_running",
        ),
    )

    restored = await store.get_conversation(conversation_id, user_id="u1")
    assert restored is not None
    assert restored.context["pendingJianyingDraftJob"] is None
    assert restored.context["jianyingDraftRecords"][storyboard_id]["status"] == "succeeded"
    assert restored.context.get("jianying_draft_job_resume_error") is None
    assert restored.last_phase == "jianying_draft_succeeded"


@pytest.mark.asyncio
async def test_memory_jianying_draft_only_allows_new_job_after_succeeded_record_expires():
    store = MemoryPixelFlowTaskStore()
    storyboard_id = "storyboard-expiration"
    for suffix, expire_at in (
        ("future", "2099-01-01T00:00:00Z"),
        ("missing", None),
        ("invalid", "not-a-date"),
    ):
        conversation_id = f"c-jianying-memory-{suffix}"
        record = _jianying_record("succeeded", f"job-{suffix}-old", storyboard_id)
        if expire_at is not None:
            record["expire_at"] = expire_at
        await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=conversation_id,
                user_id="u1",
                title="有效剪映草稿",
                last_phase="jianying_draft_succeeded",
                context={
                    "pendingJianyingDraftJob": None,
                    "pending_jianying_draft_job": None,
                    "jianyingDraftRecords": {storyboard_id: record},
                    "jianying_draft_records": {storyboard_id: record},
                },
            )
        )
        await store.patch_jianying_draft_conversation_context(
            conversation_id,
            user_id="u1",
            expected_job_id=f"job-{suffix}-new",
            pending_job=_jianying_pending(f"job-{suffix}-new", conversation_id, storyboard_id),
            records={},
            last_phase="jianying_draft_running",
        )
        restored = await store.get_conversation(conversation_id, user_id="u1")
        assert restored is not None
        assert restored.context["pendingJianyingDraftJob"] is None
        assert restored.context["jianyingDraftRecords"][storyboard_id]["job_id"] == f"job-{suffix}-old"
        assert restored.last_phase == "jianying_draft_succeeded"

    conversation_id = "c-jianying-memory-expired"
    expired_record = {
        **_jianying_record("succeeded", "job-expired-old", storyboard_id),
        "expire_at": "2000-01-01T00:00:00Z",
    }
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id="u1",
            title="过期剪映草稿",
            last_phase="jianying_draft_succeeded",
            context={
                "pendingJianyingDraftJob": None,
                "pending_jianying_draft_job": None,
                "jianyingDraftRecords": {storyboard_id: expired_record},
                "jianying_draft_records": {storyboard_id: expired_record},
            },
        )
    )
    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-expired-new",
        pending_job=_jianying_pending("job-expired-new", conversation_id, storyboard_id),
        records={},
        last_phase="jianying_draft_running",
    )
    running = await store.get_conversation(conversation_id, user_id="u1")
    assert running is not None
    assert running.context["pendingJianyingDraftJob"]["job_id"] == "job-expired-new"
    assert running.last_phase == "jianying_draft_running"

    replacement = {
        **_jianying_record("succeeded", "job-expired-new", storyboard_id),
        "expire_at": "2099-01-01T00:00:00Z",
    }
    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-expired-new",
        pending_job=None,
        records={storyboard_id: replacement},
        last_phase="jianying_draft_succeeded",
    )
    restored = await store.get_conversation(conversation_id, user_id="u1")
    assert restored is not None
    assert restored.context["pendingJianyingDraftJob"] is None
    assert restored.context["jianyingDraftRecords"][storyboard_id] == replacement
    assert restored.last_phase == "jianying_draft_succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_download_url", [None, "http://cdn.example.com/old.zip"])
async def test_memory_jianying_draft_invalid_succeeded_record_allows_retry_and_new_terminal(
    invalid_download_url,
):
    store = MemoryPixelFlowTaskStore()
    storyboard_id = "storyboard-invalid-success"
    conversation_id = f"c-jianying-invalid-success-{invalid_download_url is None}"
    old_record = _jianying_record("succeeded", "job-old", storyboard_id)
    if invalid_download_url is None:
        old_record.pop("download_url")
    else:
        old_record["download_url"] = invalid_download_url
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id="u1",
            title="无效成功剪映草稿",
            last_phase="jianying_draft_succeeded",
            context={
                "pendingJianyingDraftJob": None,
                "pending_jianying_draft_job": None,
                "jianyingDraftRecords": {storyboard_id: old_record},
                "jianying_draft_records": {storyboard_id: old_record},
            },
        )
    )

    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-new",
        pending_job=_jianying_pending("job-new", conversation_id, storyboard_id),
        records={},
        last_phase="jianying_draft_running",
    )
    running = await store.get_conversation(conversation_id, user_id="u1")
    assert running is not None
    assert running.context["pendingJianyingDraftJob"]["job_id"] == "job-new"
    assert running.last_phase == "jianying_draft_running"

    replacement = {
        **_jianying_record("succeeded", "job-new", storyboard_id),
        "download_url": "https://cdn.example.com/new.zip",
    }
    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-new",
        pending_job=None,
        records={storyboard_id: replacement},
        last_phase="jianying_draft_succeeded",
    )
    completed = await store.get_conversation(conversation_id, user_id="u1")
    assert completed is not None
    assert completed.context["pendingJianyingDraftJob"] is None
    assert completed.context["jianyingDraftRecords"][storyboard_id] == replacement
    assert completed.last_phase == "jianying_draft_succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["failed", "timeout"])
async def test_memory_jianying_draft_rejects_old_success_after_retry_reaches_terminal(terminal_status):
    store = MemoryPixelFlowTaskStore()
    conversation_id = f"c-jianying-memory-late-old-{terminal_status}"
    storyboard_id = "storyboard-late-old"
    old_record = {
        **_jianying_record("succeeded", "job-old", storyboard_id),
        "expire_at": "2000-01-01T00:00:00Z",
    }
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id="u1",
            title="剪映草稿旧任务迟到",
            last_phase="jianying_draft_succeeded",
            context={
                "pendingJianyingDraftJob": None,
                "pending_jianying_draft_job": None,
                "jianyingDraftRecords": {storyboard_id: old_record},
                "jianying_draft_records": {storyboard_id: old_record},
            },
        )
    )

    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-new",
        pending_job=_jianying_pending("job-new", conversation_id, storyboard_id),
        records={},
        last_phase="jianying_draft_running",
    )
    new_terminal = _jianying_record(terminal_status, "job-new", storyboard_id)
    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-new",
        pending_job=None,
        records={storyboard_id: new_terminal},
        last_phase=f"jianying_draft_{terminal_status}",
    )
    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-old",
        pending_job=None,
        records={
            storyboard_id: {
                **_jianying_record("succeeded", "job-old", storyboard_id),
                "expire_at": "2099-01-01T00:00:00Z",
            }
        },
        last_phase="jianying_draft_succeeded",
    )

    restored = await store.get_conversation(conversation_id, user_id="u1")
    assert restored is not None
    assert restored.context["pendingJianyingDraftJob"] is None
    assert restored.context["jianyingDraftRecords"][storyboard_id] == new_terminal
    assert restored.context["jianying_draft_records"][storyboard_id] == new_terminal
    assert restored.last_phase == f"jianying_draft_{terminal_status}"


@pytest.mark.asyncio
async def test_memory_jianying_draft_allows_explicit_new_job_after_failed_result():
    store = MemoryPixelFlowTaskStore()
    conversation_id = "c-jianying-memory-retry"
    storyboard_id = "storyboard-retry"
    failed_record = _jianying_record("failed", "job-old", storyboard_id)
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id="u1",
            title="剪映草稿失败重试",
            last_phase="jianying_draft_failed",
            context={
                "pendingJianyingDraftJob": None,
                "pending_jianying_draft_job": None,
                "jianyingDraftRecords": {storyboard_id: failed_record},
                "jianying_draft_records": {storyboard_id: failed_record},
            },
        )
    )

    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-retry",
        pending_job=_jianying_pending("job-retry", conversation_id, storyboard_id),
        records={},
        last_phase="jianying_draft_running",
    )
    running = await store.get_conversation(conversation_id, user_id="u1")
    assert running is not None
    assert running.context["pendingJianyingDraftJob"]["job_id"] == "job-retry"
    assert running.last_phase == "jianying_draft_running"

    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-old",
        pending_job=None,
        records={storyboard_id: _jianying_record("timeout", "job-old", storyboard_id)},
        last_phase="jianying_draft_timeout",
    )
    after_stale_terminal = await store.get_conversation(conversation_id, user_id="u1")
    assert after_stale_terminal is not None
    assert after_stale_terminal.context["pendingJianyingDraftJob"]["job_id"] == "job-retry"
    assert after_stale_terminal.context["jianyingDraftRecords"][storyboard_id]["status"] == "failed"
    assert after_stale_terminal.last_phase == "jianying_draft_running"

    await store.patch_jianying_draft_conversation_context(
        conversation_id,
        user_id="u1",
        expected_job_id="job-retry",
        pending_job=None,
        records={storyboard_id: _jianying_record("failed", "job-retry", storyboard_id)},
        last_phase="jianying_draft_failed",
    )
    restored = await store.get_conversation(conversation_id, user_id="u1")
    assert restored is not None
    assert restored.context["pendingJianyingDraftJob"] is None
    assert restored.context["jianyingDraftRecords"][storyboard_id]["job_id"] == "job-retry"
    assert restored.last_phase == "jianying_draft_failed"


@pytest.mark.asyncio
async def test_memory_conversation_store_sorts_by_created_at_not_updated_at():
    from datetime import UTC, datetime, timedelta

    store = MemoryPixelFlowTaskStore()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    older = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="older",
            user_id="u1",
            title="更早创建",
            created_at=base.isoformat(),
            updated_at=(base + timedelta(hours=2)).isoformat(),
        )
    )
    newer = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="newer",
            user_id="u1",
            title="更晚创建",
            created_at=(base + timedelta(hours=1)).isoformat(),
            updated_at=(base + timedelta(hours=1)).isoformat(),
        )
    )

    await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="m1",
            conversation_id=older.conversation_id,
            user_id="u1",
            role="assistant",
            content="后续更新不应改变最近对话的创建时间排序",
        )
    )
    first_page, next_cursor = await store.list_conversations(user_id="u1", limit=1)
    second_page, final_cursor = await store.list_conversations(user_id="u1", limit=1, cursor=next_cursor)

    assert [record.conversation_id for record in first_page] == [newer.conversation_id]
    assert [record.conversation_id for record in second_page] == [older.conversation_id]
    assert final_cursor is None


@pytest.mark.asyncio
async def test_sql_conversation_store_sorts_by_created_at_not_updated_at(tmp_path):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(async_sessionmaker(engine, expire_on_commit=False))
        base = datetime(2026, 1, 1, tzinfo=UTC)

        await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="newer",
                user_id="u1",
                title="更晚创建",
                created_at=(base + timedelta(hours=1)).isoformat(),
                updated_at=(base + timedelta(hours=1)).isoformat(),
            )
        )
        await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="older",
                user_id="u1",
                title="更早创建",
                created_at=base.isoformat(),
                updated_at=(base + timedelta(hours=2)).isoformat(),
            )
        )

        first_page, next_cursor = await store.list_conversations(user_id="u1", limit=1)
        second_page, final_cursor = await store.list_conversations(user_id="u1", limit=1, cursor=next_cursor)

        assert [record.conversation_id for record in first_page] == ["newer"]
        assert [record.conversation_id for record in second_page] == ["older"]
        assert final_cursor is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_conversation_store_emits_timezone_aware_timestamps(tmp_path):
    import re

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    tz_suffix = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(async_sessionmaker(engine, expire_on_commit=False))

        created = await store.create_conversation(PixelFlowConversationRecord(conversation_id="c-tz", user_id="u1", title="时区"))
        assert tz_suffix.search(created.created_at)
        assert tz_suffix.search(created.updated_at)

        message = await store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id="m-tz",
                conversation_id="c-tz",
                user_id="u1",
                role="user",
                content="你好",
            )
        )
        assert tz_suffix.search(message.created_at)

        restored = await store.get_conversation("c-tz", user_id="u1")
        assert restored is not None
        assert tz_suffix.search(restored.created_at)
        assert tz_suffix.search(restored.updated_at)
        listed_messages = await store.list_conversation_messages("c-tz", user_id="u1")
        assert tz_suffix.search(listed_messages[0].created_at)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_conversation_store_updates_message_by_client_message_id(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(async_sessionmaker(engine, expire_on_commit=False))
        await store.create_conversation(PixelFlowConversationRecord(conversation_id="c-ppt-sql", user_id="u1", title="PPT"))
        await store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id="server-message-id",
                conversation_id="c-ppt-sql",
                user_id="u1",
                role="assistant",
                content="PPT 图片生成中。",
                payload={
                    "client_message_id": "client-message-id",
                    "artifact": {
                        "type": "ppt_images",
                        "pptImages": {"pages": [{"page_index": 1, "status": "running", "image_url": None}]},
                    },
                },
            )
        )

        updated = await store.update_conversation_message(
            "c-ppt-sql",
            "client-message-id",
            user_id="u1",
            content="PPT 图片已生成。",
            payload={
                "client_message_id": "client-message-id",
                "artifact": {
                    "type": "ppt_images",
                    "pptImages": {"pages": [{"page_index": 1, "status": "completed", "image_url": "https://cdn.example/p1.png"}]},
                },
            },
        )

        assert updated is not None
        assert updated.message_id == "server-message-id"
        messages = await store.list_conversation_messages("c-ppt-sql", user_id="u1")
        assert messages[0].content == "PPT 图片已生成。"
        assert messages[0].payload["artifact"]["pptImages"]["pages"][0]["image_url"] == "https://cdn.example/p1.png"
        assert await store.update_conversation_message("c-ppt-sql", "client-message-id", user_id="other", content="x") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_conversation_store_concurrent_duplicate_message_returns_existing(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow-idempotency.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(async_sessionmaker(engine, expire_on_commit=False))
        await store.create_conversation(
            PixelFlowConversationRecord(conversation_id="c-plan-sql", user_id="u1", title="Plan")
        )

        first, retried = await asyncio.gather(
            store.append_conversation_message(
                PixelFlowConversationMessageRecord(
                    message_id="stable-plan-message-sql",
                    conversation_id="c-plan-sql",
                    user_id="u1",
                    role="assistant",
                    content="plan.md 首次写入",
                    payload={"client_message_id": "plan-v1"},
                )
            ),
            store.append_conversation_message(
                PixelFlowConversationMessageRecord(
                    message_id="stable-plan-message-sql",
                    conversation_id="c-plan-sql",
                    user_id="u1",
                    role="assistant",
                    content="重试不应重复插入",
                    payload={"client_message_id": "plan-v1"},
                )
            ),
        )

        assert first.message_id == retried.message_id == "stable-plan-message-sql"
        assert first.content == retried.content
        messages = await store.list_conversation_messages("c-plan-sql", user_id="u1")
        assert len(messages) == 1
        assert messages[0].content in {"plan.md 首次写入", "重试不应重复插入"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_conversation_store_atomically_merges_concurrent_jianying_draft_context(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow-jianying.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        first_store = SQLPixelFlowTaskStore(session_factory)
        second_store = SQLPixelFlowTaskStore(session_factory)
        await first_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="c-jianying-sql",
                user_id="u1",
                title="剪映草稿",
                context={
                    "brand_name": "A 品牌",
                    "concurrent_server_field": "保留",
                    "jianyingDraftRecords": {"storyboard-0": {"status": "succeeded"}},
                },
            )
        )

        await asyncio.gather(
            first_store.patch_jianying_draft_conversation_context(
                "c-jianying-sql",
                user_id="u1",
                expected_job_id="job-1",
                pending_job=_jianying_pending("job-1", "c-jianying-sql", "storyboard-1"),
                records={"storyboard-1": _jianying_record("running", "job-1", "storyboard-1")},
                last_phase="jianying_draft_running",
            ),
            second_store.patch_jianying_draft_conversation_context(
                "c-jianying-sql",
                user_id="u1",
                expected_job_id="job-2",
                pending_job=None,
                records={"storyboard-2": _jianying_record("succeeded", "job-2", "storyboard-2")},
                last_phase="jianying_draft_succeeded",
            ),
            second_store.update_conversation(
                "c-jianying-sql",
                user_id="u1",
                context={
                    "brand_name": "A 品牌",
                    "concurrent_server_field": "保留",
                    "generic_concurrent_field": "普通更新已保存",
                    "pendingJianyingDraftJob": {"job_id": "stale-job"},
                    "pending_jianying_draft_job": {"job_id": "stale-job"},
                    "jianyingDraftRecords": {},
                    "jianying_draft_records": {},
                },
            ),
        )

        restored = await first_store.get_conversation("c-jianying-sql", user_id="u1")
        assert restored is not None
        assert restored.context["brand_name"] == "A 品牌"
        assert restored.context["concurrent_server_field"] == "保留"
        assert restored.context["generic_concurrent_field"] == "普通更新已保存"
        assert set(restored.context["jianyingDraftRecords"]) == {
            "storyboard-0",
            "storyboard-1",
            "storyboard-2",
        }
        assert restored.context["jianying_draft_records"] == restored.context["jianyingDraftRecords"]
        assert restored.context["pending_jianying_draft_job"] == restored.context["pendingJianyingDraftJob"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_sql_stores_keep_jianying_draft_succeeded_state_monotonic_for_same_storyboard(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow-jianying-monotonic.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        first_store = SQLPixelFlowTaskStore(session_factory)
        second_store = SQLPixelFlowTaskStore(session_factory)
        conversation_id = "c-jianying-sql-monotonic"
        storyboard_id = "storyboard-same"
        pending = _jianying_pending("job-1", conversation_id, storyboard_id)
        await first_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=conversation_id,
                user_id="u1",
                title="剪映草稿单调状态",
                last_phase="jianying_draft_running",
                context={
                    "pendingJianyingDraftJob": pending,
                    "pending_jianying_draft_job": pending,
                    "jianyingDraftRecords": {},
                    "jianying_draft_records": {},
                },
            )
        )
        await first_store.patch_jianying_draft_conversation_context(
            conversation_id,
            user_id="u1",
            expected_job_id="job-1",
            pending_job=None,
            records={storyboard_id: _jianying_record("succeeded", "job-1", storyboard_id)},
            last_phase="jianying_draft_succeeded",
        )

        await asyncio.gather(
            second_store.patch_jianying_draft_conversation_context(
                conversation_id,
                user_id="u1",
                expected_job_id="job-1",
                pending_job=None,
                records={storyboard_id: _jianying_record("timeout", "job-1", storyboard_id)},
                last_phase="jianying_draft_timeout",
            ),
            first_store.patch_jianying_draft_conversation_context(
                conversation_id,
                user_id="u1",
                expected_job_id="job-1",
                pending_job=pending,
                records={},
                last_phase="jianying_draft_running",
            ),
        )

        restored = await second_store.get_conversation(conversation_id, user_id="u1")
        assert restored is not None
        assert restored.context["pendingJianyingDraftJob"] is None
        assert restored.context["jianyingDraftRecords"][storyboard_id]["status"] == "succeeded"
        assert restored.last_phase == "jianying_draft_succeeded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_sql_stores_allow_replacing_only_expired_succeeded_jianying_draft(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow-jianying-expiration.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        first_store = SQLPixelFlowTaskStore(session_factory)
        second_store = SQLPixelFlowTaskStore(session_factory)
        storyboard_id = "storyboard-expiration"

        valid_conversation_id = "c-jianying-sql-valid"
        valid_record = {
            **_jianying_record("succeeded", "job-valid-old", storyboard_id),
            "expire_at": "2099-01-01T00:00:00Z",
        }
        await first_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=valid_conversation_id,
                user_id="u1",
                title="有效剪映草稿",
                last_phase="jianying_draft_succeeded",
                context={
                    "pendingJianyingDraftJob": None,
                    "pending_jianying_draft_job": None,
                    "jianyingDraftRecords": {storyboard_id: valid_record},
                    "jianying_draft_records": {storyboard_id: valid_record},
                },
            )
        )
        await second_store.patch_jianying_draft_conversation_context(
            valid_conversation_id,
            user_id="u1",
            expected_job_id="job-valid-new",
            pending_job=_jianying_pending("job-valid-new", valid_conversation_id, storyboard_id),
            records={},
            last_phase="jianying_draft_running",
        )
        valid_restored = await first_store.get_conversation(valid_conversation_id, user_id="u1")
        assert valid_restored is not None
        assert valid_restored.context["pendingJianyingDraftJob"] is None
        assert valid_restored.context["jianyingDraftRecords"][storyboard_id]["job_id"] == "job-valid-old"

        expired_conversation_id = "c-jianying-sql-expired"
        expired_record = {
            **_jianying_record("succeeded", "job-expired-old", storyboard_id),
            "expire_at": "2000-01-01T00:00:00Z",
        }
        await first_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=expired_conversation_id,
                user_id="u1",
                title="过期剪映草稿",
                last_phase="jianying_draft_succeeded",
                context={
                    "pendingJianyingDraftJob": None,
                    "pending_jianying_draft_job": None,
                    "jianyingDraftRecords": {storyboard_id: expired_record},
                    "jianying_draft_records": {storyboard_id: expired_record},
                },
            )
        )
        await second_store.patch_jianying_draft_conversation_context(
            expired_conversation_id,
            user_id="u1",
            expected_job_id="job-expired-new",
            pending_job=_jianying_pending("job-expired-new", expired_conversation_id, storyboard_id),
            records={},
            last_phase="jianying_draft_running",
        )
        expired_running = await first_store.get_conversation(expired_conversation_id, user_id="u1")
        assert expired_running is not None
        assert expired_running.context["pendingJianyingDraftJob"]["job_id"] == "job-expired-new"

        replacement = {
            **_jianying_record("succeeded", "job-expired-new", storyboard_id),
            "expire_at": "2099-01-01T00:00:00Z",
        }
        await first_store.patch_jianying_draft_conversation_context(
            expired_conversation_id,
            user_id="u1",
            expected_job_id="job-expired-new",
            pending_job=None,
            records={storyboard_id: replacement},
            last_phase="jianying_draft_succeeded",
        )
        expired_restored = await second_store.get_conversation(expired_conversation_id, user_id="u1")
        assert expired_restored is not None
        assert expired_restored.context["pendingJianyingDraftJob"] is None
        assert expired_restored.context["jianyingDraftRecords"][storyboard_id] == replacement
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_sql_stores_reject_old_success_after_retry_failure_clears_pending(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow-jianying-late-old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        first_store = SQLPixelFlowTaskStore(session_factory)
        second_store = SQLPixelFlowTaskStore(session_factory)
        conversation_id = "c-jianying-sql-late-old"
        storyboard_id = "storyboard-late-old"
        old_record = {
            **_jianying_record("succeeded", "job-old", storyboard_id),
            "expire_at": "2000-01-01T00:00:00Z",
        }
        await first_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=conversation_id,
                user_id="u1",
                title="剪映草稿旧任务迟到",
                last_phase="jianying_draft_succeeded",
                context={
                    "pendingJianyingDraftJob": None,
                    "pending_jianying_draft_job": None,
                    "jianyingDraftRecords": {storyboard_id: old_record},
                    "jianying_draft_records": {storyboard_id: old_record},
                },
            )
        )

        await second_store.patch_jianying_draft_conversation_context(
            conversation_id,
            user_id="u1",
            expected_job_id="job-new",
            pending_job=_jianying_pending("job-new", conversation_id, storyboard_id),
            records={},
            last_phase="jianying_draft_running",
        )
        new_terminal = _jianying_record("failed", "job-new", storyboard_id)
        await first_store.patch_jianying_draft_conversation_context(
            conversation_id,
            user_id="u1",
            expected_job_id="job-new",
            pending_job=None,
            records={storyboard_id: new_terminal},
            last_phase="jianying_draft_failed",
        )
        await second_store.patch_jianying_draft_conversation_context(
            conversation_id,
            user_id="u1",
            expected_job_id="job-old",
            pending_job=None,
            records={
                storyboard_id: {
                    **_jianying_record("succeeded", "job-old", storyboard_id),
                    "expire_at": "2099-01-01T00:00:00Z",
                }
            },
            last_phase="jianying_draft_succeeded",
        )

        restored = await first_store.get_conversation(conversation_id, user_id="u1")
        assert restored is not None
        assert restored.context["pendingJianyingDraftJob"] is None
        assert restored.context["jianyingDraftRecords"][storyboard_id] == new_terminal
        assert restored.context["jianying_draft_records"][storyboard_id] == new_terminal
        assert restored.last_phase == "jianying_draft_failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_store_drops_legacy_snapshot_messages_from_context():
    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="c-legacy",
            user_id="u1",
            title="旧快照",
            context={
                "taskId": "t1",
                "messages": [{"id": "old", "time": "10:06", "content": "旧的前端时间"}],
                "canvasOpen": True,
            },
        )
    )

    restored = await store.get_conversation("c-legacy", user_id="u1")
    assert restored is not None
    assert restored.context == {"taskId": "t1", "canvasOpen": True}

    updated = await store.update_conversation(
        "c-legacy",
        user_id="u1",
        context={
            "taskId": "t2",
            "messages": [{"id": "old-2", "time": "10:07", "content": "旧的前端时间 2"}],
            "briefConfirmed": False,
        },
    )
    assert updated is not None
    assert updated.context == {"taskId": "t2", "briefConfirmed": False}


@pytest.mark.asyncio
async def test_sql_conversation_store_drops_legacy_snapshot_messages_from_context(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pixelflow.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(async_sessionmaker(engine, expire_on_commit=False))

        created = await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="c-legacy-sql",
                user_id="u1",
                title="旧快照",
                context={
                    "taskId": "t1",
                    "messages": [{"id": "old", "time": "10:06", "content": "旧的前端时间"}],
                    "canvasOpen": True,
                },
            )
        )
        assert created.context == {"taskId": "t1", "canvasOpen": True}

        restored = await store.get_conversation("c-legacy-sql", user_id="u1")
        assert restored is not None
        assert restored.context == {"taskId": "t1", "canvasOpen": True}

        updated = await store.update_conversation(
            "c-legacy-sql",
            user_id="u1",
            context={
                "taskId": "t2",
                "messages": [{"id": "old-2", "time": "10:07", "content": "旧的前端时间 2"}],
                "briefConfirmed": False,
            },
        )
        assert updated is not None
        assert updated.context == {"taskId": "t2", "briefConfirmed": False}
    finally:
        await engine.dispose()


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
