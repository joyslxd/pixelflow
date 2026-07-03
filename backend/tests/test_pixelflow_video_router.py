from __future__ import annotations

import asyncio
from uuid import UUID

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
    assert "/agent/flows/video/quality-review" in paths
    assert "/agent/flows/video/analyze-flaws" in paths
    assert "/agent/flows/video/analyze-storyboards" in paths


def test_video_router_prepares_scene_packages():
    from app.gateway.routers import pixelflow_video

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
            assert kwargs["size"] == "1080p"
            if "角色三视图" in prompt:
                assert kwargs["ratio"] == "1:1"
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/role.png"}], raw={"endpoint": "/api/picture/text_to_image"})
            if "场景图" in prompt:
                assert kwargs["ratio"] == "9:16"
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/scene.png"}], raw={"endpoint": "/api/picture/text_to_image"})
            if "道具图" in prompt:
                assert kwargs["ratio"] == "1:1"
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/prop.png"}], raw={"endpoint": "/api/picture/text_to_image"})
            raise AssertionError(f"unexpected prompt: {prompt}")

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
                ]
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/picture/text_to_image"
    assert data["global_assets"]["characters"][0]["three_view_images"] == ["https://x/role.png"]
    assert data["global_assets"]["scenes"][0]["images"] == ["https://x/scene.png"]
    assert data["global_assets"]["props"][0]["images"] == ["https://x/prop.png"]


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
                            "mentions": [
                                {"asset_id": f"asset-{index}", "image_url": f"https://x/ref-{index}.png"}
                                for index in range(10)
                            ],
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


def test_video_router_clamps_scene_call_duration_to_seedance_single_call_range(monkeypatch):
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
    assert [call["duration"] for call in calls] == [5, 10]
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


def test_video_router_analyzes_flaws(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import VideoQualityReviewResult

    class FakeVideoQualitySkill:
        async def review_video_quality(self, **kwargs):
            assert kwargs["merged_video_url"] == "https://x/merged.mp4"
            assert kwargs["scene_videos"] == [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}]
            assert kwargs["scene_packages"] == [{"scene_id": "scene-1", "storyline": "白色耳机展示"}]
            assert kwargs["user_feedback"] == "耳机颜色不一致"
            assert kwargs["checks"] == ["product_consistency"]
            return VideoQualityReviewResult(
                ok=True,
                task_id="flaw-task-1",
                summary_markdown="scene-1 存在颜色穿帮",
                flaw_analysis_markdown="scene-1 存在颜色穿帮",
                issues=[
                    {"scene_id": "scene-1", "current": "黑色", "expected": "白色", "category": "product_consistency"},
                    {"scene_id": "scene-2", "message": "黑屏", "category": "playback_stability"},
                ],
                affected_scene_ids=["scene-1"],
                revision_prompt="保持白色耳机",
                raw={
                    "endpoint": "/api/creative/analyze_video_flaws",
                    "issues": [
                        {"scene_id": "scene-1", "current": "黑色", "expected": "白色", "category": "product_consistency"},
                        {"scene_id": "scene-2", "message": "黑屏", "category": "playback_stability"},
                    ],
                },
            )

    monkeypatch.setattr(pixelflow_video, "get_video_quality_review_skill", lambda: FakeVideoQualitySkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/analyze-flaws",
            json={
                "merged_video_url": "https://x/merged.mp4",
                "scene_videos": [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
                "scene_packages": [{"scene_id": "scene-1", "storyline": "白色耳机展示"}],
                "user_feedback": "耳机颜色不一致",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["task_id"] == "flaw-task-1"
    assert data["endpoint"] == "/api/creative/analyze_video_flaws"
    assert data["affected_scene_ids"] == ["scene-1"]
    assert data["issues"] == [{"scene_id": "scene-1", "current": "黑色", "expected": "白色", "category": "product_consistency"}]
    assert "code" not in data["issues"][0]
    assert "severity" not in data["issues"][0]
    assert data["passed"] is False
    assert "check_results" in data


def test_video_router_analyze_flaws_respects_explicit_only_scene_feedback(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import VideoQualityReviewResult

    class FakeVideoQualitySkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                task_id="flaw-task-only-scene-2",
                summary_markdown="多个分镜可能需要处理",
                flaw_analysis_markdown="多个分镜可能需要处理",
                issues=[
                    {"scene_id": "scene-1", "message": "第1个分镜也被供应商误判", "category": "product_consistency"},
                    {"scene_id": "scene-2", "message": "第2个分镜出现红色手机", "category": "product_consistency"},
                    {"scene_id": "scene-3", "message": "第3个分镜也被供应商误判", "category": "product_consistency"},
                ],
                affected_scene_ids=["scene-1", "scene-2", "scene-3"],
                revision_prompt="修复全部分镜",
                raw={
                    "endpoint": "/api/creative/analyze_video_flaws",
                    "issues": [
                        {"scene_id": "scene-1", "message": "第1个分镜也被供应商误判", "category": "product_consistency"},
                        {"scene_id": "scene-2", "message": "第2个分镜出现红色手机", "category": "product_consistency"},
                        {"scene_id": "scene-3", "message": "第3个分镜也被供应商误判", "category": "product_consistency"},
                    ],
                },
            )

    monkeypatch.setattr(pixelflow_video, "get_video_quality_review_skill", lambda: FakeVideoQualitySkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_video.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/video/analyze-flaws",
            json={
                "merged_video_url": "https://x/merged.mp4",
                "scene_videos": [
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-3", "scene_index": 3, "video_url": "https://x/scene-3.mp4"},
                ],
                "scene_packages": [
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "保温杯开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "保温杯卖点证明"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "保温杯收口"},
                ],
                "user_feedback": "第2个分镜画面出现红色手机，和保温杯产品无关。请只修复第2个分镜。第1个分镜和第3个分镜没有问题，不要重新生成。",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["affected_scene_ids"] == ["scene-2"]
    assert [issue["scene_id"] for issue in data["issues"]] == ["scene-2"]
    assert "第2个分镜" in data["revision_prompt"]
    assert "全部" not in data["revision_prompt"]


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
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
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
    assert data["flaw_analysis_markdown"] == "检测到黑屏"
    assert data["affected_scene_ids"] == ["scene-1"]
    assert any(item["item"] == "播放稳定性" and item["status"] == "fail" for item in data["check_results"])


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
