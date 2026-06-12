"""Tests for the GENERATE phase: segment-based parallel generation."""

from __future__ import annotations

import asyncio

from pixelflow.nodes import generate_node
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


def _shot(dur, prompt="p", **kw):
    base = {"generation_prompt": prompt, "duration": dur, "asset_strategy": "use_real_asset"}
    base.update(kw)
    return base


def test_no_shots_not_ready():
    # 空分镜:停在 GENERATE,generation_ready=False(不进 EDIT)。
    out = asyncio.run(generate_node({"brief": {"shots": []}, "product_info": _PI}))
    assert out["phase"] == Phase.GENERATE.value
    assert out["generated_assets"] == []
    assert out["generation_ready"] is False


def test_short_video_is_one_segment_one_call(monkeypatch):
    fake = _FakeSkill()
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake)

    # 2.5 + 3 + 2 = 7.5s total <= 15 -> single segment, single seedance call.
    state = {
        "brief": {"ratio": "16:9", "shots": [_shot(2.5, "a"), _shot(3, "b"), _shot(2, "c")]},
        "product_info": _PI,
    }
    out = asyncio.run(generate_node(state))

    assert out["phase"] == Phase.EDIT.value
    assert len(fake.calls) == 1  # NOT one call per shot
    call = fake.calls[0]
    assert call["image_url"] == "https://x/main.jpg"
    assert call["ratio"] == "16:9"
    assert call["duration"] == 8  # ceil(7.5)
    # the fused prompt carries every shot's action
    assert "a" in call["prompt"] and "b" in call["prompt"] and "c" in call["prompt"]

    assets = out["generated_assets"]
    assert len(assets) == 1
    assert assets[0]["segment_index"] == 0
    assert assets[0]["shot_indices"] == [0, 1, 2]
    assert assets[0]["duration"] == 7.5
    assert assets[0]["ok"]
    assert assets[0]["url"] == "https://x/clip.mp4"


def test_long_video_splits_into_segments(monkeypatch):
    fake = _FakeSkill()
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake)

    # seedance ≤10s/段:10 + 8 + 6 各自成段(任意两段相加 >10),共 3 段。
    state = {
        "brief": {"shots": [_shot(10, "s0"), _shot(8, "s1"), _shot(6, "s2")]},
        "product_info": _PI,
    }
    out = asyncio.run(generate_node(state))

    assert len(fake.calls) == 3
    assets = out["generated_assets"]
    assert [a["shot_indices"] for a in assets] == [[0], [1], [2]]
    assert [a["duration"] for a in assets] == [10, 8, 6]
    assert [c["duration"] for c in fake.calls] == [10, 8, 6]  # 均 ≤10,无钳制


def test_duration_clamped_to_vendor_range(monkeypatch):
    fake = _FakeSkill()
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake)

    # single 2.5s shot -> segment duration 2.5 -> generate at the 4s minimum.
    out = asyncio.run(generate_node({"brief": {"shots": [_shot(2.5)]}, "product_info": _PI}))
    assert fake.calls[0]["duration"] == 4
    assert out["generated_assets"][0]["duration"] == 2.5  # edit-side duration unchanged


def test_missing_image_fails_all_segments_without_calling_skill(monkeypatch):
    fake = _FakeSkill()
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake)

    state = {"brief": {"shots": [_shot(5), _shot(5)]}, "product_info": {}}  # no main_image_url
    out = asyncio.run(generate_node(state))

    assert fake.calls == []
    assets = out["generated_assets"]
    assert len(assets) == 1  # 5+5=10 <= 15 -> one segment
    assert assets[0]["ok"] is False
    assert "无可用图源" in assets[0]["error"]
