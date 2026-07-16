from __future__ import annotations

import asyncio
import time
from uuid import UUID

from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from pixelflow.jianying_draft import (
    JianyingDraftCapability,
    JianyingDraftRequest,
    JianyingDraftResult,
    JianyingDraftScene,
    JianyingDraftService,
    JianyingDraftStatus,
    UnavailableJianyingDraftSkill,
    compute_storyboard_version_id,
)
from pixelflow.tasks import MemoryPixelFlowTaskStore, PixelFlowConversationRecord
from tests._router_auth_helpers import make_authed_test_app


def _stable_user() -> User:
    return User(
        email="pixelflow-jianying-draft@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000904"),
    )


def _other_user() -> User:
    return User(
        email="pixelflow-jianying-draft-other@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000905"),
    )


def _create_conversation(
    store: MemoryPixelFlowTaskStore,
    *,
    conversation_id: str,
    user: User,
) -> None:
    asyncio.run(
        store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=conversation_id,
                user_id=str(user.id),
                title="剪映草稿测试对话",
            )
        )
    )


def _make_router_app(
    *,
    user_factory=lambda: _stable_user(),
    service: JianyingDraftService | object | None = None,
):
    from app.gateway.routers import pixelflow_jianying_draft

    app = make_authed_test_app(user_factory=user_factory)
    app.state.pixelflow_task_store = MemoryPixelFlowTaskStore()
    if service is not None:
        app.state.pixelflow_jianying_draft_service = service
    app.include_router(pixelflow_jianying_draft.router)
    return app


def _payload() -> dict[str, object]:
    scenes = [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "video_url": "https://cdn.example.com/scene-1.mp4",
            "task_id": "video-task-1",
        }
    ]
    storyboard_version_id = compute_storyboard_version_id([JianyingDraftScene(**scene) for scene in scenes])
    return {
        "conversation_id": "conversation-1",
        "storyboard_version_id": storyboard_version_id,
        "scenes": scenes,
        "video_task_id": "video-task-1",
    }


def test_jianying_draft_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_jianying_draft

    paths = {route.path for route in pixelflow_jianying_draft.router.routes}
    assert pixelflow_jianying_draft.router.prefix == "/agent/flows/video/jianying-draft"
    assert "/agent/flows/video/jianying-draft/capability" in paths
    assert "/agent/flows/video/jianying-draft/start" in paths
    assert "/agent/flows/video/jianying-draft/jobs/{job_id}" in paths


def test_jianying_draft_capability_reports_unavailable_by_default():
    app = _make_router_app(service=JianyingDraftService(skill=UnavailableJianyingDraftSkill()))

    with TestClient(app) as client:
        response = client.get("/agent/flows/video/jianying-draft/capability")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "reason": "剪映草稿服务待接入",
        "poll_interval_seconds": 2.0,
    }


def test_jianying_draft_start_does_not_create_placeholder_job():
    service = JianyingDraftService(skill=UnavailableJianyingDraftSkill())
    app = _make_router_app(service=service)
    _create_conversation(app.state.pixelflow_task_store, conversation_id="conversation-1", user=_stable_user())

    with TestClient(app) as client:
        response = client.post("/agent/flows/video/jianying-draft/start", json=_payload())

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "not_configured"
    assert service.job_count == 0


def test_jianying_draft_start_passes_explicit_retry_failed_to_service():
    class CapturingService:
        def __init__(self) -> None:
            self.retry_flags: list[bool] = []

        async def start(
            self,
            request: JianyingDraftRequest,
            *,
            retry_failed: bool = False,
        ) -> JianyingDraftResult:
            self.retry_flags.append(retry_failed)
            return JianyingDraftResult(
                status=JianyingDraftStatus.FAILED,
                job_id="failed-job",
                conversation_id=request.conversation_id,
                storyboard_version_id=request.storyboard_version_id,
            )

    service = CapturingService()
    app = _make_router_app(service=service)
    _create_conversation(app.state.pixelflow_task_store, conversation_id="conversation-1", user=_stable_user())

    with TestClient(app) as client:
        ordinary = client.post("/agent/flows/video/jianying-draft/start", json=_payload())
        retry = client.post(
            "/agent/flows/video/jianying-draft/start",
            json={**_payload(), "retry_failed": True},
        )

    assert ordinary.status_code == 200
    assert retry.status_code == 200
    assert service.retry_flags == [False, True]


def test_jianying_draft_unknown_job_returns_404():
    app = _make_router_app(service=JianyingDraftService(skill=UnavailableJianyingDraftSkill()))

    with TestClient(app) as client:
        response = client.get("/agent/flows/video/jianying-draft/jobs/missing")

    assert response.status_code == 404


def test_jianying_draft_terminal_job_records_one_safe_powermem_experience(monkeypatch):
    from app.gateway.routers import pixelflow_jianying_draft

    class CompletedSkill:
        async def capability(self) -> JianyingDraftCapability:
            return JianyingDraftCapability(available=True)

        async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
            return JianyingDraftResult(
                status=JianyingDraftStatus.SUCCEEDED,
                download_url="https://cdn.example.com/draft.zip",
                message="provider response token=secret-token",
            )

    service = JianyingDraftService(skill=CompletedSkill())
    records: list[dict[str, object]] = []
    monkeypatch.setattr(
        pixelflow_jianying_draft,
        "record_power_mem_background",
        lambda *args, **kwargs: records.append(kwargs),
    )

    app = _make_router_app(service=service)
    _create_conversation(app.state.pixelflow_task_store, conversation_id="conversation-1", user=_stable_user())

    with TestClient(app) as client:
        start_response = client.post("/agent/flows/video/jianying-draft/start", json=_payload())
        assert start_response.status_code == 200
        job_id = start_response.json()["job_id"]

        status_response = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/video/jianying-draft/jobs/{job_id}")
            assert status_response.status_code == 200
            if status_response.json()["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert status_response is not None
        assert status_response.json()["status"] == "succeeded"

        repeated_response = client.get(f"/agent/flows/video/jianying-draft/jobs/{job_id}")

    assert repeated_response.status_code == 200
    assert len(records) == 1
    record = records[0]
    assert record["category"] == "experience"
    assert record["source_agent"] == "jianying_draft_agent"
    assert record["infer"] is False
    assert record["metadata"]["source"] == "video_jianying_draft_job"
    assert "secret-token" not in str(record["content"])
    assert "provider response" not in str(record["content"])


def test_jianying_draft_start_hides_foreign_conversation_from_other_user():
    active_user = [_other_user()]
    service = JianyingDraftService(skill=UnavailableJianyingDraftSkill())
    app = _make_router_app(user_factory=lambda: active_user[0], service=service)
    _create_conversation(app.state.pixelflow_task_store, conversation_id="conversation-1", user=_stable_user())

    with TestClient(app) as client:
        response = client.post("/agent/flows/video/jianying-draft/start", json=_payload())

    assert response.status_code == 404
    assert service.job_count == 0


def test_jianying_draft_job_hides_foreign_terminal_job_without_powermem_record(monkeypatch):
    from app.gateway.routers import pixelflow_jianying_draft

    class TerminalJobService:
        async def get_job(self, job_id: str) -> JianyingDraftResult | None:
            return JianyingDraftResult(
                status=JianyingDraftStatus.SUCCEEDED,
                job_id=job_id,
                conversation_id="conversation-1",
                storyboard_version_id="storyboard-1",
            )

        async def claim_terminal_experience(self, job_id: str) -> bool:
            raise AssertionError("foreign job must not claim PowerMem experience")

    records: list[dict[str, object]] = []
    monkeypatch.setattr(
        pixelflow_jianying_draft,
        "record_power_mem_background",
        lambda *args, **kwargs: records.append(kwargs),
    )
    app = _make_router_app(user_factory=_other_user, service=TerminalJobService())
    _create_conversation(app.state.pixelflow_task_store, conversation_id="conversation-1", user=_stable_user())

    with TestClient(app) as client:
        response = client.get("/agent/flows/video/jianying-draft/jobs/foreign-job")

    assert response.status_code == 404
    assert records == []


def test_jianying_draft_router_requires_app_scoped_service():
    app = _make_router_app()

    with TestClient(app) as client:
        capability = client.get("/agent/flows/video/jianying-draft/capability")
        start = client.post("/agent/flows/video/jianying-draft/start", json=_payload())
        job = client.get("/agent/flows/video/jianying-draft/jobs/missing")

    assert capability.status_code == 503
    assert start.status_code == 503
    assert job.status_code == 503


def test_jianying_draft_router_has_no_module_scoped_service_or_dedupe_state():
    from app.gateway.routers import pixelflow_jianying_draft

    assert not hasattr(pixelflow_jianying_draft, "_JIANYING_DRAFT_SKILL")
    assert not hasattr(pixelflow_jianying_draft, "_JIANYING_DRAFT_SERVICE")
    assert not hasattr(pixelflow_jianying_draft, "_TERMINAL_EXPERIENCE_JOB_IDS")


def test_gateway_app_registers_jianying_draft_router():
    from app.gateway.app import create_app

    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/agent/flows/video/jianying-draft/capability" in paths
