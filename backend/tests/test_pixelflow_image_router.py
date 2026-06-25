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


def test_image_router_rejects_unavailable_multi_image_fusion(monkeypatch):
    from app.gateway.routers import pixelflow_image

    def fail_if_called():
        raise AssertionError("multi_image_fusion must not call the current image skill")

    monkeypatch.setattr(pixelflow_image, "get_image_skill", fail_if_called)

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_image.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/image/generate",
            json={
                "method": "multi_image_fusion",
                "prompt": "融合两张图",
                "params": {"reference_image_urls": ["https://x/a.png", "https://x/b.png"]},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["endpoint"] == "/api/picture/multi_image_fusion"
    assert "未接入" in data["error"]
