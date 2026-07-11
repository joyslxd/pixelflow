from __future__ import annotations

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
                        "text": (
                            "0-1000ms: 特写镜头, "
                            "2000-3000ms: 切至 @scene-office, "
                            "00:03.000-00:04.000: 固定画面, 角色:@character-presenter 展示 @prop-product。"
                        ),
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
