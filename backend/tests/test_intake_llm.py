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


def test_recognize_intent_extracts_complete_video_creation_contract_from_llm() -> None:
    fake_model = FakeModel(
        """
        {
          "intent": "video_generation",
          "confidence": 0.98,
          "reason": "用户明确指定了视频和图片生产参数",
          "product_subject": "智能健康戒指",
          "creation_goal": "智能健康戒指新品宣传视频",
          "industry_type": "数码3C",
          "values": {
            "product_info": "智能健康戒指",
            "product_category": "数码3C",
            "target_audience": "25-35岁健康管理人群",
            "conversion_goal": "品牌曝光",
            "video_duration_sec": 180,
            "video_ratio": "16:9",
            "video_model_mode": "manual",
            "video_model": "seedance-2.0",
            "image_model": "gpt-image-2",
            "video_usage": "新品宣传",
            "visual_style": "电影写实风"
          }
        }
        """
    )

    result = asyncio.run(
        recognize_intent_with_llm(
            "用 seedance-2.0 和 gpt-image-2 做一个180秒、16:9、电影写实风的新品宣传视频",
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert result.intent == "video"
    assert result.values["video_duration_sec"] == 180
    assert result.values["video_ratio"] == "16:9"
    assert result.values["video_model_mode"] == "manual"
    assert result.values["video_model"] == "seedance-2.0"
    assert result.values["image_model"] == "gpt-image-2"
    assert result.values["video_usage"] == "新品宣传"
    assert result.values["visual_style"] == "电影写实风"


def test_recognize_intent_fallback_extracts_video_creation_contract() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    result = asyncio.run(
        recognize_intent_with_llm(
            "用 seedance-2.0 和 gpt-image-2 做一个180秒、16:9、电影写实风的新品宣传视频",
            model_factory=lambda *_args, **_kwargs: BrokenModel(),
        )
    )

    assert result.intent == "video"
    assert result.values["video_duration_sec"] == 180
    assert result.values["video_ratio"] == "16:9"
    assert result.values["video_model_mode"] == "manual"
    assert result.values["video_model"] == "seedance-2.0"
    assert result.values["image_model"] == "gpt-image-2"
    assert result.values["video_usage"] == "新品宣传"
    assert result.values["visual_style"] == "电影写实风"


def test_recognize_intent_preserves_requested_image_count_from_llm_json() -> None:
    fake_model = FakeModel(
        """
        {
          "intent": "image_generation",
          "confidence": 0.95,
          "reason": "用户明确要生成多张图片",
          "values": {
            "image_goal": "篮球主题宣传图",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "自动适配",
            "image_count": 3
          }
        }
        """
    )

    result = asyncio.run(recognize_intent_with_llm("帮我生成3张篮球图片", model_factory=lambda *_args, **_kwargs: fake_model))

    assert result.intent == "image"
    assert result.values["image_count"] == 3


def test_recognize_intent_marks_image_edit_operation_from_llm_json() -> None:
    fake_model = FakeModel(
        """
        {
          "intent": "image_generation",
          "confidence": 0.96,
          "reason": "用户要求编辑上传图片",
          "product_subject": "上传图片",
          "creation_goal": "把上传图片背景改成白色摄影棚",
          "industry_type": "general",
          "requested_output_count": 2,
          "image_operation": "image_edit",
          "values": {
            "image_goal": "把上传图片背景改成白色摄影棚",
            "image_style": "真实摄影",
            "image_size": "自动适配",
            "image_quality": "4K",
            "image_count": 2
          }
        }
        """
    )

    result = asyncio.run(recognize_intent_with_llm("把这张图背景换成白色摄影棚，生成2张", model_factory=lambda *_args, **_kwargs: fake_model))

    assert result.intent == "image"
    assert result.values["image_operation"] == "image_edit"
    assert result.values["image_quality"] == "4K"
    assert result.values["image_count"] == 2
    assert result.intake_context["image_operation"] == "image_edit"
    assert result.intake_context["image_quality"] == "4K"


def test_recognize_intent_enriches_generic_image_goal_with_product_subject() -> None:
    fake_model = FakeModel(
        """
        {
          "intent": "image_generation",
          "confidence": 0.95,
          "reason": "用户明确要生成书包宣传图",
          "product_subject": "书包",
          "creation_goal": "宣传图",
          "industry_type": "服饰鞋包",
          "values": {
            "image_goal": "宣传图",
            "image_type": "商品广告图",
            "image_usage": "活动宣传",
            "image_style": "真实摄影",
            "image_size": "自动适配"
          }
        }
        """
    )

    result = asyncio.run(recognize_intent_with_llm("帮我生成书包的宣传图", model_factory=lambda *_args, **_kwargs: fake_model))

    assert result.intent == "image"
    assert result.values["image_goal"] == "书包宣传图"
    assert result.intake_context["source_prompt"] == "帮我生成书包的宣传图"
    assert result.intake_context["product_subject"] == "书包"
    assert result.intake_context["creation_goal"] == "书包宣传图"
    assert result.intake_context["industry_type"] == "服饰鞋包"


def test_recognize_intent_accepts_video_analysis() -> None:
    fake_model = FakeModel('{"intent":"video_analysis","confidence":0.88,"reason":"用户要求分析参考视频","values":{}}')

    result = asyncio.run(recognize_intent_with_llm("分析这个视频 https://x/one.mp4", model_factory=lambda *_args, **_kwargs: fake_model))

    assert result.intent == "video_analysis"
    assert result.llm_used is True
    assert result.values == {}


def test_recognize_intent_uses_llm_json_and_normalizes_ppt_generation() -> None:
    fake_model = FakeModel(
        """
        {
          "intent": "ppt_generation",
          "confidence": 0.94,
          "reason": "用户明确要求制作PPT",
          "product_subject": "绿色供应链",
          "creation_goal": "绿色供应链转型汇报PPT",
          "industry_type": "企业服务",
          "values": {
            "ppt_topic": "绿色供应链转型汇报",
            "ppt_style": "极简商务"
          }
        }
        """
    )

    result = asyncio.run(recognize_intent_with_llm("帮我做绿色供应链转型汇报PPT", model_factory=lambda *_args, **_kwargs: fake_model))

    assert result.intent == "ppt"
    assert result.llm_used is True
    assert result.values["ppt_topic"] == "绿色供应链转型汇报"
    assert result.values["ppt_style"] == "极简商务"
    assert result.intake_context["industry_type"] == "企业服务"


def test_recognize_intent_falls_back_when_llm_fails() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    result = asyncio.run(recognize_intent_with_llm("分析这个视频 https://x/one.mp4", model_factory=lambda *_args, **_kwargs: BrokenModel()))

    assert result.intent == "video_analysis"
    assert result.llm_used is False
    assert "model down" in (result.error or "")


def test_recognize_intent_fallback_routes_ppt_phrases() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    phrases = [
        "帮我做一份新品发布PPT",
        "制作企业培训ppt",
        "生成一份营销策略演示文稿",
        "把这些附件做成汇报幻灯片",
    ]

    for phrase in phrases:
        result = asyncio.run(recognize_intent_with_llm(phrase, model_factory=lambda *_args, **_kwargs: BrokenModel()))
        assert result.intent == "ppt", phrase
        assert result.values["ppt_topic"], phrase


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


def test_recognize_intent_fallback_extracts_requested_image_count() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    cases = [
        ("帮我生成3张篮球图片", 3),
        ("做四张小红书封面", 4),
        ("生成十张商品主图", 10),
    ]

    for phrase, expected in cases:
        result = asyncio.run(recognize_intent_with_llm(phrase, model_factory=lambda *_args, **_kwargs: BrokenModel()))
        assert result.intent == "image", phrase
        assert result.values["image_count"] == expected


def test_recognize_intent_fallback_marks_image_edit_operation() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    result = asyncio.run(recognize_intent_with_llm("把这张图片改成蓝色背景，生成2张", model_factory=lambda *_args, **_kwargs: BrokenModel()))

    assert result.intent == "image"
    assert result.values["image_operation"] == "image_edit"
    assert result.values["image_count"] == 2
    assert result.intake_context["image_operation"] == "image_edit"


def test_recognize_intent_fallback_marks_uploaded_image_change_as_edit() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    result = asyncio.run(
        recognize_intent_with_llm(
            "帮我把上传的图片中的路飞衣服变成黄色。",
            model_factory=lambda *_args, **_kwargs: BrokenModel(),
        )
    )

    assert result.intent == "image"
    assert result.values["image_operation"] == "image_edit"
    assert result.intake_context["image_operation"] == "image_edit"


def test_recognize_intent_overrides_llm_text_to_image_for_obvious_uploaded_image_edit() -> None:
    fake_model = FakeModel(
        """
        {
          "intent": "image_generation",
          "confidence": 0.92,
          "reason": "误判为文生图",
          "image_operation": "text_to_image",
          "values": {"image_goal": "路飞衣服黄色图"}
        }
        """
    )

    result = asyncio.run(
        recognize_intent_with_llm(
            "帮我把上传的图片中的路飞衣服变成黄色。",
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert result.intent == "image"
    assert result.llm_used is True
    assert result.values["image_operation"] == "image_edit"
    assert result.intake_context["image_operation"] == "image_edit"


def test_recognize_intent_fallback_extracts_image_edit_ratio_and_quality() -> None:
    class BrokenModel:
        def invoke(self, _prompt):
            raise RuntimeError("model down")

    result = asyncio.run(recognize_intent_with_llm("把这张图片改成蓝色科技风海报，9:16，4K清晰度", model_factory=lambda *_args, **_kwargs: BrokenModel()))

    assert result.intent == "image"
    assert result.values["image_operation"] == "image_edit"
    assert result.values["image_size"] == "9:16"
    assert result.values["image_quality"] == "4K"
    assert result.intake_context["image_quality"] == "4K"


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
