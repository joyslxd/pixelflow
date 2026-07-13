"""GENERATE 阶段的 segment 规划，纯逻辑实现。

Seedance v2 skill 当前限制单次生成最长 10 秒。短视频不应该拆成“每个 shot 调一次
生成”：独立片段硬拼会破坏连贯性，且每段都有最小时长成本。

``plan_segments`` 会把连续 Brief shots 贪心合并成尽量少的 segment，并保证每个
segment 不超过调用方传入的第三方单次时长上限。``build_segment_prompt`` 再把一个
segment 内的多个 shot 融合成带时间码的 prompt，global visual 只描述一次。短视频
通常是单 segment/单调用，较长视频会拆为多个 segment 并行生成，再交给 EDIT 拼接。

这是纯逻辑，不做 I/O，输出完全由入参决定，方便离线单测。
"""

from __future__ import annotations

from typing import Any

_NO_TEXT = "无字幕、无水印、无画面生成文字"
_MAX_CHARS = 2000  # Seedance 单条 prompt 的字符上限。


def _join(sep: str, parts: list) -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def plan_segments(shots: list[dict], max_sec: float) -> list[dict[str, Any]]:
    """把连续 shots 合并为每段不超过 ``max_sec`` 的 segments。

    算法是贪心：能放进当前 segment 就继续放；下一个 shot 会超长时，就结算当前
    segment 并开启新 segment。单个 shot 自身超过 ``max_sec`` 时仍会单独成段，
    由调用方负责夹取合法生成时长。返回值按播放顺序排列，字段包含
    ``index``、``shot_indices``、``shots``、``duration``。
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
    """把一个 segment 内的多个 shots 融合成一条 Seedance prompt。

    共享的 ``global_visual``（风格、光线、环境、连续性、禁止元素）只写一次；
    每个 shot 转成累计时间码动作行，让模型生成一段连续的多场景视频。负向约束行
    始终存在，至少禁止画面文字/字幕/水印。
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
