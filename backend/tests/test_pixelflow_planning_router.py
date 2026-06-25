from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from tests._router_auth_helpers import make_authed_test_app


def test_pixelflow_planning_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_planning

    paths = {route.path for route in pixelflow_planning.router.routes}
    assert pixelflow_planning.router.prefix == "/agent/flows/planning"
    assert "/agent/flows/planning/plan" in paths


def _stable_user() -> User:
    return User(
        email="pixelflow-planning@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000789"),
    )


def test_planning_router_creates_reviewable_plan_markdown():
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan",
            json={
                "intent": "video",
                "form_values": {
                    "product_info": "苹果什么什么PRO",
                    "product_category": "数码3C",
                    "target_audience": "25-35",
                    "conversion_goal": "引流直播间",
                },
                "selected_direction": {
                    "direction_id": "direction_1",
                    "title": "痛点开场 + 产品解决",
                    "description": "先抛出续航痛点，再用产品能力完成解决。",
                    "data": {"visual_anchor": "通勤、质感"},
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["output_type"] == "video"
    assert data["review_timeout_sec"] == 30
    assert data["consistency_issues"] == []
    assert data["template_path"].endswith("backend/skills/public/borgrise-creative-assistant-v2/templates/plan.md")
    assert "苹果什么什么PRO" in data["plan_markdown"]
    assert "痛点开场 + 产品解决" in data["plan_markdown"]


def test_planning_router_accepts_generation_intent_aliases():
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan",
            json={
                "intent": "generate_video",
                "form_values": {
                    "product_info": "智能健康戒指",
                    "product_category": "数码3C",
                    "target_audience": "25-35",
                    "conversion_goal": "引流直播间",
                },
                "selected_direction": {"title": "晨跑隐形教练", "description": "用晨跑数据讲清健康戒指卖点。"},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["output_type"] == "video"
    assert "智能健康戒指" in data["plan_markdown"]


def test_planning_router_expands_collection_payload():
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan",
            json={
                "intent": "generate_video",
                "selected_direction": {"title": "90秒生活方式广告", "description": "晨跑、办公、睡眠完整串联。"},
                "collection": {
                    "form_values": {
                        "product_info": "AuroraFit智能健康戒指",
                        "product_category": "数码3C",
                        "target_audience": "25-35",
                        "conversion_goal": "引流直播间",
                    },
                    "materials": [{"url": "https://example.com/ref.png"}],
                    "product_creative_profile": {"core_message": "需要生成90秒左右的视频"},
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["output_type"] == "video"
    assert "AuroraFit智能健康戒指" in data["plan_markdown"]
    assert "90 秒" in data["plan_markdown"]
