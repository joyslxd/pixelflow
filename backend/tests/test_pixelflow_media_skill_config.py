"""PixelFlow 媒体生成供应商配置测试。"""

from __future__ import annotations

import pytest

from pixelflow.skills.base import get_video_decompose_skill, get_video_skill


def test_media_skill_selects_borgrise_for_video_and_decompose(monkeypatch: pytest.MonkeyPatch):
    """视频生成和参考视频拆解都读取同一个 PIXELFLOW_MEDIA_SKILL 配置。"""
    monkeypatch.setenv("PIXELFLOW_MEDIA_SKILL", "borgrise")
    monkeypatch.delenv("PIXELFLOW_VIDEO_SKILL", raising=False)
    monkeypatch.delenv("PIXELFLOW_DECOMPOSE_SKILL", raising=False)

    assert type(get_video_skill()).__name__ == "BorgriseSkill"
    assert type(get_video_decompose_skill()).__name__ == "BorgriseSkill"


def test_unknown_media_skill_rejects_video_and_decompose(monkeypatch: pytest.MonkeyPatch):
    """当前媒体供应商只支持 borgrise，未知值必须尽早报错，避免静默走错供应商。"""
    monkeypatch.setenv("PIXELFLOW_MEDIA_SKILL", "unknown-vendor")

    with pytest.raises(ValueError, match="Unknown media skill implementation"):
        get_video_skill()
    with pytest.raises(ValueError, match="Unknown media skill implementation"):
        get_video_decompose_skill()
