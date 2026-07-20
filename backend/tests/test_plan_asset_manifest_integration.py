from __future__ import annotations

import copy

from pixelflow.creative.plan_markdown import build_plan_markdown, restore_plan_version


VIDEO_FORM = {
    "product_info": "黑色防水背包",
    "product_category": "箱包",
    "target_audience": "城市通勤者",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 20,
    "video_ratio": "9:16",
    "video_model": "seedance-2.0",
    "video_size": "1080p",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["9:16"],
        "sizes": ["4K"],
    },
    "video_usage": "商品宣传",
    "visual_style": "写实电影感",
}

DIRECTION = {
    "direction_id": "direction_1",
    "title": "雨夜通勤实测",
    "description": "通勤者在雨夜公交站用防水背包完成实测。",
}


def test_fallback_video_plan_publishes_manifest_in_result_markdown_and_history() -> None:
    result = build_plan_markdown("video", VIDEO_FORM, DIRECTION)

    assert result.asset_manifest["characters"]
    assert result.asset_manifest["scenes"]
    assert result.asset_manifest["props"]
    assert "## 四、全局资产清单" in result.plan_markdown
    assert result.asset_manifest == result.to_dict()["asset_manifest"]
    assert result.asset_manifest == result.plan_history[0]["asset_manifest"]

    for collection in ("characters", "scenes", "props"):
        manifest_names = {item["name"] for item in result.asset_manifest[collection]}
        referenced_names = {
            name
            for blueprint in result.scene_blueprints
            for name in blueprint["asset_requirements"][collection]
        }
        assert manifest_names == referenced_names


def test_next_version_snapshots_asset_manifest_without_sharing_nested_state() -> None:
    original = build_plan_markdown("video", VIDEO_FORM, DIRECTION)
    next_manifest = copy.deepcopy(original.asset_manifest)
    next_manifest["props"][0]["description"] = "修订后的固定道具说明。"

    revised = original.next_version(
        plan_markdown=original.plan_markdown,
        asset_manifest=next_manifest,
    )
    next_manifest["props"][0]["description"] = "调用方再次污染"
    revised.asset_manifest["props"][0]["description"] = "结果对象被修改"

    assert revised.plan_history[-1]["asset_manifest"]["props"][0]["description"] == "修订后的固定道具说明。"
    assert original.asset_manifest["props"][0]["description"] != "修订后的固定道具说明。"


def test_restore_plan_version_restores_matching_asset_manifest_without_appending() -> None:
    original = build_plan_markdown("video", VIDEO_FORM, DIRECTION)
    v1_manifest = copy.deepcopy(original.asset_manifest)
    v2_manifest = copy.deepcopy(v1_manifest)
    v2_manifest["props"][0]["description"] = "第二版道具说明。"
    history = [
        {
            **copy.deepcopy(original.plan_history[0]),
            "version": 1,
            "asset_manifest": v1_manifest,
        },
        {
            **copy.deepcopy(original.plan_history[0]),
            "version": 2,
            "plan_markdown": original.plan_markdown + "\n第二版",
            "asset_manifest": v2_manifest,
        },
    ]

    restored = restore_plan_version(
        intent="video",
        current_plan_markdown=history[1]["plan_markdown"],
        current_plan_version=2,
        plan_history=history,
        restore_version=1,
        creation_contract=original.creation_contract,
        scene_durations_sec=original.scene_durations_sec,
        scene_blueprints=original.scene_blueprints,
        asset_manifest=v2_manifest,
    )

    assert restored.asset_manifest == v1_manifest
    assert len(restored.plan_history) == 2
    restored.asset_manifest["props"][0]["description"] = "污染恢复结果"
    assert history[0]["asset_manifest"] == v1_manifest
