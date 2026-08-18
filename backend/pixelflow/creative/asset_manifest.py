from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

AssetManifest = dict[str, list[dict[str, str]]]

_COLLECTION_SPECS = (
    ("characters", "character", "three_view_prompt"),
    ("scenes", "scene", "image_prompt"),
    ("props", "prop", "image_prompt"),
)


def empty_asset_manifest() -> AssetManifest:
    return {"characters": [], "scenes": [], "props": []}


def normalize_asset_manifest(
    raw_manifest: Mapping[str, Any] | None,
    scene_blueprints: Sequence[Mapping[str, Any]],
) -> AssetManifest:
    if not isinstance(raw_manifest, Mapping):
        raise ValueError("asset_manifest 必须是对象")

    normalized = empty_asset_manifest()
    seen_names: dict[str, str] = {}
    seen_ids: set[str] = set()

    for collection, asset_type, prompt_field in _COLLECTION_SPECS:
        raw_items = raw_manifest.get(collection, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"asset_manifest.{collection} 必须是数组")

        for position, raw_item in enumerate(raw_items, start=1):
            if not isinstance(raw_item, Mapping):
                raise ValueError(f"asset_manifest.{collection}[{position}] 必须是对象")

            name = _required_text(raw_item, "name", collection, position)
            name_key = _name_key(name)
            if name_key in seen_names:
                raise ValueError(
                    "资产名称必须全局唯一："
                    f"{name} 与 {seen_names[name_key]} 重复；角色、道具、场景不能同名"
                )
            seen_names[name_key] = name

            description = _required_text(raw_item, "description", collection, position)
            prompt = _required_text(raw_item, prompt_field, collection, position)
            asset_id = _stable_asset_id(asset_type, name, seen_ids)
            seen_ids.add(asset_id)

            normalized_item = {
                "asset_id": asset_id,
                "name": name,
                "description": description,
                prompt_field: prompt,
            }
            normalized[collection].append(normalized_item)

    validate_asset_manifest_consistency(normalized, scene_blueprints)
    return normalized


def validate_asset_manifest_consistency(
    asset_manifest: Mapping[str, Any],
    scene_blueprints: Sequence[Mapping[str, Any]],
) -> None:
    referenced = _collect_blueprint_asset_names(scene_blueprints)
    mismatches: list[str] = []

    for collection, _, _ in _COLLECTION_SPECS:
        manifest_items = asset_manifest.get(collection, [])
        if not isinstance(manifest_items, list):
            raise ValueError(f"asset_manifest.{collection} 必须是数组")
        manifest_names = [
            str(item.get("name") or "").strip()
            for item in manifest_items
            if isinstance(item, Mapping)
        ]
        manifest_by_key = {_name_key(name): name for name in manifest_names if name}
        referenced_by_key = {
            _name_key(name): name for name in referenced[collection] if name
        }
        missing = [name for key, name in referenced_by_key.items() if key not in manifest_by_key]
        extra = [name for key, name in manifest_by_key.items() if key not in referenced_by_key]
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append(f"缺少 {missing}")
            if extra:
                details.append(f"多出 {extra}")
            mismatches.append(f"{collection}: {', '.join(details)}")

    if mismatches:
        raise ValueError(
            "Plan 全局资产清单必须与分镜资产需求完全一致（按类别取所有分镜引用并集）："
            + "; ".join(mismatches)
        )


def fallback_asset_manifest(
    scene_blueprints: Sequence[Mapping[str, Any]],
) -> AssetManifest:
    names = _collect_blueprint_asset_names(scene_blueprints)
    raw: AssetManifest = empty_asset_manifest()

    for name in names["characters"]:
        raw["characters"].append(
            {
                "name": name,
                "description": f"角色“{name}”的固定人物设定；外貌、年龄感、发型、服装与气质在所有分镜中保持一致。",
                "three_view_prompt": (
                    f"角色“{name}”同一人物的正面、侧面、背面三视图，完整展示固定外貌、发型和服装，"
                    "三幅人物身份与造型严格一致，纯净背景，无文字水印。"
                ),
            }
        )
    for name in names["scenes"]:
        raw["scenes"].append(
            {
                "name": name,
                "description": f"场景“{name}”的固定空间、时代、光线与氛围设定，在相关分镜中保持一致。",
                "image_prompt": (
                    f"场景“{name}”环境参考图，清晰展示空间结构、时代特征、光线和氛围，"
                    "无人、无文字水印。"
                ),
            }
        )
    for name in names["props"]:
        raw["props"].append(
            {
                "name": name,
                "description": f"道具“{name}”的固定外观、材质、颜色与结构设定，在相关分镜中保持一致。",
                "image_prompt": (
                    f"道具“{name}”产品参考图，完整展示固定外观、材质、颜色和结构，"
                    "纯净背景，无人物、无文字水印。"
                ),
            }
        )

    return normalize_asset_manifest(raw, scene_blueprints)


def render_asset_manifest_markdown(asset_manifest: Mapping[str, Any]) -> str:
    sections = ["## 四、全局资产清单"]
    render_specs = (
        ("characters", "### 4.1 出场角色列表", "三视图生成要求", "three_view_prompt"),
        ("props", "### 4.2 道具列表", "图片生成要求", "image_prompt"),
        ("scenes", "### 4.3 场景列表", "图片生成要求", "image_prompt"),
    )
    for collection, heading, prompt_label, prompt_field in render_specs:
        sections.extend(["", heading])
        items = asset_manifest.get(collection, [])
        if not isinstance(items, list) or not items:
            sections.append("- 无")
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            sections.extend(
                [
                    f"- 名称：{str(item.get('name') or '').strip()}",
                    f"  - 文字说明：{str(item.get('description') or '').strip()}",
                    f"  - {prompt_label}：{str(item.get(prompt_field) or '').strip()}",
                ]
            )
    return "\n".join(sections).strip()


def extract_script_setting_assets(plan_markdown: str) -> AssetManifest:
    """从脚本 Markdown 的角色/场景/道具设定章节解析资产种子（不依赖 LLM）。

    优先读独立二级标题；再读合并段「角色/场景/道具设定」内嵌套小节；
    若仍是扁平三级标题混写，按名称/正文语义分流到三类，避免地点进角色、叙事段名进场景。
    """

    text = str(plan_markdown or "").strip()
    result = empty_asset_manifest()
    if not text:
        return result

    character_section, scene_section, prop_section = _resolve_setting_sections(text)

    for name, body in _iter_setting_entries(character_section):
        _append_setting_asset(result, "characters", name, body)
    for name, body in _iter_setting_entries(scene_section):
        _append_setting_asset(result, "scenes", name, body)
    for name, body in _iter_setting_entries(prop_section):
        _append_setting_asset(result, "props", name, body)

    # 合并段扁平混写：角色桶里混进地点/道具，或场景桶仍空时，按语义重分流
    mixed_in_characters = any(
        isinstance(item, dict)
        and _classify_setting_entry_kind(
            str(item.get("name") or ""),
            str(item.get("description") or ""),
        )
        != "character"
        for item in result["characters"]
    )
    if result["characters"] and (mixed_in_characters or not result["scenes"]):
        result = _rebucket_mixed_setting_assets(result)

    # 丢掉叙事分镜标题（开场钩子/补充证明）与空名
    result = _filter_invalid_setting_assets(result)

    # 无「角色/场景/道具设定」章节时：从对白说话人与时间线标题兜底抽资产，
    # 避免 0—10秒｜成稿拆镜后 global_assets 被空蓝图需求清空。
    if not any(result[collection] for collection in ("characters", "scenes", "props")):
        dialogue = extract_dialogue_cast_assets(text)
        for collection in ("characters", "scenes", "props"):
            result[collection] = list(dialogue.get(collection) or [])
        result = _filter_invalid_setting_assets(result)
    return result


def _resolve_setting_sections(text: str) -> tuple[str, str, str]:
    """解析设定三桶正文：独立 H2 > 合并段内嵌套 H2/H3 > 合并段全文（待重分流）。"""

    character_section = _extract_markdown_section(text, r"角色设定")
    scene_section = _extract_markdown_section(text, r"场景设定")
    prop_section = _extract_markdown_section(text, r"道具(?:与产品)?设定|道具设定")
    # 导出稿可能在完整合并设定后追加三个「结构对齐保留」空章节。
    # 只有能解析出实体的独立章节才可覆盖前面的合并设定。
    if any(
        _iter_setting_entries(section)
        for section in (character_section, scene_section, prop_section)
        if section
    ):
        return character_section, scene_section, prop_section

    combined = _extract_markdown_section(text, r"角色\s*[/／]\s*场景\s*[/／]\s*道具(?:设定)?")
    if not combined:
        # 仅当正文明显是设定集时才整篇当合并段，避免把分镜大纲标题抽成资产
        if re.search(
            r"(?:^|\n)#{2,4}\s*.*(?:角色设定|场景设定|道具(?:与产品)?设定)|视觉形象|时空背景|外观材质",
            text,
        ):
            combined = text
        else:
            return "", "", ""

    nested_character = _extract_markdown_section(combined, r"角色设定") or _extract_hashed_bucket(
        combined, r"角色设定"
    )
    nested_scene = _extract_markdown_section(combined, r"场景设定") or _extract_hashed_bucket(
        combined, r"场景设定"
    )
    nested_prop = _extract_markdown_section(
        combined, r"道具(?:与产品)?设定|道具设定"
    ) or _extract_hashed_bucket(combined, r"道具(?:与产品)?设定|道具设定")
    if nested_character or nested_scene or nested_prop:
        return nested_character, nested_scene, nested_prop

    # 扁平混写：暂全部交给角色桶，后续 _rebucket_mixed_setting_assets 分流
    return combined, "", ""


def _extract_hashed_bucket(text: str, heading_pattern: str) -> str:
    """从 ###/#### 桶标题截取（LLM 常把「角色设定」写成三级标题）。"""

    match = re.search(
        rf"^###{{1,2}}\s*[0-9一二三四五六七八九十.、)）]*\s*(?:{heading_pattern})[^\n]*\n([\s\S]*?)"
        rf"(?=^#{{2,4}}\s*[0-9一二三四五六七八九十.、)）]*\s*"
        rf"(?:角色设定|场景设定|道具|大纲|完整镜头|合规|标题|规格|分镜提示词|镜头列表|分镜大纲)|\Z)",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def _append_setting_asset(
    result: AssetManifest,
    collection: str,
    name: str,
    body: str,
) -> None:
    cleaned = str(name or "").strip()
    if not cleaned or _is_narrative_beat_name(cleaned) or _is_empty_asset_name(cleaned):
        return
    description = body or ""
    if collection == "characters":
        description = description or (
            f"角色“{cleaned}”的固定人物设定；外貌、发型、服装与气质在所有分镜中保持一致。"
        )
        result["characters"].append(
            {
                "name": cleaned,
                "description": description,
                "three_view_prompt": (
                    f"角色“{cleaned}”同一人物的正面、侧面、背面三视图，"
                    f"{description}，三幅人物身份与造型严格一致，纯净背景，无文字水印、无产品道具。"
                ),
            }
        )
        return
    if collection == "scenes":
        description = description or f"场景“{cleaned}”的固定空间、光线与氛围设定。"
        result["scenes"].append(
            {
                "name": cleaned,
                "description": description,
                "image_prompt": (
                    f"场景“{cleaned}”环境参考图，{description}，清晰展示空间结构与光线氛围，"
                    "无人、无文字水印。"
                ),
            }
        )
        return
    description = description or f"道具“{cleaned}”的固定外观、材质与颜色设定。"
    result["props"].append(
        {
            "name": cleaned,
            "description": description,
            "image_prompt": (
                f"道具“{cleaned}”产品参考图，{description}，完整展示外观材质颜色，"
                "纯净背景，无人物、无文字水印。"
            ),
        }
    )


def _rebucket_mixed_setting_assets(manifest: AssetManifest) -> AssetManifest:
    """扁平设定混写时，把角色桶里的地点/道具分流到正确集合。"""

    rebucketed = empty_asset_manifest()
    # 已有正确桶先保留
    for collection in ("scenes", "props"):
        for item in manifest.get(collection) or []:
            if isinstance(item, dict) and item.get("name"):
                rebucketed[collection].append(dict(item))

    seen = {
        collection: {_name_key(str(item.get("name") or "")) for item in rebucketed[collection]}
        for collection in ("characters", "scenes", "props")
    }

    for item in manifest.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        body = str(item.get("description") or "")
        if not name:
            continue
        kind = _classify_setting_entry_kind(name, body)
        target = {"character": "characters", "scene": "scenes", "prop": "props"}[kind]
        key = _name_key(name)
        if key in seen[target]:
            continue
        seen[target].add(key)
        if target == "characters":
            rebucketed["characters"].append(dict(item))
        else:
            # 从角色条目重建场景/道具字段
            _append_setting_asset(rebucketed, target, name, body)
    return rebucketed


def _filter_invalid_setting_assets(manifest: AssetManifest) -> AssetManifest:
    cleaned = empty_asset_manifest()
    for collection in ("characters", "scenes", "props"):
        for item in manifest.get(collection) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or _is_empty_asset_name(name) or _is_narrative_beat_name(name):
                continue
            if collection == "scenes" and _is_narrative_beat_name(name):
                continue
            cleaned[collection].append(dict(item))
    return cleaned


def _is_empty_asset_name(name: str) -> bool:
    cleaned = re.sub(r"[\s\{\}\[\]\(\)（）]", "", str(name or ""))
    return not cleaned


def _is_narrative_beat_name(name: str) -> bool:
    """分镜叙事职能名（开场钩子/补充证明）不是可拍摄物理场景。"""

    key = _name_key(name)
    if key in {_name_key(item) for item in _NARRATIVE_BEAT_NAMES}:
        return True
    return bool(re.fullmatch(r"补充证明\d*", key))


def _classify_setting_entry_kind(name: str, body: str) -> str:
    """扁平混写时判断条目属于角色 / 场景 / 道具。"""

    text = f"{name} {body}"
    if _is_narrative_beat_name(name):
        return "scene"  # 稍后会被 filter 丢掉；避免进角色
    # 道具：产品词、器材词、正文偏外观材质
    if re.search(
        r"(防晒|乳液|粉底|精华|面霜|口红|手机|反光板|三脚架|灯|包装|瓶|盒|袋)",
        name,
    ) or re.search(r"(外观材质|品牌露出|使用动作|产品参考|道具)", body):
        return "prop"
    # 场景：地点后缀 / 空间陈设词
    if re.search(
        r"(办公室|梳妆台|工作室|拍摄区|提案室|房间|酒店|海岛|餐厅|店|厅|台|区|室|外景|内景|窗边)",
        name,
    ) or re.search(r"(时空背景|陈设细节|光线氛围|可拍要点|环境参考|无人)", body):
        return "scene"
    # 角色：人设字段或明显人名/身份
    if re.search(r"(视觉形象|身份|核心标签|性格|金句|人物|主讲|闺蜜|代表)", text):
        return "character"
    # 短专名默认当角色（安然 / Yann）；含地点后缀已在上面拦截
    if len(name) <= 12 and not re.search(r"(证明|钩子|收口|大纲|提示词)", name):
        return "character"
    return "prop"


_NARRATIVE_BEAT_NAMES = {
    "开场钩子",
    "卖点证明",
    "转化收口",
    "补充证明",
    "追剧钩子",
    "投流记忆点",
}


_DIALOGUE_LABEL_BLOCKLIST = frozenset(
    {
        "剧情",
        "动作",
        "剧情动作",
        "新增对白",
        "原片对白",
        "产品演示",
        "追剧钩子",
        "投流记忆点",
        "画面",
        "旁白",
        "字幕",
        "镜头描述",
        "提示词",
        "画外音",
    }
)


def extract_dialogue_cast_assets(plan_markdown: str) -> AssetManifest:
    """从时间线成稿对白/标题抽角色、场景、道具种子（无设定章节时用）。"""

    text = str(plan_markdown or "")
    result = empty_asset_manifest()
    if not text.strip():
        return result

    speakers: list[str] = []
    seen_speakers: set[str] = set()
    for match in re.finditer(
        r"(?m)(?:^|[\n\r])\s*(?:【[^】]{0,20}】\s*)?"
        r"([A-Za-z][A-Za-z0-9_\-]{0,20}|[\u4e00-\u9fff]{1,12})"
        r"\s*[：:]\s*",
        text,
    ):
        name = match.group(1).strip()
        name = re.sub(r"(画外音|旁白)$", "", name).strip()
        key = _name_key(name)
        if (
            not key
            or key in seen_speakers
            or key in {_name_key(item) for item in _GENERIC_SETTING_NAMES}
            or key in {_name_key(item) for item in _DIALOGUE_LABEL_BLOCKLIST}
            or re.search(r"(关系|档案|弧线|设定|场景|道具|对白|动作)", name)
        ):
            continue
        seen_speakers.add(key)
        speakers.append(name)

    for name in speakers:
        result["characters"].append(
            {
                "name": name,
                "description": f"角色“{name}”在成片中反复出场，造型与身份保持一致。",
                "three_view_prompt": (
                    f"角色“{name}”同一人物的正面、侧面、背面三视图，"
                    "外貌发型服装气质全片一致，纯净背景，无文字水印、无产品道具。"
                ),
            }
        )

    # 时间线/正文里的地点作场景种子（保守枚举，避免把标题整句当场景名）
    scene_candidates = (
        ("办公室", "办公室"),
        ("梳妆台", "办公室梳妆台"),
        ("海岛", "海岛外拍"),
        ("酒店", "酒店"),
        ("晚宴", "晚宴现场"),
        ("房间", "室内房间"),
        ("窗边", "窗边"),
    )
    seen_scenes: set[str] = set()
    for needle, scene_name in scene_candidates:
        if needle not in text:
            continue
        key = _name_key(scene_name)
        if key in seen_scenes:
            continue
        seen_scenes.add(key)
        result["scenes"].append(
            {
                "name": scene_name,
                "description": f"场景“{scene_name}”的固定空间、光线与氛围设定。",
                "image_prompt": (
                    f"场景“{scene_name}”环境参考图，清晰展示空间结构与光线氛围，"
                    "无人、无文字水印。"
                ),
            }
        )
        if len(result["scenes"]) >= 6:
            break

    # 成稿里高频产品名：氧气防晒 等（2–12 字 + 防晒/乳液/粉底）
    prop_hits: list[str] = []
    seen_props: set[str] = set()
    for match in re.finditer(
        r"([\u4e00-\u9fffA-Za-z0-9]{2,12}(?:防晒|乳液|粉底|精华|面霜|口红))",
        text,
    ):
        name = match.group(1).strip()
        key = _name_key(name)
        if not key or key in seen_props or key in {_name_key(item) for item in _GENERIC_SETTING_NAMES}:
            continue
        seen_props.add(key)
        prop_hits.append(name)
    for name in prop_hits[:8]:
        result["props"].append(
            {
                "name": name,
                "description": f"道具“{name}”的固定外观、材质与颜色设定。",
                "image_prompt": (
                    f"道具“{name}”产品参考图，完整展示外观材质颜色，"
                    "纯净背景，无人物、无文字水印。"
                ),
            }
        )
    return result


def _extract_markdown_section(text: str, heading_pattern: str) -> str:
    # 只在行首二级大节（## 且非 ###）截断；避免 ### 被吃掉一个 # 后误触发截断。
    match = re.search(
        rf"^##(?!#)\s*[0-9一二三四五六七八九十.、)）]*\s*(?:{heading_pattern})[^\n]*\n([\s\S]*?)"
        rf"(?=^##(?!#)\s*[0-9一二三四五六七八九十.、)）]*\s*"
        rf"(?:角色设定|场景设定|道具|大纲|完整镜头|合规|标题|规格|分镜提示词|镜头列表|分镜大纲)|\Z)",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


_GENERIC_SETTING_NAMES = {
    "角色设定",
    "场景设定",
    "道具",
    "道具与产品设定",
    "道具设定",
    "视觉形象",
    "身份",
    "性格",
    "金句",
    "核心标签",
    "名称",
    "文字说明",
    "核心产品",
    "产品",
    "商品",
    "主商品",
    "关键道具",
    "产品道具",
    "目标用户",
    "用户",
    "消费者",
    "人物",
    "角色",
    "模特",
    "真实使用场景",
    "使用场景",
    "真实场景",
    "场景",
    "环境",
    # Skill /characters 常见容器标题，不是可出镜角色名
    "角色关系",
    "角色关系图",
    "主要角色档案",
    "角色档案",
    "角色弧线",
    "感情线弧线",
    "关键互动场景预设",
    "四层反派体系",
    # 设定模板字段名：常被误抽成出场角色/场景/道具
    "视觉特征",
    "动作习惯",
    "人物弧光",
    "关键关系",
    "定位",
    "人设",
    "人设定位",
    "时段",
    "光线",
    "光影",
    "色调",
    "视觉要点",
    "功能",
    "时空背景",
    "陈设细节",
    "光线氛围",
    "可拍要点",
    "外观材质",
    "品牌露出",
    "使用动作",
    "分镜提示词",
    "镜头列表",
    "分镜大纲",
    "对白",
    "旁白",
}

# 容器标题：不可当作资产名；应继续解析其下的列表/子标题人名
_CONTAINER_SETTING_HEADINGS = {
    "角色关系",
    "角色关系图",
    "主要角色档案",
    "角色档案",
    "角色弧线",
    "感情线弧线",
    "关键互动场景预设",
    "四层反派体系",
}

# 字段标签：只描述属性，绝不是可生成实体名
_SETTING_FIELD_LABELS = {
    "视觉特征",
    "动作习惯",
    "人物弧光",
    "关键关系",
    "视觉形象",
    "身份",
    "性格",
    "金句",
    "核心标签",
    "定位",
    "人设",
    "人设定位",
    "时段",
    "光线",
    "光影",
    "色调",
    "视觉要点",
    "功能",
    "时空背景",
    "陈设细节",
    "光线氛围",
    "可拍要点",
    "外观材质",
    "品牌露出",
    "使用动作",
    "名称",
    "文字说明",
    "分镜提示词",
    "镜头列表",
    "分镜大纲",
}


def _is_container_setting_heading(name: str) -> bool:
    key = _name_key(name)
    return key in {_name_key(item) for item in _CONTAINER_SETTING_HEADINGS} or key.startswith(
        _name_key("角色关系")
    )


def _is_setting_field_label(name: str) -> bool:
    key = _name_key(name)
    return key in {_name_key(item) for item in _SETTING_FIELD_LABELS}


def _concrete_name_from_setting_body(body: str) -> str:
    """泛化标题（如「核心产品」）时，从正文抽可复用的具体实体名。"""

    text = str(body or "").strip()
    if not text:
        return ""
    labeled = re.search(
        r"(?:名称|品牌|产品名|商品名|道具名)\s*[:：]\s*([^\n\s，,。；;]{2,40})",
        text,
    )
    if labeled:
        candidate = re.sub(r"[*_#`]", "", labeled.group(1)).strip()
        candidate = re.split(r"[（(：:\-—|/]", candidate, maxsplit=1)[0].strip()
        if candidate and _name_key(candidate) not in {_name_key(item) for item in _GENERIC_SETTING_NAMES}:
            return candidate
    first_line = re.split(r"[\n。；;]", text, maxsplit=1)[0].strip()
    first_line = re.sub(r"^(?:外观|材质|颜色|描述|说明|名称|品牌)\s*[:：]\s*", "", first_line)
    first_line = re.sub(r"[*_#`]", "", first_line).strip()
    # 短专名优先，例如「蓝妹啤酒，绿色瓶身」
    short = re.split(r"[，,、/\s|]", first_line, maxsplit=1)[0].strip()
    if 2 <= len(short) <= 20 and _name_key(short) not in {_name_key(item) for item in _GENERIC_SETTING_NAMES}:
        if not re.search(r"(卖点|强调|主张|要求|需要|目标)", short):
            return short
    return ""


def _names_from_cast_list_prose(body: str) -> list[tuple[str, str]]:
    """从「安然、Yann、联名方代表三人同框」这类关系散文抽人名。"""

    text = str(body or "").strip()
    if not text:
        return []
    # 优先匹配「A、B、C三人/之间/同框」
    match = re.search(
        r"^([^\n。；;]{2,80}?)(?:三人|三位|两人|两位|四人|四位|之间|同框|共同)",
        text,
    )
    chunk = match.group(1) if match else ""
    if not chunk:
        return []
    names: list[tuple[str, str]] = []
    for raw in re.split(r"[、，,/／|]", chunk):
        cleaned = re.sub(r"[*_#`]", "", raw).strip()
        cleaned = re.split(r"[（(：:\-—]", cleaned, maxsplit=1)[0].strip()
        if not cleaned or len(cleaned) > 20:
            continue
        if _name_key(cleaned) in {_name_key(item) for item in _GENERIC_SETTING_NAMES}:
            continue
        if re.search(r"(关系|档案|弧线|设定|场景|道具)", cleaned):
            continue
        names.append((cleaned, text[:400]))
    return names


def _iter_setting_entries(section: str) -> list[tuple[str, str]]:
    if not section.strip():
        return []
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def push(name: str, body: str) -> None:
        cleaned = re.sub(r"[*_#`]", "", name).strip()
        cleaned = re.split(r"[（(：:\-—|/]", cleaned, maxsplit=1)[0].strip()
        if not cleaned or len(cleaned) > 40:
            return
        if _is_container_setting_heading(cleaned):
            return
        # 「视觉特征 / 时段 / 分镜提示词」是字段标签，不是可出镜实体
        if _is_setting_field_label(cleaned):
            return
        body_text = re.sub(r"\s+", " ", body).strip()[:400]
        if _name_key(cleaned) in {_name_key(item) for item in _GENERIC_SETTING_NAMES}:
            concrete = _concrete_name_from_setting_body(body_text)
            if not concrete or _is_setting_field_label(concrete):
                return
            cleaned = concrete
        # 过长身份描述（如「从执行者成长为…的创意工作者」）不是稳定角色名
        if len(cleaned) > 16 and re.search(
            r"(工作者|负责人|创业者|上班族|宝妈|用户画像|目标人群)",
            cleaned,
        ):
            return
        key = _name_key(cleaned)
        if key in seen:
            return
        seen.add(key)
        entries.append((cleaned, body_text))

    def push_list_entries(text: str) -> None:
        list_hits = 0
        for match in re.finditer(
            r"^[-*]\s+\*{0,2}([^:*\n]{1,40})\*{0,2}\s*[:：]\s*(.*)$",
            text,
            flags=re.MULTILINE,
        ):
            before = len(entries)
            push(match.group(1), match.group(2))
            if len(entries) > before:
                list_hits += 1
        # 仅当列表未命中时，再扫「**安然**：」行，避免把正文强调误当角色
        if list_hits == 0:
            for match in re.finditer(
                r"\*\*([^*]{1,40})\*\*\s*[:：]\s*([^\n]*)",
                text,
            ):
                push(match.group(1), match.group(2))

    # ### 阿杰 / #### 程岚（女1） / - ### 安然
    heading_pattern = r"(?:[-*]\s+)?#{2,4}\s+"
    heading_blocks = re.split(rf"(?=^{heading_pattern})", section, flags=re.MULTILINE)
    found_heading = False
    for block in heading_blocks:
        cleaned_block = block.strip()
        heading = re.match(rf"^{heading_pattern}(.+)$", cleaned_block, flags=re.MULTILINE)
        if not heading:
            continue
        found_heading = True
        title = heading.group(1).strip()
        body = cleaned_block[heading.end() :].strip()
        title_name = re.sub(r"[*_#`]", "", title).strip()
        title_name = re.split(r"[（(：:\-—|/]", title_name, maxsplit=1)[0].strip()
        if _is_container_setting_heading(title_name):
            before = len(entries)
            push_list_entries(body)
            if len(entries) == before:
                for name, prose in _names_from_cast_list_prose(body):
                    push(name, prose)
        else:
            push(title, body)

    if found_heading and entries:
        return entries

    # - **阿杰**：... / - 阿杰：...
    push_list_entries(section)

    # **阿杰**：段落（整段仍无条目时）
    if not entries:
        for match in re.finditer(
            r"\*\*([^*]{1,40})\*\*\s*[:：]?\s*([^\n]*)",
            section,
        ):
            push(match.group(1), match.group(2))

    return entries


def _collect_blueprint_asset_names(
    scene_blueprints: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    collected = {"characters": [], "scenes": [], "props": []}
    seen = {"characters": set(), "scenes": set(), "props": set()}

    for blueprint in scene_blueprints:
        if not isinstance(blueprint, Mapping):
            continue
        requirements = blueprint.get("asset_requirements")
        if not isinstance(requirements, Mapping):
            continue
        for collection, _, _ in _COLLECTION_SPECS:
            raw_names = requirements.get(collection, [])
            if not isinstance(raw_names, list):
                continue
            for raw_name in raw_names:
                name = str(raw_name or "").strip()
                key = _name_key(name)
                if not name or key in seen[collection]:
                    continue
                seen[collection].add(key)
                collected[collection].append(name)
    return collected


def _required_text(
    item: Mapping[str, Any],
    field: str,
    collection: str,
    position: int,
) -> str:
    value = str(item.get(field) or "").strip()
    if not value:
        raise ValueError(f"asset_manifest.{collection}[{position}].{field} 不能为空")
    return value


def _name_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _stable_asset_id(asset_type: str, name: str, existing_ids: set[str]) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    suffix = ascii_slug[:48] if ascii_slug else hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    candidate = f"{asset_type}-{suffix}"
    if candidate not in existing_ids:
        return candidate
    digest = hashlib.sha1(f"{asset_type}:{name}".encode()).hexdigest()[:12]
    return f"{asset_type}-{suffix}-{digest}"
