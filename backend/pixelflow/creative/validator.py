"""Brief 硬约束校验器，纯逻辑实现（PRD §9.5）。

这里不调用 LLM。输入一个 LLM 生成的 ``Brief``，按 PRD 的八条硬约束逐条检查。
可以用确定性逻辑安全修复的地方会直接修复；需要语义判断的地方，比如自由文本
里是否真的包含禁止元素、商品是否真实呈现，只标成 ``warn``，交给 Brief 人工
确认或后续 AI 改写。

返回值包含修复后的 Brief 和问题列表，问题结构为::

    {"rule": str, "level": "fixed" | "warn", "shot_id": str | None, "message": str}
"""

from __future__ import annotations

import re

from .models import Brief, HardConstraints, Shot, ShotAudio

# 总时长校验容忍度，PRD §9.5 要求允许 ±2 秒。
DURATION_TOLERANCE_SEC = 2.0
# hook 镜头需要快速抓注意力，PRD §9.5 将它限制在 3 秒内。
MAX_HOOK_DURATION_SEC = 3.0


def _issue(rule: str, level: str, message: str, shot_id: str | None = None) -> dict:
    return {"rule": rule, "level": level, "shot_id": shot_id, "message": message}


def _check_first_is_hook(brief: Brief, issues: list[dict]) -> None:
    """规则 1a：第一镜必须是 hook；能前移已有 hook 就自动前移，否则 warn。"""
    shots = brief.shots
    if not shots or shots[0].scene_type == "hook":
        return
    # 如果 Brief 中已经有 hook 镜头，只是位置不对，则把第一个 hook 前移到首位。
    hook_idx = next((i for i, s in enumerate(shots) if s.scene_type == "hook"), None)
    if hook_idx is not None:
        shot = shots.pop(hook_idx)
        shots.insert(0, shot)
        issues.append(_issue("first_shot_must_be_hook", "fixed", "首镜非 hook，已将 hook 镜头前移", shot.shot_id))
    else:
        issues.append(_issue("first_shot_must_be_hook", "warn", "无 hook 镜头，需人工补充开场", shots[0].shot_id))


def _clamp_hook_duration(brief: Brief, issues: list[dict]) -> None:
    """规则 1b：hook 时长必须 ≤3 秒。

    这个函数必须在总时长缩放之后执行，否则缩放可能又把 hook 拉回 3 秒以上。
    """
    shots = brief.shots
    if not shots or shots[0].scene_type != "hook" or shots[0].duration <= MAX_HOOK_DURATION_SEC:
        return
    old = shots[0].duration
    shots[0].duration = MAX_HOOK_DURATION_SEC
    issues.append(_issue("first_shot_must_be_hook", "fixed", f"hook 镜头时长 {old}s 过长，已收紧至 {MAX_HOOK_DURATION_SEC}s", shots[0].shot_id))


def _check_last_is_cta(brief: Brief, issues: list[dict]) -> None:
    """规则 2：最后一镜必须是 cta；缺失时自动补一个行动号召镜头。"""
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
    """规则 3：总时长要匹配目标时长；超出容忍度时按比例缩放各镜头。"""
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
    """规则 4 和 5：旁白 ≤50 字、花字 ≤20 字；超长时直接截断。"""
    hc: HardConstraints = brief.hard_constraints
    for s in brief.shots:
        if len(s.narration_text) > hc.max_narration_length:
            s.narration_text = s.narration_text[: hc.max_narration_length]
            issues.append(_issue("max_narration_length", "fixed", f"旁白超过 {hc.max_narration_length} 字，已截断", s.shot_id))
        if len(s.onscreen_text) > hc.max_onscreen_length:
            s.onscreen_text = s.onscreen_text[: hc.max_onscreen_length]
            issues.append(_issue("max_onscreen_length", "fixed", f"花字超过 {hc.max_onscreen_length} 字，已截断", s.shot_id))


def _check_forbidden_elements(brief: Brief, issues: list[dict]) -> None:
    """规则 6：不能出现禁止元素。

    禁止元素的语义改写不适合在纯逻辑里做，所以这里只做命中提示。
    """
    forbidden = [t.strip() for t in brief.global_visual.forbidden_elements.replace("，", ",").split(",") if t.strip()]
    if not forbidden:
        return
    for s in brief.shots:
        hit = next((t for t in forbidden if t in s.visual_description), None)
        if hit:
            issues.append(_issue("forbidden_elements", "warn", f"画面描述包含禁止元素「{hit}」，需 AI 改写", s.shot_id))


_SUBTITLE_MARKER = "no text, no caption, no watermark"
# ``generation_prompt`` 按系统提示要求可能是英文，因此这里用英文单词边界匹配
# text/caption/subtitle/watermark，避免 texture/context 这类词误命中 text。
_SUBTITLE_EN = re.compile(r"\b(text|caption|subtitles?|watermark)\b", re.IGNORECASE)
_SUBTITLE_ZH = ("画面生成文字", "生成字幕")


def _wants_onscreen_text(prompt: str) -> bool:
    return any(t in prompt for t in _SUBTITLE_ZH) or bool(_SUBTITLE_EN.search(prompt))


def _check_subtitle_strategy(brief: Brief, issues: list[dict]) -> None:
    """规则 7：字幕策略合规，生成模型不能直接在画面里渲染文字。"""
    for s in brief.shots:
        if _SUBTITLE_MARKER in s.generation_prompt:
            continue  # 已经注入过负向约束，重复校验时保持幂等。
        if _wants_onscreen_text(s.generation_prompt):
            s.generation_prompt = f"{s.generation_prompt.rstrip('. ')}. {_SUBTITLE_MARKER}"
            issues.append(_issue("subtitle_strategy", "fixed", "提示词要求画面生成文字，已注入负向约束", s.shot_id))


def _check_product_authenticity(brief: Brief, issues: list[dict], product_info: dict | None) -> None:
    """规则 8：商品真实性。

    是否真实呈现商品需要结合视觉语义判断，纯逻辑只能提示人工确认。
    """
    if not product_info:
        return
    name = product_info.get("name") or product_info.get("product_name")
    if not name:
        return
    if not any(name in s.visual_description for s in brief.shots if s.asset_strategy in ("use_real_asset", "mixed")):
        issues.append(_issue("product_authenticity", "warn", f"未发现明确呈现商品「{name}」的真实镜头，请人工确认", None))


def validate_and_fix(brief: Brief, product_info: dict | None = None) -> tuple[Brief, list[dict]]:
    """运行所有硬约束校验，并在安全时自动修复。

    为避免污染调用方原始对象，这里先深拷贝 Brief，再返回修复副本和问题列表。
    上层用“是否存在 warn 级问题”来计算 ``brief_valid``；fixed 级问题代表已解决。
    """
    fixed = brief.model_copy(deep=True)
    issues: list[dict] = []
    _check_first_is_hook(fixed, issues)
    _check_last_is_cta(fixed, issues)
    _check_total_duration(fixed, issues)
    _clamp_hook_duration(fixed, issues)  # 放在缩放之后，确保 hook 的 3 秒限制最终生效。
    _check_text_lengths(fixed, issues)
    _check_forbidden_elements(fixed, issues)
    _check_subtitle_strategy(fixed, issues)
    _check_product_authenticity(fixed, issues, product_info)
    return fixed, issues
