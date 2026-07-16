from __future__ import annotations

import hashlib

import pytest

from pixelflow.generate.scene_packages import prepare_video_scene_packages, prepare_video_scene_packages_with_llm


def test_prepare_video_scene_packages_splits_plan_into_confirmable_scenes():
    result = prepare_video_scene_packages(
        form_values={
            "product_info": "苹果降噪耳机 Pro",
            "product_category": "数码3C",
            "target_audience": "25-35 岁通勤人群",
            "conversion_goal": "引流直播间",
        },
        plan_markdown="## 一、选题方向\n突出通勤降噪痛点，展示佩戴前后对比，并引导进入直播间。",
        selected_direction={"title": "通勤降噪挑战", "description": "用通勤噪声反差制造记忆点"},
        target_duration_ms=30_000,
    )

    assert result["ok"] is True
    assert result["requires_confirmation"] is True
    assert result["review_timeout_sec"] is None
    assert result["global_assets"]["visual_style"]["name"]
    character_assets = result["global_assets"]["characters"]
    assert len(character_assets) >= 2
    assert character_assets[0]["asset_id"].startswith("character-")
    assert character_assets[0]["asset_id"] != character_assets[1]["asset_id"]
    assert all("three_view_prompt" in asset for asset in character_assets)
    assert all("三视图" in asset["three_view_prompt"] for asset in character_assets)
    assert all("正面" in asset["three_view_prompt"] and "侧面" in asset["three_view_prompt"] and "背面" in asset["three_view_prompt"] for asset in character_assets)
    assert all(asset["asset_id"] != "character-product" for asset in character_assets)
    assert all("苹果降噪耳机 Pro" not in asset["name"] for asset in character_assets)
    assert result["global_assets"]["scenes"][0]["asset_id"].startswith("scene-")
    assert result["global_assets"]["props"][0]["asset_id"] == "prop-product"
    assert result["global_assets"]["props"][0]["name"] == "苹果降噪耳机 Pro"
    assert [scene["scene_id"] for scene in result["scene_packages"]] == ["scene-1", "scene-2", "scene-3"]
    assert sum(scene["duration_ms"] for scene in result["scene_packages"]) == 30_000
    assert all(scene["duration_ms"] % 1000 == 0 for scene in result["scene_packages"])
    assert all(4_000 <= scene["duration_ms"] <= 15_000 for scene in result["scene_packages"])
    assert "苹果降噪耳机 Pro" in result["scene_packages"][0]["prompt"]
    assert "引流直播间" in result["scene_packages"][-1]["narration"]
    first_scene = result["scene_packages"][0]
    assert "characters" not in first_scene
    assert "scene_images" not in first_scene
    assert "prop_images" not in first_scene
    assert first_scene["reference_asset_ids"][:1] == [character_assets[0]["asset_id"]]
    assert first_scene["reference_asset_ids"][1:] == ["scene-opening", "prop-product"]
    assert set(first_scene["shot_description"]) == {"text", "mentions"}
    assert "地点:@" in first_scene["shot_description"]["text"]
    assert "角色:@" in first_scene["shot_description"]["text"]
    assert [mention["asset_id"] for mention in first_scene["shot_description"]["mentions"]] == first_scene["reference_asset_ids"]
    assert {mention["type"] for mention in first_scene["shot_description"]["mentions"]} == {"character", "scene", "prop"}


def test_prepare_video_scene_packages_uses_second_ranges_in_shot_description():
    result = prepare_video_scene_packages(
        form_values={
            "product_info": "男士通勤背包",
            "product_category": "服饰鞋包",
            "target_audience": "25-35 岁通勤男性",
            "conversion_goal": "直接购买",
        },
        plan_markdown="## 一、选题方向\n展示背包通勤、收纳和防泼水卖点。",
        selected_direction={"title": "通勤效率感", "description": "用早高峰场景展示背包卖点"},
        target_duration_ms=10_000,
    )

    shot_text = result["scene_packages"][0]["shot_description"]["text"]
    assert "ms" not in shot_text
    assert ".000" not in shot_text
    assert "0-10秒" in shot_text


def test_prepare_video_scene_packages_consumes_authoritative_plan_blueprints():
    blueprints = [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "title": "雨水钩子",
            "structure_role": "opening",
            "start_sec": 0,
            "end_sec": 6,
            "duration_sec": 6,
            "storyline": "雨水突袭形成冲突。",
            "shot_description": "0-6秒: 特写雨滴砸向背包，镜头快速推近材质。",
            "narration": "下雨最怕包里一起遭殃。",
            "transition": "顺着水滴切到拉链。",
            "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包"]},
        },
        {
            "scene_id": "scene-2",
            "scene_index": 2,
            "title": "防水证明",
            "structure_role": "climax",
            "start_sec": 6,
            "end_sec": 18,
            "duration_sec": 12,
            "storyline": "泼水和开包检查证明防水。",
            "shot_description": "0-12秒: 中景连续泼水后切入拉链特写，打开背包展示干燥内胆。",
            "narration": "高密防泼水面料，把雨留在外面。",
            "transition": "由内胆匹配剪辑到办公区。",
            "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包", "水杯"]},
        },
        {
            "scene_id": "scene-3",
            "scene_index": 3,
            "title": "通勤收束",
            "structure_role": "conclusion",
            "start_sec": 18,
            "end_sec": 26,
            "duration_sec": 8,
            "storyline": "抵达办公区并完成购买引导。",
            "shot_description": "0-8秒: 跟拍背包进入办公区，定格完整外观和干燥内胆。",
            "narration": "全天候通勤，现在就选它。",
            "transition": "产品定格结束。",
            "asset_requirements": {"characters": [], "scenes": ["办公区"], "props": ["防水背包"]},
        },
    ]

    result = prepare_video_scene_packages(
        form_values={
            "product_info": "防水通勤背包",
            "product_category": "服饰鞋包",
            "target_audience": "通勤人群",
            "conversion_goal": "直接购买",
        },
        plan_markdown="## 五、镜头列表\n严格执行权威分镜蓝图。",
        selected_direction={"title": "雨天防水实测"},
        target_duration_ms=26_000,
        scene_blueprints=blueprints,
    )

    assert [scene["duration_ms"] for scene in result["scene_packages"]] == [6_000, 12_000, 8_000]
    assert [scene["title"] for scene in result["scene_packages"]] == ["雨水钩子", "防水证明", "通勤收束"]
    assert result["scene_packages"][1]["storyline"] == "泼水和开包检查证明防水。"
    assert result["scene_packages"][1]["narration"] == "高密防泼水面料，把雨留在外面。"
    assert {item["name"] for item in result["global_assets"]["characters"]} == set()
    assert {item["name"] for item in result["global_assets"]["scenes"]} == {"雨中街道", "办公区"}
    assert {item["name"] for item in result["global_assets"]["props"]} == {"防水背包", "水杯"}
    second_scene = result["scene_packages"][1]
    shot_text = second_scene["shot_description"]["text"]
    assert "0-12秒" in shot_text
    reference_lookup = {
        item["asset_id"]: item["name"]
        for collection in ("characters", "scenes", "props")
        for item in result["global_assets"][collection]
    }
    assert {reference_lookup[asset_id] for asset_id in second_scene["reference_asset_ids"]} == {
        "雨中街道",
        "防水背包",
        "水杯",
    }
    assert second_scene["shot_description"]["mentions"]
    assert all(f"@{asset_id}" in shot_text for asset_id in second_scene["reference_asset_ids"])


def test_authoritative_blueprint_moves_product_out_of_characters() -> None:
    blueprints = [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "title": "雨中通勤",
            "structure_role": "opening",
            "start_sec": 0,
            "end_sec": 10,
            "duration_sec": 10,
            "storyline": "林晓背着防水背包穿过雨幕。",
            "shot_description": "0-10秒: 林晓在雨中展示防水背包。",
            "narration": "雨再大，也不怕。",
            "transition": "动作匹配转场",
            "asset_requirements": {
                "characters": ["林晓", "防水背包"],
                "scenes": ["雨中街道"],
                "props": [],
            },
        }
    ]

    result = prepare_video_scene_packages(
        form_values={
            "product_info": "防水背包",
            "product_category": "服饰箱包",
            "target_audience": "通勤人群",
            "conversion_goal": "直接购买",
            "video_ratio": "9:16",
            "visual_style": "真实摄影",
        },
        plan_markdown="# 防水背包宣传片",
        selected_direction={"title": "雨中守护", "description": "真实通勤场景"},
        target_duration_ms=10_000,
        scene_blueprints=blueprints,
    )

    assert [asset["name"] for asset in result["global_assets"]["characters"]] == ["林晓"]
    assert [asset["name"] for asset in result["global_assets"]["props"]] == ["防水背包"]
    reference_ids = result["scene_packages"][0]["reference_asset_ids"]
    assert any(asset_id.startswith("character-") for asset_id in reference_ids)
    assert any(asset_id.startswith("prop-") for asset_id in reference_ids)


def test_scene_package_llm_must_materialize_every_final_plan_asset_requirement():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def invoke(self, _prompt):
            return FakeMessage(
                __import__("json").dumps(
                    {
                        "global_assets": {
                            "characters": [
                                {
                                    "asset_id": "character-presenter",
                                    "name": "旧主讲人",
                                    "description": "旧方案人物",
                                    "three_view_prompt": "旧主讲人正面、侧面、背面三视图",
                                }
                            ],
                            "scenes": [
                                {
                                    "asset_id": "scene-studio",
                                    "name": "旧摄影棚",
                                    "description": "旧方案场景",
                                    "image_prompt": "旧摄影棚场景图",
                                }
                            ],
                            "props": [
                                {
                                    "asset_id": "prop-product",
                                    "name": "旧产品",
                                    "description": "旧方案道具",
                                    "image_prompt": "旧产品道具图",
                                }
                            ],
                            "visual_style": {"asset_id": "style-main", "name": "写实风", "description": "写实广告"},
                        },
                        "scene_packages": [
                            {
                                "title": "旧标题",
                                "storyline": "旧故事线",
                                "shot_description": {"text": "0-10秒: 旧摄影棚中的旧主讲人展示旧产品。"},
                                "reference_asset_ids": ["character-presenter", "scene-studio", "prop-product"],
                                "prompt": "旧方案自由提示词",
                                "narration": "旧旁白",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )

    blueprints = [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "title": "最终雨夜出发",
            "structure_role": "opening",
            "start_sec": 0,
            "end_sec": 10,
            "duration_sec": 10,
            "storyline": "林晓在雨夜公交站背起防水背包，准备通勤。",
            "shot_description": "0-10秒: 中景跟拍林晓在雨夜公交站拿起折叠伞并背上防水背包。",
            "narration": "下雨，也不耽误从容出发。",
            "transition": "跟随伞面擦镜转场。",
            "asset_requirements": {
                "characters": ["林晓"],
                "scenes": ["雨夜公交站"],
                "props": ["防水背包", "折叠伞"],
            },
        }
    ]

    result = __import__("asyncio").run(
        prepare_video_scene_packages_with_llm(
            form_values={
                "product_info": "防水背包",
                "product_category": "服饰鞋包",
                "target_audience": "通勤人群",
                "conversion_goal": "直接购买",
                "video_ratio": "9:16",
                "video_model": "seedance-2.0",
            },
            plan_markdown="## 五、镜头列表\n最终版本要求林晓在雨夜公交站使用防水背包和折叠伞。",
            selected_direction={"title": "雨夜从容通勤"},
            target_duration_ms=10_000,
            scene_blueprints=blueprints,
            model_factory=lambda *_args, **_kwargs: FakeModel(),
        )
    )

    assert {item["name"] for item in result["global_assets"]["characters"]} == {"林晓"}
    assert {item["name"] for item in result["global_assets"]["scenes"]} == {"雨夜公交站"}
    assert {item["name"] for item in result["global_assets"]["props"]} == {"防水背包", "折叠伞"}
    scene = result["scene_packages"][0]
    assert scene["title"] == "最终雨夜出发"
    assert scene["storyline"] == "林晓在雨夜公交站背起防水背包，准备通勤。"
    assert scene["narration"] == "下雨，也不耽误从容出发。"
    assert scene["transition"] == "跟随伞面擦镜转场。"
    assert "中景跟拍" in scene["shot_description"]["text"]
    assert "拿起" in scene["shot_description"]["text"]
    assert "并背上" in scene["shot_description"]["text"]
    assert "旧摄影棚中的旧主讲人展示旧产品" not in scene["shot_description"]["text"]
    reference_lookup = {
        item["asset_id"]: item["name"]
        for collection in ("characters", "scenes", "props")
        for item in result["global_assets"][collection]
    }
    assert [reference_lookup[asset_id] for asset_id in scene["reference_asset_ids"]] == [
        "林晓",
        "雨夜公交站",
        "防水背包",
        "折叠伞",
    ]
    assert "同一个人物的正面、侧面、背面三视图" in result["global_assets"]["characters"][0]["three_view_prompt"]
    assert "故事线：林晓在雨夜公交站背起防水背包，准备通勤。" in scene["prompt"]
    assert "旁白：下雨，也不耽误从容出发。" in scene["prompt"]
    assert "视觉风格：" in scene["prompt"]
    assert "最终确认方案" not in scene["prompt"]
    assert "旧方案自由提示词" not in scene["prompt"]
    assert [mention["asset_id"] for mention in scene["shot_description"]["mentions"]] == scene["reference_asset_ids"]
    assert all(f"@{asset_id}" in scene["shot_description"]["text"] for asset_id in scene["reference_asset_ids"])


def test_authoritative_blueprint_rejects_more_than_nine_deduplicated_scene_assets():
    blueprint = {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "十种产品陈列",
        "structure_role": "opening",
        "start_sec": 0,
        "end_sec": 10,
        "duration_sec": 10,
        "storyline": "依次展示十种产品。",
        "shot_description": "0-10秒: 镜头横移展示全部产品。",
        "narration": "十种选择，一次看清。",
        "transition": "产品定格结束。",
        "asset_requirements": {
            "characters": [],
            "scenes": ["产品展台", "产品展台"],
            "props": [f"产品-{index}" for index in range(1, 10)],
        },
    }

    with pytest.raises(ValueError, match=r"scene-1.*scene_index=1.*10"):
        prepare_video_scene_packages(
            form_values={"product_info": "产品合集"},
            plan_markdown="## 权威分镜\n十种产品陈列。",
            target_duration_ms=10_000,
            scene_blueprints=[blueprint],
        )


def test_authoritative_blueprint_normalizes_asset_name_mentions_to_asset_ids():
    blueprint = {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "雨夜出发",
        "structure_role": "opening",
        "start_sec": 0,
        "end_sec": 10,
        "duration_sec": 10,
        "storyline": "林晓在雨夜公交站背起防水背包。",
        "shot_description": "0-10秒: 角色:@林晓 在地点:@雨夜公交站 背起道具:@防水背包。",
        "narration": "雨夜也能从容出发。",
        "transition": "跟随背包擦镜转场。",
        "asset_requirements": {
            "characters": ["林晓"],
            "scenes": ["雨夜公交站"],
            "props": ["防水背包"],
        },
    }

    result = prepare_video_scene_packages(
        form_values={"product_info": "防水背包"},
        plan_markdown="## 权威分镜\n雨夜出发。",
        target_duration_ms=10_000,
        scene_blueprints=[blueprint],
    )

    scene = result["scene_packages"][0]
    shot_text = scene["shot_description"]["text"]
    assert "@林晓" not in shot_text
    assert "@雨夜公交站" not in shot_text
    assert "@防水背包" not in shot_text
    assert all(f"@{asset_id}" in shot_text for asset_id in scene["reference_asset_ids"])
    assert [mention["asset_id"] for mention in scene["shot_description"]["mentions"]] == scene["reference_asset_ids"]


def test_authoritative_blueprint_preserves_existing_asset_id_mentions():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def invoke(self, _prompt):
            return FakeMessage(
                __import__("json").dumps(
                    {
                        "global_assets": {
                            "characters": [
                                {
                                    "asset_id": "Lin-v1",
                                    "name": "Lin",
                                    "description": "通勤女性",
                                    "three_view_prompt": "Lin 正面、侧面、背面三视图",
                                }
                            ],
                            "scenes": [],
                            "props": [],
                            "visual_style": {"asset_id": "style-main", "name": "写实风", "description": "写实风"},
                        },
                        "scene_packages": [],
                    },
                    ensure_ascii=False,
                )
            )

    blueprint = {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "通勤开场",
        "structure_role": "opening",
        "start_sec": 0,
        "end_sec": 10,
        "duration_sec": 10,
        "storyline": "Lin 进入通勤场景。",
        "shot_description": "0-10秒: 角色:@Lin-v1 走入画面。",
        "narration": "轻松开始通勤。",
        "transition": "跟随脚步结束。",
        "asset_requirements": {"characters": ["Lin"], "scenes": [], "props": []},
    }

    result = __import__("asyncio").run(
        prepare_video_scene_packages_with_llm(
            form_values={"product_info": "通勤服务"},
            plan_markdown="## 权威分镜\n通勤开场。",
            target_duration_ms=10_000,
            scene_blueprints=[blueprint],
            model_factory=lambda *_args, **_kwargs: FakeModel(),
        )
    )

    scene = result["scene_packages"][0]
    assert "@Lin-v1 " in scene["shot_description"]["text"]
    assert "@Lin-v1-v1" not in scene["shot_description"]["text"]
    assert scene["reference_asset_ids"] == ["Lin-v1"]
    assert [mention["asset_id"] for mention in scene["shot_description"]["mentions"]] == ["Lin-v1"]


def test_authoritative_blueprint_prefers_final_form_visual_style_over_llm_style():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def invoke(self, _prompt):
            return FakeMessage(
                __import__("json").dumps(
                    {
                        "global_assets": {
                            "characters": [],
                            "scenes": [],
                            "props": [],
                            "visual_style": {
                                "asset_id": "style-main",
                                "name": "LLM 旧复古风",
                                "description": "LLM 旧复古风",
                                "prompt": "LLM 旧复古风",
                            },
                        },
                        "scene_packages": [],
                    },
                    ensure_ascii=False,
                )
            )

    blueprint = {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "科技开场",
        "structure_role": "opening",
        "start_sec": 0,
        "end_sec": 10,
        "duration_sec": 10,
        "storyline": "产品在未来空间中亮相。",
        "shot_description": "0-10秒: 环绕产品并推进至细节特写。",
        "narration": "让科技触手可及。",
        "transition": "光线擦镜结束。",
        "asset_requirements": {"characters": [], "scenes": [], "props": []},
    }

    result = __import__("asyncio").run(
        prepare_video_scene_packages_with_llm(
            form_values={"product_info": "智能设备", "visual_style": "最终合同科技蓝"},
            plan_markdown="## 权威分镜\n科技开场。",
            target_duration_ms=10_000,
            scene_blueprints=[blueprint],
            model_factory=lambda *_args, **_kwargs: FakeModel(),
        )
    )

    assert result["global_assets"]["visual_style"]["name"] == "最终合同科技蓝"
    assert result["global_assets"]["visual_style"]["description"] == "最终合同科技蓝"
    assert "视觉风格：最终合同科技蓝" in result["scene_packages"][0]["prompt"]
    assert "LLM 旧复古风" not in result["scene_packages"][0]["prompt"]


def test_authoritative_blueprint_resolves_repeated_asset_id_collisions_until_unique():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    scene_name = "雨夜公交站"
    scene_stable_id = f"scene-{hashlib.sha1(scene_name.encode('utf-8')).hexdigest()[:10]}"

    class FakeModel:
        def invoke(self, _prompt):
            return FakeMessage(
                __import__("json").dumps(
                    {
                        "global_assets": {
                            "characters": [
                                {
                                    "asset_id": scene_stable_id,
                                    "name": "林晓",
                                    "description": "通勤女性",
                                    "three_view_prompt": "林晓正面、侧面、背面三视图",
                                }
                            ],
                            "scenes": [
                                {
                                    "asset_id": scene_stable_id,
                                    "name": scene_name,
                                    "description": "雨夜公交站",
                                    "image_prompt": "雨夜公交站场景图",
                                }
                            ],
                            "props": [],
                            "visual_style": {
                                "asset_id": scene_stable_id,
                                "name": "写实风",
                                "description": "写实风",
                            },
                        },
                        "scene_packages": [],
                    },
                    ensure_ascii=False,
                )
            )

    blueprint = {
        "scene_id": "scene-1",
        "scene_index": 1,
        "title": "雨夜出发",
        "structure_role": "opening",
        "start_sec": 0,
        "end_sec": 10,
        "duration_sec": 10,
        "storyline": "林晓从雨夜公交站出发。",
        "shot_description": "0-10秒: 林晓走出雨夜公交站。",
        "narration": "雨夜也要准时抵达。",
        "transition": "跟随脚步转场。",
        "asset_requirements": {"characters": ["林晓"], "scenes": [scene_name], "props": []},
    }

    result = __import__("asyncio").run(
        prepare_video_scene_packages_with_llm(
            form_values={"product_info": "通勤服务"},
            plan_markdown="## 权威分镜\n雨夜出发。",
            target_duration_ms=10_000,
            scene_blueprints=[blueprint],
            model_factory=lambda *_args, **_kwargs: FakeModel(),
        )
    )

    asset_ids = [
        item["asset_id"]
        for collection in ("characters", "scenes", "props")
        for item in result["global_assets"][collection]
    ]
    asset_ids.append(result["global_assets"]["visual_style"]["asset_id"])
    assert len(asset_ids) == len(set(asset_ids))
    scene = result["scene_packages"][0]
    image_asset_ids = {
        item["asset_id"]
        for collection in ("characters", "scenes", "props")
        for item in result["global_assets"][collection]
    }
    assert set(scene["reference_asset_ids"]) == image_asset_ids
    assert [mention["asset_id"] for mention in scene["shot_description"]["mentions"]] == scene["reference_asset_ids"]


def test_prepare_video_scene_packages_supports_300_seconds_without_legacy_scene_cap():
    result = prepare_video_scene_packages(
        form_values={
            "product_info": "户外防水背包",
            "product_category": "服饰鞋包",
            "target_audience": "长途旅行人群",
            "conversion_goal": "直接购买",
            "video_ratio": "9:16",
        },
        plan_markdown="## 创作目标\n严格按 300 秒计划展示产品能力。",
        selected_direction={"title": "极端天气实测"},
        target_duration_ms=300_000,
    )

    durations = [scene["duration_ms"] for scene in result["scene_packages"]]
    assert len(durations) == 30
    assert sum(durations) == 300_000
    assert all(duration % 1000 == 0 for duration in durations)
    assert all(4_000 <= duration <= 15_000 for duration in durations)


def test_scene_package_llm_prompt_includes_seedance_guidance_and_final_video_ratio():
    captured: dict[str, str] = {}

    class FailingModel:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            raise RuntimeError("capture prompt")

    __import__("asyncio").run(
        prepare_video_scene_packages_with_llm(
            form_values={
                "product_info": "户外防水背包",
                "product_category": "服饰鞋包",
                "target_audience": "长途旅行人群",
                "conversion_goal": "直接购买",
                "video_ratio": "9:16",
                "video_model": "seedance-1.5-pro",
            },
            plan_markdown="## 创作目标\n严格根据 plan.md 生成镜头。",
            selected_direction={"title": "极端天气实测"},
            target_duration_ms=20_000,
            model_factory=lambda *_args, **_kwargs: FailingModel(),
        )
    )

    assert "Seedance 系列" in captured["prompt"]
    assert "当前视频模型：seedance-1.5-pro" in captured["prompt"]
    assert "0-10秒" in captured["prompt"]
    assert "10-20秒" in captured["prompt"]
    assert "9:16" in captured["prompt"]
    assert "最多 9" in captured["prompt"]
    assert "严格根据 plan.md 生成镜头" in captured["prompt"]


def test_prepare_video_scene_packages_with_llm_normalizes_millisecond_ranges():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def invoke(self, _prompt):
            global_assets = {
                "characters": [
                    {"asset_id": "character-presenter", "name": "主讲人", "description": "稳定讲解者", "three_view_prompt": "主讲人正面侧面背面人物三视图"},
                    {"asset_id": "character-user", "name": "通勤用户", "description": "目标用户", "three_view_prompt": "通勤用户正面侧面背面人物三视图"},
                ],
                "scenes": [{"asset_id": "scene-office", "name": "办公室", "description": "现代办公室", "image_prompt": "办公室场景图"}],
                "props": [{"asset_id": "prop-product", "name": "男士通勤背包", "description": "背包", "image_prompt": "背包道具图"}],
                "visual_style": {"asset_id": "style-main", "name": "真实摄影", "description": "真实广告风格"},
            }
            scenes = [
                {
                    "title": f"LLM 场景 {index}",
                    "storyline": f"LLM 故事线 {index}",
                    "prompt": f"LLM 分镜提示词 {index}",
                    "narration": f"LLM 旁白 {index}",
                    "shot_description": {
                        "text": ("0-1000ms: 特写镜头, 2000-3000ms: 切至 @scene-office, 00:03.000-00:04.000: 固定画面, 角色:@character-presenter 展示 @prop-product。"),
                    },
                    "reference_asset_ids": ["character-presenter", "scene-office", "prop-product"],
                }
                for index in range(1, 4)
            ]
            return FakeMessage(__import__("json").dumps({"global_assets": global_assets, "scene_packages": scenes}, ensure_ascii=False))

    result = __import__("asyncio").run(
        prepare_video_scene_packages_with_llm(
            form_values={
                "product_info": "男士通勤背包",
                "product_category": "服饰鞋包",
                "target_audience": "25-35 岁通勤男性",
                "conversion_goal": "直接购买",
            },
            plan_markdown="## 一、选题方向\n展示背包通勤、收纳和防泼水卖点。",
            selected_direction={"title": "通勤效率感", "description": "用早高峰场景展示背包卖点"},
            target_duration_ms=30_000,
            model_factory=lambda *_args, **_kwargs: FakeModel(),
        )
    )

    shot_text = result["scene_packages"][0]["shot_description"]["text"]
    assert "ms" not in shot_text
    assert ".000" not in shot_text
    assert "0-1秒: 特写镜头" in shot_text
    assert "2-3秒: 切至 @scene-office" in shot_text
    assert "3-4秒: 固定画面" in shot_text


def test_prepare_video_scene_packages_with_llm_uses_model_content_for_90s_video():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def invoke(self, _prompt):
            global_assets = {
                "characters": [
                    {"asset_id": "character-presenter", "name": "主讲人", "description": "稳定的讲解者", "three_view_prompt": "主讲人拿着智能洗地机 X9产品，正面侧面背面人物三视图"},
                    {"asset_id": "character-product", "name": "智能洗地机 X9", "description": "产品主体", "image_prompt": "产品图"},
                    {"asset_id": "character-user", "name": "养宠用户", "description": "目标用户", "image_prompt": "目标用户单人角色图"},
                ],
                "scenes": [{"asset_id": "scene-home", "name": "居家场景", "description": "真实居家场景", "image_prompt": "真实场景图"}],
                "props": [],
                "visual_style": {"asset_id": "style-main", "name": "真实摄影", "description": "真实电商广告风格"},
            }
            scenes = [
                {
                    "title": f"LLM 场景 {index}",
                    "storyline": f"LLM 故事线 {index}",
                    "prompt": f"LLM 分镜提示词 {index}",
                    "narration": f"LLM 旁白 {index}",
                    "shot_description": {
                        "text": f"0-10秒: 地点：@scene-home 中,角色@character-presenter 展示产品。LLM 镜头描述 {index}",
                    },
                    "reference_asset_ids": ["character-presenter", "scene-home", "prop-product"],
                }
                for index in range(1, 10)
            ]
            return FakeMessage(__import__("json").dumps({"global_assets": global_assets, "scene_packages": scenes}, ensure_ascii=False))

    result = __import__("asyncio").run(
        prepare_video_scene_packages_with_llm(
            form_values={
                "product_info": "智能洗地机 X9",
                "product_category": "家居日用",
                "target_audience": "30-45 岁养宠家庭",
                "conversion_goal": "直接购买",
            },
            plan_markdown="## 一、选题方向\n90秒完整解释清洁痛点、卖点证明和转化。",
            selected_direction={"title": "宠物家庭深度种草", "description": "用复杂场景证明清洁力"},
            target_duration_ms=90_000,
            model_factory=lambda *_args, **_kwargs: FakeModel(),
        )
    )

    assert result["ok"] is True
    assert result["global_assets"]["characters"][0]["asset_id"] == "character-presenter"
    assert all("three_view_prompt" in asset for asset in result["global_assets"]["characters"])
    assert all("智能洗地机 X9" not in asset["name"] for asset in result["global_assets"]["characters"])
    assert "智能洗地机 X9" not in result["global_assets"]["characters"][0]["three_view_prompt"]
    assert "拿着" not in result["global_assets"]["characters"][0]["three_view_prompt"]
    assert result["global_assets"]["props"][0]["asset_id"] == "prop-product"
    assert result["global_assets"]["props"][0]["name"] == "智能洗地机 X9"
    assert len(result["scene_packages"]) == 9
    assert sum(scene["duration_ms"] for scene in result["scene_packages"]) == 90_000
    assert all(scene["duration_ms"] % 1000 == 0 for scene in result["scene_packages"])
    assert all(4_000 <= scene["duration_ms"] <= 15_000 for scene in result["scene_packages"])
    assert result["scene_packages"][0]["storyline"] == "LLM 故事线 1"
    assert result["scene_packages"][0]["prompt"] == "LLM 分镜提示词 1"
    assert result["scene_packages"][0]["narration"] == "LLM 旁白 1"
    assert "LLM 镜头描述 1" in result["scene_packages"][0]["shot_description"]["text"]
    assert "地点:@" in result["scene_packages"][0]["shot_description"]["text"]
    assert "角色:@" in result["scene_packages"][0]["shot_description"]["text"]
    assert [mention["asset_id"] for mention in result["scene_packages"][0]["shot_description"]["mentions"]] == [
        "character-presenter",
        "scene-home",
        "prop-product",
    ]
