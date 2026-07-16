from __future__ import annotations

import asyncio
import time
from uuid import UUID

from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from pixelflow.tasks import MemoryPixelFlowTaskStore
from tests._router_auth_helpers import make_authed_test_app


def test_pixelflow_conversations_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_conversations

    paths = {route.path for route in pixelflow_conversations.router.routes}
    assert pixelflow_conversations.router.prefix == "/agent/conversations"
    assert "/agent/conversations" in paths
    assert "/agent/conversations/{conversation_id}" in paths
    assert "/agent/conversations/{conversation_id}/jianying-draft-context" in paths
    assert "/agent/conversations/{conversation_id}/messages" in paths
    assert "/agent/conversations/{conversation_id}/messages/start" in paths
    assert "/agent/conversations/{conversation_id}/messages/jobs/{job_id}" in paths
    assert "/agent/conversations/{conversation_id}/resume" in paths


def test_jianying_draft_context_patch_merges_fields_and_preserves_null_semantics():
    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = MemoryPixelFlowTaskStore()
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        created = client.post(
            "/agent/conversations",
            json={
                "title": "剪映草稿",
                "context": {
                    "brand_name": "A 品牌",
                    "concurrent_server_field": "保留",
                    "jianying_draft_records": {"storyboard-0": {"status": "succeeded"}},
                },
            },
        ).json()
        conversation_id = created["conversation_id"]
        pending_job = {
            "job_id": "job-1",
            "conversation_id": conversation_id,
            "storyboard_version_id": "storyboard-1",
        }

        running = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "jianying_draft_running",
                "expected_job_id": "job-1",
                "pendingJianyingDraftJob": pending_job,
                "jianyingDraftRecords": {
                    "storyboard-1": {
                        "status": "running",
                        "job_id": "job-1",
                        "storyboard_version_id": "storyboard-1",
                    }
                },
            },
        )
        assert running.status_code == 200
        running_context = running.json()["context"]
        assert running_context["brand_name"] == "A 品牌"
        assert running_context["concurrent_server_field"] == "保留"
        assert running_context["pendingJianyingDraftJob"] == pending_job
        assert running_context["pending_jianying_draft_job"] == pending_job
        assert set(running_context["jianyingDraftRecords"]) == {"storyboard-0", "storyboard-1"}
        assert running_context["jianying_draft_records"] == running_context["jianyingDraftRecords"]

        stale_put = client.put(
            f"/agent/conversations/{conversation_id}",
            json={
                "context": {
                    "brand_name": "A 品牌",
                    "concurrent_server_field": "保留",
                    "generic_concurrent_field": "普通更新已保存",
                    "pendingJianyingDraftJob": None,
                    "pending_jianying_draft_job": None,
                    "jianyingDraftRecords": {},
                    "jianying_draft_records": {},
                }
            },
        )
        assert stale_put.status_code == 200
        stale_put_context = stale_put.json()["context"]
        assert stale_put_context["generic_concurrent_field"] == "普通更新已保存"
        assert stale_put_context["pendingJianyingDraftJob"] == pending_job
        assert set(stale_put_context["jianyingDraftRecords"]) == {"storyboard-0", "storyboard-1"}

        expired = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "jianying_draft_job_expired",
                "expected_job_id": "job-1",
                "pending_jianying_draft_job": None,
                "jianying_draft_records": {
                    "storyboard-1": {
                        "status": "failed",
                        "job_id": "job-1",
                        "storyboard_version_id": "storyboard-1",
                    }
                },
                "jianying_draft_job_resume_error": "任务已过期",
            },
        )
        assert expired.status_code == 200
        expired_context = expired.json()["context"]
        assert expired_context["pendingJianyingDraftJob"] is None
        assert expired_context["pending_jianying_draft_job"] is None
        assert expired_context["jianyingDraftRecords"]["storyboard-1"]["status"] == "failed"
        assert expired_context["jianying_draft_job_resume_error"] == "任务已过期"

        omitted_error = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "jianying_draft_failed",
                "expected_job_id": "job-1",
                "pendingJianyingDraftJob": None,
                "jianyingDraftRecords": {},
            },
        )
        assert omitted_error.status_code == 200
        assert omitted_error.json()["context"]["jianying_draft_job_resume_error"] == "任务已过期"

        retry_pending_job = {
            "job_id": "job-2",
            "conversation_id": conversation_id,
            "storyboard_version_id": "storyboard-1",
        }
        cleared_error = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "jianying_draft_running",
                "expected_job_id": "job-2",
                "pendingJianyingDraftJob": retry_pending_job,
                "jianyingDraftRecords": {},
                "jianying_draft_job_resume_error": None,
            },
        )
        assert cleared_error.status_code == 200
        assert cleared_error.json()["context"]["jianying_draft_job_resume_error"] is None

        extra_field = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "forbidden",
                "expected_job_id": "job-2",
                "pendingJianyingDraftJob": None,
                "jianyingDraftRecords": {},
                "brand_name": "禁止覆盖",
            },
        )
        assert extra_field.status_code == 422


def test_jianying_draft_context_patch_rejects_stale_job_state_and_requires_matching_condition():
    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = MemoryPixelFlowTaskStore()
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        created = client.post("/agent/conversations", json={"title": "剪映草稿单调状态"}).json()
        conversation_id = created["conversation_id"]
        storyboard_id = "storyboard-same"
        pending = {
            "job_id": "job-1",
            "conversation_id": conversation_id,
            "storyboard_version_id": storyboard_id,
        }
        running_body = {
            "last_phase": "jianying_draft_running",
            "expected_job_id": "job-1",
            "pendingJianyingDraftJob": pending,
            "jianyingDraftRecords": {},
        }
        running = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json=running_body,
        )
        assert running.status_code == 200

        succeeded = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "jianying_draft_succeeded",
                "expected_job_id": "job-1",
                "pendingJianyingDraftJob": None,
                "jianyingDraftRecords": {
                    storyboard_id: {
                        "status": "succeeded",
                        "job_id": "job-1",
                        "storyboard_version_id": storyboard_id,
                    }
                },
            },
        )
        assert succeeded.status_code == 200

        stale_failed = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "jianying_draft_failed",
                "expected_job_id": "job-1",
                "pendingJianyingDraftJob": None,
                "jianyingDraftRecords": {
                    storyboard_id: {
                        "status": "failed",
                        "job_id": "job-1",
                        "storyboard_version_id": storyboard_id,
                    }
                },
            },
        )
        assert stale_failed.status_code == 200
        stale_pending = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json=running_body,
        )
        assert stale_pending.status_code == 200
        final = stale_pending.json()
        assert final["context"]["pendingJianyingDraftJob"] is None
        assert final["context"]["jianyingDraftRecords"][storyboard_id]["status"] == "succeeded"
        assert final["last_phase"] == "jianying_draft_succeeded"

        missing_condition = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={key: value for key, value in running_body.items() if key != "expected_job_id"},
        )
        assert missing_condition.status_code == 422
        mismatched_pending = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={**running_body, "expected_job_id": "job-other"},
        )
        assert mismatched_pending.status_code == 422
        mismatched_record = client.patch(
            f"/agent/conversations/{conversation_id}/jianying-draft-context",
            json={
                "last_phase": "jianying_draft_failed",
                "expected_job_id": "job-other",
                "pendingJianyingDraftJob": None,
                "jianyingDraftRecords": {
                    storyboard_id: {
                        "status": "failed",
                        "job_id": "job-1",
                        "storyboard_version_id": storyboard_id,
                    }
                },
            },
        )
        assert mismatched_record.status_code == 422


def test_jianying_draft_context_patch_checks_conversation_owner():
    from app.gateway.routers import pixelflow_conversations

    store = MemoryPixelFlowTaskStore()
    asyncio.run(
        store.create_conversation(
            pixelflow_conversations.PixelFlowConversationRecord(
                conversation_id="other-user-conversation",
                user_id="other-user",
                title="其他用户",
            )
        )
    )
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = store
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        response = client.patch(
            "/agent/conversations/other-user-conversation/jianying-draft-context",
            json={
                "last_phase": "forbidden",
                "expected_job_id": "forbidden-job",
                "pendingJianyingDraftJob": None,
                "jianyingDraftRecords": {},
            },
        )

    assert response.status_code == 404


def _stable_user() -> User:
    return User(
        email="pixelflow-router@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000123"),
    )


def test_conversation_router_creates_lists_and_resumes_history():
    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = MemoryPixelFlowTaskStore()
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        created = client.post("/agent/conversations", json={"title": "口红短视频"}).json()
        conversation_id = created["conversation_id"]

        message = client.post(
            f"/agent/conversations/{conversation_id}/messages",
            json={"role": "user", "content": "生成一条口红短视频", "payload": {"time": "10:00"}},
        )
        assert message.status_code == 200
        assert message.json()["content"] == "生成一条口红短视频"

        page = client.get("/agent/conversations?page_size=5").json()
        assert page["next_cursor"] is None
        assert page["items"][0]["conversation_id"] == conversation_id
        assert page["items"][0]["title"] == "口红短视频"

        detail = client.get(f"/agent/conversations/{conversation_id}").json()
        assert detail["conversation"]["conversation_id"] == conversation_id
        assert detail["messages"][0]["role"] == "user"
        assert detail["messages"][0]["payload"] == {"time": "10:00"}

        resumed = client.post(f"/agent/conversations/{conversation_id}/resume").json()
        assert resumed["conversation"]["conversation_id"] == conversation_id
        assert resumed["messages"][0]["content"] == "生成一条口红短视频"


def test_conversation_message_job_returns_pollable_saved_message():
    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = MemoryPixelFlowTaskStore()
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        created = client.post("/agent/conversations", json={"title": "图片需求"}).json()
        conversation_id = created["conversation_id"]

        started = client.post(
            f"/agent/conversations/{conversation_id}/messages/start",
            json={
                "role": "user",
                "content": "帮我生成书包宣传图",
                "payload": {"client_message_id": "client-1", "materials": [{"url": "https://x/bag.png"}]},
            },
        )
        assert started.status_code == 200
        job_id = started.json()["job_id"]

        status = None
        for _ in range(20):
            status = client.get(f"/agent/conversations/{conversation_id}/messages/jobs/{job_id}")
            assert status.status_code == 200
            if status.json()["status"] == "completed":
                break
            time.sleep(0.01)

        assert status is not None
        data = status.json()
        assert data["status"] == "completed"
        assert data["result"]["content"] == "帮我生成书包宣传图"
        assert data["result"]["payload"]["client_message_id"] == "client-1"

        detail = client.get(f"/agent/conversations/{conversation_id}").json()
        assert [message["content"] for message in detail["messages"]] == ["帮我生成书包宣传图"]


def test_conversation_message_retry_is_idempotent_per_conversation():
    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = MemoryPixelFlowTaskStore()
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        first_conversation = client.post("/agent/conversations", json={"title": "第一条对话"}).json()
        second_conversation = client.post("/agent/conversations", json={"title": "第二条对话"}).json()
        payload = {
            "role": "assistant",
            "content": "plan.md v1",
            "payload": {"client_message_id": "plan-card-v1", "artifact": {"type": "plan"}},
        }

        first = client.post(
            f"/agent/conversations/{first_conversation['conversation_id']}/messages",
            json=payload,
        )
        retried = client.post(
            f"/agent/conversations/{first_conversation['conversation_id']}/messages",
            json={**payload, "content": "这次重试不应覆盖首次内容"},
        )
        other_conversation = client.post(
            f"/agent/conversations/{second_conversation['conversation_id']}/messages",
            json=payload,
        )

        assert first.status_code == retried.status_code == other_conversation.status_code == 200
        assert retried.json()["message_id"] == first.json()["message_id"]
        assert retried.json()["content"] == "plan.md v1"
        assert other_conversation.json()["message_id"] != first.json()["message_id"]
        first_detail = client.get(
            f"/agent/conversations/{first_conversation['conversation_id']}"
        ).json()
        second_detail = client.get(
            f"/agent/conversations/{second_conversation['conversation_id']}"
        ).json()
        assert [message["content"] for message in first_detail["messages"]] == ["plan.md v1"]
        assert [message["content"] for message in second_detail["messages"]] == ["plan.md v1"]


def test_conversation_message_job_retry_does_not_duplicate_plan_message():
    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = MemoryPixelFlowTaskStore()
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        conversation_id = client.post("/agent/conversations", json={"title": "Plan 重试"}).json()[
            "conversation_id"
        ]
        request = {
            "role": "assistant",
            "content": "plan.md v1",
            "payload": {"client_message_id": "plan-job-v1", "artifact": {"type": "plan"}},
        }

        started_jobs = [
            client.post(f"/agent/conversations/{conversation_id}/messages/start", json=request).json()["job_id"]
            for _ in range(2)
        ]
        results = []
        for job_id in started_jobs:
            for _ in range(30):
                status = client.get(f"/agent/conversations/{conversation_id}/messages/jobs/{job_id}")
                assert status.status_code == 200
                if status.json()["status"] == "completed":
                    results.append(status.json()["result"])
                    break
                time.sleep(0.01)

        assert len(results) == 2
        assert results[0]["message_id"] == results[1]["message_id"]
        detail = client.get(f"/agent/conversations/{conversation_id}").json()
        assert [message["content"] for message in detail["messages"]] == ["plan.md v1"]
