"""Tests for edit_node: Timeline assembly + render skill wiring (mocked)."""

from __future__ import annotations

import asyncio

from pixelflow.nodes import edit_node
from pixelflow.skills import EditResult
from pixelflow.state import Phase

_BRIEF = {
    "ratio": "9:16",
    "size": "1080x1920",
    "platform": "douyin",
    "shots": [{"shot_id": "shot_000", "duration": 5.0}],
}
_ASSETS = [{"shot_index": 0, "ok": True, "url": "https://x/clip.mp4"}]


class _FakeEditSkill:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    async def render(self, timeline, *, draft_name, output_root=None):
        self.calls.append({"timeline": timeline, "draft_name": draft_name})
        return self.result


def test_render_success_sets_draft_path(monkeypatch):
    fake = _FakeEditSkill(EditResult(ok=True, output_path="/tmp/drafts/pixelflow_t1"))
    monkeypatch.setattr("pixelflow.nodes.get_video_edit_skill", lambda: fake)

    state = {"task_id": "t1", "brief": _BRIEF, "generated_assets": _ASSETS}
    out = asyncio.run(edit_node(state))

    assert out["phase"] == Phase.QC.value
    assert out["draft_path"] == "/tmp/drafts/pixelflow_t1"
    assert out["edit_notes"] == []
    assert len(fake.calls) == 1
    assert fake.calls[0]["draft_name"] == "pixelflow_t1"
    # timeline still assembled and passed to the skill
    assert len(fake.calls[0]["timeline"]["clips"]) == 1


def test_render_video_kind_sets_final_video_url(monkeypatch):
    fake = _FakeEditSkill(EditResult(ok=True, output_path="/tmp/renders/pixelflow_t1.mp4", kind="video"))
    monkeypatch.setattr("pixelflow.nodes.get_video_edit_skill", lambda: fake)

    state = {"task_id": "t1", "brief": _BRIEF, "generated_assets": _ASSETS}
    out = asyncio.run(edit_node(state))

    assert out["final_video_url"] == "/tmp/renders/pixelflow_t1.mp4"
    assert out["draft_path"] == ""


def test_render_failure_recorded_in_notes(monkeypatch):
    fake = _FakeEditSkill(EditResult(ok=False, error="pyJianYingDraft 未安装"))
    monkeypatch.setattr("pixelflow.nodes.get_video_edit_skill", lambda: fake)

    state = {"task_id": "t1", "brief": _BRIEF, "generated_assets": _ASSETS}
    out = asyncio.run(edit_node(state))

    assert out["draft_path"] == ""
    assert any("剪辑渲染失败" in n for n in out["edit_notes"])


def test_no_clips_skips_render(monkeypatch):
    fake = _FakeEditSkill(EditResult(ok=True, output_path="/should/not/be/used"))
    monkeypatch.setattr("pixelflow.nodes.get_video_edit_skill", lambda: fake)

    # all shots failed generation -> empty timeline -> skill not called
    state = {"task_id": "t1", "brief": _BRIEF, "generated_assets": [{"shot_index": 0, "ok": False, "url": None}]}
    out = asyncio.run(edit_node(state))

    assert fake.calls == []
    assert out["draft_path"] == ""
    assert len(out["edit_notes"]) == 1  # the skipped-shot note from build_timeline
