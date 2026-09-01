"""验证真实 content-app 视频 Provider 的稳定 start/status 防腐合同。"""

import json

import httpx
import pytest

from pixelflow.capabilities.video_generation.providers.content_app import (
    ContentAppVideoGenerationProvider,
    ContentAppVideoProviderSettings,
)
from pixelflow.operations.jobs.providers import ProviderJobMappingError, ProviderJobOutcome


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


def test_video_provider_defaults_to_enabled_when_content_app_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置了 Content-App 时，视频 Provider 默认装配。"""

    monkeypatch.delenv("PIXELFLOW_M06_VIDEO_PROVIDER_ENABLED", raising=False)
    monkeypatch.setenv("BORGRISE_BASE_URL", "https://content.example/api")

    settings = ContentAppVideoProviderSettings.from_env()

    assert settings is not None
    assert settings.base_url == "https://content.example/api"


def test_video_provider_can_be_explicitly_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 false 是唯一关闭已配置视频 Provider 的默认开关。"""

    monkeypatch.setenv("PIXELFLOW_M06_VIDEO_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("BORGRISE_BASE_URL", "https://content.example/api")

    assert ContentAppVideoProviderSettings.from_env() is None


@pytest.mark.asyncio
async def test_start_and_status_reuse_browser_authorization_without_environment_secret() -> None:
    """start 与 status 复用同一浏览器 Authorization，配置文件不保存用户凭据。"""

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
    assert requests[0].headers["modelType"] == "seedance-2.0"
    assert requests[0].headers["billType"] == "3"
    assert requests[0].headers["duration"] == "5"
    assert json.loads(requests[0].headers["apiModelParamObj"]) == {"size": "720p"}
    assert requests[1].headers["Authorization"] == "Bearer user-start-token"
    body = json.loads(requests[0].content)
    assert body["videoCount"] == 1
    assert body["sound"] == "on"


@pytest.mark.asyncio
async def test_status_fails_closed_when_browser_authorization_lease_is_absent() -> None:
    """未由本进程创建的任务不能借用或猜测其他用户 Authorization。"""

    provider = _provider(lambda _request: httpx.Response(500))

    with pytest.raises(ProviderJobMappingError, match="provider_status_authorization_unavailable"):
        await provider.status("task-without-lease", user_id="user", conversation_id="conversation")

    await provider.aclose()


@pytest.mark.asyncio
async def test_402_and_expired_map_to_stable_outcomes() -> None:
    """402 与缺失任务不泄露下游正文，分别映射额度暂停和 expired。"""

    responses = iter((
        httpx.Response(402),
        httpx.Response(200, json={"success": True, "data": {"taskId": "task-expired", "status": "processing"}}),
        httpx.Response(404),
    ))
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
    paused = await provider.start(
        request,
        authorization="Bearer user-start-token",
        idempotency_key="operation:v1:test-402",
    )
    started = await provider.start(
        request,
        authorization="Bearer user-start-token",
        idempotency_key="operation:v1:test-expired",
    )
    expired = await provider.status("task-expired", user_id="user", conversation_id="conversation")
    await provider.aclose()

    assert paused.outcome is ProviderJobOutcome.PAUSED_QUOTA
    assert paused.provider_job_id is None
    assert started.outcome is ProviderJobOutcome.POLLING
    assert expired.outcome is ProviderJobOutcome.EXPIRED
    assert expired.provider_job_id == "task-expired"


@pytest.mark.asyncio
async def test_submitted_start_status_maps_to_polling() -> None:
    """content-app 已提交类状态不是终态，M06 必须持续轮询而非丢弃已扣费任务。"""

    provider = _provider(
        lambda _request: httpx.Response(
            200,
            json={"success": True, "data": {"taskId": "task-submitted", "status": "submitted"}},
        )
    )
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
        idempotency_key="operation:v1:test-submitted",
    )
    await provider.aclose()

    assert started.outcome is ProviderJobOutcome.POLLING
    assert started.provider_job_id == "task-submitted"


@pytest.mark.asyncio
async def test_extend_video_uses_legacy_content_app_contract() -> None:
    """延展沿用旧能力的 refVideoList 与 @文件名引用约束，但仍由新 M06 接管。"""

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"success": True, "data": {"taskId": "task-extend", "status": "submitted"}},
        )

    provider = _provider(handler)
    request = provider.prepare_operation_request(
        {
            "generation_mode": "extend_video",
            "prompt": "镜头继续向前推进",
            "model": "seedance-2.0",
            "ratio": "16:9",
            "size": "720p",
            "duration": 5,
            "sound": "on",
            "image_urls": [],
            "video_urls": ["https://cdn.example.invalid/previous.mp4"],
            "audio_urls": [],
        }
    )
    started = await provider.start(
        request,
        authorization="Bearer user-start-token",
        idempotency_key="operation:v1:test-extend",
    )
    await provider.aclose()

    assert started.outcome is ProviderJobOutcome.POLLING
    assert seen["path"] == "/api/video/extend-video"
    assert seen["body"] == {
        "prompt": "将@previous.mp4向后延展，延续内容为：镜头继续向前推进",
        "model": "seedance-2.0",
        "ratio": "16:9",
        "size": "720p",
        "duration": 5,
        "videoCount": 1,
        "sound": "on",
        "refVideoList": ["https://cdn.example.invalid/previous.mp4"],
    }


@pytest.mark.asyncio
async def test_unknown_start_status_with_task_id_maps_to_polling() -> None:
    """厂商新增启动枚举时，只要 taskId 已存在就交给严格 status 轮询恢复。"""

    provider = _provider(
        lambda _request: httpx.Response(
            200,
            json={"success": True, "data": {"taskId": "task-new-state", "status": "vendor_accepted"}},
        )
    )
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
        idempotency_key="operation:v1:test-new-state",
    )
    await provider.aclose()

    assert started.outcome is ProviderJobOutcome.POLLING
    assert started.provider_job_id == "task-new-state"
