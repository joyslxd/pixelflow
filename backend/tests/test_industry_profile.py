from __future__ import annotations

import asyncio

from pixelflow.intake.industry_profile import resolve_industry_profile


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> FakeMessage:
        self.prompts.append(prompt)
        return FakeMessage(self.content)


class BrokenModel:
    def invoke(self, _prompt: str) -> None:
        raise RuntimeError("model down")


def test_known_clothing_industry_loads_project_template_without_llm() -> None:
    def fail_factory(*_args, **_kwargs):
        raise AssertionError("LLM must not run for known industry")

    result = asyncio.run(
        resolve_industry_profile(
            industry_type="服饰鞋包",
            source_prompt="生成书包宣传图",
            form_values={"image_goal": "书包宣传图"},
            materials=[],
            model_factory=fail_factory,
        )
    )

    assert result.industry == "clothing"
    assert result.industry_name == "服饰鞋包"
    assert result.source == "template"
    assert result.profile["visual_anchor_keywords"]
    assert "prompt_injection" in result.profile


def test_unknown_industry_uses_deepseek_profile_with_same_shape() -> None:
    fake_model = FakeModel(
        """
        {
          "industry": "stationery",
          "industry_name": "文具教育",
          "product_creative_profile": {
            "core_message": "儿童书包宣传图需要突出护脊结构和开学场景",
            "core_expression_rules": {
              "must_include": ["护脊结构"],
              "must_avoid": ["夸大健康承诺"],
              "description": "突出承托和收纳"
            },
            "key_scenes": {},
            "product_display_rules": {},
            "safety_compliance": {},
            "audience_pain_points": [],
            "emotional_triggers": [],
            "visual_anchor_keywords": ["护脊结构"],
            "prompt_injection": {
              "creative_direction_note": "突出护脊",
              "plan_note": "开学季场景",
              "video_generation_note": "show backpack support"
            }
          }
        }
        """
    )

    result = asyncio.run(
        resolve_industry_profile(
            industry_type="文具教育",
            source_prompt="生成儿童书包宣传图",
            form_values={"image_goal": "儿童书包宣传图"},
            materials=[],
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert result.source == "llm"
    assert result.industry == "stationery"
    assert result.industry_name == "文具教育"
    assert result.profile["visual_anchor_keywords"] == ["护脊结构"]
    assert "儿童书包" in result.profile["core_message"]


def test_unknown_industry_falls_back_to_general_profile_when_llm_fails() -> None:
    result = asyncio.run(
        resolve_industry_profile(
            industry_type="未知行业",
            source_prompt="生成特殊产品宣传图",
            form_values={"image_goal": "特殊产品宣传图"},
            materials=[],
            model_factory=lambda *_args, **_kwargs: BrokenModel(),
        )
    )

    assert result.source == "general_fallback"
    assert result.industry == "general"
    assert result.industry_name == "通用电商"
    assert "特殊产品宣传图" in result.profile["core_message"]
    assert result.error == "model down"
