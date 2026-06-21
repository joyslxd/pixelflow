"""Deterministic scoring for CREATIVE Brief optimization.

SkillOpt should train prompt/skill text, not production code. This module gives
the optimizer a stable validation gate over generated Brief objects by reusing
PixelFlow's existing pure-logic validator and adding a few business-level
checks that are cheap, deterministic, and offline-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pixelflow.creative.models import Brief
from pixelflow.creative.validator import validate_and_fix

CreativeMode = Literal["original", "reference", "attribution"]

_MIDDLE_SCENES = {"pain_point", "solution", "demo", "social_proof"}
_TEXT_RENDER_MARKERS = (
    "text overlay",
    "caption",
    "subtitle",
    "subtitles",
    "watermark",
    "画面生成文字",
    "生成字幕",
    "字幕",
    "水印",
)


@dataclass(frozen=True)
class BriefScore:
    """Normalized score plus gate details for a generated Brief."""

    score: float
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)


def _coerce_brief(brief: Brief | dict[str, Any]) -> Brief:
    return brief if isinstance(brief, Brief) else Brief.model_validate(brief)


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(n.lower() in lowered for n in needles)


def _product_name(product_info: dict[str, Any] | None) -> str:
    if not product_info:
        return ""
    return str(product_info.get("name") or product_info.get("product_name") or "").strip()


def _has_product_anchor(brief: Brief, product_info: dict[str, Any] | None) -> bool:
    name = _product_name(product_info)
    if not name:
        return True
    for shot in brief.shots:
        if shot.asset_strategy not in ("use_real_asset", "mixed"):
            continue
        text = f"{shot.visual_description}\n{shot.generation_prompt}"
        if name in text:
            return True
    return False


def _reference_strategy_ok(brief: Brief, creative_mode: CreativeMode) -> bool:
    strategies = {shot.asset_strategy for shot in brief.shots}
    if creative_mode == "original":
        return "use_reference_structure" not in strategies
    return bool(strategies & {"use_reference_structure", "mixed"})


def _has_middle_arc(brief: Brief) -> bool:
    if brief.duration_sec < 12:
        return True
    return any(shot.scene_type in _MIDDLE_SCENES for shot in brief.shots)


def _generation_prompts_are_clean(brief: Brief) -> bool:
    return not any(_contains_any(shot.generation_prompt, _TEXT_RENDER_MARKERS) for shot in brief.shots)


def score_brief(
    brief: Brief | dict[str, Any],
    *,
    product_info: dict[str, Any] | None = None,
    video_params: dict[str, Any] | None = None,
    creative_mode: CreativeMode = "original",
) -> BriefScore:
    """Score a generated Brief for SkillOpt validation.

    A pass means the Brief is safe enough to accept as an improvement candidate:
    no semantic validator warnings, no deterministic repairs, and all extra
    business checks pass. The numeric score is intentionally smoother so
    SkillOpt can compare rejected candidates and learn from near misses.
    """

    candidate = _coerce_brief(brief)
    _, validator_issues = validate_and_fix(candidate, product_info=product_info)

    fixed_count = sum(1 for issue in validator_issues if issue["level"] == "fixed")
    warn_count = sum(1 for issue in validator_issues if issue["level"] == "warn")

    target_duration = int((video_params or {}).get("duration_sec") or candidate.duration_sec or 0)
    actual_duration = round(sum(shot.duration for shot in candidate.shots), 2)
    duration_drift = abs(actual_duration - target_duration) if target_duration else 0.0

    checks = {
        "validator_clean": fixed_count == 0 and warn_count == 0,
        "product_anchor": _has_product_anchor(candidate, product_info),
        "reference_strategy": _reference_strategy_ok(candidate, creative_mode),
        "middle_arc": _has_middle_arc(candidate),
        "generation_prompts_clean": _generation_prompts_are_clean(candidate),
        "duration_within_2s": duration_drift <= 2.0,
        "shot_count_reasonable": 2 <= len(candidate.shots) <= max(2, int(target_duration // 3) + 3),
    }

    penalties = 0.0
    penalties += min(0.32, fixed_count * 0.08)
    penalties += min(0.54, warn_count * 0.18)
    penalties += 0.12 if not checks["product_anchor"] else 0.0
    penalties += 0.10 if not checks["reference_strategy"] else 0.0
    penalties += 0.08 if not checks["middle_arc"] else 0.0
    penalties += 0.14 if not checks["generation_prompts_clean"] else 0.0
    penalties += min(0.20, max(0.0, duration_drift - 2.0) * 0.03)
    penalties += 0.06 if not checks["shot_count_reasonable"] else 0.0

    score = max(0.0, round(1.0 - penalties, 4))
    passed = score >= 0.9 and all(checks.values())

    metrics = {
        "fixed_count": fixed_count,
        "warn_count": warn_count,
        "actual_duration": actual_duration,
        "target_duration": target_duration,
        "duration_drift": round(duration_drift, 2),
        "shot_count": len(candidate.shots),
        **checks,
    }
    return BriefScore(score=score, passed=passed, metrics=metrics, issues=validator_issues)

