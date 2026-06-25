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
    assert [scene["scene_id"] for scene in result["scene_packages"]] == ["scene-1", "scene-2", "scene-3"]
    assert sum(scene["duration_ms"] for scene in result["scene_packages"]) == 30_000
    assert all(scene["duration_ms"] <= 10_000 for scene in result["scene_packages"])
    assert "苹果降噪耳机 Pro" in result["scene_packages"][0]["prompt"]
    assert "引流直播间" in result["scene_packages"][-1]["narration"]


def test_prepare_video_scene_packages_with_llm_uses_model_content_for_90s_video():
    class FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def invoke(self, _prompt):
            scenes = [
                {
                    "title": f"LLM 场景 {index}",
                    "storyline": f"LLM 故事线 {index}",
                    "prompt": f"LLM 分镜提示词 {index}",
                    "narration": f"LLM 旁白 {index}",
                    "characters": [{"name": "主讲人", "description": "稳定的讲解者", "three_view_prompt": "统一角色三视图"}],
                    "scene_images": [{"description": "真实场景", "image_prompt": "真实场景图"}],
                    "prop_images": [{"name": "产品", "description": "产品道具", "image_prompt": "产品道具图"}],
                }
                for index in range(1, 10)
            ]
            return FakeMessage('{"scene_packages": ' + __import__("json").dumps(scenes, ensure_ascii=False) + "}")

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
    assert len(result["scene_packages"]) == 9
    assert sum(scene["duration_ms"] for scene in result["scene_packages"]) == 90_000
    assert all(scene["duration_ms"] <= 10_000 for scene in result["scene_packages"])
    assert result["scene_packages"][0]["storyline"] == "LLM 故事线 1"
    assert result["scene_packages"][0]["prompt"] == "LLM 分镜提示词 1"
    assert result["scene_packages"][0]["narration"] == "LLM 旁白 1"
