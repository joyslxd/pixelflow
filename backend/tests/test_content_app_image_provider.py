"""Content-App 图片 Provider 响应映射回归测试。"""

from __future__ import annotations

import json

import httpx
import pytest

from pixelflow.capabilities.image_generation.providers.content_app import (
    ContentAppImageGenerationAdapter,
    ContentAppImageProviderSettings,
)
from pixelflow.generation_jobs.providers import ProviderJobMappingError, ProviderJobOutcome


def _settings() -> ContentAppImageProviderSettings:
    return ContentAppImageProviderSettings(
        base_url="https://content.example",
        provider_id="content-app-image",
        profile_version="v1",
    )


def _request() -> dict[str, object]:
    return {
        "generation_mode": "text_to_image",
        "prompt": "厨房",
        "model": "seeddream-5.0",
        "size": "1080p",
        "ratio": "9:16",
    }


@pytest.mark.asyncio
async def test_start_maps_nested_content_app_task_id_to_polling() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "data": {"task": {"id": "image-task-1", "status": "processing"}},
            },
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        snapshot = await adapter.start(_request(), authorization="Bearer test", idempotency_key="idem-1")

    assert snapshot.provider_job_id == "image-task-1"
    assert snapshot.outcome is ProviderJobOutcome.POLLING


@pytest.mark.asyncio
async def test_start_maps_portrait_ratio_to_provider_pixel_dimensions() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"success": True, "data": {"task": {"id": "image-task-1", "status": "processing"}}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        await adapter.start(_request(), authorization="Bearer test", idempotency_key="idem-ratio")

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["width"] == 1440
    assert body["height"] == 2560


@pytest.mark.asyncio
async def test_start_preserves_provider_mapping_reason_without_raw_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"success": True, "data": {"status": "processing"}},
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        with pytest.raises(ProviderJobMappingError) as error:
            await adapter.start(_request(), authorization="Bearer test", idempotency_key="idem-2")

    assert error.value.reason_code == "image_provider_job_id_missing"


@pytest.mark.asyncio
async def test_start_reports_safe_diagnostics_for_non_json_response() -> None:
    raw_response = "provider internal detail with prompt kitchen"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=raw_response.encode(),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        with pytest.raises(ProviderJobMappingError) as error:
            await adapter.start(_request(), authorization="Bearer test", idempotency_key="idem-3")

    assert error.value.reason_code == "provider_response_not_json"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.status_code == 200
    assert error.value.diagnostics.content_type == "text/plain"
    assert error.value.diagnostics.response_length == len(raw_response.encode())
    assert error.value.diagnostics.field_paths == ()
    assert raw_response not in str(error.value)


@pytest.mark.asyncio
async def test_start_reports_json_field_paths_without_response_values() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "message": "private prompt should not be logged",
                "data": {"task": {"status": "processing"}},
            },
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        with pytest.raises(ProviderJobMappingError) as error:
            await adapter.start(_request(), authorization="Bearer test", idempotency_key="idem-4")

    diagnostics = error.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.status_code == 200
    assert diagnostics.content_type == "application/json"
    assert "data.task.status" in diagnostics.field_paths
    assert "private prompt should not be logged" not in diagnostics.field_paths
    assert "processing" not in diagnostics.field_paths


def test_image_provider_normalizes_borgrise_site_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIXELFLOW_M06_IMAGE_PROVIDER_ENABLED", raising=False)
    monkeypatch.setenv("BORGRISE_BASE_URL", "https://test-video.borgrise.com/ /api")

    settings = ContentAppImageProviderSettings.from_env()

    assert settings is not None
    assert settings.base_url == "https://test-video.borgrise.com/api"


@pytest.mark.asyncio
async def test_status_sends_authorization_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={"success": True, "data": {"task": {"id": "image-task-1", "status": "processing"}}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        snapshot = await adapter.status(
            "image-task-1",
            user_id="user-1",
            conversation_id="conversation-1",
            authorization="Bearer poll-token",
        )

    assert snapshot.outcome is ProviderJobOutcome.POLLING
    assert seen["authorization"] == "Bearer poll-token"


@pytest.mark.asyncio
async def test_status_falls_back_to_browser_authorization_store() -> None:
    from pixelflow.platform.content_app_authorization import TransientContentAppAuthorizationStore

    seen: dict[str, str] = {}
    store = TransientContentAppAuthorizationStore()
    await store.put_user(user_id="user-1", authorization="Bearer browser-token")

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={"success": True, "data": {"task": {"id": "image-task-1", "status": "processing"}}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ContentAppImageGenerationAdapter(
            _settings(),
            client=client,
            authorization_store=store,
        )
        snapshot = await adapter.status(
            "image-task-1",
            user_id="user-1",
            conversation_id="conversation-1",
        )

    assert snapshot.outcome is ProviderJobOutcome.POLLING
    assert seen["authorization"] == "Bearer browser-token"


@pytest.mark.asyncio
async def test_status_maps_nested_result_data_url_to_success() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": "image-task-1",
                    "status": "success",
                    "result": {"data": "https://cdn.example/hero.png?X-Tos-Signature=abc"},
                },
            },
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        snapshot = await adapter.status(
            "image-task-1",
            user_id="user-1",
            conversation_id="conversation-1",
            authorization="Bearer poll-token",
        )

    assert snapshot.outcome is ProviderJobOutcome.SUCCEEDED
    assert snapshot.result == {
        "image_url": "https://cdn.example/hero.png",
        "artifact_ref": "artifact:image:hero.png",
    }


@pytest.mark.asyncio
async def test_status_maps_result_data_object_and_list_urls() -> None:
    payloads = (
        {"url": "https://cdn.example/from-object.png"},
        [{"imageUrl": "https://cdn.example/from-list.png"}],
        {"images": ["https://cdn.example/from-images.png"]},
    )
    expected = (
        "https://cdn.example/from-object.png",
        "https://cdn.example/from-list.png",
        "https://cdn.example/from-images.png",
    )
    for result_data, image_url in zip(payloads, expected, strict=True):
        transport = httpx.MockTransport(
            lambda request, nested=result_data: httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"id": "image-task-1", "status": "success", "result": {"data": nested}},
                },
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
            snapshot = await adapter.status(
                "image-task-1",
                user_id="user-1",
                conversation_id="conversation-1",
                authorization="Bearer poll-token",
            )
        assert snapshot.outcome is ProviderJobOutcome.SUCCEEDED
        assert snapshot.result["image_url"] == image_url


@pytest.mark.asyncio
async def test_status_success_without_image_url_is_mapping_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": "image-task-1",
                    "status": "success",
                    "result": {"data": {"message": "done"}},
                },
            },
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = ContentAppImageGenerationAdapter(_settings(), client=client)
        with pytest.raises(ProviderJobMappingError) as error:
            await adapter.status(
                "image-task-1",
                user_id="user-1",
                conversation_id="conversation-1",
                authorization="Bearer poll-token",
            )

    assert error.value.reason_code == "image_result_url_missing"
    assert error.value.diagnostics is not None
    assert "data.result.data" in error.value.diagnostics.field_paths
    assert "done" not in error.value.diagnostics.field_paths
