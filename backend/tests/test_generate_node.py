"""Tests for the GENERATE phase: shot image resolution + generation loop."""

from __future__ import annotations

from pixelflow.nodes import _resolve_shot_image, generate_node
from pixelflow.skills import GenerationResult
from pixelflow.state import Phase

_PI = {"main_image_url": "https://x/main.jpg"}


class _FakeSkill:
    """Records image_to_video calls and returns a canned success result."""

    def __init__(self):
        self.calls: list[dict] = []

    async def image_to_video(self, image_url, prompt=None, duration=10, ratio="9:16", model=None):
        self.calls.append({"image_url": image_url, "prompt": prompt, "duration": duration, "ratio": ratio})
        return GenerationResult(ok=True, url="https://x/clip.mp4", task_id="t1")


# -- _resolve_shot_image --


def test_use_real_asset_uses_main_image():
    url, note = _resolve_shot_image({"asset_strategy": "use_real_asset"}, _PI)
    assert url == "https://x/main.jpg"
    assert note is None


def test_mixed_uses_main_image():
    url, note = _resolve_shot_image({"asset_strategy": "mixed"}, _PI)
    assert url == "https://x/main.jpg"
    assert note is None


def test_default_strategy_uses_main_image():
    url, note = _resolve_shot_image({}, _PI)
    assert url == "https://x/main.jpg"
    assert note is None


def test_generate_asset_falls_back_with_note():
    url, note = _resolve_shot_image({"asset_strategy": "generate_asset"}, _PI)
    assert url == "https://x/main.jpg"
    assert note and "回退" in note


def test_no_main_image_returns_none():
    url, note = _resolve_shot_image({"asset_strategy": "use_real_asset"}, {})
    assert url is None
    assert note is None


# -- generate_node --


def test_no_shots_is_noop():
    import asyncio

    state = {"brief": {"shots": []}, "product_info": _PI}
    out = asyncio.run(generate_node(state))
    assert out["phase"] == Phase.EDIT.value
    assert out["generated_assets"] == []


def test_generates_clip_per_shot(monkeypatch):
    fake = _FakeSkill()
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake)
    import asyncio

    state = {
        "brief": {"ratio": "16:9", "shots": [{"generation_prompt": "p0", "duration": 5, "asset_strategy": "use_real_asset"}]},
        "product_info": _PI,
    }
    out = asyncio.run(generate_node(state))

    assert out["phase"] == Phase.EDIT.value
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["image_url"] == "https://x/main.jpg"
    assert call["prompt"] == "p0"
    assert call["duration"] == 5
    assert call["ratio"] == "16:9"  # brief-level ratio threaded through

    asset = out["generated_assets"][0]
    assert asset["ok"]
    assert asset["url"] == "https://x/clip.mp4"
    assert "note" not in asset


def test_fallback_strategy_records_note(monkeypatch):
    fake = _FakeSkill()
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake)
    import asyncio

    state = {
        "brief": {"shots": [{"generation_prompt": "p0", "asset_strategy": "generate_asset"}]},
        "product_info": _PI,
    }
    out = asyncio.run(generate_node(state))

    assert len(fake.calls) == 1  # still generated, via fallback image
    assert "note" in out["generated_assets"][0]


def test_missing_image_marks_failure_without_calling_skill(monkeypatch):
    fake = _FakeSkill()
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake)
    import asyncio

    state = {
        "brief": {"shots": [{"generation_prompt": "p0", "asset_strategy": "use_real_asset"}]},
        "product_info": {},  # no main_image_url
    }
    out = asyncio.run(generate_node(state))

    assert fake.calls == []  # no image source => never call the skill
    asset = out["generated_assets"][0]
    assert asset["ok"] is False
    assert "无可用图源" in asset["error"]
