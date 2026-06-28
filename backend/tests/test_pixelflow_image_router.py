from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from tests._router_auth_helpers import make_authed_test_app


def test_pixelflow_image_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_image

    paths = {route.path for route in pixelflow_image.router.routes}
    assert pixelflow_image.router.prefix == "/agent/flows/image"
    assert "/agent/flows/image/prepare" in paths
    assert "/agent/flows/image/generate" in paths


def _stable_user() -> User:
    return User(
        email="pixelflow-image@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000901"),
    )


def test_image_router_prepares_text_to_image_contract():
    from app.gateway.routers import pixelflow_image

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/prepare",
            json={
                "form_values": {
                    "image_goal": "科技感耳机海报",
                    "image_style": "科技感",
                    "image_size": "9:16 竖版海报",
                },
                "plan_markdown": "## 一、选题方向\n图片生成方案",
                "selected_direction": {"title": "核心卖点海报", "description": "突出金属质感"},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/picture/text_to_image"
    assert data["method"] == "text_to_image"
    assert data["params"]["ratio"] == "9:16"
    assert "科技感耳机海报" in data["prompt"]


def test_image_router_prepares_prompt_from_complete_intake_context():
    from app.gateway.routers import pixelflow_image

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/prepare",
            json={
                "form_values": {
                    "image_goal": "宣传图",
                    "image_type": "海报/封面图",
                    "image_usage": "社媒发布",
                    "image_style": "真实摄影",
                    "image_size": "自动适配",
                },
                "plan_markdown": "## 一、选题方向\n图片生成方案",
                "selected_direction": {"title": "通学收纳主视觉", "description": "突出容量和护脊"},
                "intake_context": {
                    "source_prompt": "帮我生成3张书包的宣传图",
                    "product_subject": "书包",
                    "creation_goal": "书包宣传图",
                    "industry_type": "服饰鞋包",
                    "requested_output_count": 3,
                    "product_creative_profile": {
                        "core_message": "儿童通学场景里的轻量护脊书包",
                        "visual_anchor_keywords": ["通学路", "收纳分区", "护脊背负"],
                    },
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["params"]["num_images"] == 3
    assert "图片目标：书包宣传图" in data["prompt"]
    assert "产品主体：书包" in data["prompt"]
    assert "儿童通学场景里的轻量护脊书包" in data["prompt"]


def test_image_router_prepares_image_edit_from_camel_case_material():
    from app.gateway.routers import pixelflow_image

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/prepare",
            json={
                "form_values": {
                    "image_goal": "修改图片，换成科技蓝背景",
                    "image_size": "9:16 竖版海报",
                },
                "plan_markdown": "## 图片编辑",
                "selected_direction": {"title": "改图", "description": "基于上传素材编辑"},
                "materials": [{"imageUrl": "https://x/source.png", "mediaType": "image"}],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["method"] == "image_edit"
    assert data["endpoint"] == "/api/picture/image_edit"
    assert data["params"]["image_url"] == "https://x/source.png"
    assert data["params"]["width"] == 9
    assert data["params"]["height"] == 16
    assert data["params"]["max_images"] == 1


def test_image_router_generates_text_to_image(monkeypatch):
    from app.gateway.routers import pixelflow_image
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            assert kwargs["prompt"] == "生成商品主图"
            assert kwargs["ratio"] == "9:16"
            assert kwargs["size"] == "1080p"
            assert kwargs["num_images"] == 1
            return ImageGenerationResult(
                ok=True,
                task_id="img-task-1",
                images=[{"asset_id": "img-task-1-0", "url": "https://x/image.png", "download_url": "https://x/image.png"}],
                raw={"endpoint": "/api/picture/text_to_image"},
            )

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/generate",
            json={
                "method": "text_to_image",
                "prompt": "生成商品主图",
                "params": {"ratio": "9:16", "size": "1080p", "num_images": 1},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["task_id"] == "img-task-1"
    assert data["endpoint"] == "/api/picture/text_to_image"
    assert data["images"][0]["url"] == "https://x/image.png"


def test_image_router_generates_requested_multiple_images_by_repeated_calls(monkeypatch):
    from app.gateway.routers import pixelflow_image
    from pixelflow.skills import ImageGenerationResult

    calls: list[dict] = []

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["num_images"] == 1
            index = len(calls)
            return ImageGenerationResult(
                ok=True,
                task_id=f"img-task-{index}",
                images=[{"asset_id": f"img-{index}", "url": f"https://x/image-{index}.png"}],
                raw={"endpoint": "/api/picture/text_to_image", "task_id": f"img-task-{index}"},
            )

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/generate",
            json={
                "method": "text_to_image",
                "prompt": "帮我生成3张台球图片",
                "params": {"ratio": "1:1", "size": "1080p", "num_images": 3},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(calls) == 3
    assert data["task_id"] == "img-task-1"
    assert [image["url"] for image in data["images"]] == [
        "https://x/image-1.png",
        "https://x/image-2.png",
        "https://x/image-3.png",
    ]


def test_image_router_marks_quota_insufficient_generation(monkeypatch):
    from app.gateway.routers import pixelflow_image
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            return ImageGenerationResult(
                ok=False,
                error="额度不足，剩余额度: 0，需要: 1",
                raw={"quota_insufficient": True, "message": "额度不足，剩余额度: 0，需要: 1"},
            )

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/generate",
            json={
                "method": "text_to_image",
                "prompt": "生成商品主图",
                "params": {"ratio": "9:16", "size": "1080p", "num_images": 1},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["quota_insufficient"] is True
    assert "充值后" in data["message"]


def test_image_router_defaults_gpt_image_to_price_configured_quality(monkeypatch):
    from app.gateway.routers import pixelflow_image
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def text_to_image(self, **kwargs):
            assert kwargs["model"] == "gpt-image-2"
            assert kwargs["size"] == "4K"
            return ImageGenerationResult(ok=True, task_id="img-task-2", images=[], raw={"endpoint": "/api/picture/text_to_image"})

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/generate",
            json={"method": "text_to_image", "prompt": "生成商品主图", "params": {"ratio": "1:1", "model": "gpt-image-2"}},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_image_router_generates_image_edit_from_image_url_alias(monkeypatch):
    from app.gateway.routers import pixelflow_image
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def image_edit(self, **kwargs):
            assert kwargs == {
                "image_url": "https://x/source.png",
                "prompt": "换背景",
                "model": "gpt-image-2",
                "ratio": "9:16",
                "size": "4K",
                "max_images": 1,
            }
            return ImageGenerationResult(
                ok=True,
                task_id="edit-task-1",
                images=[{"asset_id": "edit-task-1-0", "url": "https://x/edited.png", "download_url": "https://x/edited.png"}],
                raw={"endpoint": "/api/picture/image_edit"},
            )

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/generate",
            json={
                "method": "image_edit",
                "prompt": "换背景",
                "params": {"imageUrl": "https://x/source.png", "model": "gpt-image-2", "width": 9, "height": 16},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/picture/image_edit"
    assert data["images"][0]["url"] == "https://x/edited.png"


def test_image_router_generates_multi_image_fusion(monkeypatch):
    from app.gateway.routers import pixelflow_image
    from pixelflow.skills import ImageGenerationResult

    class FakeImageSkill:
        async def multi_image_fusion(self, **kwargs):
            assert kwargs == {
                "image_urls": ["https://x/a.png", "https://x/b.png"],
                "prompt": "融合两张图",
                "ratio": "9:16",
                "size": "1080p",
                "model": "seeddream-5.0",
                "num_images": 1,
            }
            return ImageGenerationResult(
                ok=True,
                task_id="fusion-task-1",
                images=[{"asset_id": "fusion-task-1-0", "url": "https://x/fusion.png", "download_url": "https://x/fusion.png"}],
                raw={"endpoint": "/api/picture/multi_image_fusion"},
            )

    monkeypatch.setattr(pixelflow_image, "get_image_skill", lambda: FakeImageSkill())

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/generate",
            json={
                "method": "multi_image_fusion",
                "prompt": "融合两张图",
                "params": {"image_urls": ["https://x/a.png", "https://x/b.png"], "ratio": "9:16", "model": "seeddream-5.0"},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint"] == "/api/picture/multi_image_fusion"
    assert data["images"][0]["url"] == "https://x/fusion.png"
