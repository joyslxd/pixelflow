from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from pixelflow.preferences import MemoryUserPreferenceStore, extract_structured_preferences
from tests._router_auth_helpers import make_authed_test_app


def test_extract_structured_preferences_negative_and_defaults():
    patch = extract_structured_preferences("以后默认抖音 9:16，喜欢高级感，不要出现价格文字")

    assert patch["defaults"]["platform"] == "douyin"
    assert patch["defaults"]["ratio"] == "9:16"
    assert patch["style_preferences"]["overall_style"] == "高级感"
    assert "不要出现价格文字" in patch["negative_rules"]


def test_extract_structured_preferences_from_brief_patch():
    patch = extract_structured_preferences("", brief_patch={"platform": "taobao", "ratio": "1:1", "duration_sec": 15})

    assert patch["defaults"] == {"platform": "taobao", "ratio": "1:1", "duration_sec": 15}


@pytest.mark.asyncio
async def test_memory_preference_store_merges_and_dedupes():
    store = MemoryUserPreferenceStore()

    await store.update("u1", {"style_preferences": {"pace": "快节奏"}, "negative_rules": ["不要价格文字"], "defaults": {"ratio": "9:16"}})
    row = await store.update("u1", {"style_preferences": {"bgm_vibe": "轻音乐"}, "negative_rules": ["不要价格文字", "不要水印"]})
    row = await store.append_feedback("u1", "以后都用轻音乐", task_id="t1")

    assert row.style_preferences == {"pace": "快节奏", "bgm_vibe": "轻音乐"}
    assert row.negative_rules == ["不要价格文字", "不要水印"]
    assert row.defaults == {"ratio": "9:16"}
    assert row.recent_feedback[0]["task_id"] == "t1"


def test_pixelflow_preferences_router_imports():
    from app.gateway.routers import pixelflow_preferences

    paths = {route.path for route in pixelflow_preferences.router.routes}
    assert pixelflow_preferences.router.prefix == "/agent/users"
    assert "/agent/users/{user_id}/preferences" in paths
    assert "/agent/users/{user_id}/preferences/feedback" in paths


def _stable_user() -> User:
    return User(
        email="pixelflow-preferences@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000321"),
    )


def test_preferences_feedback_records_power_mem_and_returns_status():
    from app.gateway.routers import pixelflow_preferences

    class FakePowerMemService:
        def __init__(self):
            self.records = []

        def status_snapshot(self):
            return {"enabled": True, "provider": "powermem", "status": "configured", "write_enabled": True}

        async def record(self, **kwargs):
            self.records.append(kwargs)
            return True

    service = FakePowerMemService()
    user_id = str(_stable_user().id)
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_preference_store = MemoryUserPreferenceStore()
    app.state.pixelflow_power_mem_service = service
    app.include_router(pixelflow_preferences.router)

    with TestClient(app) as client:
        response = client.post(
            f"/agent/users/{user_id}/preferences/feedback",
            json={"feedback": "以后默认真实摄影风格，不要价格文字", "task_id": "task-1"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["semantic_memory"]["provider"] == "powermem"
    assert data["semantic_memory"]["status"] == "configured"
    assert service.records[0]["user_id"] == user_id
    assert service.records[0]["category"] == "preference"
    assert service.records[0]["memory_type"] == "preference"
    assert service.records[0]["source_agent"] == "preference_api"
    assert service.records[0]["infer"] is True
    assert "真实摄影" in service.records[0]["content"]
