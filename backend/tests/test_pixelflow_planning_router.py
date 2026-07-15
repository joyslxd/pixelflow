from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from tests._router_auth_helpers import make_authed_test_app


@pytest.fixture(autouse=True)
def deterministic_plan_builder(monkeypatch):
    from app.gateway.routers import pixelflow_planning
    from pixelflow.creative.plan_markdown import build_plan_markdown

    async def fake_build_plan_markdown_with_llm(intent, form_values, selected_direction, product_creative_profile=None, materials=None, intake_context=None, **_kwargs):
        return build_plan_markdown(intent, form_values, selected_direction, product_creative_profile, materials, intake_context)

    monkeypatch.setattr(pixelflow_planning, "build_plan_markdown_with_llm", fake_build_plan_markdown_with_llm)


def test_pixelflow_planning_router_prefix_and_paths():
    from app.gateway.routers import pixelflow_planning

    paths = {route.path for route in pixelflow_planning.router.routes}
    assert pixelflow_planning.router.prefix == "/agent/flows/planning"
    assert "/agent/flows/planning/plan" in paths
    assert "/agent/flows/planning/plan/revise" in paths
    assert "/agent/flows/planning/plan/restore" in paths
    assert "/agent/flows/planning/plan/save-edit" in paths


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
                    "product_info": "AuroraFit智能健康戒指",
                    "product_category": "数码3C",
                    "target_audience": "25-35",
                    "conversion_goal": "引流直播间",
                    "video_duration_sec": 180,
                    "video_ratio": "9:16",
                    "video_model": "seedance-2.0",
                    "image_model": "gpt-image-2",
                    "image_model_capabilities": {
                        "aspect_ratios": ["1:1", "16:9", "9:16"],
                        "sizes": ["1080p", "2K", "4K"],
                    },
                    "video_usage": "新品宣传",
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
    assert data["review_timeout_sec"] is None
    assert data["consistency_issues"] == []
    assert data["template_path"].endswith("backend/skills/public/borgrise-creative-assistant-v2/templates/plan_video.md")
    assert "AuroraFit智能健康戒指" in data["plan_markdown"]
    assert "痛点开场 + 产品解决" in data["plan_markdown"]
    assert data["plan_version"] == 1
    assert data["creation_contract"]["video_duration_sec"] == 180
    assert sum(data["scene_durations_sec"]) == 180
    assert data["scene_blueprints"]
    assert [item["duration_sec"] for item in data["scene_blueprints"]] == data["scene_durations_sec"]
    assert data["plan_history"][0]["scene_blueprints"] == data["scene_blueprints"]


def test_planning_router_preserves_complete_intake_context_for_image_plan():
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan",
            json={
                "intent": "image",
                "form_values": {
                    "image_goal": "宣传图",
                    "image_type": "海报/封面图",
                    "image_usage": "社媒发布",
                    "image_style": "真实摄影",
                    "image_size": "自动适配",
                },
                "selected_direction": {
                    "direction_id": "direction_1",
                    "title": "通学收纳主视觉",
                    "description": "突出书包容量、护脊和耐磨卖点。",
                },
                "intake_context": {
                    "source_prompt": "帮我生成书包的宣传图",
                    "product_subject": "书包",
                    "creation_goal": "书包宣传图",
                    "industry_type": "服饰鞋包",
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "# 书包宣传图｜通学收纳主视觉" in data["plan_markdown"]
    assert "原始需求：帮我生成书包的宣传图" in data["plan_markdown"]


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
                        "video_duration_sec": 90,
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


def test_planning_router_revises_current_plan_without_regenerating_directions(monkeypatch):
    from app.gateway.routers import pixelflow_planning
    from pixelflow.creative.plan_markdown import build_plan_markdown

    initial = build_plan_markdown(
        "image",
        {
            "image_goal": "书包宣传图",
            "image_type": "商品广告图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "9:16",
        },
        {"direction_id": "direction_1", "title": "通学主视觉", "description": "突出护脊和收纳。"},
    )

    async def fake_revise_plan_markdown_with_llm(**kwargs):
        return initial.next_version(
            plan_markdown=f"{kwargs['current_plan_markdown']}\n\n修改意见：增加开学季氛围。",
            plan_history=kwargs["plan_history"],
            current_version=kwargs["current_plan_version"],
        )

    monkeypatch.setattr(pixelflow_planning, "revise_plan_markdown_with_llm", fake_revise_plan_markdown_with_llm)
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan/revise",
            json={
                "intent": "image",
                "form_values": {
                    "image_goal": "书包宣传图",
                    "image_type": "商品广告图",
                    "image_usage": "社媒发布",
                    "image_style": "真实摄影",
                    "image_size": "9:16",
                },
                "selected_direction": {"direction_id": "direction_1", "title": "通学主视觉", "description": "突出护脊和收纳。"},
                "current_plan_markdown": initial.plan_markdown,
                "current_plan_version": 1,
                "plan_history": initial.plan_history,
                "revision_feedback": "增加开学季氛围",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["plan_version"] == 2
    assert len(data["plan_history"]) == 2
    assert "增加开学季氛围" in data["plan_markdown"]


@pytest.mark.parametrize("intent", ["image", "video"])
def test_planning_router_restores_history_without_creating_version(intent: str):
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)
    history = [
        {"version": 1, "plan_markdown": "# plan.md v1"},
        {"version": 2, "plan_markdown": "# plan.md v2"},
    ]

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan/restore",
            json={
                "intent": intent,
                "current_plan_markdown": "# plan.md v2",
                "current_plan_version": 2,
                "plan_history": history,
                "restore_version": 1,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["plan_version"] == 1
    assert data["restored_from_version"] == 1
    assert data["plan_markdown"] == "# plan.md v1"
    assert data["plan_history"] == history


def test_planning_router_preserves_explicit_empty_image_snapshots():
    from app.gateway.routers import pixelflow_planning

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_planning.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan/restore",
            json={
                "intent": "image",
                "current_plan_markdown": "# plan.md v2",
                "current_plan_version": 2,
                "plan_history": [
                    {
                        "version": 1,
                        "plan_markdown": "# plan.md v1",
                        "creation_contract": {},
                        "scene_durations_sec": [],
                    },
                    {
                        "version": 2,
                        "plan_markdown": "# plan.md v2",
                        "creation_contract": {"intent": "image", "image_size": "9:16"},
                        "scene_durations_sec": [10],
                    },
                ],
                "restore_version": 1,
                "creation_contract": {"intent": "image", "image_size": "9:16"},
                "scene_durations_sec": [10],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["creation_contract"] == {}
    assert data["scene_durations_sec"] == []


def test_planning_router_uses_power_mem_context_without_exposing_internal_memory():
    from app.gateway.routers import pixelflow_planning
    from pixelflow.memory import SemanticMemoryItem

    class FakePowerMemService:
        async def search(self, **_kwargs):
            return [
                SemanticMemoryItem(
                    memory_id="m1",
                    content="品牌长期偏好：真实摄影，高级感，不要价格文字。",
                    score=0.91,
                    metadata={"category": "preference"},
                )
            ]

    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_power_mem_service = FakePowerMemService()
    app.include_router(pixelflow_planning.router)

    with TestClient(app) as client:
        response = client.post(
            "/agent/flows/planning/plan",
            json={
                "intent": "image",
                "form_values": {
                    "image_goal": "书包宣传图",
                    "image_type": "海报/封面图",
                    "image_usage": "社媒发布",
                    "image_style": "真实摄影",
                    "image_size": "自动适配",
                },
                "selected_direction": {"title": "通学收纳主视觉", "description": "突出书包容量。"},
                "intake_context": {"source_prompt": "做书包宣传图", "product_subject": "书包"},
            },
        )

    assert response.status_code == 200
    plan_markdown = response.json()["plan_markdown"]
    assert "长期记忆约束" not in plan_markdown
    assert "品牌长期偏好：真实摄影，高级感，不要价格文字。" not in plan_markdown
