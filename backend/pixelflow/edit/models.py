"""Timeline IR — the EDIT phase's assembly contract.

The Timeline is a vendor-neutral description of the final cut: an ordered list
of clips (each bound to a generated source video) plus the output format. A
render skill (FFmpeg / 剪映 draft) consumes this to produce the final video, so
the graph never encodes editing-tool specifics.
"""

from __future__ import annotations

from pydantic import BaseModel


class Clip(BaseModel):
    """One generated shot placed on the timeline, in playback order."""

    shot_index: int
    shot_id: str
    source_url: str  # the generated clip URL bound to this shot
    duration: float  # seconds (from the Brief shot)
    transition_in: str = ""
    transition_out: str = ""
    narration_text: str = ""  # 旁白 — overlaid/TTS at render time
    onscreen_text: str = ""  # 花字 — burned-in caption at render time


class Timeline(BaseModel):
    """The full edit: ordered clips + output format."""

    clips: list[Clip]
    ratio: str = "9:16"
    size: str = "1080x1920"
    platform: str = ""
    total_duration: float = 0.0


class DraftSegment(BaseModel):
    """A clip placed at an absolute timeline offset, ready for a draft builder."""

    source_url: str
    start: float  # absolute start on the main track, seconds
    duration: float  # seconds
    transition_in: str = ""  # transition from the PREVIOUS clip into this one
    caption: str = ""  # 花字 burned at render time (from the shot's onscreen_text)


class DraftPlan(BaseModel):
    """Flattened, builder-agnostic plan: pixel canvas + absolutely-placed clips.

    Bridges the Timeline IR and a concrete editor (剪映 / FFmpeg). It resolves
    the pixel canvas and the per-clip start offsets so the render skill is a
    straight translation with no timing math of its own.
    """

    width: int
    height: int
    fps: int = 30
    segments: list[DraftSegment]
