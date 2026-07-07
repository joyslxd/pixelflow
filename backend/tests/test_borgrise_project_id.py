"""Borgrise/content-app 调用不再传 projectId 的回归测试。"""

from __future__ import annotations

from typing import Any

from pixelflow.skills.borgrise import run_generation
from pixelflow.skills.borgrise import skill as borgrise_skill


def _contains_project_id(value: Any) -> bool:
    """递归检查请求体中是否仍带有旧的 projectId 字段。"""
    if isinstance(value, dict):
        return any(key == "projectId" or _contains_project_id(inner) for key, inner in value.items())
    if isinstance(value, list):
        return any(_contains_project_id(item) for item in value)
    return False


def test_borgrise_generation_urls_do_not_append_project_id(monkeypatch):
    """视频、图片、参考视频拆解等接口的 URL 都不再追加 projectId query 参数。"""
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(run_generation, "get_headers", lambda *args, **kwargs: {})

    def fake_make_request(endpoint: str, data: Any = None, *args, **kwargs):
        calls.append((endpoint, data))
        if "batch_text_to_image" in endpoint:
            return {"data": {"taskIds": ["task-1"]}}
        if "decompose_video_to_storyboard" in endpoint:
            return {"data": {"result": {"segments": [{"visualContent": "展示商品", "timeRange": "0-3s"}]}}}
        return {"data": {"taskId": "task-1"}}

    def fake_poll_task(task_id: str, timeout=None, *, default_timeout=None):
        return {
            "data": {
                "status": "completed",
                "result": {
                    "url": "https://x/result.png",
                    "video_url": "https://x/result.mp4",
                },
            }
        }

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)
    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    run_generation.image_to_video("https://x/source.png", prompt="生成商品短视频", auto_poll=False)
    run_generation.text_to_video("生成商品短视频", auto_poll=False)
    run_generation.reference_mode_video("生成商品短视频", image_urls=["https://x/ref.png"], auto_poll=False)
    run_generation.extend_video("https://x/source.mp4", prompt="继续展示商品", auto_poll=False)
    run_generation.text_to_image("生成商品主图")
    run_generation.reference_image(["https://x/ref.png"], "生成商品主图")
    run_generation.image_edit("https://x/source.png", "替换背景")
    run_generation.batch_text_to_image(["生成商品主图"])
    borgrise_skill._decompose_blocking("https://x/reference.mp4")

    assert calls
    assert all("projectId" not in endpoint for endpoint, _data in calls)


def test_borgrise_request_bodies_do_not_send_project_id(monkeypatch):
    """以前 body 里的 projectId 也要删除，避免 content-app 新接口继续收到旧字段。"""
    bodies: list[Any] = []

    monkeypatch.setattr(run_generation, "get_headers", lambda *args, **kwargs: {})

    def fake_make_request(endpoint: str, data: Any = None, *args, **kwargs):
        bodies.append(data)
        if endpoint == "/asset/virtual-human-asset":
            return {"data": {"thirdAssetId": "asset-1"}}
        if endpoint == "/video/merge":
            return {"data": {"url": "https://x/merged.mp4"}}
        return {"data": {"id": 1}}

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)

    run_generation.merge_videos(["https://x/1.mp4", "https://x/2.mp4"])
    run_generation.create_virtual_human_asset(asset_name="主播", image_url="https://x/avatar.png")

    assert bodies
    assert not any(_contains_project_id(body) for body in bodies)


def test_borgrise_merge_uses_long_request_timeout(monkeypatch):
    """长视频合并会同步等待 content-app 完成，不能复用普通 30 秒读超时。"""
    captured: dict[str, Any] = {}

    monkeypatch.setattr(run_generation, "get_headers", lambda *args, **kwargs: {})

    def fake_make_request(endpoint: str, data: Any = None, *args, **kwargs):
        captured.update({"endpoint": endpoint, "data": data, "request_timeout": kwargs.get("request_timeout")})
        return {"data": {"url": "https://x/merged.mp4"}}

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)

    result = run_generation.merge_videos(["https://x/1.mp4", "https://x/2.mp4"])

    assert result["video_url"] == "https://x/merged.mp4"
    assert captured["endpoint"] == "/video/merge"
    assert captured["request_timeout"] == run_generation.VIDEO_MERGE_REQUEST_TIMEOUT
