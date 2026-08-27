"""验证真实 content-app 视频 Provider 的稳定 start/status 防腐合同。"""

import json

import httpx
import pytest

from pixelflow.capabilities.video_generation.providers.content_app import (
    ContentAppVideoGenerationProvider,
    ContentAppVideoProviderSettings,
)
from pixelflow.operations.jobs.providers import ProviderJobOutcome


def _provider(handler) -> ContentAppVideoGenerationProvider:
    return ContentAppVideoGenerationProvider(
        ContentAppVideoProviderSettings(
            base_url="https://content.example.invalid/api",
            provider_id="content-app-video",
            profile_version="profile-v1",
            connect_timeout_seconds=2,
            read_timeout_seconds=2,
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_start_and_status_use_separate_authorizations(monkeypatch) -> None:
    """start 使用用户授权；恢复 status 只使用环境注入的服务授权。"""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/video/text-to-video"):
            return httpx.Response(
                200,
                json={"success": True, "data": {"taskId": "task-1", "status": "processing"}},
            )
        assert request.url.path.endswith("/task/task-1/status")
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "taskId": "task-1",
                    "status": "succeeded",
                    "result": {"videoUrl": "https://cdn.example.invalid/video.mp4"},
                },
            },
        )

    monkeypatch.setenv("PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION", "Bearer service-status-token")
    provider = _provider(handler)
    request = provider.prepare_operation_request(
        {
            "generation_mode": "text_to_video",
            "prompt": "测试视频",
            "model": "seedance-2.0",
            "ratio": "16:9",
            "size": "720p",
            "duration": 5,
            "sound": "on",
            "image_urls": [],
            "video_urls": [],
            "audio_urls": [],
        }
    )
    started = await provider.start(
        request,
        authorization="Bearer user-start-token",
        idempotency_key="operation:v1:test",
    )
    completed = await provider.status(
        "task-1",
        user_id="user",
        conversation_id="conversation",
    )
    await provider.aclose()

    assert started.outcome is ProviderJobOutcome.POLLING
    assert completed.outcome is ProviderJobOutcome.SUCCEEDED
    assert completed.result["video_url"] == "https://cdn.example.invalid/video.mp4"
    assert requests[0].headers["Authorization"] == "Bearer user-start-token"
    assert requests[0].headers["Idempotency-Key"] == "operation:v1:test"
    assert requests[1].headers["Authorization"] == "Bearer service-status-token"
    body = json.loads(requests[0].content)
    assert body["videoCount"] == 1
    assert body["sound"] == "yes"


@pytest.mark.asyncio
async def test_402_and_expired_map_to_stable_outcomes(monkeypatch) -> None:
    """402 与缺失任务不泄露下游正文，分别映射额度暂停和 expired。"""

    responses = iter((httpx.Response(402), httpx.Response(404)))
    provider = _provider(lambda _request: next(responses))
    request = provider.prepare_operation_request(
        {
            "generation_mode": "text_to_video",
            "prompt": "测试视频",
            "model": "seedance-2.0",
            "ratio": "16:9",
            "size": "720p",
            "duration": 5,
            "sound": "on",
            "image_urls": [],
            "video_urls": [],
            "audio_urls": [],
        }
    )
    monkeypatch.setenv("PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION", "Bearer service-status-token")
    paused = await provider.start(
        request,
        authorization="Bearer user-start-token",
        idempotency_key="operation:v1:test-402",
    )
    expired = await provider.status("task-missing", user_id="user", conversation_id="conversation")
    await provider.aclose()

    assert paused.outcome is ProviderJobOutcome.PAUSED_QUOTA
    assert paused.provider_job_id is None
    assert expired.outcome is ProviderJobOutcome.EXPIRED
    assert expired.provider_job_id == "task-missing"
