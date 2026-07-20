"""Seedance Skill 驱动的 Plan 分镜写作提示词与不可变合同校验。"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pixelflow.creative.asset_manifest import validate_asset_manifest_consistency
from pixelflow.creative.scene_blueprint import (
    normalize_scene_blueprints,
    validate_shot_description_quality,
)
from pixelflow.generate.seedance_prompt import MAX_IMAGE_REFERENCES, load_seedance_guidance

_ASSET_COLLECTION_PREFIXES = {
    "characters": "character-",
    "scenes": "scene-",
    "props": "prop-",
}
_ASSET_REFERENCE_PATTERN = re.compile(r"@(?P<asset_id>[A-Za-z0-9_-]+)")
_DECIMAL_TIMECODE_PATTERN = re.compile(r"\d+(?:\.\d+)\s*(?:[-~—至]|秒)")
_MILLISECOND_PATTERN = re.compile(r"(?:\bms\b|毫秒)", flags=re.IGNORECASE)
_NARRATIVE_FIELDS = ("title", "storyline", "shot_description", "narration", "transition")
_IMMUTABLE_FIELDS = (
    "scene_id",
    "scene_index",
    "structure_role",
    "start_sec",
    "end_sec",
    "duration_sec",
    "asset_requirements",
)
_ASSET_USAGE_MARKERS = (
    "固定",
    "保持",
    "参考",
    "锚定",
    "锁定",
    "作为",
    "用于",
    "依据",
    "参照",
    "统一",
    "确保",
    "延续",
    "基准",
    "为准",
    "锚点",
    "绑定",
    "一致",
)


def build_seedance_plan_authoring_prompt(
    *,
    plan_markdown: str,
    scene_blueprints: list[dict[str, Any]],
    asset_manifest: dict[str, list[dict[str, str]]],
    creation_contract: dict[str, Any],
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any],
    materials: list[dict[str, Any]],
    revision_feedback: str = "",
    validation_feedback: str = "",
) -> str:
    """构造一次覆盖全部分镜的 Seedance 专用写作请求。"""
    video_model = str(creation_contract.get("video_model") or "").strip()
    if not video_model:
        raise ValueError("Seedance Plan 写作必须传入用户已确认的 video_model")

    scene_contracts = _scene_authoring_contracts(scene_blueprints, asset_manifest)
    return f"""你是 PixelFlow 策划 Agent 的 Seedance Plan 分镜写作 Skill。
你只负责把已经确定的结构化分镜改写成可直接执行的 Seedance 镜头描述，不重新做策划、不改变创作合同。

不可变合同：
1. 当前用户已确认的视频模型是 `{video_model}`。不得修改 video_model，不得假设另一个 Seedance 版本或能力。
2. 不得修改分镜数量、顺序、全局时间线和单镜时长；每镜仍为 4-15 个整数秒。
3. 不得修改画幅、清晰度和声音能力；不得修改商品卖点、转化目标和其他 PixelFlow 创作合同字段。
4. 不得新增、删除、改名或跨分镜挪用资产；每镜只能引用该镜 allowed_assets 中声明的稳定 @asset_id。
5. 如果 Skill 规则与当前模型实时能力冲突，保留 PixelFlow 创作合同；不要擅自改参数，由调用层报告不兼容。

写作合同：
1. 每个 shot_description 是一整段中文；可以用分号细分动作，但不得换段或列清单。
2. 使用当前分镜内部的 `0-N秒` 整数时间码，从 0 秒连续覆盖到 duration_sec，禁止 ms、毫秒和小数时间码。
3. 每段必须写出有起点、过程和结果的动作，以及地点、主体、景别、运镜、光影、环境声/动作声/对白或旁白、镜尾收束。
4. 参考素材只写 @character-x、@scene-x、@prop-x。每个分镜最多 {MAX_IMAGE_REFERENCES} 张不同图片参考，同一素材只计一次。
5. 每次引用必须说明用途。allowed_assets 已为每项提供 reference_phrase；每个出场资产必须在本镜 shot_description 中逐字使用对应 reference_phrase 一次，再描述动作、构图和声音。
6. characters 只放人物；商品、包装、工具、配件和卖点物件放 props；环境放 scenes。无声明素材时用文字完整描述，不虚构 @asset_id。
7. 参考图决定身份和外观，文字决定本镜头动作、构图、镜头和声音；冲突时以 PixelFlow 创作合同与明确用户要求为准。
8. title、storyline、narration、transition 可在不改变原意的前提下写得更具体；禁止返回或修改其他字段。

只返回 JSON 对象，不要 Markdown 代码围栏：
{{
  "scene_blueprints": [
    {{
      "scene_id": "scene-1",
      "scene_index": 1,
      "title": "具体分镜标题",
      "storyline": "本镜头承担的因果与卖点任务",
      "shot_description": "0-3秒：...；3-N秒：...。",
      "narration": "对白/旁白，或本分镜无旁白",
      "transition": "明确镜尾和下一镜衔接"
    }}
  ]
}}

当前创作合同：
{_json(creation_contract)}

当前表单：
{_json(form_values)}

当前创意方向：
{_json(selected_direction)}

采集上下文与用户原始要求：
{_json(intake_context)}

附件摘要：
{_json(materials)}

用户本次 Plan 修订意见：
{revision_feedback.strip() or "无（首次生成）"}

上次专用写作校验反馈：
{validation_feedback.strip() or "无"}

每镜不可变字段与可用稳定资产：
{_json(scene_contracts)}

当前完整结构化分镜：
{_json(scene_blueprints)}

当前完整 plan.md：
{str(plan_markdown or "").strip()}

Seedance 系列 Skill 原始规则摘录（必须执行）：
{load_seedance_guidance()}
""".strip()


def apply_seedance_plan_authoring(
    scene_blueprints: list[dict[str, Any]],
    authored_blueprints: Any,
    *,
    asset_manifest: Mapping[str, Any],
    total_duration_sec: int,
) -> list[dict[str, Any]]:
    """校验专用写作结果，只合并叙事字段并保留权威结构。"""
    if not isinstance(authored_blueprints, list) or not authored_blueprints:
        raise ValueError("Seedance Plan 写作未返回 scene_blueprints")
    if len(authored_blueprints) != len(scene_blueprints):
        raise ValueError("Seedance Plan 写作必须完整返回全部分镜，不能少也不能多")

    expected_by_id = {
        str(item.get("scene_id") or "").strip(): item
        for item in scene_blueprints
        if isinstance(item, dict)
    }
    authored_by_id: dict[str, Mapping[str, Any]] = {}
    for position, raw in enumerate(authored_blueprints, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Seedance Plan 写作分镜 {position} 必须是对象")
        scene_id = str(raw.get("scene_id") or "").strip()
        if scene_id not in expected_by_id:
            raise ValueError(f"Seedance Plan 写作返回了未知 scene_id：{scene_id or '空'}")
        if scene_id in authored_by_id:
            raise ValueError(f"Seedance Plan 写作重复返回 scene_id：{scene_id}")
        expected_index = expected_by_id[scene_id].get("scene_index")
        if raw.get("scene_index") != expected_index:
            raise ValueError(f"{scene_id} 的 scene_index 与当前 Plan 不一致")
        authored_by_id[scene_id] = raw

    merged: list[dict[str, Any]] = []
    for original in scene_blueprints:
        scene_id = str(original.get("scene_id") or "").strip()
        authored = authored_by_id.get(scene_id)
        if authored is None:
            raise ValueError(f"Seedance Plan 写作缺少 {scene_id}")
        item = copy.deepcopy(original)
        for field in _NARRATIVE_FIELDS:
            value = str(authored.get(field) or "").strip()
            if not value:
                raise ValueError(f"{scene_id} 的 {field} 不能为空")
            item[field] = value
        _validate_raw_authored_text(scene_id, item["shot_description"])
        merged.append(item)

    normalized = normalize_scene_blueprints(merged, total_duration_sec=total_duration_sec)
    for original, result in zip(scene_blueprints, normalized, strict=True):
        for field in _IMMUTABLE_FIELDS:
            if result.get(field) != original.get(field):
                raise ValueError(f"{result['scene_id']} 的不可变字段 {field} 被修改")

    validate_asset_manifest_consistency(asset_manifest, normalized)
    for blueprint in normalized:
        _validate_authored_shot(blueprint, asset_manifest)
    validate_shot_description_quality(normalized)
    return normalized


def _scene_authoring_contracts(
    scene_blueprints: Sequence[Mapping[str, Any]],
    asset_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    manifest_by_name = _manifest_by_collection_and_name(asset_manifest)
    contracts: list[dict[str, Any]] = []
    for blueprint in scene_blueprints:
        allowed_assets: list[dict[str, str]] = []
        requirements = blueprint.get("asset_requirements")
        if not isinstance(requirements, Mapping):
            requirements = {}
        for collection in _ASSET_COLLECTION_PREFIXES:
            raw_names = requirements.get(collection, [])
            if not isinstance(raw_names, list):
                continue
            for raw_name in raw_names:
                name = str(raw_name or "").strip()
                asset = manifest_by_name[collection].get(_name_key(name))
                if asset is None:
                    raise ValueError(f"分镜资产 {collection}.{name} 不在全局资产清单中")
                allowed_assets.append(
                    {
                        "asset_id": str(asset.get("asset_id") or "").strip(),
                        "asset_type": collection,
                        "name": name,
                        "description": str(asset.get("description") or "").strip(),
                        "reference_phrase": _reference_phrase(
                            collection,
                            str(asset.get("asset_id") or "").strip(),
                            name,
                        ),
                    }
                )
        contracts.append(
            {
                "scene_id": blueprint.get("scene_id"),
                "scene_index": blueprint.get("scene_index"),
                "structure_role": blueprint.get("structure_role"),
                "start_sec": blueprint.get("start_sec"),
                "end_sec": blueprint.get("end_sec"),
                "duration_sec": blueprint.get("duration_sec"),
                "asset_requirements": copy.deepcopy(requirements),
                "allowed_assets": allowed_assets,
            }
        )
    return contracts


def _validate_authored_shot(
    blueprint: Mapping[str, Any],
    asset_manifest: Mapping[str, Any],
) -> None:
    scene_id = str(blueprint.get("scene_id") or "分镜")
    description = str(blueprint.get("shot_description") or "")
    _validate_raw_authored_text(scene_id, description)

    allowed_ids = {
        item["asset_id"]
        for item in _scene_authoring_contracts([blueprint], asset_manifest)[0]["allowed_assets"]
    }
    referenced_ids = {match.group("asset_id") for match in _ASSET_REFERENCE_PATTERN.finditer(description)}
    invalid_syntax = sorted(
        asset_id
        for asset_id in referenced_ids
        if not asset_id.startswith(tuple(_ASSET_COLLECTION_PREFIXES.values()))
    )
    if invalid_syntax:
        raise ValueError(f"{scene_id} 使用了非法 @asset_id：{invalid_syntax}")
    undeclared = sorted(referenced_ids - allowed_ids)
    if undeclared:
        raise ValueError(f"{scene_id} 使用了本分镜未声明的 @asset_id：{undeclared}")
    missing = sorted(allowed_ids - referenced_ids)
    if missing:
        raise ValueError(f"{scene_id} 未引用本分镜已声明的资产：{missing}")
    if len(referenced_ids) > MAX_IMAGE_REFERENCES:
        raise ValueError(f"{scene_id} 每个分镜最多 {MAX_IMAGE_REFERENCES} 张图片参考")

    for asset_id in sorted(referenced_ids):
        if not _asset_reference_has_usage(description, asset_id):
            raise ValueError(f"{scene_id} 的 @{asset_id} 引用缺少明确用途说明")


def _validate_raw_authored_text(scene_id: str, description: str) -> None:
    if "\n" in description or "\r" in description:
        raise ValueError(f"{scene_id} 的镜头描述必须是一整段中文")
    if _MILLISECOND_PATTERN.search(description):
        raise ValueError(f"{scene_id} 的镜头描述不能使用毫秒时间码")
    if _DECIMAL_TIMECODE_PATTERN.search(description):
        raise ValueError(f"{scene_id} 的镜头描述不能使用小数时间码")


def _asset_reference_has_usage(description: str, asset_id: str) -> bool:
    token = f"@{asset_id}"
    return any(
        token in clause and any(marker in clause for marker in _ASSET_USAGE_MARKERS)
        for clause in re.split(r"[，。；;\n]", description)
    )


def _manifest_by_collection_and_name(
    asset_manifest: Mapping[str, Any],
) -> dict[str, dict[str, Mapping[str, Any]]]:
    result: dict[str, dict[str, Mapping[str, Any]]] = {
        collection: {} for collection in _ASSET_COLLECTION_PREFIXES
    }
    for collection, prefix in _ASSET_COLLECTION_PREFIXES.items():
        raw_items = asset_manifest.get(collection, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"asset_manifest.{collection} 必须是数组")
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                raise ValueError(f"asset_manifest.{collection} 必须只包含对象")
            name = str(raw.get("name") or "").strip()
            asset_id = str(raw.get("asset_id") or "").strip().lstrip("@")
            if not name or not asset_id:
                raise ValueError(f"asset_manifest.{collection} 的 name/asset_id 不能为空")
            if not asset_id.startswith(prefix):
                raise ValueError(f"资产 {name} 的 asset_id 类型与 {collection} 不一致")
            result[collection][_name_key(name)] = raw
    return result


def _reference_phrase(collection: str, asset_id: str, name: str) -> str:
    if collection == "characters":
        return f"以 @{asset_id} 固定人物“{name}”的身份与外观"
    if collection == "scenes":
        return f"以 @{asset_id} 固定场景“{name}”的环境空间"
    return f"以 @{asset_id} 固定道具“{name}”的商品外观"


def _name_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
