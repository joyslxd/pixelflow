from __future__ import annotations

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
    assert "/agent/conversations/{conversation_id}/messages" in paths
    assert "/agent/conversations/{conversation_id}/messages/start" in paths
    assert "/agent/conversations/{conversation_id}/messages/jobs/{job_id}" in paths
    assert "/agent/conversations/{conversation_id}/resume" in paths


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
