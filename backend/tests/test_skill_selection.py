from __future__ import annotations

from pixelflow.skills.base import get_video_edit_skill


def test_default_edit_skill_is_ffmpeg(monkeypatch):
    monkeypatch.delenv("PIXELFLOW_EDIT_SKILL", raising=False)

    assert type(get_video_edit_skill()).__name__ == "FFmpegEditSkill"


def test_can_select_jianying_edit_skill(monkeypatch):
    monkeypatch.setenv("PIXELFLOW_EDIT_SKILL", "jianying")

    assert type(get_video_edit_skill()).__name__ == "JianYingEditSkill"
