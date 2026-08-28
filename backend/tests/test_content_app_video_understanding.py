import httpx
import pytest

from pixelflow.capabilities.video_understanding.providers.content_app import ContentAppVideoUnderstandingAdapter


@pytest.mark.asyncio
async def test_decompose_video_contract() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.content
        return httpx.Response(200, json={
            "success": True,
            "status": "processing",
            "data": {"task_id": "task-1", "parent_generation_dialog_id": "parent-1"},
        })

    adapter = ContentAppVideoUnderstandingAdapter(
        base_url="https://test-video.borgrise.com/api",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await adapter.analyze(
        {"video_url": "https://cdn.example/video.mp4"},
        authorization="Bearer test",
        project_id=1,
    )
    assert result.task_id == "task-1"
    assert "projectId=1" in str(seen["url"])
    assert b'"video_url":"https://cdn.example/video.mp4"' in seen["json"]  # type: ignore[operator]
    await adapter.aclose()


@pytest.mark.asyncio
async def test_decompose_rejects_non_https_url() -> None:
    adapter = ContentAppVideoUnderstandingAdapter(base_url="https://test-video.borgrise.com/api")
    with pytest.raises(ValueError):
        await adapter.analyze({"video_url": "file:///tmp/video.mp4"}, authorization="Bearer test")
    await adapter.aclose()
