from __future__ import annotations

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
    assert "/agent/flows/video/generate-scene-assets" in paths
    assert "/agent/flows/video/generate-scenes" in paths
    assert "/agent/flows/video/generate-scenes/start" in paths
    assert "/agent/flows/video/generate-scenes/jobs/{job_id}" in paths
    assert "/agent/flows/video/generate-direct" in paths
    assert "/agent/flows/video/generate-direct/start" in paths
    assert "/agent/flows/video/generate-direct/jobs/{job_id}" in paths
    assert "/agent/flows/video/merge" in paths
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
    assert data["scene_packages"][0]["scene_id"] == "scene-1"
    assert data["scene_packages"][0]["duration_ms"] <= 10_000
    assert "苹果降噪耳机 Pro" in data["scene_packages"][0]["prompt"]


def test_video_router_generates_scene_asset_images(monkeypatch):
    from app.gateway.routers import pixelflow_video
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            prompt = kwargs["prompt"]
            assert kwargs["size"] == "1080p"
            if "三视图" in prompt:
                assert kwargs["ratio"] == "1:1"
                return ImageGenerationResult(ok=True, images=[{"url": "https://x/three-view.png"}], raw={"endpoint": "/api/picture/text_to_image"})
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
                "scene_packages": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "characters": [{"name": "讲解者", "three_view_prompt": "角色三视图"}],
                        "scene_images": [{"description": "桌面场景", "image_prompt": "桌面场景图"}],
                        "prop_images": [{"name": "耳机", "image_prompt": "耳机道具图"}],
                    }
                ]
            },
        )

    assert response.status_code == 200
    data = response.json()
    scene = data["scene_packages"][0]
    assert data["ok"] is True
    assert data["endpoint"] == "/api/picture/text_to_image"
    assert scene["characters"][0]["three_view_images"] == ["https://x/three-view.png"]
    assert scene["scene_images"][0]["images"] == ["https://x/scene.png"]
    assert scene["prop_images"][0]["images"] == ["https://x/prop.png"]


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
    from pixelflow.skills import VideoFlawAnalysisResult

    class FakeVideoFlawSkill:
        async def analyze_video_flaws(self, **kwargs):
            assert kwargs["merged_video_url"] == "https://x/merged.mp4"
            assert kwargs["scene_videos"] == [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}]
            assert kwargs["scene_packages"] == [{"scene_id": "scene-1", "storyline": "白色耳机展示"}]
            assert kwargs["user_feedback"] == "耳机颜色不一致"
            return VideoFlawAnalysisResult(
                ok=True,
                task_id="flaw-task-1",
                flaw_analysis_markdown="scene-1 存在颜色穿帮",
                issues=[{"scene_id": "scene-1", "current": "黑色", "expected": "白色"}],
                affected_scene_ids=["scene-1"],
                revision_prompt="保持白色耳机",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    monkeypatch.setattr(pixelflow_video, "get_video_flaw_analysis_skill", lambda: FakeVideoFlawSkill())

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
    assert data["issues"][0]["expected"] == "白色"


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
