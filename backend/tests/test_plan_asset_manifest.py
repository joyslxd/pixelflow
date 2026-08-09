from __future__ import annotations

import copy

import pytest

from pixelflow.creative.asset_manifest import (
    empty_asset_manifest,
    extract_script_setting_assets,
    fallback_asset_manifest,
    normalize_asset_manifest,
    render_asset_manifest_markdown,
    validate_asset_manifest_consistency,
)


def _blueprint(
    *,
    characters: list[str] | None = None,
    scenes: list[str] | None = None,
    props: list[str] | None = None,
) -> dict[str, object]:
    return {
        "scene_id": "scene-1",
        "scene_index": 1,
        "asset_requirements": {
            "characters": characters or [],
            "scenes": scenes or [],
            "props": props or [],
        },
    }


def _valid_manifest() -> dict[str, list[dict[str, str]]]:
    return {
        "characters": [
            {
                "name": "林晓",
                "description": "24岁女性通勤者，齐肩黑发，浅灰风衣，气质沉稳。",
                "three_view_prompt": "林晓同一人物的正面、侧面、背面三视图，服装发型五官一致。",
            }
        ],
        "scenes": [
            {
                "name": "雨夜公交站",
                "description": "现代城市公交站，夜雨，冷蓝路灯，湿润地面反光。",
                "image_prompt": "雨夜公交站环境参考图，冷蓝路灯和湿润地面反光。",
            }
        ],
        "props": [
            {
                "name": "黑色防水背包",
                "description": "哑光黑色方形通勤背包，银色拉链，正面无文字。",
                "image_prompt": "黑色防水背包产品参考图，哑光黑色，银色拉链。",
            }
        ],
    }


def _matching_blueprints() -> list[dict[str, object]]:
    return [_blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"])]


def test_empty_asset_manifest_has_all_three_collections() -> None:
    assert empty_asset_manifest() == {"characters": [], "scenes": [], "props": []}


def test_normalize_asset_manifest_generates_ids_and_preserves_canonical_names() -> None:
    manifest = normalize_asset_manifest(_valid_manifest(), _matching_blueprints())

    assert [item["name"] for item in manifest["characters"]] == ["林晓"]
    assert manifest["characters"][0]["asset_id"].startswith("character-")
    assert manifest["scenes"][0]["asset_id"].startswith("scene-")
    assert manifest["props"][0]["asset_id"].startswith("prop-")
    assert manifest["characters"][0]["description"] == _valid_manifest()["characters"][0]["description"]
    assert manifest["characters"][0]["three_view_prompt"] == _valid_manifest()["characters"][0]["three_view_prompt"]


def test_normalize_asset_manifest_ignores_llm_ids_and_keeps_ids_stable() -> None:
    raw = _valid_manifest()
    raw["characters"][0]["asset_id"] = "llm-random-id"

    first = normalize_asset_manifest(raw, _matching_blueprints())
    second = normalize_asset_manifest(copy.deepcopy(raw), _matching_blueprints())

    assert first["characters"][0]["asset_id"] != "llm-random-id"
    assert first["characters"][0]["asset_id"] == second["characters"][0]["asset_id"]


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        (
            {
                "characters": [],
                "scenes": _valid_manifest()["scenes"],
                "props": _valid_manifest()["props"],
            },
            "必须与分镜资产需求完全一致",
        ),
        (
            {
                **_valid_manifest(),
                "props": [
                    *_valid_manifest()["props"],
                    {"name": "银色保温杯", "description": "银色杯体", "image_prompt": "银色保温杯产品图"},
                ],
            },
            "必须与分镜资产需求完全一致",
        ),
    ],
)
def test_normalize_asset_manifest_rejects_missing_or_extra_assets(
    manifest: dict[str, list[dict[str, str]]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_asset_manifest(manifest, _matching_blueprints())


def test_normalize_asset_manifest_rejects_cross_category_name_duplicates() -> None:
    raw = _valid_manifest()
    raw["props"][0]["name"] = "林晓"
    blueprints = [_blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["林晓"])]

    with pytest.raises(ValueError, match="资产名称必须全局唯一"):
        normalize_asset_manifest(raw, blueprints)


@pytest.mark.parametrize("field", ["description", "three_view_prompt"])
def test_character_manifest_requires_complete_generation_contract(field: str) -> None:
    raw = _valid_manifest()
    raw["characters"][0][field] = ""

    with pytest.raises(ValueError, match=field):
        normalize_asset_manifest(raw, _matching_blueprints())


@pytest.mark.parametrize("collection", ["scenes", "props"])
def test_image_manifest_requires_image_prompt(collection: str) -> None:
    raw = _valid_manifest()
    raw[collection][0]["image_prompt"] = ""

    with pytest.raises(ValueError, match="image_prompt"):
        normalize_asset_manifest(raw, _matching_blueprints())


def test_validate_asset_manifest_consistency_accepts_cross_scene_asset_reuse_once() -> None:
    manifest = normalize_asset_manifest(
        _valid_manifest(),
        [
            _blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"]),
            _blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"]),
        ],
    )

    validate_asset_manifest_consistency(
        manifest,
        [
            _blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"]),
            _blueprint(characters=["林晓"], scenes=["雨夜公交站"], props=["黑色防水背包"]),
        ],
    )

    assert len(manifest["characters"]) == 1
    assert len(manifest["scenes"]) == 1
    assert len(manifest["props"]) == 1


def test_fallback_asset_manifest_uses_blueprint_names_and_complete_prompts() -> None:
    manifest = fallback_asset_manifest(_matching_blueprints())

    assert [item["name"] for item in manifest["characters"]] == ["林晓"]
    assert "正面、侧面、背面" in manifest["characters"][0]["three_view_prompt"]
    assert manifest["scenes"][0]["description"]
    assert manifest["scenes"][0]["image_prompt"]
    assert manifest["props"][0]["description"]
    assert manifest["props"][0]["image_prompt"]


def test_render_asset_manifest_markdown_uses_exact_names_and_prompts() -> None:
    manifest = normalize_asset_manifest(_valid_manifest(), _matching_blueprints())

    markdown = render_asset_manifest_markdown(manifest)

    assert "## 四、全局资产清单" in markdown
    assert "### 4.1 出场角色列表" in markdown
    assert "- 名称：林晓" in markdown
    assert f"  - 文字说明：{manifest['characters'][0]['description']}" in markdown
    assert f"  - 三视图生成要求：{manifest['characters'][0]['three_view_prompt']}" in markdown
    assert "### 4.2 道具列表" in markdown
    assert "- 名称：黑色防水背包" in markdown
    assert "### 4.3 场景列表" in markdown
    assert "- 名称：雨夜公交站" in markdown


def test_render_asset_manifest_markdown_marks_empty_groups() -> None:
    markdown = render_asset_manifest_markdown(empty_asset_manifest())

    assert markdown.count("- 无") == 3


def test_extract_script_setting_assets_reads_all_cast_and_props() -> None:
    markdown = """
## 角色设定
### 阿杰（男1）
- 视觉形象：浅灰衬衫
### 程岚（女1）
- 视觉形象：深蓝Polo
### 老周（男2）
- 视觉形象：夹克
### 小夏（女2）
- 视觉形象：针织衫
## 场景设定
### 中餐厅
暖光圆桌
## 道具与产品设定
### 蓝妹啤酒
绿色瓶身
### 旧相册
泛黄照片
"""
    seed = extract_script_setting_assets(markdown)
    assert [item["name"] for item in seed["characters"]] == ["阿杰", "程岚", "老周", "小夏"]
    assert [item["name"] for item in seed["scenes"]] == ["中餐厅"]
    assert [item["name"] for item in seed["props"]] == ["蓝妹啤酒", "旧相册"]
    assert all("三视图" in item["three_view_prompt"] for item in seed["characters"])


def test_extract_script_setting_assets_resolves_generic_core_product_heading() -> None:
    markdown = """
## 道具与产品设定
### 核心产品
名称：蓝妹啤酒
绿色玻璃瓶，易拉环拉环清晰
### 旧相册
泛黄照片
"""
    seed = extract_script_setting_assets(markdown)
    assert [item["name"] for item in seed["props"]] == ["蓝妹啤酒", "旧相册"]
