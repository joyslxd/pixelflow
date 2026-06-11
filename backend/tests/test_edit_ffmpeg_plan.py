"""Tests for build_ffmpeg_args: DraftPlan -> deterministic ffmpeg argv (pure logic)."""

from __future__ import annotations

import pytest

from pixelflow.edit import build_ffmpeg_args, passthrough_eligible
from pixelflow.edit.ffmpeg_plan import _escape_drawtext
from pixelflow.edit.models import DraftPlan, DraftSegment


def _plan(segments: list[DraftSegment], width: int = 1080, height: int = 1920, fps: int = 30) -> DraftPlan:
    return DraftPlan(width=width, height=height, fps=fps, segments=segments)


def test_single_clip_argv():
    plan = _plan([DraftSegment(source_url="https://x/a.mp4", start=0.0, duration=5.0)])
    args = build_ffmpeg_args(plan, ["/tmp/clip_000.mp4"], "/tmp/out.mp4")

    assert args[:2] == ["ffmpeg", "-y"]
    assert args[2:4] == ["-i", "/tmp/clip_000.mp4"]
    assert args[-1] == "/tmp/out.mp4"

    fc = args[args.index("-filter_complex") + 1]
    assert "trim=duration=5" in fc
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in fc
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2" in fc
    assert "fps=30" in fc
    # source audio preserved and concatenated alongside video
    assert "[0:a]atrim=duration=5" in fc
    assert "[v0][a0]concat=n=1:v=1:a=1[vout][aout]" in fc
    assert args[args.index("-map") + 1] == "[vout]"
    assert "[aout]" in args
    assert "aac" in args


def test_multi_clip_concat_order():
    plan = _plan(
        [
            DraftSegment(source_url="https://x/a.mp4", start=0.0, duration=3.0),
            DraftSegment(source_url="https://x/b.mp4", start=3.0, duration=4.5),
        ]
    )
    args = build_ffmpeg_args(plan, ["/tmp/a.mp4", "/tmp/b.mp4"], "/tmp/out.mp4")

    fc = args[args.index("-filter_complex") + 1]
    assert "[0:v]trim=duration=3" in fc
    assert "[1:v]trim=duration=4.5" in fc
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vout][aout]" in fc
    # every input is registered
    assert args.count("-i") == 2


def test_caption_burned_only_with_font():
    seg = DraftSegment(source_url="https://x/a.mp4", start=0.0, duration=5.0, caption="限时五折")
    plan = _plan([seg])

    no_font = build_ffmpeg_args(plan, ["/tmp/a.mp4"], "/tmp/out.mp4")
    assert "drawtext" not in no_font[no_font.index("-filter_complex") + 1]

    with_font = build_ffmpeg_args(plan, ["/tmp/a.mp4"], "/tmp/out.mp4", font_file="/fonts/zh.ttf")
    fc = with_font[with_font.index("-filter_complex") + 1]
    assert "drawtext=fontfile=/fonts/zh.ttf:text='限时五折'" in fc


def test_drawtext_escaping():
    assert _escape_drawtext(r"a:b'c%d\e") == r"a\:b\'c\%d\\e"


def _probe(width=1080, height=1920, fps=30.0, duration=14.0):
    return {"width": width, "height": height, "fps": fps, "duration": duration}


def test_passthrough_eligible_when_specs_match():
    plan = _plan([DraftSegment(source_url="x", start=0.0, duration=14.0)])
    assert passthrough_eligible(plan, _probe(), has_caption=False) is True
    # tiny duration overage within epsilon still eligible
    assert passthrough_eligible(plan, _probe(duration=14.07), has_caption=False) is True


def test_passthrough_blocked_by_each_condition():
    plan = _plan([DraftSegment(source_url="x", start=0.0, duration=14.0)])
    assert passthrough_eligible(plan, _probe(width=720, height=1280), has_caption=False) is False  # wrong size
    assert passthrough_eligible(plan, _probe(fps=24.0), has_caption=False) is False  # wrong fps
    assert passthrough_eligible(plan, _probe(duration=12.0), has_caption=False) is False  # needs trim
    assert passthrough_eligible(plan, _probe(), has_caption=True) is False  # caption to burn


def test_passthrough_blocked_for_multi_segment():
    plan = _plan(
        [
            DraftSegment(source_url="a", start=0.0, duration=10.0),
            DraftSegment(source_url="b", start=10.0, duration=5.0),
        ]
    )
    assert passthrough_eligible(plan, _probe(), has_caption=False) is False


def test_passthrough_tolerates_bad_probe():
    plan = _plan([DraftSegment(source_url="x", start=0.0, duration=14.0)])
    assert passthrough_eligible(plan, {}, has_caption=False) is False
    assert passthrough_eligible(plan, {"width": None, "height": None, "fps": None, "duration": None}, has_caption=False) is False


def test_length_mismatch_and_empty_plan_raise():
    plan = _plan([DraftSegment(source_url="https://x/a.mp4", start=0.0, duration=5.0)])
    with pytest.raises(ValueError, match="mismatch"):
        build_ffmpeg_args(plan, [], "/tmp/out.mp4")
    with pytest.raises(ValueError, match="empty plan"):
        build_ffmpeg_args(_plan([]), [], "/tmp/out.mp4")
