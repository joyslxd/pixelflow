"""brief_constraint_validator — pure-logic hard-constraint enforcement (PRD §9.5).

No LLM here. Given a generated :class:`Brief`, run the eight hard-constraint
checks and auto-fix what can be fixed deterministically. Checks that need
semantic judgement (forbidden elements in free text, product authenticity)
cannot be safely auto-fixed in pure logic, so they are surfaced as ``warn``
issues for the human Brief-review gate / a downstream AI rewrite.

Returns the (possibly mutated) Brief plus a list of issue records::

    {"rule": str, "level": "fixed" | "warn", "shot_id": str | None, "message": str}
"""

from __future__ import annotations

import re

from .models import Brief, HardConstraints, Shot, ShotAudio

# Tolerance (seconds) for the total-duration check. PRD §9.5 uses ±2s.
DURATION_TOLERANCE_SEC = 2.0
# A hook shot should grab attention fast; PRD §9.5 caps its length.
MAX_HOOK_DURATION_SEC = 3.0


def _issue(rule: str, level: str, message: str, shot_id: str | None = None) -> dict:
    return {"rule": rule, "level": level, "shot_id": shot_id, "message": message}


def _check_first_is_hook(brief: Brief, issues: list[dict]) -> None:
    """Rule 1a: 开头是 hook — first shot must be a hook (reorder, or warn)."""
    shots = brief.shots
    if not shots or shots[0].scene_type == "hook":
        return
    # Reorder: pull the first hook shot to the front if one exists.
    hook_idx = next((i for i, s in enumerate(shots) if s.scene_type == "hook"), None)
    if hook_idx is not None:
        shot = shots.pop(hook_idx)
        shots.insert(0, shot)
        issues.append(_issue("first_shot_must_be_hook", "fixed", "首镜非 hook，已将 hook 镜头前移", shot.shot_id))
    else:
        issues.append(_issue("first_shot_must_be_hook", "warn", "无 hook 镜头，需人工补充开场", shots[0].shot_id))


def _clamp_hook_duration(brief: Brief, issues: list[dict]) -> None:
    """Rule 1b: hook ≤3s. Runs AFTER duration scaling so the clamp is final
    (otherwise proportional scaling could silently push the hook back over 3s)."""
    shots = brief.shots
    if not shots or shots[0].scene_type != "hook" or shots[0].duration <= MAX_HOOK_DURATION_SEC:
        return
    old = shots[0].duration
    shots[0].duration = MAX_HOOK_DURATION_SEC
    issues.append(_issue("first_shot_must_be_hook", "fixed", f"hook 镜头时长 {old}s 过长，已收紧至 {MAX_HOOK_DURATION_SEC}s", shots[0].shot_id))


def _check_last_is_cta(brief: Brief, issues: list[dict]) -> None:
    """Rule 2: 结尾是 cta — auto-append a CTA shot when missing."""
    shots = brief.shots
    if shots and shots[-1].scene_type == "cta":
        return
    cta = Shot(
        shot_id=f"shot_{len(shots) + 1:03d}",
        time_range="",
        duration=2.0,
        shot_type="近景",
        camera_movement="固定",
        visual_description="商品主图 + 行动号召花字",
        generation_prompt="product hero shot, clean background, call to action",
        narration_text="立即下单，先到先得",
        onscreen_text="点击购买",
        audio=ShotAudio(),
        scene_type="cta",
        asset_strategy="use_real_asset",
        transition_in="淡入",
        transition_out="淡出",
    )
    shots.append(cta)
    issues.append(_issue("last_shot_must_be_cta", "fixed", "结尾缺少 cta，已自动补充 cta 镜头", cta.shot_id))


def _check_total_duration(brief: Brief, issues: list[dict]) -> None:
    """Rule 3: 总时长匹配 — scale shot durations to hit duration_sec within ±2s."""
    shots = brief.shots
    target = float(brief.duration_sec)
    total = sum(s.duration for s in shots)
    if total <= 0 or abs(total - target) <= DURATION_TOLERANCE_SEC:
        return
    factor = target / total
    for s in shots:
        s.duration = round(s.duration * factor, 2)
    issues.append(_issue("total_duration", "fixed", f"总时长 {round(total, 2)}s 偏离目标 {target}s，已按比例缩放至 {target}s", None))


def _check_text_lengths(brief: Brief, issues: list[dict]) -> None:
    """Rules 4 & 5: 旁白≤50字 / 花字≤20字 — truncate overflow."""
    hc: HardConstraints = brief.hard_constraints
    for s in brief.shots:
        if len(s.narration_text) > hc.max_narration_length:
            s.narration_text = s.narration_text[: hc.max_narration_length]
            issues.append(_issue("max_narration_length", "fixed", f"旁白超过 {hc.max_narration_length} 字，已截断", s.shot_id))
        if len(s.onscreen_text) > hc.max_onscreen_length:
            s.onscreen_text = s.onscreen_text[: hc.max_onscreen_length]
            issues.append(_issue("max_onscreen_length", "fixed", f"花字超过 {hc.max_onscreen_length} 字，已截断", s.shot_id))


def _check_forbidden_elements(brief: Brief, issues: list[dict]) -> None:
    """Rule 6: 无禁止元素 — flag (semantic rewrite is out of pure-logic scope)."""
    forbidden = [t.strip() for t in brief.global_visual.forbidden_elements.replace("，", ",").split(",") if t.strip()]
    if not forbidden:
        return
    for s in brief.shots:
        hit = next((t for t in forbidden if t in s.visual_description), None)
        if hit:
            issues.append(_issue("forbidden_elements", "warn", f"画面描述包含禁止元素「{hit}」，需 AI 改写", s.shot_id))


_SUBTITLE_MARKER = "no text, no caption, no watermark"
# generation_prompt is English (per the brief_generate system prompt); match on
# word boundaries so "texture"/"context" don't false-positive on "text".
_SUBTITLE_EN = re.compile(r"\b(text|caption|subtitles?|watermark)\b", re.IGNORECASE)
_SUBTITLE_ZH = ("画面生成文字", "生成字幕")


def _wants_onscreen_text(prompt: str) -> bool:
    return any(t in prompt for t in _SUBTITLE_ZH) or bool(_SUBTITLE_EN.search(prompt))


def _check_subtitle_strategy(brief: Brief, issues: list[dict]) -> None:
    """Rule 7: 字幕策略合规 — generation_prompt must not ask the model to render text."""
    for s in brief.shots:
        if _SUBTITLE_MARKER in s.generation_prompt:
            continue  # already negated — idempotent on re-validation
        if _wants_onscreen_text(s.generation_prompt):
            s.generation_prompt = f"{s.generation_prompt.rstrip('. ')}. {_SUBTITLE_MARKER}"
            issues.append(_issue("subtitle_strategy", "fixed", "提示词要求画面生成文字，已注入负向约束", s.shot_id))


def _check_product_authenticity(brief: Brief, issues: list[dict], product_info: dict | None) -> None:
    """Rule 8: 商品真实性 — warn only; requires human confirmation."""
    if not product_info:
        return
    name = product_info.get("name") or product_info.get("product_name")
    if not name:
        return
    if not any(name in s.visual_description for s in brief.shots if s.asset_strategy in ("use_real_asset", "mixed")):
        issues.append(_issue("product_authenticity", "warn", f"未发现明确呈现商品「{name}」的真实镜头，请人工确认", None))


def validate_and_fix(brief: Brief, product_info: dict | None = None) -> tuple[Brief, list[dict]]:
    """Run all eight hard-constraint checks, auto-fixing in place where safe.

    The Brief is mutated and returned alongside the issue list. ``brief_valid``
    is the absence of any ``warn``-level issue (fixed issues are resolved).
    """
    fixed = brief.model_copy(deep=True)
    issues: list[dict] = []
    _check_first_is_hook(fixed, issues)
    _check_last_is_cta(fixed, issues)
    _check_total_duration(fixed, issues)
    _clamp_hook_duration(fixed, issues)  # after scaling: the clamp is the final word
    _check_text_lengths(fixed, issues)
    _check_forbidden_elements(fixed, issues)
    _check_subtitle_strategy(fixed, issues)
    _check_product_authenticity(fixed, issues, product_info)
    return fixed, issues
