from __future__ import annotations

import asyncio
import copy
import json

from pixelflow.creative.plan_markdown import build_plan_markdown, build_plan_markdown_with_llm, restore_plan_version


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


def test_initial_plan_retries_invalid_blueprints_without_losing_explicit_named_assets() -> None:
    class SequenceModel:
        def __init__(self, payloads: list[dict[str, object]]) -> None:
            self.payloads = payloads
            self.prompts: list[str] = []

        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            payload = self.payloads[min(len(self.prompts) - 1, len(self.payloads) - 1)]
            return type("Message", (), {"content": json.dumps(payload, ensure_ascii=False)})()

    markdown = "# 雨夜通勤实测\n\n## 一、选题方向\n双人接力。\n\n## 三、视频规格\n20秒。\n\n## 五、镜头列表\n按蓝图执行。"
    named_blueprints = [
        {
            "scene_id": f"scene-{index}",
            "scene_index": index,
            "title": "雨夜接力" if index == 1 else "收纳证明",
            "structure_role": "opening" if index == 1 else "conclusion",
            "start_sec": (index - 1) * 10,
            "end_sec": index * 10,
            "duration_sec": 10,
            "storyline": "林晓与陈默在雨夜公交站接力验证背包。",
            "shot_description": "0-10秒: 地点：雨夜公交站；主体：林晓、陈默与防水背包；动作：两人接力完成防水收纳验证；景别：中景切特写；运镜：跟随后推近；光影：冷蓝路灯照亮雨丝；声音：雨声和拉链声；收束：定格背包防水细节。",
            "narration": "雨夜通勤也从容。",
            "transition": "动作匹配转场。" if index == 1 else "产品定格收束。",
            "asset_requirements": {
                "characters": ["林晓-浅灰风衣", "陈默-藏蓝夹克"],
                "scenes": ["雨夜公交站"],
                "props": ["曜石黑防水背包", "透明雨伞", "银色保温杯"],
            },
        }
        for index in (1, 2)
    ]
    manifest = {
        "characters": [
            {"name": "林晓-浅灰风衣", "description": "林晓固定浅灰风衣造型。", "three_view_prompt": "林晓同一人物浅灰风衣的正面、侧面、背面三视图。"},
            {"name": "陈默-藏蓝夹克", "description": "陈默固定藏蓝夹克造型。", "three_view_prompt": "陈默同一人物藏蓝夹克的正面、侧面、背面三视图。"},
        ],
        "scenes": [{"name": "雨夜公交站", "description": "冷蓝路灯下的雨夜公交站。", "image_prompt": "雨夜公交站环境参考图。"}],
        "props": [
            {"name": "曜石黑防水背包", "description": "哑光黑色防水背包。", "image_prompt": "曜石黑防水背包产品参考图。"},
            {"name": "透明雨伞", "description": "透明长柄雨伞。", "image_prompt": "透明长柄雨伞参考图。"},
            {"name": "银色保温杯", "description": "银色金属保温杯。", "image_prompt": "银色金属保温杯参考图。"},
        ],
    }
    model = SequenceModel(
        [
            {"plan_markdown": markdown, "scene_image_ratio": "9:16", "scene_image_size": "4K", "scene_blueprints": []},
            {"plan_markdown": markdown, "scene_image_ratio": "9:16", "scene_image_size": "4K", "scene_blueprints": named_blueprints, "asset_manifest": manifest},
        ]
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {**DIRECTION, "description": "明确要求林晓、陈默、雨夜公交站、透明雨伞和银色保温杯。"},
            intake_context={"original_prompt": "林晓和陈默在雨夜公交站使用透明雨伞、银色保温杯和防水背包。"},
            model_factory=lambda *_args, **_kwargs: model,
        )
    )

    assert len(model.prompts) == 2
    assert [item["name"] for item in result.asset_manifest["characters"]] == ["林晓-浅灰风衣", "陈默-藏蓝夹克"]
    assert [item["name"] for item in result.asset_manifest["props"]] == ["曜石黑防水背包", "透明雨伞", "银色保温杯"]
    assert "用户明确命名" in model.prompts[1]
