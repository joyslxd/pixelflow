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
    digest = hashlib.sha1(f"{asset_type}:{name}".encode("utf-8")).hexdigest()[:12]
    return f"{asset_type}-{suffix}-{digest}"
