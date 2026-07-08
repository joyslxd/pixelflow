"""Tests for edit_node: Timeline assembly + render skill wiring (mocked)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from pixelflow.nodes import _signed_media_url_needs_refresh, edit_node
from pixelflow.skills import EditResult, GenerationResult
from pixelflow.state import Phase

_BRIEF = {
    "ratio": "9:16",
    "size": "1080x1920",
    "platform": "douyin",
    "shots": [{"shot_id": "shot_000", "duration": 5.0}],
}
_ASSETS = [{"segment_index": 0, "shot_indices": [0], "duration": 5.0, "ok": True, "url": "https://x/clip.mp4"}]


class _FakeEditSkill:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    async def render(self, timeline, *, draft_name, output_root=None):
        self.calls.append({"timeline": timeline, "draft_name": draft_name})
        return self.result


class _FakeVideoSkill:
    def __init__(self):
        self.polled: list[str] = []

    async def poll_video_task(self, task_id):
        self.polled.append(task_id)
        return GenerationResult(ok=True, url="https://ark.example/fresh.mp4", task_id=task_id)


def _signed_url(signed_at: datetime, expires: int = 86400) -> str:
    date = signed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"https://ark.example/clip.mp4?X-Tos-Date={date}&X-Tos-Expires={expires}&X-Tos-Signature=sig"


def test_signed_media_url_refresh_window():
    issued_at = datetime(2026, 7, 7, 0, 0, tzinfo=UTC)
    url = _signed_url(issued_at)

    assert _signed_media_url_needs_refresh(url, now=issued_at + timedelta(hours=23, minutes=50))
    assert not _signed_media_url_needs_refresh(url, now=issued_at + timedelta(hours=1))


def test_render_success_sets_draft_path(monkeypatch):
    fake = _FakeEditSkill(EditResult(ok=True, output_path="/tmp/drafts/pixelflow_t1"))
    monkeypatch.setattr("pixelflow.nodes.get_video_edit_skill", lambda: fake)

    state = {"task_id": "t1", "brief": _BRIEF, "generated_assets": _ASSETS}
    out = asyncio.run(edit_node(state))

    assert out["phase"] == Phase.EDIT_REVIEW.value
    assert out["draft_path"] == "/tmp/drafts/pixelflow_t1"
    assert out["edit_notes"] == []
    assert len(fake.calls) == 1
    assert fake.calls[0]["draft_name"] == "pixelflow_t1"
    # timeline still assembled and passed to the skill
    assert len(fake.calls[0]["timeline"]["clips"]) == 1


def test_edit_refreshes_expiring_ark_url_before_render(monkeypatch):
    fake_edit = _FakeEditSkill(EditResult(ok=True, output_path="/tmp/drafts/pixelflow_t1"))
    fake_video = _FakeVideoSkill()
    expired_asset = {
        "segment_index": 0,
        "shot_indices": [0],
        "duration": 5.0,
        "ok": True,
        "url": _signed_url(datetime(2026, 7, 7, 0, 0, tzinfo=UTC), expires=60),
        "task_id": "cgt-1",
    }
    monkeypatch.setattr("pixelflow.nodes.get_video_edit_skill", lambda: fake_edit)
    monkeypatch.setattr("pixelflow.nodes.get_video_skill", lambda: fake_video)

    state = {"task_id": "t1", "brief": _BRIEF, "generated_assets": [expired_asset]}
    out = asyncio.run(edit_node(state))

    assert fake_video.polled == ["cgt-1"]
    assert out["generated_assets"][0]["url"] == "https://ark.example/fresh.mp4"
    assert fake_edit.calls[0]["timeline"]["clips"][0]["source_url"] == "https://ark.example/fresh.mp4"


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

    # all segments failed generation -> empty timeline -> skill not called
    state = {"task_id": "t1", "brief": _BRIEF, "generated_assets": [{"segment_index": 0, "ok": False, "url": None}]}
    out = asyncio.run(edit_node(state))

    assert fake.calls == []
    assert out["draft_path"] == ""
    assert len(out["edit_notes"]) == 1  # the skipped-segment note from build_timeline
