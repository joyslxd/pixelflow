from __future__ import annotations

from pixelflow.generate.image_prepare import prepare_image_generation


def test_prepare_image_generation_uses_text_to_image_without_reference_materials():
    result = prepare_image_generation(
        {"image_goal": "科技感耳机海报", "image_style": "科技感", "image_size": "9:16 竖版海报"},
        "## 一、选题方向\n图片生成方案",
        {"title": "核心卖点海报", "description": "突出金属质感"},
    )

    assert result.ok is True
    assert result.endpoint == "/api/picture/text_to_image"
    assert result.method == "text_to_image"
    assert result.params["ratio"] == "9:16"
    assert result.params["model"] == "seeddream-5.0"
    assert result.params["size"] == "1080p"
    assert result.params["num_images"] == 1
    assert "科技感耳机海报" in result.prompt
    assert "核心卖点海报" in result.prompt


def test_prepare_image_generation_uses_requested_image_count_for_text_to_image():
    result = prepare_image_generation(
        {
            "image_goal": "篮球主题宣传图",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "自动适配",
            "image_count": 3,
        },
        "## 一、选题方向\n生成 3 张不同构图的篮球图片",
        {"title": "篮球海报", "description": "三张不同角度"},
    )

    assert result.ok is True
    assert result.method == "text_to_image"
    assert result.params["num_images"] == 3


def test_prepare_image_generation_prompt_uses_intake_context_subject_and_profile():
    result = prepare_image_generation(
        {
            "image_goal": "宣传图",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "自动适配",
        },
        "## 一、选题方向\n图片生成方案",
        {"title": "通学收纳主视觉", "description": "突出容量和护脊"},
        intake_context={
            "source_prompt": "帮我生成书包的宣传图",
            "product_subject": "书包",
            "creation_goal": "书包宣传图",
            "industry_type": "服饰鞋包",
            "requested_output_count": 3,
            "product_creative_profile": {
                "core_message": "儿童通学场景里的轻量护脊书包",
                "visual_anchor_keywords": ["通学路", "收纳分区", "护脊背负"],
            },
        },
    )

    assert result.ok is True
    assert result.params["num_images"] == 3
    assert "图片目标：书包宣传图" in result.prompt
    assert "产品主体：书包" in result.prompt
    assert "原始需求：帮我生成书包的宣传图" in result.prompt
    assert "行业类型：服饰鞋包" in result.prompt
    assert "儿童通学场景里的轻量护脊书包" in result.prompt
    assert "通学路、收纳分区、护脊背负" in result.prompt


def test_prepare_image_generation_uses_requested_image_count_for_reference_generation():
    result = prepare_image_generation(
        {
            "image_goal": "参考商品图生成3张卖点海报",
            "image_style": "真实摄影",
            "image_size": "1:1 正方形",
            "image_count": "3",
        },
        "plan",
        {"title": "参考图海报", "description": "基于素材出三张"},
        materials=[{"type": "image", "url": "https://x/product.png"}],
    )

    assert result.ok is True
    assert result.method == "multi_reference_image_generation"
    assert result.params["max_images"] == 3


def test_prepare_image_generation_auto_size_uses_context_for_vertical_poster():
    result = prepare_image_generation(
        {
            "image_goal": "小红书新品封面图",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "自动适配",
        },
        "## 一、选题方向\n适合竖版内容流的封面",
        {"title": "竖版种草海报", "description": "突出产品质感和点击欲望"},
    )

    assert result.ok is True
    assert result.params["ratio"] == "9:16"


def test_prepare_image_generation_auto_size_uses_context_for_horizontal_banner():
    result = prepare_image_generation(
        {
            "image_goal": "官网横版 banner 头图",
            "image_type": "背景/素材图",
            "image_usage": "内部展示",
            "image_style": "科技感",
            "image_size": "auto",
        },
        "## 一、选题方向\n电脑端首屏横幅",
        {"title": "横版品牌视觉", "description": "用于网页头图和大屏展示"},
    )

    assert result.ok is True
    assert result.params["ratio"] == "16:9"


def test_prepare_image_generation_normalizes_unsupported_explicit_ratio():
    result = prepare_image_generation(
        {"image_goal": "信息流广告图", "image_style": "真实摄影", "image_size": "4:5 信息流图"},
        "plan",
        {"title": "信息流广告", "description": "突出商品"},
    )

    assert result.ok is True
    assert result.params["ratio"] == "9:16"


def test_prepare_image_generation_uses_multi_reference_with_image_materials():
    result = prepare_image_generation(
        {"image_goal": "商品场景图", "image_style": "真实摄影", "image_size": "1:1 正方形"},
        "plan",
        {"title": "真实场景氛围图", "description": "放到桌面场景"},
        materials=[{"type": "image", "url": "https://x/product.png"}],
    )

    assert result.ok is True
    assert result.endpoint == "/api/picture/multi_reference_image_generation"
    assert result.method == "multi_reference_image_generation"
    assert result.params["reference_image_urls"] == ["https://x/product.png"]
    assert result.params["model"] == "gpt-image-2"
    assert result.params["width"] == 1
    assert result.params["height"] == 1
    assert result.params["imageSize"] == "4K"


def test_prepare_image_generation_uses_image_edit_for_edit_operation():
    result = prepare_image_generation(
        {"image_goal": "给商品图换背景", "image_style": "简洁干净", "image_size": "16:9 横版图"},
        "plan",
        {"title": "图像编辑", "description": "换成白底摄影棚"},
        materials=[{"type": "image", "url": "https://x/source.png", "operation": "edit"}],
    )

    assert result.ok is True
    assert result.endpoint == "/api/picture/image_edit"
    assert result.method == "image_edit"
    assert result.params["image_url"] == "https://x/source.png"
    assert result.params["model"] == "gpt-image-2"
    assert result.params["imageSize"] == "4K"
    assert "换背景" in result.prompt


def test_prepare_image_generation_uses_image_edit_when_plan_mentions_image_edit():
    result = prepare_image_generation(
        {"image_goal": "篮球主题社媒海报", "image_style": "真实摄影", "image_size": "9:16 竖版海报"},
        "## 创作方案\n对用户上传图片进行图片编辑，修改背景为蓝色篮球馆灯光氛围。",
        {"title": "参考图延展", "description": "保留主体质感"},
        materials=[{"type": "image", "url": "https://x/source.png"}],
    )

    assert result.ok is True
    assert result.endpoint == "/api/picture/image_edit"
    assert result.method == "image_edit"
    assert result.params["image_url"] == "https://x/source.png"


def test_prepare_image_generation_prepares_multi_image_fusion():
    result = prepare_image_generation(
        {"image_goal": "把两张图融合成一张", "image_style": "真实摄影", "image_size": "4:5 信息流图"},
        "plan",
        {"title": "多图融合", "description": "融合产品和场景"},
        materials=[
            {"type": "image", "url": "https://x/a.png", "operation": "fusion"},
            {"type": "image", "url": "https://x/b.png"},
        ],
    )

    assert result.ok is True
    assert result.endpoint == "/api/picture/multi_image_fusion"
    assert result.method == "multi_image_fusion"
    assert result.params["image_urls"] == ["https://x/a.png", "https://x/b.png"]
    assert result.params["width"] == 9
    assert result.params["height"] == 16
