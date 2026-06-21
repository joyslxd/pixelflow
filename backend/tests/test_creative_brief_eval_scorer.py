from __future__ import annotations

from pixelflow.creative.models import Brief, GlobalVisual, Shot, ShotAudio
from pixelflow.evals.creative_brief import score_brief


def _shot(
    shot_id: str,
    scene_type: str,
    *,
    duration: float,
    visual_description: str,
    generation_prompt: str = "clean product lifestyle shot",
    asset_strategy: str = "use_real_asset",
) -> Shot:
    return Shot(
        shot_id=shot_id,
        time_range="",
        duration=duration,
        shot_type="近景",
        camera_movement="推",
        visual_description=visual_description,
        generation_prompt=generation_prompt,
        audio=ShotAudio(),
        scene_type=scene_type,
        asset_strategy=asset_strategy,
    )


def _brief(shots: list[Shot], *, duration_sec: int = 15) -> Brief:
    return Brief(
        brief_id="eval",
        global_visual=GlobalVisual(
            subject_type="商品",
            environment="厨房",
            lighting="自然光",
            character_style="简洁",
            overall_style="生活方式",
            forbidden_elements="",
        ),
        shots=shots,
        platform="douyin",
        duration_sec=duration_sec,
    )


def test_clean_brief_passes_gate():
    brief = _brief(
        [
            _shot("shot_001", "hook", duration=3.0, visual_description="保温杯真实特写"),
            _shot("shot_002", "demo", duration=8.0, visual_description="保温杯倒水演示"),
            _shot("shot_003", "cta", duration=4.0, visual_description="保温杯与购买提示"),
        ]
    )

    result = score_brief(brief, product_info={"product_name": "保温杯"}, video_params={"duration_sec": 15})

    assert result.passed
    assert result.score == 1.0
    assert result.metrics["validator_clean"]


def test_brief_with_validator_repairs_is_penalized_and_rejected():
    brief = _brief(
        [
            _shot("shot_001", "demo", duration=12.0, visual_description="普通演示"),
            _shot("shot_002", "hook", duration=6.0, visual_description="抽象开场"),
        ]
    )

    result = score_brief(brief, product_info={"product_name": "保温杯"}, video_params={"duration_sec": 15})

    assert not result.passed
    assert result.score < 0.9
    assert result.metrics["fixed_count"] > 0


def test_reference_mode_requires_reference_or_mixed_strategy():
    brief = _brief(
        [
            _shot("shot_001", "hook", duration=3.0, visual_description="保温杯真实特写"),
            _shot("shot_002", "demo", duration=8.0, visual_description="保温杯倒水演示"),
            _shot("shot_003", "cta", duration=4.0, visual_description="保温杯与购买提示"),
        ]
    )

    result = score_brief(brief, product_info={"product_name": "保温杯"}, video_params={"duration_sec": 15}, creative_mode="reference")

    assert not result.passed
    assert not result.metrics["reference_strategy"]

