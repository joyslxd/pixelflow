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


def test_prepare_image_generation_reports_unavailable_multi_image_fusion():
    result = prepare_image_generation(
        {"image_goal": "把两张图融合成一张", "image_style": "真实摄影", "image_size": "4:5 信息流图"},
        "plan",
        {"title": "多图融合", "description": "融合产品和场景"},
        materials=[
            {"type": "image", "url": "https://x/a.png", "operation": "fusion"},
            {"type": "image", "url": "https://x/b.png"},
        ],
    )

    assert result.ok is False
    assert result.endpoint == "/api/picture/multi_image_fusion"
    assert result.method == "multi_image_fusion"
    assert "未接入" in result.message
    assert result.params["reference_image_urls"] == ["https://x/a.png", "https://x/b.png"]
