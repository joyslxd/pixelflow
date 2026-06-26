from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from tests._router_auth_helpers import make_authed_test_app


def test_pixelflow_intake_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_intake

    paths = {route.path for route in pixelflow_intake.router.routes}
    assert pixelflow_intake.router.prefix == "/agent/flows/intake"
    assert "/agent/flows/intake/forms/{intent}" in paths
    assert "/agent/flows/intake/analyze" in paths
    assert "/agent/flows/intake/validate" in paths
    assert "/agent/flows/intake/directions" in paths


def _stable_user() -> User:
    return User(
        email="pixelflow-intake@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000456"),
    )


def test_intake_router_validates_and_returns_three_directions():
    from app.gateway.routers import pixelflow_intake

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_intake.router)

    with TestClient(app) as client:
        schema = client.get("/agent/flows/intake/forms/video").json()
        assert schema["form_id"] == "ad_short_video_intake"
        assert [field["id"] for field in schema["fields"]] == ["product_info", "product_category", "target_audience", "conversion_goal"]

        invalid = client.post(
            "/agent/flows/intake/validate",
            json={"intent": "video", "values": {"product_info": ""}, "intake_rounds": 3},
        ).json()
        assert invalid["is_complete"] is False
        assert invalid["terminated"] is True
        assert invalid["creative_directions"] == []

        complete = client.post(
            "/agent/flows/intake/directions",
            json={
                "intent": "video",
                "values": {
                    "product_info": "苹果什么什么PRO",
                    "product_category": "数码3C",
                    "target_audience": "25-35",
                    "conversion_goal": "引流直播间",
                },
                "product_creative_profile": {"visual_anchor_keywords": ["通勤", "质感"]},
            },
        ).json()
        assert complete["validation"]["is_complete"] is True
        assert len(complete["creative_directions"]) == 3
        assert complete["creative_directions"][0]["recommended"] is True


def test_intake_router_accepts_generation_intent_aliases():
    from app.gateway.routers import pixelflow_intake

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_intake.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/intake/directions",
            json={
                "intent": "generate_video",
                "values": {
                    "product_info": "智能健康戒指",
                    "product_category": "数码3C",
                    "target_audience": "25-35",
                    "conversion_goal": "引流直播间",
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["validation"]["intent"] == "video"
    assert len(data["creative_directions"]) == 3


def test_intake_router_analyzes_intent_with_llm(monkeypatch):
    from app.gateway.routers import pixelflow_intake
    from pixelflow.intake.llm import IntentRecognitionResult

    async def fake_recognize_intent_with_llm(prompt, materials=None):
        assert prompt == "帮我分析这个视频 https://x/one.mp4"
        assert materials == [{"url": "https://x/one.mp4"}]
        return IntentRecognitionResult(
            intent="video_analysis",
            confidence=0.91,
            reason="用户要求分析视频",
            values={},
            llm_used=True,
            model_name="deepseek-v4-pro",
        )

    monkeypatch.setattr(pixelflow_intake, "recognize_intent_with_llm", fake_recognize_intent_with_llm)

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_intake.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/intake/analyze",
            json={
                "prompt": "帮我分析这个视频 https://x/one.mp4",
                "materials": [{"url": "https://x/one.mp4"}],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "video_analysis"
    assert data["llm_used"] is True
    assert data["model_name"] == "deepseek-v4-pro"


def test_intake_router_directions_use_llm_when_form_complete(monkeypatch):
    from app.gateway.routers import pixelflow_intake
    from pixelflow.intake.forms import CreativeDirection

    async def fake_draft_creative_directions_with_llm(intent, values, product_creative_profile=None):
        assert intent == "image"
        assert values["image_goal"] == "科技感耳机海报"
        assert product_creative_profile == {"visual_anchor_keywords": ["金属质感"]}
        return [
            CreativeDirection(direction_id="direction_1", title="LLM 主视觉", description="科技蓝背景突出耳机质感。", recommended=True),
            CreativeDirection(direction_id="direction_2", title="LLM 场景图", description="办公桌场景承接社媒发布。"),
            CreativeDirection(direction_id="direction_3", title="LLM 封面图", description="强标题留白适合封面。"),
        ]

    monkeypatch.setattr(pixelflow_intake, "draft_creative_directions_with_llm", fake_draft_creative_directions_with_llm)

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_intake.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/intake/directions",
            json={
                "intent": "image",
                "values": {
                    "image_goal": "科技感耳机海报",
                    "image_type": "海报/封面图",
                    "image_usage": "社媒发布",
                    "image_style": "科技感",
                    "image_size": "9:16 竖版海报",
                },
                "product_creative_profile": {"visual_anchor_keywords": ["金属质感"]},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["creative_directions"][0]["title"] == "LLM 主视觉"
    assert data["creative_directions"][0]["recommended"] is True


def test_intake_router_passes_materials_to_creative_direction_llm(monkeypatch):
    from app.gateway.routers import pixelflow_intake
    from pixelflow.intake.forms import CreativeDirection

    materials = [{"type": "image", "url": "https://x/product.png", "filename": "product.png"}]

    async def fake_draft_creative_directions_with_llm(intent, values, product_creative_profile=None):
        assert intent == "image"
        assert values["image_goal"] == "参考上传素材生成商品海报"
        assert product_creative_profile == {"materials": materials}
        return [
            CreativeDirection(direction_id="direction_1", title="素材主视觉", description="参考上传素材组织画面。", recommended=True),
            CreativeDirection(direction_id="direction_2", title="素材场景图", description="延展产品使用场景。"),
            CreativeDirection(direction_id="direction_3", title="素材封面图", description="提炼素材卖点做封面。"),
        ]

    monkeypatch.setattr(pixelflow_intake, "draft_creative_directions_with_llm", fake_draft_creative_directions_with_llm)

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_intake.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/intake/directions",
            json={
                "intent": "image",
                "values": {
                    "image_goal": "参考上传素材生成商品海报",
                    "image_type": "商品广告图",
                    "image_usage": "社媒发布",
                    "image_style": "真实摄影",
                    "image_size": "自动适配",
                },
                "materials": materials,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["creative_directions"][0]["title"] == "素材主视觉"
