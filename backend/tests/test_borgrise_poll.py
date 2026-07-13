"""Regression test for poll_task status handling (offline, make_request mocked).

The Borgrise API returns lowercase task statuses ("pending"/"processing"/
"completed"/"failed"); poll_task must match them case-insensitively or it polls
until timeout even after the task is done.
"""

from __future__ import annotations

from pixelflow.skills.borgrise import run_generation
from pixelflow.skills.borgrise import skill as borgrise_skill


def _patch(monkeypatch, responses):
    calls = iter(responses)
    monkeypatch.setattr(run_generation, "make_request", lambda *a, **k: next(calls))
    monkeypatch.setattr(run_generation.time, "sleep", lambda *_: None)


def test_poll_returns_on_lowercase_completed(monkeypatch):
    _patch(
        monkeypatch,
        [
            {"data": {"status": "pending"}},
            {"data": {"status": "processing"}},
            {"data": {"status": "completed", "result": {"video_url": "https://x/v.mp4"}}},
        ],
    )
    result = run_generation.poll_task("t1")
    assert not result.get("error")
    assert run_generation.extract_video_url(result) == "https://x/v.mp4"


def test_poll_returns_error_on_lowercase_failed(monkeypatch):
    _patch(monkeypatch, [{"data": {"status": "failed", "error": "boom"}}])
    result = run_generation.poll_task("t1")
    assert result.get("error")
    assert result.get("message") == "boom"


def test_poll_recovers_from_transient_status_query_error(monkeypatch):
    _patch(
        monkeypatch,
        [
            {"data": {"status": "processing"}},
            {"error": True, "message": "<urlopen error EOF occurred in violation of protocol>"},
            {"data": {"status": "completed", "result": {"image_url": ["https://x/recovered.png"]}}},
        ],
    )

    result = run_generation.poll_task("t1")

    assert not result.get("error")
    assert result["data"]["result"]["image_url"] == ["https://x/recovered.png"]


def test_video_and_image_generation_use_separate_poll_timeouts(monkeypatch):
    """视频生成和图片生成都走 /task/status，但等待上限必须按业务类型区分。"""
    captured_defaults: list[int | None] = []

    monkeypatch.setattr(run_generation, "get_headers", lambda *a, **k: {})
    monkeypatch.setattr(run_generation, "make_request", lambda *a, **k: {"data": {"taskId": "task-1"}})

    def fake_poll_task(task_id, timeout=None, *, default_timeout=None):
        captured_defaults.append(default_timeout)
        return {
            "data": {
                "status": "completed",
                "result": {
                    "video_url": "https://x/video.mp4",
                    "url": "https://x/image.png",
                },
            }
        }

    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    run_generation.image_to_video("https://x/source.png", prompt="生成一段商品短视频")
    run_generation.text_to_image("生成一张商品主图")

    assert captured_defaults == [
        run_generation.VIDEO_POLL_TIMEOUT,
        run_generation.IMAGE_POLL_TIMEOUT,
    ]


def test_decompose_video_uses_video_analysis_poll_timeout(monkeypatch):
    """参考视频拆解属于视频分析任务，轮询上限应独立于视频生成和图片生成。"""
    captured_defaults: list[int | None] = []

    monkeypatch.setattr(run_generation, "get_headers", lambda *a, **k: {})
    monkeypatch.setattr(run_generation, "make_request", lambda *a, **k: {"data": {"taskId": "task-1"}})

    def fake_poll_task(task_id, timeout=None, *, default_timeout=None):
        captured_defaults.append(default_timeout)
        return {
            "data": {
                "status": "completed",
                "result": {
                    "segments": [
                        {
                            "visualContent": "主播展示商品",
                            "timeRange": "0-3s",
                        }
                    ]
                },
            }
        }

    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    result = borgrise_skill._decompose_blocking("https://x/reference.mp4")

    assert captured_defaults == [run_generation.VIDEO_ANALYSIS_POLL_TIMEOUT]
    assert result["data"]["result"]["segments"][0]["visualContent"] == "主播展示商品"


def test_extract_media_links_calls_creative_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    def fake_make_request(endpoint, data=None, **kwargs):
        captured["endpoint"] = endpoint
        captured["data"] = data
        captured["kwargs"] = kwargs
        return {"success": True, "data": ["https://x/one.mp4"]}

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)

    result = run_generation.extract_media_links("请分析 https://x/one.mp4")

    assert captured["endpoint"] == "/creative/extractMediaLinks"
    assert captured["data"] == {"text": "请分析 https://x/one.mp4"}
    assert result["endpoint"] == "/api/creative/extractMediaLinks"
    assert result["links"] == ["https://x/one.mp4"]


def test_batch_decompose_uses_video_urls_and_analysis_timeout(monkeypatch):
    captured_requests: list[tuple[str, object]] = []
    captured_defaults: list[int | None] = []

    monkeypatch.setattr(run_generation, "get_headers", lambda *a, **k: {})

    def fake_make_request(endpoint, data=None, **kwargs):
        captured_requests.append((endpoint, data))
        return {"data": {"taskId": "task-1"}}

    def fake_poll_task(task_id, timeout=None, *, default_timeout=None):
        captured_defaults.append(default_timeout)
        return {
            "data": {
                "status": "completed",
                "result": {
                    "batch_video_analysis_markdown": "批量归因",
                    "batch_video_generation_prompt": "生成同款短视频",
                },
            }
        }

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)
    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    result = run_generation.batch_decompose_video_to_storyboard(["https://x/one.mp4", "https://x/two.mp4"])

    assert captured_requests == [
        (
            "/creative/batch_decompose_video_to_storyboard",
            {"videoUrls": ["https://x/one.mp4", "https://x/two.mp4"]},
        )
    ]
    assert captured_defaults == [run_generation.VIDEO_ANALYSIS_POLL_TIMEOUT]
    assert result["endpoint"] == "/api/creative/batch_decompose_video_to_storyboard"
    assert result["storyboards"][0]["analysis_markdown"] == "批量归因"


def test_batch_decompose_reads_markdown_from_nested_video_url_payload(monkeypatch):
    """content-app 可能把批量拆解结果包在 data.result.video_url 对象里。"""
    monkeypatch.setattr(run_generation, "get_headers", lambda *a, **k: {})
    monkeypatch.setattr(run_generation, "make_request", lambda *a, **k: {"data": {"task_id": "task-1"}})

    def fake_poll_task(task_id, timeout=None, *, default_timeout=None):
        return {
            "success": True,
            "data": {
                "status": "completed",
                "result": {
                    "video_url": {
                        "batch_video_analysis_markdown": "嵌套批量归因",
                        "batch_video_generation_prompt": "嵌套复刻提示词",
                    }
                },
            },
        }

    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    result = run_generation.batch_decompose_video_to_storyboard(["https://x/one.mp4", "https://x/two.mp4"])

    assert result["batch_video_analysis_markdown"] == "嵌套批量归因"
    assert result["batch_video_generation_prompt"] == "嵌套复刻提示词"
    assert result["storyboards"] == [
        {
            "video_urls": ["https://x/one.mp4", "https://x/two.mp4"],
            "analysis_markdown": "嵌套批量归因",
            "generation_prompt": "嵌套复刻提示词",
        }
    ]
