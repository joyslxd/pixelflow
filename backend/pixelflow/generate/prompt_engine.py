"""PromptEngine — expand a Brief shot into a Seedance video prompt (pure logic).

The CREATIVE LLM already produces structured shots (action, camera, scene) plus
a Brief-level ``global_visual`` (style, lighting, environment, continuity,
forbidden elements). This assembles those fields into Seedance 2.0's preferred
prompt shape — composition + time-coded action/camera + style + continuity +
negative constraints — so the generation call gets a well-formed prompt without
another LLM round-trip.

Deterministic and offline-testable. A future LLM-based PromptEngine could swap
in behind the same signature if richer prose expansion is needed.
"""

from __future__ import annotations

_NO_TEXT = "无字幕、无水印、无画面生成文字"
_MAX_CHARS = 2000  # Seedance per-prompt character ceiling


def _join(sep: str, parts: list) -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def build_seedance_prompt(shot: dict, global_visual: dict | None = None, duration: float = 0.0, *, max_chars: int = _MAX_CHARS) -> str:
    """Build a structured Seedance prompt for one shot.

    Empty fields are skipped; the negative-constraints line is always present
    (per the Seedance methodology) and at minimum forbids on-screen text. The
    result is capped at ``max_chars``.
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
