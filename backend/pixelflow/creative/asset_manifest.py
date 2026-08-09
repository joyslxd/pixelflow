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
    """从脚本 Markdown 的角色/场景/道具设定章节解析资产种子（不依赖 LLM）。"""

    text = str(plan_markdown or "").strip()
    result = empty_asset_manifest()
    if not text:
        return result

    character_section = _extract_markdown_section(
        text,
        r"角色设定|角色\s*[/／]\s*场景\s*[/／]\s*道具",
    )
    scene_section = _extract_markdown_section(text, r"场景设定")
    prop_section = _extract_markdown_section(text, r"道具(?:与产品)?设定|道具设定")

    for name, body in _iter_setting_entries(character_section):
        description = body or (
            f"角色“{name}”的固定人物设定；外貌、发型、服装与气质在所有分镜中保持一致。"
        )
        result["characters"].append(
            {
                "name": name,
                "description": description,
                "three_view_prompt": (
                    f"角色“{name}”同一人物的正面、侧面、背面三视图，"
                    f"{description}，三幅人物身份与造型严格一致，纯净背景，无文字水印、无产品道具。"
                ),
            }
        )

    for name, body in _iter_setting_entries(scene_section):
        description = body or f"场景“{name}”的固定空间、光线与氛围设定。"
        result["scenes"].append(
            {
                "name": name,
                "description": description,
                "image_prompt": (
                    f"场景“{name}”环境参考图，{description}，清晰展示空间结构与光线氛围，"
                    "无人、无文字水印。"
                ),
            }
        )

    for name, body in _iter_setting_entries(prop_section):
        description = body or f"道具“{name}”的固定外观、材质与颜色设定。"
        result["props"].append(
            {
                "name": name,
                "description": description,
                "image_prompt": (
                    f"道具“{name}”产品参考图，{description}，完整展示外观材质颜色，"
                    "纯净背景，无人物、无文字水印。"
                ),
            }
        )
    return result


def _extract_markdown_section(text: str, heading_pattern: str) -> str:
    match = re.search(
        rf"#{{1,3}}\s*[0-9一二三四五六七八九十.、)）]*\s*(?:{heading_pattern})[^\n]*\n([\s\S]*?)"
        rf"(?=#{{1,3}}\s*[0-9一二三四五六七八九十.、)）]*\s*(?:角色设定|场景设定|道具|大纲|完整镜头|合规|标题|规格)|$)",
        text,
        flags=re.IGNORECASE,
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
}


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
        body_text = re.sub(r"\s+", " ", body).strip()[:400]
        if _name_key(cleaned) in {_name_key(item) for item in _GENERIC_SETTING_NAMES}:
            concrete = _concrete_name_from_setting_body(body_text)
            if not concrete:
                return
            cleaned = concrete
        key = _name_key(cleaned)
        if key in seen:
            return
        seen.add(key)
        entries.append((cleaned, body_text))

    # ### 阿杰 / #### 程岚（女1）
    heading_blocks = re.split(r"(?=^#{2,4}\s+)", section, flags=re.MULTILINE)
    found_heading = False
    for block in heading_blocks:
        heading = re.match(r"^#{2,4}\s+(.+)$", block.strip(), flags=re.MULTILINE)
        if not heading:
            continue
        found_heading = True
        title = heading.group(1).strip()
        body = block[heading.end() :].strip()
        push(title, body)

    if found_heading and entries:
        return entries

    # - **阿杰**：... / - 阿杰：...
    for match in re.finditer(
        r"^[-*]\s+\*{0,2}([^:*\n]{1,40})\*{0,2}\s*[:：]\s*(.*)$",
        section,
        flags=re.MULTILINE,
    ):
        push(match.group(1), match.group(2))

    # **阿杰**：段落
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
