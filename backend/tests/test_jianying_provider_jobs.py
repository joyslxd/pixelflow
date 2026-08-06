from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from pixelflow.agent_runtime.jobs import (
    ProviderJobAdapter,
    ProviderJobMappingError,
    ProviderJobOutcome,
)
from pixelflow.jianying_draft import (
    JianyingDraftRequest,
    JianyingDraftScene,
    compute_storyboard_version_id,
)
from pixelflow.jianying_draft.provider_jobs import (
    JianyingDraftProviderJobService,
)

USER_AUTHORIZATION = "Bearer transient-user-token"
SERVICE_AUTHORIZATION = "Bearer internal-service-token"


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("draft.json", "{}")
    return buffer.getvalue()


def _provider_request() -> dict:
    scenes = [
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=1,
            task_id="scene-task-1",
            video_url="https://cdn.example.invalid/scene-1.mp4",
        ),
        JianyingDraftScene(
            scene_id="scene-2",
            scene_index=2,
            task_id="scene-task-2",
            video_url="https://cdn.example.invalid/scene-2.mp4",
        ),
    ]
    request = JianyingDraftRequest(
        conversation_id="conversation-jianying-live",
        storyboard_version_id=compute_storyboard_version_id(scenes),
        scenes=scenes,
        project_name="商品视频",
    )
    return {"request": request.model_dump(mode="json"), "retry_failed": False}


@pytest.mark.asyncio
async def test_jianying_provider_resumes_and_uploads_for_target_user_idempotently() -> None:
    result_queries = 0
    upload_keys: list[str] = []
    requests: list[httpx.Request] = []
    archive = _zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal result_queries
        requests.append(request)
        if request.url.path == "/api/jianying/draft/tasks":
            assert request.headers["token"] == "provider-fixed-token"
            assert request.headers["Idempotency-Key"] == "operation:v1:jianying-live"
            assert json.loads(request.content)[0] == {
                "videoUrl": "https://cdn.example.invalid/scene-1.mp4",
                "videoOrder": 1,
            }
            return httpx.Response(200, json={"code": 200, "data": "jianying-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            result_queries += 1
            if result_queries == 1:
                return httpx.Response(200, json={"code": 20201, "data": None})
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": ["https://archive.example.invalid/draft.zip?ticket=short"],
                },
            )
        if request.url.path == "/draft.zip":
            return httpx.Response(
                200,
                content=archive,
                headers={"content-type": "application/zip"},
            )
        if request.url.path == "/api/internal/upload":
            assert request.headers["Authorization"] == SERVICE_AUTHORIZATION
            upload_keys.append(request.headers["Idempotency-Key"])
            body = request.content.decode("utf-8", errors="ignore")
            assert 'name="target_user_id"' in body
            assert "user-jianying-owner" in body
            assert "PixelFlow-剪映草稿-" in body
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "url": "https://tos.example.invalid/user-jianying-owner/draft.zip"
                    },
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ProviderJobAdapter(
            JianyingDraftProviderJobService(
                client=client,
                provider_base_url="https://jianying.example.invalid",
                provider_token="provider-fixed-token",
                content_app_base_url="https://content.example.invalid/api",
                service_authorization_provider=lambda: SERVICE_AUTHORIZATION,
            )
        )
        started = await adapter.start(
            _provider_request(),
            authorization=USER_AUTHORIZATION,
            idempotency_key="operation:v1:jianying-live",
        )
        polling = await adapter.status(
            "jianying-task-1",
            user_id="user-jianying-owner",
            conversation_id="conversation-jianying-live",
        )
        completed = await adapter.status(
            "jianying-task-1",
            user_id="user-jianying-owner",
            conversation_id="conversation-jianying-live",
        )
        replayed = await adapter.status(
            "jianying-task-1",
            user_id="user-jianying-owner",
            conversation_id="conversation-jianying-live",
        )

    assert started.outcome is ProviderJobOutcome.POLLING
    assert polling.outcome is ProviderJobOutcome.POLLING
    assert completed.outcome is ProviderJobOutcome.SUCCEEDED
    assert completed.result["download_url"].endswith("draft.zip")
    assert replayed.result == completed.result
    assert len(upload_keys) == 2
    assert len(set(upload_keys)) == 1
    assert USER_AUTHORIZATION not in completed.model_dump_json()
    assert SERVICE_AUTHORIZATION not in completed.model_dump_json()
    assert all("target_user_id" not in str(request.headers) for request in requests)


@pytest.mark.asyncio
async def test_jianying_status_requires_repository_owner_scope() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("缺少用户作用域时不得访问Provider")
        )
    ) as client:
        adapter = ProviderJobAdapter(
            JianyingDraftProviderJobService(
                client=client,
                provider_base_url="https://jianying.example.invalid",
                provider_token="provider-fixed-token",
                content_app_base_url="https://content.example.invalid/api",
                service_authorization_provider=lambda: SERVICE_AUTHORIZATION,
            )
        )
        with pytest.raises(
            ProviderJobMappingError,
            match="provider_status_scope_required",
        ):
            await adapter.status("jianying-task-1")
