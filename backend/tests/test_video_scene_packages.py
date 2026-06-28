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
    assert all("image_prompt" in asset for asset in character_assets)
    assert all("three_view_prompt" not in asset for asset in character_assets)
    assert all("three_view_images" not in asset for asset in character_assets)
    assert result["global_assets"]["scenes"][0]["asset_id"].startswith("scene-")
    assert result["global_assets"]["props"][0]["asset_id"] == "prop-product"
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
    assert set(first_scene["shot_description"]) == {"text"}
    assert "地点:@" in first_scene["shot_description"]["text"]
    assert "角色:@" in first_scene["shot_description"]["text"]


def test_prepare_video_scene_packages_with_llm_uses_model_content_for_90s_video():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def invoke(self, _prompt):
            global_assets = {
                "characters": [
                    {"asset_id": "character-presenter", "name": "主讲人", "description": "稳定的讲解者", "image_prompt": "主讲人单人角色图"},
                    {"asset_id": "character-user", "name": "养宠用户", "description": "目标用户", "image_prompt": "目标用户单人角色图"},
                ],
                "scenes": [{"asset_id": "scene-home", "name": "居家场景", "description": "真实居家场景", "image_prompt": "真实场景图"}],
                "props": [{"asset_id": "prop-product", "name": "产品", "description": "产品道具", "image_prompt": "产品道具图"}],
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
    assert len(result["scene_packages"]) == 9
    assert sum(scene["duration_ms"] for scene in result["scene_packages"]) == 90_000
    assert all(4_000 <= scene["duration_ms"] <= 15_000 for scene in result["scene_packages"])
    assert result["scene_packages"][0]["storyline"] == "LLM 故事线 1"
    assert result["scene_packages"][0]["prompt"] == "LLM 分镜提示词 1"
    assert result["scene_packages"][0]["narration"] == "LLM 旁白 1"
    assert "LLM 镜头描述 1" in result["scene_packages"][0]["shot_description"]["text"]
    assert "地点:@" in result["scene_packages"][0]["shot_description"]["text"]
    assert "角色:@" in result["scene_packages"][0]["shot_description"]["text"]
