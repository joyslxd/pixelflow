"""Segment planning for the GENERATE phase (pure logic).

seedance-2.0 generates a coherent clip up to 15s in a single call, so a short
video should NOT be fragmented into one generation per shot (independent clips
hard-cut together, and N×min-duration costs more than one continuous clip).

``plan_segments`` groups consecutive Brief shots into the fewest segments that
each fit the vendor's single-call ceiling; ``build_segment_prompt`` fuses a
segment's shots into one time-coded prompt (global visual stated once). A ≤15s
video becomes a single segment / single call; longer videos become several
segments generated in parallel and concatenated downstream.

Pure and deterministic: no I/O, fully testable offline.
"""

from __future__ import annotations

from typing import Any

_NO_TEXT = "无字幕、无水印、无画面生成文字"
_MAX_CHARS = 2000  # Seedance per-prompt character ceiling


def _join(sep: str, parts: list) -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def plan_segments(shots: list[dict], max_sec: float) -> list[dict[str, Any]]:
    """Group consecutive shots into segments that each fit ``max_sec``.

    Greedy: extend the current segment until the next shot would overflow, then
    start a new one. A single shot longer than ``max_sec`` still gets its own
    segment (the caller clamps the generation duration). Returns a list of
    ``{"index", "shot_indices", "shots", "duration"}`` in playback order.
    """
    segments: list[dict[str, Any]] = []
    current: list[int] = []
    current_dur = 0.0
    for i, shot in enumerate(shots):
        dur = float(shot.get("duration", 0.0) or 0.0)
        if current and current_dur + dur > max_sec:
            segments.append(_segment(len(segments), current, shots))
            current, current_dur = [], 0.0
        current.append(i)
        current_dur += dur
    if current:
        segments.append(_segment(len(segments), current, shots))
    return segments


def _segment(index: int, shot_indices: list[int], shots: list[dict]) -> dict[str, Any]:
    seg_shots = [shots[i] for i in shot_indices]
    duration = round(sum(float(s.get("duration", 0.0) or 0.0) for s in seg_shots), 2)
    return {"index": index, "shot_indices": list(shot_indices), "shots": seg_shots, "duration": duration}


def build_segment_prompt(shots: list[dict], global_visual: dict | None = None, *, max_chars: int = _MAX_CHARS) -> str:
    """Fuse a segment's shots into one Seedance prompt.

    The shared ``global_visual`` (style/lighting/environment/continuity/forbidden)
    is stated once; each shot becomes a cumulative time-coded action line so the
    model produces one continuous multi-scene clip. The negative-constraints line
    is always present (at minimum forbidding on-screen text).
    """
    gv = global_visual or {}
    style = _join("，", [gv.get("overall_style"), gv.get("lighting"), gv.get("environment")])
    continuity = _join("、", [gv.get("subject_type"), gv.get("character_style")])
    forbidden = (gv.get("forbidden_elements") or "").strip()

    lines: list[str] = []
    if style:
        lines.append(f"整体风格：{style}。")
    lines.append("分镜序列：")
    t = 0.0
    for shot in shots:
        dur = float(shot.get("duration", 0.0) or 0.0)
        action = (shot.get("generation_prompt") or shot.get("visual_description") or "").strip()
        camera = _join("，", [shot.get("camera_movement"), shot.get("shot_type")])
        body = _join("；", [action, f"镜头：{camera}" if camera else ""])
        lines.append(f"{t:g}-{t + dur:g}s：{body}。")
        t += dur
    if continuity:
        lines.append(f"一致性：全程保持{continuity}与光线统一，分镜之间自然过渡。")
    lines.append(f"负向：{_join('；', [forbidden, _NO_TEXT])}。")

    return "\n".join(lines)[:max_chars]
