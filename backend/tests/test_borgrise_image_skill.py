from __future__ import annotations

import asyncio

from pixelflow.skills.borgrise import run_generation
from pixelflow.skills.borgrise.skill import BorgriseSkill


def test_borgrise_text_to_image_maps_urls(monkeypatch):
    def fake_text_to_image(**kwargs):
        assert kwargs["prompt"] == "生成商品主图"
        assert kwargs["ratio"] == "9:16"
        assert kwargs["size"] == "1080p"
        assert kwargs["num_images"] == 2
        return {
            "success": True,
            "task_id": "img-task-1",
            "endpoint": "/api/picture/text_to_image",
            "image_url": "https://x/1.png",
            "image_urls": ["https://x/1.png", "https://x/2.png"],
        }

    monkeypatch.setattr(run_generation, "text_to_image", fake_text_to_image)

    result = asyncio.run(
        BorgriseSkill().text_to_image(
            prompt="生成商品主图",
            ratio="9:16",
            size="1080p",
            num_images=2,
        )
    )

    assert result.ok is True
    assert result.task_id == "img-task-1"
    assert [image["url"] for image in result.images] == ["https://x/1.png", "https://x/2.png"]
    assert result.images[0]["asset_id"] == "img-task-1-0"


def test_borgrise_image_edit_maps_edited_url(monkeypatch):
    def fake_image_edit(**kwargs):
        assert kwargs["image_url"] == "https://x/source.png"
        assert kwargs["prompt"] == "换背景"
        return {
            "success": True,
            "task_id": "edit-task-1",
            "endpoint": "/api/picture/image_edit",
            "edited_image_url": "https://x/edited.png",
        }

    monkeypatch.setattr(run_generation, "image_edit", fake_image_edit)

    result = asyncio.run(BorgriseSkill().image_edit(image_url="https://x/source.png", prompt="换背景"))

    assert result.ok is True
    assert result.task_id == "edit-task-1"
    assert result.images == [{"asset_id": "edit-task-1-0", "url": "https://x/edited.png", "download_url": "https://x/edited.png"}]


def test_gpt_image_edit_uses_price_configured_quality(monkeypatch):
    captured_headers = {}
    captured_payload = {}

    def fake_make_request(_path, _payload, custom_headers=None):
        captured_payload.update(_payload)
        captured_headers.update(custom_headers or {})
        return {"error": True, "message": "stop before polling"}

    monkeypatch.setattr(run_generation, "_current_authorization", lambda: "Bearer test-token")
    monkeypatch.setattr(run_generation, "make_request", fake_make_request)

    result = run_generation.image_edit("https://x/source.png", "换背景", model="gpt-image-2")

    assert result["error"] is True
    assert captured_headers["apiModelParamObj"] == '{"size": "4K"}'
    assert captured_payload == {
        "image_url": "https://x/source.png",
        "prompt": "换背景",
        "model": "gpt-image-2",
        "width": 1,
        "height": 1,
        "imageSize": "4K",
        "size": "1:1",
        "max_images": 1,
        "num": 1,
    }


def test_image_edit_sends_content_app_frontend_contract(monkeypatch):
    captured_headers = {}
    captured_payload = {}

    def fake_make_request(path, payload, custom_headers=None):
        assert path == "/picture/image_edit"
        captured_payload.update(payload)
        captured_headers.update(custom_headers or {})
        return {"error": True, "message": "stop before polling"}

    monkeypatch.setattr(run_generation, "_current_authorization", lambda: "Bearer test-token")
    monkeypatch.setattr(run_generation, "make_request", fake_make_request)

    result = run_generation.image_edit(
        "https://x/source.png",
        "图片中的路飞衣服变成黄色",
        model="seeddream-4.5",
        ratio="9:16",
        size="2K",
        max_images=1,
    )

    assert result["error"] is True
    assert captured_headers["apiModelParamObj"] == '{"size": "2K"}'
    assert captured_payload == {
        "image_url": "https://x/source.png",
        "prompt": "图片中的路飞衣服变成黄色",
        "model": "seeddream-4.5",
        "width": 9,
        "height": 16,
        "imageSize": "2K",
        "size": "9:16",
        "max_images": 1,
        "num": 1,
    }


def test_multi_image_fusion_sends_content_app_contract(monkeypatch):
    captured = {}

    def fake_make_request(path, payload, custom_headers=None):
        captured["path"] = path
        captured["payload"] = payload
        captured["headers"] = custom_headers or {}
        return {"error": True, "message": "stop before polling"}

    monkeypatch.setattr(run_generation, "_current_authorization", lambda: "Bearer test-token")
    monkeypatch.setattr(run_generation, "make_request", fake_make_request)

    result = run_generation.multi_image_fusion(
        ["https://x/a.png", "https://x/b.png"],
        "把两张图融合成商品海报",
        ratio="9:16",
        size="1080p",
        model="seeddream-5.0",
        num_images=1,
    )

    assert result["error"] is True
    assert captured["path"] == "/picture/multi_image_fusion"
    assert captured["payload"] == {
        "image_urls": ["https://x/a.png", "https://x/b.png"],
        "prompt": "把两张图融合成商品海报",
        "width": 9,
        "height": 16,
        "model": "seeddream-5.0",
        "num": 1,
    }
    assert captured["headers"]["apiModelParamObj"] == '{"size": "1080p"}'


def test_image_edit_extracts_image_url_list_from_poll_result(monkeypatch):
    def fake_make_request(_path, _payload, custom_headers=None):
        return {"success": True, "data": {"taskId": "edit-task-2"}}

    def fake_poll_task(task_id, default_timeout=None):
        assert task_id == "edit-task-2"
        return {
            "success": True,
            "data": {
                "status": "completed",
                "result": {
                    "message": "图片生成成功",
                    "image_url": ["https://x/edited-from-image-url.png"],
                },
            },
        }

    monkeypatch.setattr(run_generation, "_current_authorization", lambda: "Bearer test-token")
    monkeypatch.setattr(run_generation, "make_request", fake_make_request)
    monkeypatch.setattr(run_generation, "poll_task", fake_poll_task)

    result = run_generation.image_edit("https://x/source.png", "换背景", model="gpt-image-2")

    assert result["success"] is True
    assert result["edited_image_url"] == "https://x/edited-from-image-url.png"
    assert result["image_urls"] == ["https://x/edited-from-image-url.png"]
