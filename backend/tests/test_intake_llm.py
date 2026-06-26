from __future__ import annotations

import asyncio

from pixelflow.intake.llm import draft_creative_directions_with_llm, recognize_intent_with_llm


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[object] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return FakeMessage(self.content)


def test_recognize_intent_uses_llm_json_and_normalizes_video_generation() -> None:
    fake_model = FakeModel(
        """
        {
          "intent": "video_generation",
          "confidence": 0.93,
          "reason": "用户明确要做投放短视频",
          "values": {
            "product_info": "降噪耳机 Pro",
            "product_category": "数码3C",
            "target_audience": "25-35 通勤人群",
            "conversion_goal": "引流直播间"
          }
        }
        """
    )

    result = asyncio.run(recognize_intent_with_llm("帮我做降噪耳机投放短视频", model_factory=lambda *_args, **_kwargs: fake_model))

    assert result.intent == "video"
    assert result.llm_used is True
    assert result.confidence == 0.93
    assert result.values["product_info"] == "降噪耳机 Pro"
    assert result.values["conversion_goal"] == "引流直播间"


def test_recognize_intent_accepts_video_analysis() -> None:
    fake_model = FakeModel('{"intent":"video_analysis","confidence":0.88,"reason":"用户要求分析参考视频","values":{}}')

    result = asyncio.run(recognize_intent_with_llm("分析这个视频 https://x/one.mp4", model_factory=lambda *_args, **_kwargs: fake_model))

    assert result.intent == "video_analysis"
    assert result.llm_used is True
    assert result.values == {}


def test_recognize_intent_falls_back_when_llm_fails() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    result = asyncio.run(recognize_intent_with_llm("分析这个视频 https://x/one.mp4", model_factory=lambda *_args, **_kwargs: BrokenModel()))

    assert result.intent == "video_analysis"
    assert result.llm_used is False
    assert "model down" in (result.error or "")


def test_recognize_intent_fallback_covers_natural_video_analysis_phrases() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    phrases = [
        "分析这个视频 https://x/one.mp4",
        "帮我拆解这个视频",
        "这个视频拆解一下",
        "帮我看看这个参考视频怎么做的",
        "对比这几个爆款视频",
        "复盘一下这个短视频节奏",
    ]

    for phrase in phrases:
        result = asyncio.run(recognize_intent_with_llm(phrase, model_factory=lambda *_args, **_kwargs: BrokenModel()))
        assert result.intent == "video_analysis", phrase


def test_recognize_intent_fallback_routes_image_and_video_operation_phrases() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    image_phrases = [
        "帮我生成2张篮球图片",
        "把这张图片改成蓝色背景",
        "参考这张图生成商品海报",
        "把两张图融合成一张图",
        "做一张小红书封面",
    ]
    video_phrases = [
        "帮我生成一个宣传视频",
        "文生视频：一只耳机在桌面旋转",
        "用这张首帧图生成视频",
        "用首尾帧生成一段视频",
        "编辑这个视频，节奏更快",
        "参考这些素材生成视频",
    ]

    for phrase in image_phrases:
        result = asyncio.run(recognize_intent_with_llm(phrase, model_factory=lambda *_args, **_kwargs: BrokenModel()))
        assert result.intent == "image", phrase

    for phrase in video_phrases:
        result = asyncio.run(recognize_intent_with_llm(phrase, model_factory=lambda *_args, **_kwargs: BrokenModel()))
        assert result.intent == "video", phrase


def test_draft_creative_directions_with_llm_returns_three_normalized_directions() -> None:
    fake_model = FakeModel(
        """
        {
          "directions": [
            {"title": "痛点开场", "description": "先抛通勤噪音痛点，再展示耳机降噪。", "recommended": true, "tags": ["痛点", "转化"], "data": {"structure": "pain_solution"}},
            {"title": "场景种草", "description": "用地铁通勤场景自然种草。", "recommended": false, "tags": ["场景"], "data": {"structure": "lifestyle"}},
            {"title": "对比证明", "description": "用开关降噪前后对比证明效果。", "recommended": false, "tags": ["对比"], "data": {"structure": "contrast"}}
          ]
        }
        """
    )

    directions = asyncio.run(
        draft_creative_directions_with_llm(
            "video",
            {
                "product_info": "降噪耳机 Pro",
                "product_category": "数码3C",
                "target_audience": "25-35",
                "conversion_goal": "引流直播间",
            },
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(directions) == 3
    assert directions[0].recommended is True
    assert directions[0].direction_id == "direction_1"
    assert directions[1].title == "场景种草"
