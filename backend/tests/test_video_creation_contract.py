from __future__ import annotations

import pytest
from pydantic import ValidationError

from pixelflow.creative.contract import (
    VideoCreationContract,
    build_video_creation_contract,
    resolve_scene_image_spec,
)
from pixelflow.creative.duration import scene_time_ranges, split_video_duration

VALID_VIDEO_FORM = {
    "product_info": "AuroraFit 智能健康戒指",
    "product_category": "数码3C",
    "target_audience": "25-35 岁健康管理人群",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 180,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_model_capabilities": {
        "generation_types": ["文生视频", "首尾帧", "全能参考"],
        "upload_file_types": ["JPG", "PNG", "MP4"],
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影感写实",
}


@pytest.mark.parametrize("total", [4, 30, 60, 90, 180, 300])
def test_split_video_duration_is_exact_for_all_supported_boundaries(total: int) -> None:
    durations = split_video_duration(total)

    assert sum(durations) == total
    assert all(4 <= item <= 15 for item in durations)
    assert len(scene_time_ranges(durations)) == len(durations)
    assert scene_time_ranges(durations)[-1][1] == total


def test_split_video_duration_prefers_seedance_max_length_scenes() -> None:
    assert split_video_duration(60) == [15, 15, 15, 15]
    assert split_video_duration(30) == [15, 15]


@pytest.mark.parametrize("invalid", [0, 3, 301, 5.5, True, "30"])
def test_split_video_duration_rejects_invalid_values(invalid: object) -> None:
    with pytest.raises(ValueError, match="4.*300"):
        split_video_duration(invalid)  # type: ignore[arg-type]


def test_build_video_creation_contract_normalizes_confirmed_form() -> None:
    contract = build_video_creation_contract(VALID_VIDEO_FORM)

    assert contract.video_duration_sec == 180
    assert contract.video_ratio == "9:16"
    assert contract.video_model == "seedance-2.0"
    assert contract.video_model_capabilities.generation_types == ["文生视频", "首尾帧", "全能参考"]
    assert contract.video_model_capabilities.upload_file_types == ["JPG", "PNG", "MP4"]
    assert contract.image_model == "gpt-image-2"
    assert contract.image_model_capabilities.aspect_ratios == ["1:1", "16:9", "9:16"]
    assert contract.scene_image_ratio is None
    assert contract.scene_image_size is None


@pytest.mark.parametrize("model", ["seedance-2.0-mini", "seedance-2.0-fast"])
def test_creation_contract_accepts_dynamic_720p_capability_for_compact_seedance_models(model: str) -> None:
    contract = build_video_creation_contract(
        {
            **VALID_VIDEO_FORM,
            "video_model": model,
            "video_size": "720p",
            "video_model_capabilities": {
                "generation_types": ["文生视频", "首尾帧", "全能参考"],
                "upload_file_types": ["JPG", "PNG", "MP4"],
                "aspect_ratios": ["9:16", "16:9", "1:1"],
                "sizes": ["480p", "720p"],
                "sound_options": ["on", "off"],
                "durations_sec": list(range(4, 16)),
            },
        }
    )

    assert contract.video_model == model
    assert contract.video_size == "720p"
    assert contract.video_model_capabilities.sizes == ["480p", "720p"]


@pytest.mark.parametrize("model", ["seedance-2.0-mini", "seedance-2.0-fast"])
def test_creation_contract_rejects_1080p_outside_dynamic_model_capabilities(model: str) -> None:
    with pytest.raises(ValidationError, match="1080p.*480p.*720p"):
        build_video_creation_contract(
            {
                **VALID_VIDEO_FORM,
                "video_model": model,
                "video_size": "1080p",
                "video_model_capabilities": {
                    "generation_types": ["文生视频", "首尾帧", "全能参考"],
                    "upload_file_types": ["JPG", "PNG", "MP4"],
                    "aspect_ratios": ["9:16", "16:9", "1:1"],
                    "sizes": ["480p", "720p"],
                    "sound_options": ["on", "off"],
                    "durations_sec": list(range(4, 16)),
                },
            }
        )


@pytest.mark.parametrize("invalid", [3, 301, 9.5, "180", True])
def test_creation_contract_rejects_out_of_range_or_non_integer_duration(invalid: object) -> None:
    with pytest.raises((ValidationError, ValueError)):
        build_video_creation_contract({**VALID_VIDEO_FORM, "video_duration_sec": invalid})


def test_creation_contract_requires_distinct_video_and_image_models() -> None:
    contract = build_video_creation_contract(VALID_VIDEO_FORM)

    assert contract.video_model.startswith("seedance")
    assert contract.image_model == "gpt-image-2"
    assert contract.video_model != contract.image_model


def test_scene_image_spec_accepts_llm_values_supported_by_selected_image_model() -> None:
    contract = build_video_creation_contract(VALID_VIDEO_FORM)

    resolved, corrections = resolve_scene_image_spec(contract, "16:9", "2K")

    assert resolved.scene_image_ratio == "16:9"
    assert resolved.scene_image_size == "2K"
    assert resolved.scene_image_spec_source == "plan_llm"
    assert corrections == []


def test_scene_image_spec_corrects_llm_values_outside_selected_model_capabilities() -> None:
    contract = build_video_creation_contract(VALID_VIDEO_FORM)

    resolved, corrections = resolve_scene_image_spec(contract, "3:4", "8K")

    assert resolved.scene_image_ratio == "9:16"
    assert resolved.scene_image_size == "4K"
    assert resolved.scene_image_spec_source == "deterministic_fallback"
    assert len(corrections) == 2


def test_scene_image_spec_uses_first_capability_when_video_ratio_and_preferred_sizes_are_unavailable() -> None:
    contract = VideoCreationContract.model_validate(
        {
            **VALID_VIDEO_FORM,
            "image_model_capabilities": {
                "aspect_ratios": ["3:4"],
                "sizes": ["720p"],
            },
        }
    )

    resolved, corrections = resolve_scene_image_spec(contract, None, None)

    assert resolved.scene_image_ratio == "3:4"
    assert resolved.scene_image_size == "720p"
    assert resolved.scene_image_spec_source == "deterministic_fallback"
    assert corrections
