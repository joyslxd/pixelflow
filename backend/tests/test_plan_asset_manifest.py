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


def test_extract_script_setting_assets_skips_field_label_headings() -> None:
    """设定模板字段名不能当成出场角色/场景/道具。"""

    markdown = """
## 角色设定
### 从执行者成长为独立负责人的创意工作者
- 视觉特征：短发利落
### 视觉特征
短发
### 动作习惯
常推眼镜
### 安然（女1）
短发主讲，利落职业装
## 场景设定
### 时段
白天
### 光线
暖光
### 办公室梳妆台
暖光窗边
## 道具与产品设定
### 分镜提示词
不要当道具
### 氧气防晒
白色瓶身
## 分镜提示词
0—10秒｜开场
安然：今天讲防晒
"""
    seed = extract_script_setting_assets(markdown)
    assert [item["name"] for item in seed["characters"]] == ["安然"]
    assert [item["name"] for item in seed["scenes"]] == ["办公室梳妆台"]
    assert [item["name"] for item in seed["props"]] == ["氧气防晒"]
    assert "视觉特征" not in [item["name"] for item in seed["characters"]]
    assert "分镜提示词" not in [item["name"] for item in seed["props"]]


def test_extract_script_setting_assets_rebuckets_flat_combined_cast() -> None:
    """合并设定扁平混写：地点/道具不能进出场角色，叙事段名不能进场景。"""

    markdown = """
## 角色/场景/道具设定
### 安然
- 视觉形象：短发主讲
### Yann
- 身份：闺蜜
### 联名方代表
- 核心标签：品牌背书
### 办公室梳妆台
- 时空背景：白天办公室
### 剪辑工作室
- 陈设细节：双屏工作台
### 窗边拍摄区
- 光线氛围：自然光
### 最终提案室
- 可拍要点：会议桌
### 氧气防晒
- 外观材质：白色瓶身
### 手机
- 使用动作：滑动对比
### 反光板
- 品牌露出：无
## 分镜大纲
### 开场钩子
0-5秒
### 补充证明 1
5-10秒
"""
    seed = extract_script_setting_assets(markdown)
    assert [item["name"] for item in seed["characters"]] == ["安然", "Yann", "联名方代表"]
    assert [item["name"] for item in seed["scenes"]] == [
        "办公室梳妆台",
        "剪辑工作室",
        "窗边拍摄区",
        "最终提案室",
    ]
    assert [item["name"] for item in seed["props"]] == ["氧气防晒", "手机", "反光板"]
    assert "开场钩子" not in [item["name"] for item in seed["scenes"]]
    assert "补充证明 1" not in [item["name"] for item in seed["scenes"]]
    assert "{}" not in [item["name"] for item in seed["props"]]


def test_extract_script_setting_assets_nested_under_combined_heading() -> None:
    """导入路径常见：外层合并 H2 + 内层角色/场景/道具 H2。"""

    markdown = """
## 角色/场景/道具设定
## 角色设定
### 安然
短发主讲
### Yann
闺蜜
## 场景设定
### 办公室梳妆台
暖光
## 道具与产品设定
### 氧气防晒
白瓶
## 分镜大纲
### 开场钩子
钩子镜
"""
    seed = extract_script_setting_assets(markdown)
    assert [item["name"] for item in seed["characters"]] == ["安然", "Yann"]
    assert [item["name"] for item in seed["scenes"]] == ["办公室梳妆台"]
    assert [item["name"] for item in seed["props"]] == ["氧气防晒"]


def test_extract_script_setting_assets_skips_character_relationship_heading() -> None:
    """Skill 常把「角色关系」写成容器标题；不能当唯一出场角色。"""

    nested = """
## 角色设定
### 角色关系
- **安然**：女主，短发，主讲
- **Yann**：闺蜜，协助演示
- **联名方代表**：品牌方背书
## 场景设定
### 海岛酒店房间
暖光
## 道具与产品设定
### 联名面霜
玻璃瓶
"""
    seed = extract_script_setting_assets(nested)
    assert [item["name"] for item in seed["characters"]] == ["安然", "Yann", "联名方代表"]
    assert "角色关系" not in [item["name"] for item in seed["characters"]]

    prose = """
## 角色设定
### 角色关系
安然、Yann、联名方代表三人同框；安然是主讲，Yann 是闺蜜，联名方代表负责背书。
"""
    prose_seed = extract_script_setting_assets(prose)
    assert [item["name"] for item in prose_seed["characters"]] == ["安然", "Yann", "联名方代表"]

    dossiers = """
## 角色设定
### 角色关系
三人互为合作关系。
### 安然（女1）
短发主讲
### Yann（女2）
闺蜜
### 联名方代表（男1）
品牌方
"""
    dossier_seed = extract_script_setting_assets(dossiers)
    assert [item["name"] for item in dossier_seed["characters"]] == ["安然", "Yann", "联名方代表"]
