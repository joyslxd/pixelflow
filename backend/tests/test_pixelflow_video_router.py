from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from tests._router_auth_helpers import make_authed_test_app


def _stable_user() -> User:
    return User(
        email="pixelflow-video@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000902"),
    )


def test_pixelflow_video_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_video

    paths = {route.path for route in pixelflow_video.router.routes}
    assert pixelflow_video.router.prefix == "/agent/flows/video"
    assert "/agent/flows/video/prepare-scene-packages" in paths
    assert "/agent/flows/video/prepare-scene-packages/start" in paths
    assert "/agent/flows/video/prepare-scene-packages/jobs/{job_id}" in paths
    assert "/agent/flows/video/generate-scene-assets" in paths
    assert "/agent/flows/video/generate-scene-assets/start" in paths
    assert "/agent/flows/video/generate-scene-assets/jobs/{job_id}" in paths
    assert "/agent/flows/video/generate-scenes" in paths
    assert "/agent/flows/video/generate-scenes/start" in paths
    assert "/agent/flows/video/generate-scenes/jobs/{job_id}" in paths
    assert "/agent/flows/video/generate-direct" in paths
    assert "/agent/flows/video/generate-direct/start" in paths
    assert "/agent/flows/video/generate-direct/jobs/{job_id}" in paths
    assert "/agent/flows/video/merge" in paths
    assert "/agent/flows/video/merge/start" in paths
    assert "/agent/flows/video/merge/jobs/{job_id}" in paths
    assert "/agent/flows/video/quality-review" in paths
    assert "/agent/flows/video/quality-review/start" in paths
    assert "/agent/flows/video/quality-review/jobs/{job_id}" in paths
    assert "/agent/flows/video/analyze-storyboards" in paths


def test_video_router_prepares_scene_packages(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.generate import scene_packages

    def unavailable_model_factory(*_args, **_kwargs):
        raise RuntimeError("test model unavailable")

    monkeypatch.setattr(scene_packages, "_default_model_factory", unavailable_model_factory)

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/prepare-scene-packages",
            json={
                "form_values": {
                    "product_info": "苹果降噪耳机 Pro",
                    "product_category": "数码3C",
                    "target_audience": "25-35 岁通勤人群",
                    "conversion_goal": "引流直播间",
                },
                "plan_markdown": "## 一、选题方向\n突出通勤降噪痛点，展示佩戴前后对比，并引导进入直播间。",
                "selected_direction": {"title": "通勤降噪挑战", "description": "用通勤噪声反差制造记忆点"},
                "target_duration_ms": 30_000,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["requires_confirmation"] is True
    assert data["review_timeout_sec"] is None
    character_assets = data["global_assets"]["characters"]
    assert len(character_assets) >= 2
    assert all("three_view_prompt" in asset for asset in character_assets)
    assert all("三视图" in asset["three_view_prompt"] for asset in character_assets)
    assert all("苹果降噪耳机 Pro" not in asset["name"] for asset in character_assets)
    assert data["global_assets"]["props"][0]["name"] == "苹果降噪耳机 Pro"
    assert data["global_assets"]["visual_style"]["name"]
    assert data["scene_packages"][0]["scene_id"] == "scene-1"
    assert 4_000 <= data["scene_packages"][0]["duration_ms"] <= 15_000
    assert data["scene_packages"][0]["reference_asset_ids"]
    assert set(data["scene_packages"][0]["shot_description"]) == {"text", "mentions"}
    assert "地点:@" in data["scene_packages"][0]["shot_description"]["text"]
    assert data["scene_packages"][0]["shot_description"]["mentions"]
    assert "苹果降噪耳机 Pro" in data["scene_packages"][0]["prompt"]


def test_video_router_derives_scene_timeline_from_confirmed_creation_contract(monkeypatch):
    from app.gateway.routers import pixelflow_video

    captured: dict[str, object] = {}

    async def fake_prepare_video_scene_packages_with_llm(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "message": "场景包已生成。",
            "requires_confirmation": True,
            "review_timeout_sec": None,
            "target_duration_ms": kwargs["target_duration_ms"],
            "global_assets": {"characters": [], "scenes": [], "props": []},
            "scene_packages": [{"scene_id": f"scene-{index}", "scene_index": index, "duration_ms": 10_000, "prompt": f"分镜 {index}"} for index in range(1, 19)],
        }

    monkeypatch.setattr(pixelflow_video, "prepare_video_scene_packages_with_llm", fake_prepare_video_scene_packages_with_llm)
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    creation_contract = {
        "version": 1,
        "intent": "video",
        "video_duration_sec": 180,
        "video_ratio": "9:16",
        "video_model_mode": "system_recommended",
        "video_model": "seedance-2.0",
        "video_model_capabilities": {
            "generation_types": ["文生视频", "首尾帧", "全能参考", "编辑视频", "延伸视频"],
            "upload_file_types": ["JPG", "JPEG", "PNG", "WEBP", "MP4", "MP3"],
        },
        "video_size": "1080p",
        "video_sound": "on",
        "image_model": "gpt-image-2",
        "image_model_capabilities": {"aspect_ratios": ["9:16", "1:1"], "sizes": ["4K", "2K"]},
        "video_usage": "宣传片",
        "visual_style": "电影写实",
        "confirmed_by_user": True,
        "scene_image_ratio": "9:16",
        "scene_image_size": "4K",
        "scene_image_spec_source": "plan_llm",
    }
    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/prepare-scene-packages",
            json={
                "form_values": {"product_info": "通勤背包"},
                "plan_markdown": "## 视频计划\n总时长 180 秒。",
                "selected_direction": {"title": "暴雨通勤"},
                "target_duration_ms": 30_000,
                "creation_contract": creation_contract,
            },
        )

    assert response.status_code == 200
    assert captured["target_duration_ms"] == 180_000
    assert captured["form_values"]["video_ratio"] == "9:16"
    returned_contract = response.json()["creation_contract"]
    assert returned_contract["video_model"] == creation_contract["video_model"]
    assert returned_contract["video_size"] == creation_contract["video_size"]
    assert returned_contract["video_model_capabilities"]["generation_types"] == creation_contract["video_model_capabilities"]["generation_types"]
    assert returned_contract["video_model_capabilities"]["sizes"] == []


def test_video_router_passes_plan_scene_blueprints_to_scene_package_skill(monkeypatch):
    from app.gateway.routers import pixelflow_video

    captured: dict[str, object] = {}

    async def fake_prepare_video_scene_packages_with_llm(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "message": "场景包已生成。",
            "requires_confirmation": True,
            "review_timeout_sec": None,
            "target_duration_ms": kwargs["target_duration_ms"],
            "global_assets": {"characters": [], "scenes": [], "props": []},
            "scene_packages": [],
        }

    monkeypatch.setattr(pixelflow_video, "prepare_video_scene_packages_with_llm", fake_prepare_video_scene_packages_with_llm)
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)
    blueprints = [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "title": "单镜头",
            "structure_role": "opening",
            "start_sec": 0,
            "end_sec": 4,
            "duration_sec": 4,
            "storyline": "产品完成一次清晰展示。",
            "shot_description": "0-4秒: 特写产品外观并稳定定格。",
            "narration": "四秒看懂核心卖点。",
            "transition": "产品定格结束。",
            "asset_requirements": {"characters": [], "scenes": ["产品台"], "props": ["产品"]},
        }
    ]
    asset_manifest = {
        "characters": [],
        "scenes": [{"name": "产品台", "description": "纯净产品展示台。", "image_prompt": "纯净产品展示台参考图。"}],
        "props": [{"name": "产品", "description": "测试产品固定外观。", "image_prompt": "测试产品参考图。"}],
    }

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/prepare-scene-packages",
            json={
                "form_values": {"product_info": "测试产品"},
                "plan_markdown": "## 五、镜头列表\n单镜头。",
                "selected_direction": {"title": "产品特写"},
                "target_duration_ms": 4_000,
                "scene_blueprints": blueprints,
                "asset_manifest": asset_manifest,
            },
        )

    assert response.status_code == 200
    assert captured["scene_blueprints"] == blueprints
    assert captured["asset_manifest"] == asset_manifest


def test_video_router_rejects_final_plan_blueprints_without_asset_manifest():
    from app.gateway.routers import pixelflow_video

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)
    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/prepare-scene-packages",
            json={
                "plan_markdown": "# legacy plan",
                "target_duration_ms": 4_000,
                "scene_blueprints": [{"scene_id": "scene-1", "scene_index": 1}],
            },
        )

    assert response.status_code == 422
    assert "缺少 asset_manifest" in response.text


def test_provider_video_duration_uses_exact_integer_seconds():
    from app.gateway.routers.pixelflow_video import _provider_video_duration_seconds

    assert _provider_video_duration_seconds(4_000, "seedance-2.0") == 4
    assert _provider_video_duration_seconds(15_000, "seedance-2.0") == 15
    with pytest.raises(ValueError, match="4-15"):
        _provider_video_duration_seconds(3_000, "seedance-2.0")
    with pytest.raises(ValueError, match="integer seconds"):
        _provider_video_duration_seconds(4_500, "seedance-2.0")


def test_scene_video_generation_contract_overrides_conflicting_legacy_fields(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    captured: dict[str, object] = {}

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            captured.update(kwargs)
            return GenerationResult(ok=True, task_id="scene-task", url="https://x/scene.mp4", raw={"endpoint": "/api/video/text-to-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    creation_contract = {
        "version": 1,
        "intent": "video",
        "video_duration_sec": 15,
        "video_ratio": "9:16",
        "video_model_mode": "manual",
        "video_model": "seedance-2.0",
        "video_size": "1080p",
        "video_sound": "on",
        "image_model": "gpt-image-2",
        "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
        "video_usage": "宣传片",
        "visual_style": "电影写实",
        "confirmed_by_user": True,
        "scene_image_ratio": "9:16",
        "scene_image_size": "4K",
        "scene_image_spec_source": "plan_llm",
    }
    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 15_000,
                        "prompt": "精确 15 秒产品展示",
                        "generation_mode": "text_to_video",
                    }
                ],
                "ratio": "1:1",
                "size": "720p",
                "model": "legacy-model",
                "sound": "off",
                "creation_contract": creation_contract,
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["duration"] == 15
    assert captured["ratio"] == "9:16"
    assert captured["size"] == "1080p"
    assert captured["model"] == "seedance-2.0"
    assert captured["sound"] == "on"


def test_video_prepare_scene_packages_records_power_mem_experience(monkeypatch):
    from app.gateway.routers import pixelflow_video

    async def fake_prepare_video_scene_packages_with_llm(**_kwargs):
        return {
            "ok": True,
            "message": "场景包已生成。",
            "requires_confirmation": True,
            "review_timeout_sec": None,
            "target_duration_ms": 30_000,
            "global_assets": {"characters": [], "scenes": [], "props": []},
            "scene_packages": [{"scene_id": "scene-1", "scene_index": 1, "duration_ms": 8000, "prompt": "第一幕"}],
        }

    class FakePowerMemService:
        def __init__(self):
            self.records = []

        async def search(self, **_kwargs):
            return []

        async def record(self, **kwargs):
            self.records.append(kwargs)
            return True

    service = FakePowerMemService()
    monkeypatch.setattr(pixelflow_video, "prepare_video_scene_packages_with_llm", fake_prepare_video_scene_packages_with_llm)

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_power_mem_service = service
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/prepare-scene-packages",
            json={
                "form_values": {"product_info": "耳机"},
                "plan_markdown": "耳机场景",
                "selected_direction": {"title": "通勤"},
                "target_duration_ms": 30_000,
            },
        )

    assert response.status_code == 200
    assert service.records[0]["category"] == "experience"
    assert service.records[0]["source_agent"] == "video_scene_package_agent"
    assert "场景包" in service.records[0]["content"]


def test_video_router_starts_prepare_scene_package_job_and_polls_result(monkeypatch):
    import time

    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import ImageGenerationResult

    async def fake_prepare_video_scene_packages_with_llm(**_kwargs):
        return {
            "ok": True,
            "message": "场景包已生成。",
            "requires_confirmation": True,
            "review_timeout_sec": None,
            "target_duration_ms": 30_000,
            "global_assets": {
                "characters": [{"asset_id": "character-presenter", "name": "讲解者", "three_view_prompt": "讲解者角色三视图"}],
                "scenes": [{"asset_id": "scene-desk", "description": "桌面场景", "image_prompt": "桌面场景图"}],
                "props": [{"asset_id": "prop-product", "name": "耳机", "image_prompt": "耳机道具图"}],
            },
            "scene_packages": [{"scene_id": "scene-1", "scene_index": 1, "duration_ms": 8000, "prompt": "第一幕"}],
        }

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            return ImageGenerationResult(ok=True, images=[{"url": f"https://x/{kwargs['ratio']}.png"}], raw={"endpoint": "/api/picture/text_to_image"})

    monkeypatch.setattr(pixelflow_video, "prepare_video_scene_packages_with_llm", fake_prepare_video_scene_packages_with_llm)
    monkeypatch.setattr(pixelflow_video, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/video/prepare-scene-packages/start",
            json={
                "form_values": {"product_info": "耳机"},
                "plan_markdown": "耳机场景",
                "selected_direction": {"title": "通勤"},
                "target_duration_ms": 30_000,
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()
        assert started["ok"] is True
        assert started["job_id"]

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/video/prepare-scene-packages/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "completed":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "completed"
    assert status["stage"] == "completed"
    assert status["result"]["ok"] is True
    assert status["result"]["videoScenePackages"]["global_assets"]["characters"][0]["three_view_images"]
    assert status["result"]["sceneAssetFailures"] == []


def test_video_router_generates_scene_asset_images(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            prompt = kwargs["prompt"]
            assert kwargs["size"] == "4K"
            assert kwargs["ratio"] == "9:16"
            assert kwargs["model"] == "gpt-image-2"
            if "角色三视图" in prompt:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/role.png"}], raw={"endpoint": "/api/picture/text_to_image"})
            if "场景图" in prompt:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/scene.png"}], raw={"endpoint": "/api/picture/text_to_image"})
            if "道具图" in prompt:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/prop.png"}], raw={"endpoint": "/api/picture/text_to_image"})
            raise AssertionError(f"unexpected prompt: {prompt}")

        async def reference_image(self, **_kwargs):
            raise AssertionError("reference_image should not be called without materials")

    monkeypatch.setattr(pixelflow_video, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scene-assets",
            json={
                "global_assets": {
                    "characters": [{"asset_id": "character-presenter", "name": "讲解者", "three_view_prompt": "讲解者角色三视图"}],
                    "scenes": [{"asset_id": "scene-desk", "description": "桌面场景", "image_prompt": "桌面场景图"}],
                    "props": [{"asset_id": "prop-product", "name": "耳机", "image_prompt": "耳机道具图"}],
                    "visual_style": {"asset_id": "style-main", "name": "真实摄影"},
                },
                "scene_packages": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "reference_asset_ids": ["character-presenter", "scene-desk", "prop-product"],
                    }
                ],
                "image_ratio": "9:16",
                "image_size": "4K",
                "model": "gpt-image-2",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/picture/text_to_image"
    assert data["global_assets"]["characters"][0]["three_view_images"] == ["https://x/role.png"]
    assert data["global_assets"]["scenes"][0]["images"] == ["https://x/scene.png"]
    assert data["global_assets"]["props"][0]["images"] == ["https://x/prop.png"]


def test_video_router_scene_asset_target_whitelist_only_calls_failed_asset(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import ImageGenerationResult

    calls: list[str] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append(kwargs["prompt"])
            return ImageGenerationResult(ok=True, images=[{"url": "https://x/scene-retried.png"}], raw={})

        async def reference_image(self, **_kwargs):
            raise AssertionError("reference_image should not be called")

    monkeypatch.setattr(pixelflow_video, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scene-assets",
            json={
                "global_assets": {
                    "characters": [
                        {
                            "asset_id": "character-presenter",
                            "three_view_prompt": "讲解者角色三视图",
                            "three_view_images": ["https://x/role-completed.png"],
                        }
                    ],
                    "scenes": [{"asset_id": "scene-desk", "image_prompt": "桌面场景图", "images": []}],
                    "props": [
                        {
                            "asset_id": "prop-product",
                            "image_prompt": "耳机道具图",
                            "images": ["https://x/prop-completed.png"],
                        }
                    ],
                },
                "scene_packages": [{"scene_id": "scene-1", "scene_index": 1}],
                "target_assets": [{"asset_id": "scene-desk", "asset_type": "scene_image"}],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert calls == ["桌面场景图"]
    assert data["global_assets"]["characters"][0]["three_view_images"] == ["https://x/role-completed.png"]
    assert data["global_assets"]["scenes"][0]["images"] == ["https://x/scene-retried.png"]
    assert data["global_assets"]["props"][0]["images"] == ["https://x/prop-completed.png"]


def test_video_router_generates_prop_assets_with_reference_images(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            prompt = kwargs["prompt"]
            if "角色三视图" in prompt:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/role.png"}], raw={})
            if "场景图" in prompt:
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/scene.png"}], raw={})
            raise AssertionError(f"unexpected text_to_image prompt: {prompt}")

        async def reference_image(self, **kwargs):
            assert kwargs["reference_images"] == ["https://x/product.png"]
            assert kwargs["model"] == "gpt-image-2"
            assert kwargs["ratio"] == "9:16"
            assert kwargs["size"] == "4K"
            assert "参考图" in kwargs["prompt"]
            return ImageGenerationResult(
                ok=True,
                images=[{"url": "https://x/prop-ref.png"}],
                raw={"endpoint": "/api/picture/multi_reference_image_generation"},
            )

    monkeypatch.setattr(pixelflow_video, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scene-assets",
            json={
                "materials": [{"url": "https://x/product.png", "mediaType": "image"}],
                "global_assets": {
                    "characters": [{"asset_id": "character-presenter", "name": "讲解者", "three_view_prompt": "讲解者角色三视图"}],
                    "scenes": [{"asset_id": "scene-desk", "description": "桌面场景", "image_prompt": "桌面场景图"}],
                    "props": [{"asset_id": "prop-product", "name": "耳机", "image_prompt": "耳机道具图"}],
                },
                "scene_packages": [{"scene_id": "scene-1", "scene_index": 1}],
                "image_ratio": "9:16",
                "image_size": "4K",
                "model": "gpt-image-2",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/picture/mixed"
    assert data["global_assets"]["props"][0]["images"] == ["https://x/prop-ref.png"]


def test_video_router_stops_scene_asset_generation_on_quota_failure(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import ImageGenerationResult

    calls: list[str] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append(kwargs["prompt"])
            return ImageGenerationResult(
                ok=False,
                error="额度不足，剩余额度: 0，需要: 1",
                raw={"quota_insufficient": True, "message": "额度不足，剩余额度: 0，需要: 1"},
            )

    monkeypatch.setattr(pixelflow_video, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scene-assets",
            json={
                "global_assets": {
                    "characters": [{"asset_id": "character-presenter", "name": "讲解者", "image_prompt": "讲解者角色图"}],
                    "scenes": [{"asset_id": "scene-desk", "description": "桌面场景", "image_prompt": "桌面场景图"}],
                },
                "scene_packages": [{"scene_id": "scene-1", "scene_index": 1}],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["quota_insufficient"] is True
    assert "充值后" in data["message"]
    assert len(calls) == 1


def test_video_router_scene_asset_job_quota_paused(monkeypatch):
    import time

    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def text_to_image(self, **_kwargs):
            return ImageGenerationResult(
                ok=False,
                error="用户没有有效的额度",
                raw={"quota_insufficient": True, "message": "用户没有有效的额度"},
            )

    monkeypatch.setattr(pixelflow_video, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/video/generate-scene-assets/start",
            json={
                "global_assets": {
                    "characters": [{"asset_id": "character-presenter", "name": "讲解者", "image_prompt": "讲解者角色图"}],
                },
                "scene_packages": [{"scene_id": "scene-1", "scene_index": 1}],
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/video/generate-scene-assets/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "quota_paused":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "quota_paused"
    assert status["result"]["ok"] is False
    assert status["result"]["quota_insufficient"] is True
    assert status["result"]["failed_assets"][0]["asset_id"] == "character-presenter"


def test_video_router_unknown_prepare_and_scene_asset_jobs_return_404():
    from app.gateway.routers import pixelflow_video

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        scene_package_response = client.get("/agent/flows/video/prepare-scene-packages/jobs/missing")
        scene_asset_response = client.get("/agent/flows/video/generate-scene-assets/jobs/missing")

    assert scene_package_response.status_code == 404
    assert scene_asset_response.status_code == 404


def test_video_router_generates_scene_videos(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    class FakeVideoSkill:
        async def reference_mode_video(self, **kwargs):
            assert kwargs["prompt"] in {"第一幕展示白色耳机", "第二幕展示降噪场景"}
            return GenerationResult(
                ok=True,
                task_id=f"{kwargs['prompt']}-task",
                url=f"https://x/{kwargs['prompt']}.mp4",
                raw={"endpoint": "/api/video/reference-mode-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "第一幕展示白色耳机",
                        "image_urls": ["https://x/role.png"],
                    },
                    {
                        "scene_id": "scene-2",
                        "scene_index": 2,
                        "duration_ms": 8000,
                        "prompt": "第二幕展示降噪场景",
                        "image_urls": ["https://x/scene.png"],
                    },
                ],
                "ratio": "9:16",
                "size": "720p",
                "sound": "on",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/video/reference-mode-video"
    assert [scene["scene_id"] for scene in data["scene_videos"]] == ["scene-1", "scene-2"]
    assert data["scene_videos"][0]["video_url"] == "https://x/第一幕展示白色耳机.mp4"


def test_video_router_uses_shot_description_mentions_as_reference_images(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[dict] = []

    class FakeVideoSkill:
        async def reference_mode_video(self, **kwargs):
            calls.append(kwargs)
            return GenerationResult(
                ok=True,
                task_id="scene-task",
                url="https://x/scene.mp4",
                raw={"endpoint": "/api/video/reference-mode-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "第一幕展示白色耳机",
                        "shot_description": {
                            "text": "0-8秒: 地点:@scene-desk 中,角色:@character-presenter 展示道具:@prop-product。",
                            "mentions": [
                                {"asset_id": "character-presenter", "name": "讲解者", "type": "character", "image_url": "https://x/role.png"},
                                {"asset_id": "scene-desk", "name": "桌面", "type": "scene", "image_url": "https://x/scene.png"},
                                {"asset_id": "prop-product", "name": "耳机", "type": "prop", "image_url": "https://x/prop.png"},
                            ],
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["scene_videos"][0]["mode"] == "reference_mode_video"
    assert calls[0]["image_urls"] == ["https://x/role.png", "https://x/scene.png", "https://x/prop.png"]


def test_video_router_rejects_more_than_nine_mention_reference_images(monkeypatch):
    from app.gateway.routers import pixelflow_video

    class FakeVideoSkill:
        async def reference_mode_video(self, **_kwargs):
            raise AssertionError("should not call video skill when mentions exceed limit")

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "参考图太多",
                        "shot_description": {
                            "text": "0-8秒: 参考太多。",
                            "mentions": [{"asset_id": f"asset-{index}", "image_url": f"https://x/ref-{index}.png"} for index in range(10)],
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["failed_scenes"][0]["scene_id"] == "scene-1"
    assert "最多只能选择9张参考图" in data["failed_scenes"][0]["error"]


def test_video_router_allows_exactly_nine_reference_images(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[dict] = []

    class FakeVideoSkill:
        async def reference_mode_video(self, **kwargs):
            calls.append(kwargs)
            return GenerationResult(
                ok=True,
                task_id="nine-reference-task",
                url="https://x/nine-reference.mp4",
                raw={"endpoint": "/api/video/reference-mode-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "九张参考图生成镜头",
                        "image_urls": [f"https://x/ref-{index}.png" for index in range(9)],
                        "generation_mode": "reference_mode_video",
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(calls) == 1
    assert len(calls[0]["image_urls"]) == 9


def test_video_router_does_not_retry_content_app_validation_failure(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    attempts = 0

    class FakeVideoSkill:
        async def reference_mode_video(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            return GenerationResult(
                ok=False,
                error="参数验证失败: imageUrls 图片URL集合不能超过9张",
                raw={
                    "error": True,
                    "status_code": 400,
                    "message": "参数验证失败",
                    "data": {"imageUrls": "个数必须在0和9之间"},
                },
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "参数验证失败场景",
                        "image_urls": ["https://x/ref.png"],
                    }
                ]
            },
        )

    assert response.status_code == 200
    failure = response.json()["failed_scenes"][0]
    assert attempts == 1
    assert failure["attempts"] == 1
    assert failure["raw"]["data"]["imageUrls"] == "个数必须在0和9之间"


def test_video_router_does_not_retry_real_person_content_rejection(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    attempts = 0

    class FakeVideoSkill:
        async def reference_mode_video(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            return GenerationResult(
                ok=False,
                error="Task failed because input image may contain real person",
                raw={
                    "error": True,
                    "message": "Task failed because input image may contain real person",
                },
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 4000,
                        "prompt": "产品特写",
                        "image_urls": ["https://x/real-person.png"],
                    }
                ]
            },
        )

    assert response.status_code == 200
    failure = response.json()["failed_scenes"][0]
    assert attempts == 1
    assert failure["attempts"] == 1
    assert failure["error"] == "Task failed because input image may contain real person"


@pytest.mark.parametrize("model", ["seedance-2.0-mini", "seedance-2.0-fast"])
def test_video_router_uses_dynamic_720p_for_compact_seedance_models(monkeypatch, model):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[dict] = []

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            calls.append(kwargs)
            return GenerationResult(
                ok=True,
                task_id=f"{model}-task",
                url=f"https://x/{model}.mp4",
                raw={"endpoint": "/api/video/text-to-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())
    contract = {
        "video_duration_sec": 4,
        "video_ratio": "9:16",
        "video_model": model,
        "video_model_capabilities": {
            "generation_types": ["文生视频", "首尾帧", "全能参考"],
            "upload_file_types": ["JPG", "PNG", "MP4"],
            "aspect_ratios": ["9:16", "16:9", "1:1"],
            "sizes": ["480p", "720p"],
            "sound_options": ["on", "off"],
            "durations_sec": list(range(4, 16)),
        },
        "video_size": "720p",
        "video_sound": "on",
        "image_model": "gpt-image-2",
        "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
        "video_usage": "产品宣传",
    }

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 4000,
                        "prompt": "产品特写",
                        "generation_mode": "text_to_video",
                    }
                ],
                "creation_contract": contract,
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls[0]["model"] == model
    assert calls[0]["size"] == "720p"


def test_video_router_generates_scene_videos_in_parallel_and_aggregates_quota_once(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[str] = []
    active = 0
    max_active = 0

    class FakeVideoSkill:
        async def reference_mode_video(self, **kwargs):
            nonlocal active, max_active
            calls.append(kwargs["prompt"])
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            if kwargs["prompt"].startswith("第一幕"):
                return GenerationResult(
                    ok=False,
                    error="用户没有有效的额度",
                    raw={"quota_insufficient": True, "message": "用户没有有效的额度"},
                )
            return GenerationResult(ok=True, task_id="scene-2-task", url="https://x/scene-2.mp4", raw={"endpoint": "/api/video/reference-mode-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "第一幕展示白色耳机",
                        "image_urls": ["https://x/role.png"],
                    },
                    {
                        "scene_id": "scene-2",
                        "scene_index": 2,
                        "duration_ms": 8000,
                        "prompt": "第二幕展示降噪场景",
                        "image_urls": ["https://x/scene.png"],
                    },
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["quota_insufficient"] is True
    assert data["message"].count("额度") <= 1
    assert [scene["scene_id"] for scene in data["scene_videos"]] == ["scene-2"]
    assert [scene["scene_id"] for scene in data["failed_scenes"]] == ["scene-1"]
    assert sorted(calls) == ["第一幕展示白色耳机", "第二幕展示降噪场景"]
    assert max_active > 1


def test_video_router_caps_single_scene_video_job_concurrency_at_100_and_queues_extra_scenes(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    active = 0
    max_active = 0
    calls: list[str] = []

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            calls.append(kwargs["prompt"])
            await asyncio.sleep(0.03)
            active -= 1
            return GenerationResult(
                ok=True,
                task_id=f"{kwargs['prompt']}-task",
                url=f"https://x/{kwargs['prompt']}.mp4",
                raw={"endpoint": "/api/video/text-to-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    scenes = [
        {
            "scene_id": f"scene-{index}",
            "scene_index": index,
            "duration_ms": 5000,
            "prompt": f"scene-{index}",
            "generation_mode": "text_to_video",
        }
        for index in range(1, 106)
    ]

    with TestClient(app) as client:
        response = client.post("/agent/flows/video/generate-scenes", json={"scenes": scenes})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["scene_videos"]) == 105
    assert len(calls) == 105
    assert max_active == 100


def test_video_router_retries_scene_video_exceptions_three_times_without_blocking_other_scenes(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    attempts: dict[str, int] = {}

    class FakeVideoSkill:
        async def reference_mode_video(self, **kwargs):
            prompt = kwargs["prompt"]
            attempts[prompt] = attempts.get(prompt, 0) + 1
            await asyncio.sleep(0.01)
            if prompt.startswith("第一幕"):
                raise RuntimeError("供应商连接超时")
            return GenerationResult(ok=True, task_id="scene-2-task", url="https://x/scene-2.mp4", raw={"endpoint": "/api/video/reference-mode-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "第一幕展示白色耳机",
                        "image_urls": ["https://x/role.png"],
                    },
                    {
                        "scene_id": "scene-2",
                        "scene_index": 2,
                        "duration_ms": 8000,
                        "prompt": "第二幕展示降噪场景",
                        "image_urls": ["https://x/scene.png"],
                    },
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert [scene["scene_id"] for scene in data["scene_videos"]] == ["scene-2"]
    assert data["failed_scenes"][0]["scene_id"] == "scene-1"
    assert data["failed_scenes"][0]["attempts"] == 3
    assert "供应商连接超时" in data["failed_scenes"][0]["error"]
    assert attempts["第一幕展示白色耳机"] == 3
    assert attempts["第二幕展示降噪场景"] == 1


def test_video_router_scene_video_mode_selection_and_reference_limit(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls = []

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            calls.append(("text_to_video", kwargs))
            return GenerationResult(ok=True, task_id="text-task", url="https://x/text.mp4", raw={"endpoint": "/api/video/text-to-video"})

        async def image_to_video(self, **kwargs):
            calls.append(("image_to_video", kwargs))
            return GenerationResult(ok=True, task_id="image-task", url="https://x/image.mp4", raw={"endpoint": "/api/video/image-to-video"})

        async def two_image_to_video(self, **kwargs):
            calls.append(("two_image_to_video", kwargs))
            return GenerationResult(ok=True, task_id="two-image-task", url="https://x/two-image.mp4", raw={"endpoint": "/api/video/two-image-to-video"})

        async def reference_mode_video(self, **kwargs):
            calls.append(("reference_mode_video", kwargs))
            return GenerationResult(ok=True, task_id="ref-task", url="https://x/ref.mp4", raw={"endpoint": "/api/video/reference-mode-video"})

        async def edit_video(self, **kwargs):
            calls.append(("edit_video", kwargs))
            return GenerationResult(ok=True, task_id="edit-task", url="https://x/edit.mp4", raw={"endpoint": "/api/video/edit-video"})

        async def extend_video(self, **kwargs):
            calls.append(("extend_video", kwargs))
            return GenerationResult(ok=True, task_id="extend-task", url="https://x/extend.mp4", raw={"endpoint": "/api/video/extend-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {"scene_id": "scene-1", "scene_index": 1, "duration_ms": 5000, "prompt": "纯文本镜头", "generation_mode": "text_to_video"},
                    {"scene_id": "scene-2", "scene_index": 2, "duration_ms": 5000, "prompt": "首帧动起来", "image_urls": ["https://x/first.png"], "generation_mode": "image_to_video"},
                    {
                        "scene_id": "scene-3",
                        "scene_index": 3,
                        "duration_ms": 5000,
                        "prompt": "首尾帧过渡",
                        "image_urls": ["https://x/first.png", "https://x/last.png"],
                        "generation_mode": "two_image_to_video",
                    },
                    {
                        "scene_id": "scene-4",
                        "scene_index": 4,
                        "duration_ms": 5000,
                        "prompt": "多参考生成",
                        "image_urls": ["https://x/a.png", "https://x/b.png"],
                        "generation_mode": "reference_mode_video",
                    },
                    {
                        "scene_id": "scene-5",
                        "scene_index": 5,
                        "duration_ms": 5000,
                        "prompt": "编辑视频节奏",
                        "video_urls": ["https://x/source.mp4"],
                        "image_urls": ["https://x/ref.png"],
                        "generation_mode": "edit_video",
                    },
                    {
                        "scene_id": "scene-6",
                        "scene_index": 6,
                        "duration_ms": 5000,
                        "prompt": "延伸视频结尾",
                        "video_urls": ["https://x/source.mp4"],
                        "generation_mode": "extend_video",
                    },
                    {
                        "scene_id": "scene-7",
                        "scene_index": 7,
                        "duration_ms": 5000,
                        "prompt": "参考图太多",
                        "image_urls": [f"https://x/ref-{index}.png" for index in range(10)],
                        "generation_mode": "reference_mode_video",
                    },
                ]
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert [scene["mode"] for scene in data["scene_videos"]] == [
        "text_to_video",
        "image_to_video",
        "two_image_to_video",
        "reference_mode_video",
        "edit_video",
        "extend_video",
    ]
    assert [name for name, _kwargs in calls] == [
        "text_to_video",
        "image_to_video",
        "two_image_to_video",
        "reference_mode_video",
        "edit_video",
        "extend_video",
    ]
    assert calls[2][1]["first_frame_image_url"] == "https://x/first.png"
    assert calls[2][1]["last_frame_image_url"] == "https://x/last.png"
    assert calls[4][1]["ref_video"] == "https://x/source.mp4"
    assert calls[5][1]["video_url"] == "https://x/source.mp4"
    assert data["failed_scenes"][0]["scene_id"] == "scene-7"
    assert "最多只能选择9张参考图" in data["failed_scenes"][0]["error"]


def test_generate_scene_videos_prefers_generation_reference_url_over_display_image(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[dict] = []

    class FakeVideoSkill:
        async def reference_mode_video(self, **kwargs):
            calls.append(kwargs)
            return GenerationResult(ok=True, task_id="ref-task", url="https://x/ref.mp4", raw={"endpoint": "/api/video/reference-mode-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 5000,
                        "prompt": "数字人出镜",
                        "generation_mode": "reference_mode_video",
                        "shot_description": {
                            "mentions": [
                                {
                                    "asset_id": "character-host",
                                    "image_url": "https://x/digital-human-cover.png",
                                    "generation_reference_url": "asset://asset-123",
                                }
                            ]
                        },
                    }
                ]
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert calls[0]["image_urls"] == ["asset://asset-123"]


def test_generate_scene_videos_falls_back_to_supported_mode_when_model_has_no_omni_reference(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[tuple[str, dict]] = []

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            calls.append(("text_to_video", kwargs))
            return GenerationResult(
                ok=True,
                task_id="text-task",
                url="https://x/text.mp4",
                raw={"endpoint": "/api/video/text-to-video"},
            )

        async def reference_mode_video(self, **kwargs):
            calls.append(("reference_mode_video", kwargs))
            return GenerationResult(
                ok=False,
                error="r2v is unsupported",
                raw={"endpoint": "/api/video/reference-mode-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)
    creation_contract = {
        "version": 1,
        "intent": "video",
        "video_duration_sec": 4,
        "video_ratio": "9:16",
        "video_model_mode": "manual",
        "video_model": "seedance-1.5",
        "video_model_capabilities": {
            "generation_types": ["首尾帧", "文生视频"],
            "upload_file_types": ["JPG", "JPEG", "PNG"],
        },
        "video_size": "1080p",
        "video_sound": "on",
        "image_model": "gpt-image-2",
        "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
        "video_usage": "产品介绍",
        "visual_style": "真实UGC摄影风",
        "confirmed_by_user": True,
    }

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 4000,
                        "prompt": "使用 Seedance 镜头规则生成雨天通勤片段",
                        "image_urls": ["https://x/character.png", "https://x/scene.png", "https://x/prop.png"],
                    }
                ],
                "creation_contract": creation_contract,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["scene_videos"][0]["mode"] == "text_to_video"
    assert data["scene_videos"][0]["endpoint"] == "/api/video/text-to-video"
    assert [name for name, _kwargs in calls] == ["text_to_video"]
    assert calls[0][1]["model"] == "seedance-1.5"


def test_generate_scene_videos_old_contract_recovers_once_after_vendor_rejects_r2v(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[str] = []

    class FakeVideoSkill:
        async def text_to_video(self, **_kwargs):
            calls.append("text_to_video")
            return GenerationResult(
                ok=True,
                task_id="text-task",
                url="https://x/text.mp4",
                raw={"endpoint": "/api/video/text-to-video"},
            )

        async def reference_mode_video(self, **_kwargs):
            calls.append("reference_mode_video")
            return GenerationResult(
                ok=False,
                error="The parameter task_type r2v does not support model current-model",
                raw={"endpoint": "/api/video/reference-mode-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)
    old_contract = {
        "video_duration_sec": 4,
        "video_ratio": "9:16",
        "video_model_mode": "manual",
        "video_model": "seedance-legacy",
        "video_size": "1080p",
        "video_sound": "on",
        "image_model": "gpt-image-2",
        "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
        "video_usage": "产品介绍",
    }

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-legacy",
                        "scene_index": 1,
                        "duration_ms": 4000,
                        "prompt": "旧对话自动场景",
                        "image_urls": ["https://x/reference.png"],
                    }
                ],
                "creation_contract": old_contract,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["scene_videos"][0]["mode"] == "text_to_video"
    assert calls == ["reference_mode_video", "text_to_video"]


@pytest.mark.parametrize(
    ("model", "generation_types", "expected_mode"),
    [
        ("seedance-model-a", ["文生视频", "首尾帧", "全能参考"], "reference_mode_video"),
        ("seedance-model-b", ["文生视频", "首尾帧"], "text_to_video"),
    ],
)
def test_scene_video_auto_mode_uses_realtime_capabilities_not_model_name(model, generation_types, expected_mode):
    from app.gateway.routers.pixelflow_video import SceneGenerationItem, _select_scene_video_mode
    from pixelflow.creative.contract import VideoCreationContract

    contract = VideoCreationContract.model_validate(
        {
            "video_duration_sec": 4,
            "video_ratio": "9:16",
            "video_model": model,
            "video_model_capabilities": {"generation_types": generation_types},
            "image_model": "gpt-image-2",
            "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
            "video_usage": "产品介绍",
        }
    )
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=4000,
        prompt="自动场景",
        image_urls=["https://x/character.png", "https://x/scene.png"],
    )

    assert _select_scene_video_mode(scene, scene.image_urls, creation_contract=contract) == expected_mode


def test_scene_video_prompt_keeps_authoritative_plan_transition() -> None:
    from app.gateway.routers.pixelflow_video import SceneGenerationItem, _build_scene_video_prompt

    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=6000,
        prompt="雨水冲突开场",
        storyline="雨滴落在背包表面。",
        shot_description={"text": "0-6秒: 镜头推近背包材质。"},
        narration="下雨最怕包里一起遭殃。",
        transition="顺着水滴运动方向切到拉链特写。",
    )

    prompt = _build_scene_video_prompt(scene)

    assert "转场：顺着水滴运动方向切到拉链特写。" in prompt


def test_scene_video_prompt_uses_structured_fields_once_in_fixed_order() -> None:
    from app.gateway.routers.pixelflow_video import SceneGenerationItem, _build_scene_video_prompt

    shot_description = (
        "0-6秒：地点：地铁口；主体：通勤者；动作：抬起背包；景别：中景；"
        "运镜：缓慢推进；光影：清晨逆光；声音：雨声；收束：定格品牌标识。"
    )
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=6000,
        prompt=(
            "故事线：雨滴落在背包表面。\n"
            "镜头描述：旧镜头。\n"
            "视觉风格：电影写实。\n"
            "旁白：旧旁白。"
        ),
        storyline="雨滴落在背包表面。",
        shot_description={"text": shot_description},
        narration="下雨也能从容通勤。",
        transition="顺着雨滴方向切到拉链特写。",
    )

    prompt = _build_scene_video_prompt(scene, visual_style="高级电影写实")

    assert prompt.splitlines() == [
        "视觉风格：高级电影写实",
        "故事线：雨滴落在背包表面。",
        f"镜头描述：{shot_description}",
        "旁白：下雨也能从容通勤。",
        "转场：顺着雨滴方向切到拉链特写。",
    ]
    assert prompt.count("雨滴落在背包表面。") == 1
    for required_dimension in ("地点", "主体", "动作", "景别", "运镜", "光影", "声音", "收束"):
        assert required_dimension in prompt


def test_scene_video_prompt_extracts_only_visual_style_from_legacy_prompt() -> None:
    from app.gateway.routers.pixelflow_video import SceneGenerationItem, _build_scene_video_prompt

    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=4000,
        prompt="故事线：旧故事；镜头描述：旧镜头；视觉风格：冷调写实；旁白：旧旁白",
        storyline="新故事",
        shot_description={
            "text": (
                "镜头描述：地点：实验室；主体：研究员；动作：观察样本；景别：近景；"
                "运镜：固定；光影：冷白光；声音：仪器声；收束：样本进入焦点。"
            )
        },
        narration="旁白：观察微观变化。",
        transition="转场：淡出。",
    )

    prompt = _build_scene_video_prompt(scene)

    assert prompt.startswith("视觉风格：冷调写实")
    assert "旧故事" not in prompt
    assert "旧镜头" not in prompt
    assert "旧旁白" not in prompt
    assert "镜头描述：镜头描述：" not in prompt
    assert "旁白：旁白：" not in prompt
    assert "转场：转场：" not in prompt


def test_long_scene_video_prompt_reaches_skill(monkeypatch) -> None:
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[dict] = []

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            calls.append(kwargs)
            return GenerationResult(
                ok=True,
                task_id="long-prompt-task",
                url="https://x/long-prompt.mp4",
                raw={"endpoint": "/api/video/text-to-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)
    long_shot = (
        "地点：演播室；主体：产品；动作：旋转展示；景别：近景；运镜：环绕；"
        "光影：轮廓光；声音：节奏音乐；收束：品牌标识定格。"
    ) * 50

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 5000,
                        "prompt": "视觉风格：高级广告摄影",
                        "storyline": "产品在演播室中完成完整展示。",
                        "shot_description": {"text": long_shot},
                        "narration": "看见产品的每一处细节。",
                        "transition": "在品牌标识定格后淡出。",
                    }
                ],
                "model": "seedance-2.0",
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(calls) == 1
    assert len(calls[0]["prompt"]) > 2500
    assert long_shot in calls[0]["prompt"]


@pytest.mark.parametrize(
    ("generation_mode", "generation_types", "image_urls", "video_urls"),
    [
        ("reference_mode_video", ["文生视频"], ["https://x/ref.png"], []),
        ("image_to_video", ["首尾帧"], ["https://x/first.png"], []),
        ("edit_video", ["文生视频"], ["https://x/ref.png"], ["https://x/source.mp4"]),
        ("extend_video", ["文生视频"], [], ["https://x/source.mp4"]),
        (None, ["首尾帧"], ["https://x/asset-a.png", "https://x/asset-b.png"], []),
    ],
)
def test_scene_video_capability_mismatch_never_silently_changes_explicit_or_asset_semantics(
    generation_mode,
    generation_types,
    image_urls,
    video_urls,
):
    from app.gateway.routers.pixelflow_video import (
        SceneGenerationItem,
        SceneVideoCapabilityError,
        _select_scene_video_mode,
    )
    from pixelflow.creative.contract import VideoCreationContract

    contract = VideoCreationContract.model_validate(
        {
            "video_duration_sec": 4,
            "video_ratio": "9:16",
            "video_model": "seedance-capability-driven",
            "video_model_capabilities": {"generation_types": generation_types},
            "image_model": "gpt-image-2",
            "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
            "video_usage": "产品介绍",
        }
    )
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=4000,
        prompt="能力不匹配场景",
        generation_mode=generation_mode,
        image_urls=image_urls,
        video_urls=video_urls,
    )

    with pytest.raises(SceneVideoCapabilityError):
        _select_scene_video_mode(scene, scene.image_urls, creation_contract=contract)


def test_scene_video_two_image_mode_only_uses_explicit_first_last_frame_capability():
    from app.gateway.routers.pixelflow_video import SceneGenerationItem, _select_scene_video_mode
    from pixelflow.creative.contract import VideoCreationContract

    contract = VideoCreationContract.model_validate(
        {
            "video_duration_sec": 4,
            "video_ratio": "9:16",
            "video_model": "seedance-capability-driven",
            "video_model_capabilities": {"generation_types": ["首尾帧"]},
            "image_model": "gpt-image-2",
            "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
            "video_usage": "产品介绍",
        }
    )
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=4000,
        prompt="明确首尾帧",
        generation_mode="two_image_to_video",
        image_urls=["https://x/first.png", "https://x/last.png"],
    )

    assert _select_scene_video_mode(scene, scene.image_urls, creation_contract=contract) == "two_image_to_video"


@pytest.mark.parametrize("prompt", ["编辑已有视频节奏", "延伸已有视频结尾"])
def test_implicit_edit_or_extend_never_silently_degrades_to_text(prompt):
    from app.gateway.routers.pixelflow_video import (
        SceneGenerationItem,
        SceneVideoCapabilityError,
        _select_scene_video_mode,
    )
    from pixelflow.creative.contract import VideoCreationContract

    contract = VideoCreationContract.model_validate(
        {
            "video_duration_sec": 4,
            "video_ratio": "9:16",
            "video_model": "seedance-capability-driven",
            "video_model_capabilities": {"generation_types": ["文生视频"]},
            "image_model": "gpt-image-2",
            "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
            "video_usage": "产品介绍",
        }
    )
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=4000,
        prompt=prompt,
        video_urls=["https://x/source.mp4"],
    )

    with pytest.raises(SceneVideoCapabilityError):
        _select_scene_video_mode(scene, [], creation_contract=contract)


def test_known_reference_only_capability_does_not_force_unsupported_text_fallback(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[str] = []

    class FakeVideoSkill:
        async def reference_mode_video(self, **_kwargs):
            calls.append("reference_mode_video")
            return GenerationResult(
                ok=False,
                error="The parameter task_type r2v does not support model current-model",
                raw={"endpoint": "/api/video/reference-mode-video"},
            )

        async def text_to_video(self, **_kwargs):
            calls.append("text_to_video")
            return GenerationResult(ok=True, url="https://x/text.mp4", raw={"endpoint": "/api/video/text-to-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)
    contract = {
        "video_duration_sec": 4,
        "video_ratio": "9:16",
        "video_model": "seedance-capability-driven",
        "video_model_capabilities": {"generation_types": ["全能参考"]},
        "video_size": "1080p",
        "video_sound": "on",
        "image_model": "gpt-image-2",
        "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
        "video_usage": "产品介绍",
    }

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 4000,
                        "prompt": "全能参考场景",
                        "image_urls": ["https://x/reference.png"],
                    }
                ],
                "creation_contract": contract,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["failed_scenes"][0]["attempts"] == 1
    assert calls == ["reference_mode_video"]


def test_i2v_alias_never_claims_first_last_frame_capability():
    from app.gateway.routers.pixelflow_video import (
        SceneGenerationItem,
        SceneVideoCapabilityError,
        _select_scene_video_mode,
    )
    from pixelflow.creative.contract import VideoCreationContract

    contract = VideoCreationContract.model_validate(
        {
            "video_duration_sec": 4,
            "video_ratio": "9:16",
            "video_model": "seedance-capability-driven",
            "video_model_capabilities": {"generation_types": ["i2v"]},
            "image_model": "gpt-image-2",
            "image_model_capabilities": {"aspect_ratios": ["9:16"], "sizes": ["4K"]},
            "video_usage": "产品介绍",
        }
    )
    scene = SceneGenerationItem(
        scene_id="scene-1",
        scene_index=1,
        duration_ms=4000,
        prompt="明确首尾帧",
        generation_mode="two_image_to_video",
        image_urls=["https://x/first.png", "https://x/last.png"],
    )

    with pytest.raises(SceneVideoCapabilityError):
        _select_scene_video_mode(scene, scene.image_urls, creation_contract=contract)


def test_video_router_preserves_exact_scene_duration_for_seedance(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls: list[dict] = []

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            calls.append(kwargs)
            return GenerationResult(
                ok=True,
                task_id=f"task-{kwargs['duration']}",
                url=f"https://x/{kwargs['duration']}.mp4",
                raw={"endpoint": "/api/video/text-to-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/generate-scenes",
            json={
                "scenes": [
                    {"scene_id": "scene-1", "scene_index": 1, "duration_ms": 4000, "prompt": "4秒业务片段", "generation_mode": "text_to_video"},
                    {"scene_id": "scene-2", "scene_index": 2, "duration_ms": 15000, "prompt": "15秒业务片段", "generation_mode": "text_to_video"},
                ]
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert [call["duration"] for call in calls] == [4, 15]
    assert [scene["duration_ms"] for scene in data["scene_videos"]] == [4000, 15000]


def test_video_router_starts_scene_video_job_and_polls_result(monkeypatch):
    import time

    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    class FakeVideoSkill:
        async def reference_mode_video(self, **kwargs):
            return GenerationResult(
                ok=True,
                task_id=f"{kwargs['prompt']}-task",
                url=f"https://x/{kwargs['prompt']}.mp4",
                raw={"endpoint": "/api/video/reference-mode-video"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/video/generate-scenes/start",
            json={
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "duration_ms": 8000,
                        "prompt": "第一幕展示白色耳机",
                        "image_urls": ["https://x/role.png"],
                    }
                ],
                "ratio": "9:16",
                "size": "720p",
                "sound": "on",
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()
        assert started["ok"] is True
        assert started["status"] in {"queued", "running"}
        assert started["job_id"]

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/video/generate-scenes/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "completed":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "completed"
    assert status["result"]["ok"] is True
    assert status["result"]["scene_videos"][0]["video_url"] == "https://x/第一幕展示白色耳机.mp4"


def test_video_router_merges_scene_videos_in_scene_order(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    class FakeVideoSkill:
        async def merge_videos(self, **kwargs):
            assert kwargs["video_urls"] == ["https://x/scene-1.mp4", "https://x/scene-2.mp4"]
            assert kwargs["duration"] == 16
            return GenerationResult(
                ok=True,
                task_id="merge-task-1",
                url="https://x/merged.mp4",
                raw={"endpoint": "/api/video/merge"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/merge",
            json={
                "scene_videos": [
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                ],
                "duration": 16,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/video/merge"
    assert data["merged_video_url"] == "https://x/merged.mp4"


def test_video_router_starts_merge_job_and_polls_result(monkeypatch):
    import time

    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    class FakeVideoSkill:
        async def merge_videos(self, **kwargs):
            assert kwargs["video_urls"] == ["https://x/scene-1.mp4", "https://x/scene-2.mp4"]
            await asyncio.sleep(0.01)
            return GenerationResult(
                ok=True,
                task_id="merge-task-async",
                url="https://x/merged-async.mp4",
                raw={"endpoint": "/api/video/merge"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/video/merge/start",
            json={
                "scene_videos": [
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                ],
                "duration": 16,
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()
        assert started["ok"] is True
        assert started["status"] in {"queued", "running"}
        assert started["job_id"]

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/video/merge/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "completed":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "completed"
    assert status["result"]["ok"] is True
    assert status["result"]["merged_video_url"] == "https://x/merged-async.mp4"


def test_video_router_marks_failed_merge_job_and_preserves_vendor_error(monkeypatch):
    import time

    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    class FakeVideoSkill:
        async def merge_videos(self, **kwargs):
            assert kwargs["video_urls"] == ["https://x/scene-1.mp4", "https://x/scene-2.mp4"]
            await asyncio.sleep(0.01)
            return GenerationResult(
                ok=False,
                error="视频合并失败: 下载分镜视频超时",
                raw={
                    "endpoint": "/api/video/merge",
                    "status_code": 500,
                    "message": "视频合并失败: 下载分镜视频超时",
                },
            )

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/video/merge/start",
            json={
                "scene_videos": [
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                ],
                "duration": 16,
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/video/merge/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "failed":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "failed"
    assert status["ok"] is False
    assert status["error"] == "视频合并失败: 下载分镜视频超时"
    assert status["message"] == "视频合并失败: 下载分镜视频超时"
    assert status["result"]["ok"] is False
    assert status["result"]["message"] == "视频合并失败: 下载分镜视频超时"
    assert status["result"]["raw"]["status_code"] == 500


def test_video_router_returns_single_scene_video_as_merged_video_without_calling_merge(monkeypatch):
    from app.gateway.routers import pixelflow_video

    class FakeVideoSkill:
        async def merge_videos(self, **_kwargs):
            raise AssertionError("single-scene video should not call content-app merge")

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/merge",
            json={
                "scene_videos": [
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                ],
                "duration": 8,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["merged_video_url"] == "https://x/scene-1.mp4"
    assert data["scene_videos"] == [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}]
    assert data["raw"]["passthrough"] is True


def test_video_router_generates_direct_video_modes(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    calls = []

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            calls.append(("text_to_video", kwargs))
            return GenerationResult(ok=True, task_id="text-task", url="https://x/text.mp4", raw={"endpoint": "/api/video/text-to-video"})

        async def image_to_video(self, **kwargs):
            calls.append(("image_to_video", kwargs))
            return GenerationResult(ok=True, task_id="image-task", url="https://x/image.mp4", raw={"endpoint": "/api/video/image-to-video"})

        async def two_image_to_video(self, **kwargs):
            calls.append(("two_image_to_video", kwargs))
            return GenerationResult(ok=True, task_id="two-image-task", url="https://x/two-image.mp4", raw={"endpoint": "/api/video/two-image-to-video"})

        async def reference_mode_video(self, **kwargs):
            calls.append(("reference_mode_video", kwargs))
            return GenerationResult(ok=True, task_id="ref-task", url="https://x/ref.mp4", raw={"endpoint": "/api/video/reference-mode-video"})

        async def edit_video(self, **kwargs):
            calls.append(("edit_video", kwargs))
            return GenerationResult(ok=True, task_id="edit-task", url="https://x/edit.mp4", raw={"endpoint": "/api/video/edit-video"})

        async def extend_video(self, **kwargs):
            calls.append(("extend_video", kwargs))
            return GenerationResult(ok=True, task_id="extend-task", url="https://x/extend.mp4", raw={"endpoint": "/api/video/extend-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    requests = [
        ("text_to_video", {"prompt": "文生视频", "duration": 5}, "/api/video/text-to-video", "https://x/text.mp4"),
        ("image_to_video", {"prompt": "首帧动起来", "image_url": "https://x/first.png", "duration": 5}, "/api/video/image-to-video", "https://x/image.mp4"),
        (
            "two_image_to_video",
            {"prompt": "首尾帧过渡", "first_frame_image_url": "https://x/first.png", "last_frame_image_url": "https://x/last.png", "duration": 5},
            "/api/video/two-image-to-video",
            "https://x/two-image.mp4",
        ),
        (
            "reference_mode_video",
            {"prompt": "参考素材生成", "image_urls": ["https://x/a.png"], "video_urls": ["https://x/a.mp4"], "duration": 5},
            "/api/video/reference-mode-video",
            "https://x/ref.mp4",
        ),
        ("edit_video", {"prompt": "改成快节奏", "ref_video": "https://x/source.mp4", "ref_image": "https://x/ref.png", "duration": 5}, "/api/video/edit-video", "https://x/edit.mp4"),
        ("extend_video", {"prompt": "继续延伸产品展示", "video_url": "https://x/source.mp4", "duration": 5}, "/api/video/extend-video", "https://x/extend.mp4"),
    ]

    with TestClient(app) as client:
        for mode, payload, endpoint, expected_url in requests:
            response = client.post("/agent/flows/video/generate-direct", json={"mode": mode, **payload})
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["endpoint"] == endpoint
            assert data["video_url"] == expected_url

    assert [name for name, _kwargs in calls] == [request[0] for request in requests]
    assert calls[0][1]["prompt"] == "文生视频"
    assert calls[2][1]["first_frame_image_url"] == "https://x/first.png"
    assert calls[4][1]["ref_video"] == "https://x/source.mp4"


def test_video_router_starts_direct_video_job_and_polls_result(monkeypatch):
    import time

    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import GenerationResult

    class FakeVideoSkill:
        async def text_to_video(self, **kwargs):
            return GenerationResult(ok=True, task_id="text-task", url="https://x/text.mp4", raw={"endpoint": "/api/video/text-to-video"})

    monkeypatch.setattr(pixelflow_video, "get_video_skill", lambda: FakeVideoSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post("/agent/flows/video/generate-direct/start", json={"mode": "text_to_video", "prompt": "文生视频", "duration": 5})
        assert start_response.status_code == 200
        started = start_response.json()
        assert started["ok"] is True
        assert started["status"] in {"queued", "running"}
        assert started["job_id"]

        status = None
        for _ in range(20):
            status_response = client.get(f"/agent/flows/video/generate-direct/jobs/{started['job_id']}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] == "completed":
                break
            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "completed"
    assert status["result"]["ok"] is True
    assert status["result"]["video_url"] == "https://x/text.mp4"


def test_video_router_reviews_video_quality(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import VideoQualityReviewResult

    class FakeVideoQualitySkill:
        async def review_video_quality(self, **kwargs):
            assert kwargs["merged_video_url"] == "https://x/merged.mp4"
            assert kwargs["scene_videos"] == [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}]
            assert kwargs["scene_packages"] == [{"scene_id": "scene-1", "storyline": "白色耳机展示"}]
            assert kwargs["checks"] == ["plan_consistency", "playback_stability"]
            assert kwargs["platform"] == "douyin"
            assert kwargs["ratio"] == "9:16"
            assert kwargs["size"] == "1080x1920"
            return VideoQualityReviewResult(
                ok=True,
                task_id="qc-task-1",
                summary_markdown="检测到黑屏",
                issues=[
                    {
                        "code": "black_screen",
                        "category": "playback_stability",
                        "severity": "blocker",
                        "scene_id": "scene-1",
                        "message": "检测到连续黑屏片段",
                    }
                ],
                affected_scene_ids=["scene-1"],
                revision_prompt="重生成 scene-1",
                quality_report_markdown="检测到黑屏",
                raw={"endpoint": "/api/creative/video_quality_review"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_quality_review_skill", lambda: FakeVideoQualitySkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/quality-review",
            json={
                "merged_video_url": "https://x/merged.mp4",
                "scene_videos": [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
                "scene_packages": [{"scene_id": "scene-1", "storyline": "白色耳机展示"}],
                "checks": ["plan_consistency", "playback_stability"],
                "platform": "douyin",
                "ratio": "9:16",
                "size": "1080x1920",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["passed"] is False
    assert data["task_id"] == "qc-task-1"
    assert data["summary_markdown"] == "检测到黑屏"
    assert data["quality_report_markdown"] == "检测到黑屏"
    assert data["affected_scene_ids"] == ["scene-1"]
    assert any(item["item"] == "播放稳定性" and item["status"] == "fail" for item in data["check_results"])


def test_video_router_starts_quality_review_job_and_polls_result(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import VideoQualityReviewResult

    class FakeVideoQualitySkill:
        async def review_video_quality(self, **kwargs):
            await asyncio.sleep(0.01)
            assert kwargs["merged_video_url"] == "https://x/merged.mp4"
            return VideoQualityReviewResult(
                ok=True,
                task_id="qc-job-task-1",
                summary_markdown="QAAgent QC 已完成",
                quality_report_markdown="QAAgent QC 已完成",
                raw={"endpoint": "/api/creative/video_quality_review"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_quality_review_skill", lambda: FakeVideoQualitySkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/video/quality-review/start",
            json={
                "merged_video_url": "https://x/merged.mp4",
                "scene_videos": [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()
        assert started["ok"] is True
        assert started["status"] == "running"
        assert started["job_id"]

        status = None
        for _ in range(20):
            poll_response = client.get(f"/agent/flows/video/quality-review/jobs/{started['job_id']}")
            assert poll_response.status_code == 200
            status = poll_response.json()
            if status["status"] == "completed":
                break
            import time

            time.sleep(0.02)

    assert status is not None
    assert status["status"] == "completed"
    assert status["result"]["ok"] is True
    assert status["result"]["task_id"] == "qc-job-task-1"


def test_video_router_marks_failed_quality_review_job_and_preserves_result(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import VideoQualityReviewResult

    class FakeVideoQualitySkill:
        async def review_video_quality(self, **_kwargs):
            await asyncio.sleep(0.01)
            return VideoQualityReviewResult(
                ok=False,
                task_id="qc-job-task-failed",
                error="request body exceeds 50 MB: request body too large",
                summary_markdown="request body exceeds 50 MB: request body too large",
                quality_report_markdown="request body exceeds 50 MB: request body too large",
                raw={
                    "status": "FAILED",
                    "message": "request body exceeds 50 MB: request body too large",
                    "details": {"code": "read_request_body_failed"},
                },
            )

    monkeypatch.setattr(pixelflow_video, "get_video_quality_review_skill", lambda: FakeVideoQualitySkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        start_response = client.post(
            "/agent/flows/video/quality-review/start",
            json={
                "merged_video_url": "https://x/merged.mp4",
                "scene_videos": [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
            },
        )
        assert start_response.status_code == 200
        started = start_response.json()

        status = None
        for _ in range(20):
            poll_response = client.get(f"/agent/flows/video/quality-review/jobs/{started['job_id']}")
            assert poll_response.status_code == 200
            status = poll_response.json()
            if status["status"] == "failed":
                break
            import time

            time.sleep(0.02)

    assert status is not None
    assert status["ok"] is False
    assert status["status"] == "failed"
    assert "request body exceeds 50 MB" in status["error"]
    assert status["result"]["ok"] is False
    assert status["result"]["task_id"] == "qc-job-task-failed"
    assert status["result"]["raw"]["details"]["code"] == "read_request_body_failed"


def test_video_router_analyzes_single_storyboard_from_extracted_link(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import MediaLinkExtractionResult, StoryboardResult

    class FakeMediaLinkSkill:
        async def extract_media_links(self, **kwargs):
            assert kwargs["text"] == "请分析这个视频 https://x/one.mp4"
            return MediaLinkExtractionResult(
                ok=True,
                links=["https://x/one.mp4"],
                raw={"endpoint": "/api/creative/extractMediaLinks"},
            )

    class FakeVideoDecomposeSkill:
        async def decompose_video_to_storyboard(self, video_url):
            assert video_url == "https://x/one.mp4"
            return StoryboardResult(ok=True, shots=[{"visual_description": "产品开箱"}], raw={"task_id": "single-task"})

    monkeypatch.setattr(pixelflow_video, "get_media_link_extraction_skill", lambda: FakeMediaLinkSkill())
    monkeypatch.setattr(pixelflow_video, "get_video_decompose_skill", lambda: FakeVideoDecomposeSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/analyze-storyboards",
            json={"prompt": "请分析这个视频 https://x/one.mp4"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["mode"] == "single"
    assert data["endpoint"] == "/api/creative/decompose_video_to_storyboard"
    assert data["video_urls"] == ["https://x/one.mp4"]
    assert data["storyboards"] == [{"video_url": "https://x/one.mp4", "shots": [{"visual_description": "产品开箱"}]}]


def test_video_router_analyzes_batch_storyboards_from_material_urls(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import BatchStoryboardResult, MediaLinkExtractionResult

    class FakeMediaLinkSkill:
        async def extract_media_links(self, **kwargs):
            assert "https://x/one.mp4" in kwargs["text"]
            assert "https://x/two.mov" in kwargs["text"]
            return MediaLinkExtractionResult(
                ok=True,
                links=["https://x/one.mp4", "https://x/two.mov"],
                raw={"endpoint": "/api/creative/extractMediaLinks"},
            )

    class FakeVideoDecomposeSkill:
        async def batch_decompose_video_to_storyboard(self, video_urls):
            assert video_urls == ["https://x/one.mp4", "https://x/two.mov"]
            return BatchStoryboardResult(
                ok=True,
                storyboards=[
                    {"video_url": "https://x/one.mp4", "analysis_markdown": "one"},
                    {"video_url": "https://x/two.mov", "analysis_markdown": "two"},
                ],
                raw={"task_id": "batch-task"},
            )

    monkeypatch.setattr(pixelflow_video, "get_media_link_extraction_skill", lambda: FakeMediaLinkSkill())
    monkeypatch.setattr(pixelflow_video, "get_video_decompose_skill", lambda: FakeVideoDecomposeSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/analyze-storyboards",
            json={
                "prompt": "分析素材",
                "materials": [
                    {"url": "https://x/one.mp4"},
                    {"video_url": "https://x/two.mov"},
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["mode"] == "batch"
    assert data["endpoint"] == "/api/creative/batch_decompose_video_to_storyboard"
    assert data["video_urls"] == ["https://x/one.mp4", "https://x/two.mov"]
    assert data["storyboards"][1]["analysis_markdown"] == "two"
