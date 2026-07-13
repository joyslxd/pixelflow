"""PromptEngine：把 Brief 的单个 shot 扩写成 Seedance 视频 prompt。

CREATIVE 阶段的 LLM 已经产出了结构化 shot，包括动作、镜头、场景，以及 Brief
级别的 ``global_visual``（风格、光线、环境、连续性、禁止元素）。这里只是把
这些字段拼成 Seedance 2.0 更容易理解的 prompt 形态：构图/动作、时间段镜头、
风格、一致性、负向约束。

这是纯逻辑，不再额外调用 LLM，所以可以离线测试。未来如果需要更强的文案扩写，
可以在保持同样函数签名的前提下替换为 LLM 版本。
"""

from __future__ import annotations

_NO_TEXT = "无字幕、无水印、无画面生成文字"
_MAX_CHARS = 2000  # Seedance 单条 prompt 的字符上限。


def _join(sep: str, parts: list) -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def build_seedance_prompt(shot: dict, global_visual: dict | None = None, duration: float = 0.0, *, max_chars: int = _MAX_CHARS) -> str:
    """为单个 shot 构造结构化 Seedance prompt。

    空字段会被跳过；负向约束行始终存在，至少禁止画面文字/字幕/水印。最终结果会
    截断到 ``max_chars``，避免第三方接口拒收过长 prompt。
    """
    gv = global_visual or {}
    core = (shot.get("generation_prompt") or shot.get("visual_description") or "").strip()
    camera = _join("，", [shot.get("camera_movement"), shot.get("shot_type")])
    style = _join("，", [gv.get("overall_style"), gv.get("lighting"), gv.get("environment")])
    continuity = _join("、", [gv.get("subject_type"), gv.get("character_style")])
    forbidden = (gv.get("forbidden_elements") or "").strip()

    lines: list[str] = []
    if core:
        lines.append(core if core.endswith(("。", ".", "!", "！", "?", "？")) else core + "。")
    if camera:
        d = f"{duration:g}" if duration else ""
        lines.append(f"0-{d}s：{camera}。" if d else f"镜头：{camera}。")
    if style:
        lines.append(f"风格：{style}。")
    if continuity:
        lines.append(f"一致性：保持{continuity}与光线统一。")
    lines.append(f"负向：{_join('；', [forbidden, _NO_TEXT])}。")

    return "\n".join(lines)[:max_chars]
