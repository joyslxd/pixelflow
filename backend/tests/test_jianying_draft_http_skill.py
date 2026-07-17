from __future__ import annotations

import asyncio
import io
import json
import threading
import zipfile
from pathlib import Path

import httpx
import pytest

from app.gateway.content_app_auth_context import (
    require_current_authorization,
    reset_current_content_app_auth,
    set_current_content_app_auth,
)
from pixelflow.jianying_draft import (
    HttpJianyingDraftSkill,
    JianyingDraftRequest,
    JianyingDraftScene,
    JianyingDraftService,
    JianyingDraftStatus,
    compute_storyboard_version_id,
)


def _request() -> JianyingDraftRequest:
    scenes = [
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=10,
            task_id="video-task-1",
            video_url="https://video.example.com/one.mp4",
        ),
        JianyingDraftScene(
            scene_id="scene-2",
            scene_index=20,
            task_id="video-task-2",
            video_url="https://video.example.com/two.mp4",
        ),
    ]
    return JianyingDraftRequest(
        conversation_id="conversation-1",
        storyboard_version_id=compute_storyboard_version_id(scenes),
        scenes=scenes,
        project_name="夏季新品",
    )


async def _wait_for_terminal(service: JianyingDraftService, job_id: str):
    for _ in range(100):
        result = await service.get_job(job_id)
        assert result is not None
        if result.status not in {JianyingDraftStatus.QUEUED, JianyingDraftStatus.RUNNING}:
            return result
        await asyncio.sleep(0.001)
    raise AssertionError("job did not complete")


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_http_skill_creates_polls_downloads_and_uploads_provider_zip(tmp_path: Path):
    create_bodies: list[object] = []
    query_count = 0
    provider_zip = _zip_bytes(
        {
            "draft-a.json": b'{"draft":"a"}',
            "draft-b.json": b'{"draft":"b"}',
        }
    )
    uploaded_archives: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_count
        if request.url.path == "/api/jianying/draft/tasks":
            assert request.headers["token"] == "provider-token"
            create_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 200, "message": "success", "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            assert request.headers["token"] == "provider-token"
            assert json.loads(request.content) == {"taskId": "provider-task-1"}
            query_count += 1
            if query_count == 1:
                return httpx.Response(200, json={"code": 20202, "message": "任务处理中", "data": []})
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": ["https://cdn.example.com/draft.zip"],
                },
            )
        if request.url.path == "/draft.zip":
            return httpx.Response(200, content=provider_zip, headers={"content-type": "application/zip"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def uploader(path: str) -> dict[str, object]:
        archive_path = Path(path)
        assert archive_path.parent != tmp_path
        uploaded_archives.append(archive_path.read_bytes())
        return {"success": True, "url": "https://tos.example.com/jianying/draft.zip"}

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        max_retries=2,
        http_client=client,
        uploader=uploader,
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.SUCCEEDED
    assert result.provider_task_id == "provider-task-1"
    assert str(result.download_url) == "https://tos.example.com/jianying/draft.zip"
    assert result.file_name == "夏季新品-剪映草稿.zip"
    assert create_bodies == [
        [
            {"videoUrl": "https://video.example.com/one.mp4", "videoOrder": 1},
            {"videoUrl": "https://video.example.com/two.mp4", "videoOrder": 2},
        ]
    ]
    assert query_count == 2
    assert uploaded_archives == [provider_zip]
    await skill.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_skill_rejects_multiple_legacy_source_urls():
    uploader_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/jianying/draft/tasks":
            return httpx.Response(200, json={"code": 200, "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": ["https://cdn.example.com/draft-a.json", "https://cdn.example.com/draft-b.json"],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    def uploader(_: str) -> dict[str, object]:
        nonlocal uploader_called
        uploader_called = True
        return {"success": True, "url": "https://tos.example.com/unused.zip"}

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        http_client=client,
        uploader=uploader,
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.FAILED
    assert uploader_called is False
    await skill.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_skill_does_not_retry_non_success_business_code():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"code": 40001, "message": "视频集合不能为空", "data": None})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        max_retries=2,
        http_client=client,
        uploader=lambda _: {"success": True, "url": "https://tos.example.com/unused.zip"},
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.FAILED
    assert result.message == "第三方剪映草稿任务创建失败：视频集合不能为空"
    assert result.provider_task_id is None
    assert call_count == 1
    await skill.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_skill_retries_http_5xx_before_create_succeeds():
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/api/jianying/draft/tasks":
            create_count += 1
            if create_count < 3:
                return httpx.Response(503, json={"message": "temporary"})
            return httpx.Response(200, json={"code": 200, "message": "success", "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            return httpx.Response(200, json={"code": 50002, "message": "任务处理失败", "data": None})
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        max_retries=2,
        retry_backoff_seconds=0,
        http_client=client,
        uploader=lambda _: {"success": True, "url": "https://tos.example.com/unused.zip"},
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.FAILED
    assert result.provider_task_id == "provider-task-1"
    assert create_count == 3
    await skill.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_skill_rejects_non_https_source_file_without_uploading():
    uploader_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/jianying/draft/tasks":
            return httpx.Response(200, json={"code": 200, "message": "success", "data": "provider-task-1"})
        return httpx.Response(
            200,
            json={"code": 200, "message": "success", "data": "http://cdn.example.com/draft.zip"},
        )

    def uploader(_: str) -> dict[str, object]:
        nonlocal uploader_called
        uploader_called = True
        return {"success": True, "url": "https://tos.example.com/unused.zip"}

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        http_client=client,
        uploader=uploader,
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.FAILED
    assert uploader_called is False
    await skill.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_skill_does_not_recreate_provider_task_when_tos_upload_raises():
    create_count = 0
    provider_zip = _zip_bytes({"draft.json": b"{}"})

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/api/jianying/draft/tasks":
            create_count += 1
            return httpx.Response(200, json={"code": 200, "message": "success", "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            return httpx.Response(
                200,
                json={"code": 200, "message": "success", "data": "https://cdn.example.com/draft.zip"},
            )
        if request.url.path == "/draft.zip":
            return httpx.Response(200, content=provider_zip)
        raise AssertionError(f"unexpected request: {request.url}")

    def uploader(_: str) -> dict[str, object]:
        raise RuntimeError("upload unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        http_client=client,
        uploader=uploader,
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.FAILED
    assert result.provider_task_id == "provider-task-1"
    assert create_count == 1
    await skill.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_background_job_preserves_content_app_authorization_for_tos_upload():
    captured_authorizations: list[str] = []
    provider_zip = _zip_bytes({"draft.json": b"{}"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/jianying/draft/tasks":
            return httpx.Response(200, json={"code": 200, "message": "success", "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            return httpx.Response(
                200,
                json={"code": 200, "message": "success", "data": "https://cdn.example.com/draft.zip"},
            )
        if request.url.path == "/draft.zip":
            return httpx.Response(200, content=provider_zip)
        raise AssertionError(f"unexpected request: {request.url}")

    def uploader(_: str) -> dict[str, object]:
        captured_authorizations.append(require_current_authorization())
        return {"success": True, "url": "https://tos.example.com/draft.zip"}

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        http_client=client,
        uploader=uploader,
    )
    service = JianyingDraftService(skill=skill, timeout_seconds=1, max_retries=0)
    auth_context_token = set_current_content_app_auth("Bearer content-app-user-token", username="tester")
    try:
        started = await service.start(_request())
    finally:
        reset_current_content_app_auth(auth_context_token)
    assert started.job_id is not None

    result = await _wait_for_terminal(service, started.job_id)

    assert result.status == JianyingDraftStatus.SUCCEEDED
    assert captured_authorizations == ["Bearer content-app-user-token"]
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_upload_keeps_temporary_zip_alive_when_cancellation_arrives():
    provider_zip = _zip_bytes({"draft.json": b"{}"})
    upload_started = threading.Event()
    allow_upload = threading.Event()
    upload_path_exists: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/jianying/draft/tasks":
            return httpx.Response(200, json={"code": 200, "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            return httpx.Response(200, json={"code": 200, "data": ["https://cdn.example.com/draft.zip"]})
        if request.url.path == "/draft.zip":
            return httpx.Response(200, content=provider_zip)
        raise AssertionError(f"unexpected request: {request.url}")

    def uploader(path: str) -> dict[str, object]:
        upload_started.set()
        assert allow_upload.wait(timeout=2)
        upload_path_exists.append(Path(path).exists())
        return {"success": True, "url": "https://tos.example.com/draft.zip"}

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        http_client=client,
        uploader=uploader,
    )
    task = asyncio.create_task(skill.generate(_request()))
    try:
        assert await asyncio.to_thread(upload_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        allow_upload.set()
        result = await task
    finally:
        allow_upload.set()
        await skill.aclose()
        await client.aclose()

    assert result.status == JianyingDraftStatus.SUCCEEDED
    assert upload_path_exists == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_content", "content_length"),
    [
        (b"not-a-zip", None),
        (_zip_bytes({}), None),
        (_zip_bytes({"draft.json": b"{}"}), str(201 * 1024 * 1024)),
    ],
)
async def test_http_skill_rejects_invalid_empty_or_oversized_provider_zip(
    provider_content: bytes,
    content_length: str | None,
):
    uploader_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/jianying/draft/tasks":
            return httpx.Response(200, json={"code": 200, "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            return httpx.Response(200, json={"code": 200, "data": "https://cdn.example.com/draft.zip"})
        headers = {"content-length": content_length} if content_length is not None else None
        return httpx.Response(200, content=provider_content, headers=headers)

    def uploader(_: str) -> dict[str, object]:
        nonlocal uploader_called
        uploader_called = True
        return {"success": True, "url": "https://tos.example.com/unused.zip"}

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        http_client=client,
        uploader=uploader,
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.FAILED
    assert uploader_called is False
    await skill.aclose()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("download_status", [404, 503])
async def test_http_skill_reports_provider_zip_download_failure(download_status: int):
    download_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal download_count
        if request.url.path == "/api/jianying/draft/tasks":
            return httpx.Response(200, json={"code": 200, "data": "provider-task-1"})
        if request.url.path == "/api/jianying/draft/tasks/result":
            return httpx.Response(200, json={"code": 200, "data": "https://cdn.example.com/draft.zip"})
        download_count += 1
        return httpx.Response(download_status)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    skill = HttpJianyingDraftSkill(
        base_url="https://provider.example.com",
        token="provider-token",
        poll_interval_seconds=0.001,
        max_retries=2,
        retry_backoff_seconds=0,
        http_client=client,
        uploader=lambda _: {"success": True, "url": "https://tos.example.com/unused.zip"},
    )

    result = await skill.generate(_request())

    assert result.status == JianyingDraftStatus.FAILED
    assert download_count == (3 if download_status == 503 else 1)
    await skill.aclose()
    await client.aclose()
