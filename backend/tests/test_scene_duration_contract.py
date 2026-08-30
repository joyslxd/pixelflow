from __future__ import annotations

import pytest
from pydantic import ValidationError

from pixelflow.agent_tools.video.scene import SceneMutablePatch
from pixelflow.agent_tools.video.storyboard import (
    CreateStoryboardTool,
    PrepareScenePackagesInput,
)
from pixelflow.video.adapters.operations.scenes import _resolve_duration_sec


def test_scene_patch_allows_seedance_maximum_thirty_seconds() -> None:
    patch = SceneMutablePatch(duration_sec=30)
    assert patch.duration_sec == 30


def test_scene_patch_rejects_duration_above_thirty_seconds() -> None:
    with pytest.raises(ValidationError):
        SceneMutablePatch(duration_sec=30.1)


def test_operation_adapter_accepts_thirty_second_scene() -> None:
    assert _resolve_duration_sec({"duration_ms": 30_000}) == 30
    assert _resolve_duration_sec({"duration_sec": 31}) is None


def test_storyboard_contract_allows_six_thirty_second_scenes() -> None:
    request = PrepareScenePackagesInput(
        script="三分钟家庭产品片",
        asset_registry=({"asset_id": "product", "kind": "product", "role": "冰箱", "generation_prompt": "冰箱产品设定图"},),
        scenes=tuple(
            {"scene_id": str(index), "prompt": "家庭场景", "duration_sec": 30, "reference_asset_ids": ["product"]}
            for index in range(1, 7)
        ),
    )
    assert sum(scene.duration_sec for scene in request.scenes) == 180
    assert CreateStoryboardTool.spec.name == "create_storyboard"


def test_storyboard_contract_allows_long_form_plan_for_batch_splitting() -> None:
    request = PrepareScenePackagesInput(
        script="长片",
        asset_registry=({"asset_id": "product", "kind": "product", "role": "冰箱", "generation_prompt": "冰箱产品设定图"},),
        scenes=tuple(
            {"scene_id": str(index), "prompt": "场景", "duration_sec": 26, "reference_asset_ids": ["product"]}
            for index in range(1, 18)
        ),
    )
    assert sum(scene.duration_sec for scene in request.scenes) == 442
