from __future__ import annotations

import pytest

from pixelflow.preferences import MemoryUserPreferenceStore, extract_structured_preferences


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
    assert "/api/users/{user_id}/preferences" in paths
    assert "/api/users/{user_id}/preferences/feedback" in paths
