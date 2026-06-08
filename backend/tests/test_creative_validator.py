"""Tests for the pure-logic brief_constraint_validator (PRD §9.5).

The validator is deterministic and LLM-free, so these are plain unit tests over
``validate_and_fix``. They pin the eight hard-constraint checks and, in
particular, two interaction bugs found in review:
  * the hook ≤3s clamp must survive duration scaling (run last);
  * subtitle detection must match the English generation_prompt and be
    idempotent on re-validation.
"""

from __future__ import annotations

from pixelflow.creative.models import Brief, GlobalVisual, Shot, ShotAudio
from pixelflow.creative.validator import MAX_HOOK_DURATION_SEC, validate_and_fix


def _gv(forbidden: str = "") -> GlobalVisual:
    return GlobalVisual(
        subject_type="主体",
        environment="室内",
        lighting="柔光",
        character_style="休闲",
        overall_style="清新",
        forbidden_elements=forbidden,
    )


def _shot(shot_id: str, scene_type: str, asset_strategy: str = "use_real_asset", **kw) -> Shot:
    base = dict(
        shot_id=shot_id,
        time_range="",
        duration=kw.pop("duration", 5.0),
        shot_type="中景",
        camera_movement="固定",
        visual_description=kw.pop("visual_description", "画面"),
        generation_prompt=kw.pop("generation_prompt", "a clean product shot"),
        scene_type=scene_type,
        asset_strategy=asset_strategy,
        audio=ShotAudio(),
    )
    base.update(kw)
    return Shot(**base)


def _brief(shots: list[Shot], *, duration_sec: int = 30, forbidden: str = "") -> Brief:
    return Brief(brief_id="b", global_visual=_gv(forbidden), shots=shots, platform="douyin", duration_sec=duration_sec)


def _levels(issues, rule):
    return [i["level"] for i in issues if i["rule"] == rule]


# -- Rule 1a: first shot must be a hook (reorder / warn) --


def test_hook_reordered_to_front():
    brief = _brief([_shot("s1", "demo"), _shot("s2", "hook"), _shot("s3", "cta")])
    fixed, issues = validate_and_fix(brief)
    assert fixed.shots[0].scene_type == "hook"
    assert "fixed" in _levels(issues, "first_shot_must_be_hook")


def test_no_hook_warns():
    brief = _brief([_shot("s1", "demo"), _shot("s2", "cta")])
    _, issues = validate_and_fix(brief)
    assert "warn" in _levels(issues, "first_shot_must_be_hook")


# -- Rule 1b: hook clamp must be FINAL, surviving duration scaling (bug #1) --


def test_hook_clamp_survives_upward_scaling():
    # total = 3 + 1 = 4, target 30 -> scaling factor 7.5 would blow the hook to
    # 22.5s if the clamp ran before scaling. The clamp runs last, so it wins.
    brief = _brief([_shot("s1", "hook", duration=3.0), _shot("s2", "cta", duration=1.0)], duration_sec=30)
    fixed, _ = validate_and_fix(brief)
    assert fixed.shots[0].duration <= MAX_HOOK_DURATION_SEC


def test_hook_clamped_when_too_long():
    brief = _brief([_shot("s1", "hook", duration=8.0), _shot("s2", "cta", duration=5.0)], duration_sec=13)
    fixed, issues = validate_and_fix(brief)
    assert fixed.shots[0].duration == MAX_HOOK_DURATION_SEC
    assert "fixed" in _levels(issues, "first_shot_must_be_hook")


# -- Rule 2: last shot must be a cta (auto-append) --


def test_cta_appended_when_missing():
    brief = _brief([_shot("s1", "hook", duration=3.0), _shot("s2", "demo")])
    fixed, issues = validate_and_fix(brief)
    assert fixed.shots[-1].scene_type == "cta"
    assert "fixed" in _levels(issues, "last_shot_must_be_cta")


# -- Rule 3: total duration scaled to target within tolerance --


def test_total_duration_scaled():
    brief = _brief([_shot("s1", "hook", duration=3.0), _shot("s2", "demo", duration=50.0), _shot("s3", "cta", duration=10.0)], duration_sec=30)
    fixed, issues = validate_and_fix(brief)
    assert abs(sum(s.duration for s in fixed.shots) - 30) <= 2.0 + 1e-6
    assert "fixed" in _levels(issues, "total_duration")


# -- Rules 4 & 5: text truncation --


def test_text_truncation():
    brief = _brief(
        [
            _shot("s1", "hook", duration=3.0),
            _shot("s2", "cta", narration_text="旁" * 60, onscreen_text="字" * 30),
        ]
    )
    fixed, _ = validate_and_fix(brief)
    assert len(fixed.shots[1].narration_text) == 50
    assert len(fixed.shots[1].onscreen_text) == 20


# -- Rule 6: forbidden elements -> warn --


def test_forbidden_element_warns():
    brief = _brief(
        [_shot("s1", "hook", duration=3.0, visual_description="带水印的画面"), _shot("s2", "cta")],
        forbidden="水印，竞品logo",
    )
    _, issues = validate_and_fix(brief)
    assert "warn" in _levels(issues, "forbidden_elements")


# -- Rule 7: subtitle strategy detects English prompt and is idempotent (bug #2) --


def test_subtitle_strategy_detects_english():
    brief = _brief([_shot("s1", "hook", duration=3.0, generation_prompt="bold text overlay on screen"), _shot("s2", "cta")])
    fixed, issues = validate_and_fix(brief)
    assert "no text, no caption, no watermark" in fixed.shots[0].generation_prompt
    assert "fixed" in _levels(issues, "subtitle_strategy")


def test_subtitle_strategy_no_false_positive_on_texture():
    brief = _brief([_shot("s1", "hook", duration=3.0, generation_prompt="soft fabric texture, close up"), _shot("s2", "cta")])
    _, issues = validate_and_fix(brief)
    assert _levels(issues, "subtitle_strategy") == []


def test_subtitle_strategy_idempotent():
    brief = _brief([_shot("s1", "hook", duration=3.0, generation_prompt="caption banner"), _shot("s2", "cta")])
    once, _ = validate_and_fix(brief)
    twice, issues2 = validate_and_fix(once)
    assert once.shots[0].generation_prompt == twice.shots[0].generation_prompt
    assert _levels(issues2, "subtitle_strategy") == []


# -- Rule 8: product authenticity -> warn --


def test_product_authenticity_warns_when_absent():
    brief = _brief([_shot("s1", "hook", duration=3.0, visual_description="抽象背景"), _shot("s2", "cta", visual_description="号召")])
    _, issues = validate_and_fix(brief, product_info={"name": "保温杯"})
    assert "warn" in _levels(issues, "product_authenticity")


def test_product_authenticity_ok_when_present():
    brief = _brief(
        [_shot("s1", "hook", duration=3.0, visual_description="展示保温杯"), _shot("s2", "cta", visual_description="号召")],
    )
    _, issues = validate_and_fix(brief, product_info={"name": "保温杯"})
    assert _levels(issues, "product_authenticity") == []


# -- input is not mutated (validate works on a copy) --


def test_input_brief_not_mutated():
    brief = _brief([_shot("s1", "demo", duration=99.0), _shot("s2", "hook", duration=3.0)])
    validate_and_fix(brief)
    assert brief.shots[0].scene_type == "demo"
    assert brief.shots[0].duration == 99.0
