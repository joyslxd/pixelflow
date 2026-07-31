"""全局素材变更后的分镜语义修订服务。"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pixelflow.skills.base import ImageAnalysisSkill

SCENE_ASSET_REVISION_LLM_MODEL_NAME = "deepseek-v4-pro"
AssetOperation = Literal["replace", "delete"]
PatchProvider = Callable[..., Awaitable[dict[str, Any]]]
ModelFactory = Callable[..., Any]

_ASSET_GROUPS = {"characters", "scenes", "props"}
_TIME_RANGE_PATTERN = re.compile(
    r"(?:\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?:-|–|—|~|至)\s*"
    r"(?:\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:秒|s|秒钟)?",
    flags=re.IGNORECASE,
)


async def revise_scene_package_asset(
    *,
    operation: AssetOperation,
    asset_id: str,
    asset_group: str,
    asset_name: str,
    source_image_url: str,
    global_assets: dict[str, Any],
    scene_packages: list[dict[str, Any]],
    new_image_url: str | None = None,
    generation_reference_url: str | None = None,
    replacement_metadata: dict[str, Any] | None = None,
    image_analysis_skill: ImageAnalysisSkill | None = None,
    patch_provider: PatchProvider | None = None,
    model_name: str = SCENE_ASSET_REVISION_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """分析新素材，并以可验证的精确补丁修订受影响分镜。"""

    normalized_operation = _validate_request(
        operation=operation,
        asset_id=asset_id,
        asset_group=asset_group,
        source_image_url=source_image_url,
        new_image_url=new_image_url,
    )
    next_assets = copy.deepcopy(global_assets)
    next_scenes = copy.deepcopy(scene_packages)
    target_asset = _find_asset(next_assets, asset_group, asset_id)
    if target_asset is None:
        raise ValueError(f"全局素材不存在：{asset_id}")

    canonical_name = str(
        target_asset.get("name")
        or target_asset.get("label")
        or asset_name
        or asset_id
    ).strip()
    affected_scenes = [
        scene
        for scene in next_scenes
        if _scene_references_asset(scene, asset_id, canonical_name)
    ]

    image_analysis_markdown = ""
    if normalized_operation == "replace":
        if image_analysis_skill is None:
            raise ValueError("替换素材时缺少图片分析 Skill")
        analysis = await image_analysis_skill.analyze_image(str(new_image_url))
        if not analysis.ok or not analysis.analysis_markdown.strip():
            raise ValueError(analysis.error or "图片分析未返回有效内容")
        image_analysis_markdown = analysis.analysis_markdown.strip()

    provider = patch_provider or _default_patch_provider
    if affected_scenes:
        validation_feedback = ""
        for attempt in range(2):
            patch_payload = await provider(
                operation=normalized_operation,
                asset_id=asset_id,
                asset_name=canonical_name,
                old_asset=copy.deepcopy(target_asset),
                image_analysis_markdown=image_analysis_markdown,
                scenes=copy.deepcopy(affected_scenes),
                global_assets=copy.deepcopy(next_assets),
                model_name=model_name,
                model_factory=model_factory,
                validation_feedback=validation_feedback,
            )
            candidate_scenes = copy.deepcopy(next_scenes)
            try:
                _apply_validated_patches(
                    operation=normalized_operation,
                    asset_id=asset_id,
                    asset_name=canonical_name,
                    global_assets=next_assets,
                    scene_packages=candidate_scenes,
                    affected_scene_ids={str(scene.get("scene_id") or "") for scene in affected_scenes},
                    patch_payload=patch_payload,
                )
            except ValueError as exc:
                if attempt >= 1:
                    raise
                validation_feedback = str(exc)
                continue
            next_scenes = candidate_scenes
            break

    if normalized_operation == "replace":
        _replace_asset_image(
            target_asset,
            asset_group=asset_group,
            new_image_url=str(new_image_url),
            generation_reference_url=generation_reference_url,
            image_analysis_markdown=image_analysis_markdown,
            replacement_metadata=replacement_metadata or {},
        )
        _sync_replacement_references(
            next_scenes,
            asset_id=asset_id,
            source_image_url=source_image_url,
            new_image_url=str(new_image_url),
            generation_reference_url=generation_reference_url,
            replacement_metadata=replacement_metadata or {},
        )
        message = "新素材分析完成，相关分镜内容已完成定向更新。"
    else:
        _clear_asset_image(target_asset)
        _remove_asset_references(
            next_scenes,
            asset_id=asset_id,
            source_image_url=source_image_url,
        )
        message = "素材已删除，相关分镜引用和描述已完成清理。"

    return {
        "ok": True,
        "operation": normalized_operation,
        "asset_id": asset_id,
        "asset_group": asset_group,
        "global_assets": next_assets,
        "scene_packages": next_scenes,
        "affected_scene_ids": [
            str(scene.get("scene_id") or "")
            for scene in affected_scenes
            if str(scene.get("scene_id") or "")
        ],
        "image_analysis_markdown": image_analysis_markdown,
        "message": message,
    }


def _validate_request(
    *,
    operation: str,
    asset_id: str,
    asset_group: str,
    source_image_url: str,
    new_image_url: str | None,
) -> AssetOperation:
    normalized_operation = operation.strip().lower()
    if normalized_operation not in {"replace", "delete"}:
        raise ValueError("素材操作只支持 replace 或 delete")
    if not asset_id.strip():
        raise ValueError("asset_id 不能为空")
    if asset_group not in _ASSET_GROUPS:
        raise ValueError("asset_group 只支持 characters、scenes 或 props")
    if not _is_public_http_url(source_image_url):
        raise ValueError("原素材图片必须是公开 HTTP(S) 地址")
    if normalized_operation == "replace" and not _is_public_http_url(new_image_url or ""):
        raise ValueError("新素材图片必须是公开 HTTP(S) 地址")
    return normalized_operation  # type: ignore[return-value]


def _is_public_http_url(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith("https://") or normalized.startswith("http://")


def _find_asset(global_assets: dict[str, Any], asset_group: str, asset_id: str) -> dict[str, Any] | None:
    records = global_assets.get(asset_group)
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("asset_id") or record.get("id") or "") == asset_id:
            return record
    return None


def _shot_text(scene: dict[str, Any]) -> str:
    shot = scene.get("shot_description")
    if not isinstance(shot, dict):
        return str(shot or "")
    return str(
        shot.get("text")
        or shot.get("description_text")
        or shot.get("shotText")
        or shot.get("description")
        or ""
    )


def _scene_references_asset(scene: dict[str, Any], asset_id: str, asset_name: str) -> bool:
    references = scene.get("reference_asset_ids")
    if isinstance(references, list) and asset_id in {str(item) for item in references}:
        return True
    shot = scene.get("shot_description")
    if isinstance(shot, dict):
        mentions = shot.get("mentions")
        if isinstance(mentions, list):
            for mention in mentions:
                if not isinstance(mention, dict):
                    continue
                mention_id = str(mention.get("asset_id") or mention.get("assetId") or mention.get("id") or "")
                if mention_id == asset_id:
                    return True
    text = _shot_text(scene)
    return f"@{asset_id}" in text or bool(asset_name and f"@{asset_name}" in text)


async def _default_patch_provider(
    *,
    operation: AssetOperation,
    asset_id: str,
    asset_name: str,
    old_asset: dict[str, Any],
    image_analysis_markdown: str,
    scenes: list[dict[str, Any]],
    global_assets: dict[str, Any],
    model_name: str,
    model_factory: ModelFactory | None,
    validation_feedback: str = "",
) -> dict[str, Any]:
    prompt = _patch_prompt(
        operation=operation,
        asset_id=asset_id,
        asset_name=asset_name,
        old_asset=old_asset,
        image_analysis_markdown=image_analysis_markdown,
        scenes=scenes,
        global_assets=global_assets,
        validation_feedback=validation_feedback,
    )
    return await asyncio.to_thread(
        _invoke_json_model,
        prompt,
        model_name,
        model_factory or _default_model_factory,
    )


def _default_model_factory(model_name: str, *, attach_tracing: bool = False) -> Any:
    from deerflow.models.factory import create_chat_model

    return create_chat_model(model_name, attach_tracing=attach_tracing)


def _invoke_json_model(prompt: str, model_name: str, model_factory: ModelFactory) -> dict[str, Any]:
    try:
        model = model_factory(model_name, attach_tracing=False)
    except TypeError:
        model = model_factory(model_name)
    response = model.invoke(prompt)
    payload = _parse_json_payload(getattr(response, "content", response))
    if not isinstance(payload, dict):
        raise ValueError("分镜素材修订 LLM 未返回 JSON 对象")
    return payload


def _parse_json_payload(content: Any) -> Any:
    text = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not starts:
            raise
        payload, _end = json.JSONDecoder().raw_decode(text[min(starts) :])
        return payload


def _patch_prompt(
    *,
    operation: AssetOperation,
    asset_id: str,
    asset_name: str,
    old_asset: dict[str, Any],
    image_analysis_markdown: str,
    scenes: list[dict[str, Any]],
    global_assets: dict[str, Any],
    validation_feedback: str = "",
) -> str:
    action = (
        "把目标素材旧图片对应的外貌、外观、特征和特点，改成新图片分析结论。目标素材的 @引用文字必须保留。"
        if operation == "replace"
        else "删除目标素材的 @引用，并删除或最小改写只有依赖该素材才成立的外貌、外观、特征、动作和收束描述。"
    )
    correction = (
        "\n上一次补丁未通过安全校验，原因如下。必须针对原因缩小替换范围，不能重复同一错误：\n"
        f"{validation_feedback}\n"
        if validation_feedback
        else ""
    )
    return f"""你是 PixelFlow 的分镜素材定向修订模块。你只能对目标素材相关文字给出“精确子串替换补丁”，不能重写整段分镜。

操作：{operation}
目标素材 ID：{asset_id}
目标素材名称：{asset_name}
目标素材旧数据：
{json.dumps(old_asset, ensure_ascii=False, indent=2)}

新图片分析 Markdown（删除操作时为空）：
{image_analysis_markdown}

受影响分镜：
{json.dumps(scenes, ensure_ascii=False, indent=2)}

全部全局素材仅用于识别哪些内容受保护：
{json.dumps(global_assets, ensure_ascii=False, indent=2)}
{correction}

必须执行：
1. {action}
2. 只允许改 shot_description.text，其他字段一律不改。
3. 每个 old_text 必须是原镜头描述中完整、连续、且只出现一次的精确子串。
4. new_text 只处理目标素材；禁止改变时间范围、地点、故事结构、逻辑、景别、运镜、光影、声音、转场、旁白、故事线。
5. 禁止删除、增加、改名任何其他角色、场景、道具的 @引用或描述。
6. 不需要修改的分镜不要返回；没有需要修改的文字时返回空 scenes。

只返回 JSON：
{{
  "scenes": [
    {{
      "scene_id": "scene-1",
      "replacements": [
        {{"old_text": "原文中的精确连续子串", "new_text": "只替换目标素材后的文字"}}
      ]
    }}
  ]
}}
"""


def _apply_validated_patches(
    *,
    operation: AssetOperation,
    asset_id: str,
    asset_name: str,
    global_assets: dict[str, Any],
    scene_packages: list[dict[str, Any]],
    affected_scene_ids: set[str],
    patch_payload: dict[str, Any],
) -> None:
    raw_scenes = patch_payload.get("scenes")
    if not isinstance(raw_scenes, list):
        raise ValueError("分镜素材修订结果缺少 scenes 数组")
    patches_by_scene: dict[str, list[dict[str, str]]] = {}
    for raw_scene in raw_scenes:
        if not isinstance(raw_scene, dict):
            raise ValueError("分镜素材修订结果包含非法场景")
        scene_id = str(raw_scene.get("scene_id") or "")
        if scene_id not in affected_scene_ids or scene_id in patches_by_scene:
            raise ValueError("分镜素材修订结果包含越权或重复场景")
        replacements = raw_scene.get("replacements")
        if not isinstance(replacements, list):
            raise ValueError("分镜素材修订结果缺少 replacements 数组")
        normalized: list[dict[str, str]] = []
        for replacement in replacements:
            if not isinstance(replacement, dict):
                raise ValueError("分镜素材修订补丁格式不合法")
            old_text = str(replacement.get("old_text") or "")
            new_text = str(replacement.get("new_text") or "")
            if not old_text or old_text == new_text:
                raise ValueError("分镜素材修订补丁没有有效变化")
            normalized.append({"old_text": old_text, "new_text": new_text})
        patches_by_scene[scene_id] = normalized

    protected_aliases = _protected_asset_aliases(global_assets, asset_id)
    target_aliases = {f"@{asset_id}"}
    if asset_name:
        target_aliases.add(f"@{asset_name}")
    for scene in scene_packages:
        scene_id = str(scene.get("scene_id") or "")
        if scene_id not in affected_scene_ids:
            continue
        original = _shot_text(scene)
        updated = original
        for patch in patches_by_scene.get(scene_id, []):
            old_text = patch["old_text"]
            if updated.count(old_text) != 1:
                raise ValueError(f"分镜 {scene_id} 的精确替换原文不存在或出现多次")
            updated = updated.replace(old_text, patch["new_text"], 1)
        if _TIME_RANGE_PATTERN.findall(original) != _TIME_RANGE_PATTERN.findall(updated):
            raise ValueError(f"分镜 {scene_id} 的时间结构不允许修改")
        for alias in protected_aliases:
            if original.count(alias) != updated.count(alias):
                raise ValueError(f"分镜 {scene_id} 不允许修改其他素材引用：{alias}")
        if operation == "replace":
            for alias in target_aliases:
                if original.count(alias) != updated.count(alias):
                    raise ValueError(f"分镜 {scene_id} 的目标素材引用必须保留")
        elif any(alias in updated for alias in target_aliases):
            raise ValueError(f"分镜 {scene_id} 删除后仍包含目标素材引用")
        _set_shot_text(scene, updated)


def _protected_asset_aliases(global_assets: dict[str, Any], target_asset_id: str) -> set[str]:
    aliases: set[str] = set()
    for group in _ASSET_GROUPS:
        records = global_assets.get(group)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            asset_id = str(record.get("asset_id") or record.get("id") or "")
            if not asset_id or asset_id == target_asset_id:
                continue
            aliases.add(f"@{asset_id}")
            name = str(record.get("name") or record.get("label") or "").strip()
            if name:
                aliases.add(f"@{name}")
    return aliases


def _set_shot_text(scene: dict[str, Any], text: str) -> None:
    shot = scene.get("shot_description")
    if not isinstance(shot, dict):
        scene["shot_description"] = {"text": text, "mentions": []}
        return
    for key in ("text", "description_text", "shotText", "description"):
        if key in shot:
            shot[key] = text
            return
    shot["text"] = text


def _replace_asset_image(
    asset: dict[str, Any],
    *,
    asset_group: str,
    new_image_url: str,
    generation_reference_url: str | None,
    image_analysis_markdown: str,
    replacement_metadata: dict[str, Any],
) -> None:
    image_key = "three_view_images" if asset_group == "characters" else "images"
    existing = asset.get(image_key)
    remaining = list(existing[1:]) if isinstance(existing, list) and len(existing) > 1 else []
    asset[image_key] = [new_image_url, *remaining]
    asset["image_url"] = new_image_url
    asset["url"] = new_image_url
    asset["image_analysis_markdown"] = image_analysis_markdown
    if generation_reference_url:
        asset["generation_reference_url"] = generation_reference_url
    else:
        asset.pop("generation_reference_url", None)
    for key, value in replacement_metadata.items():
        if value is not None:
            asset[key] = value


def _sync_replacement_references(
    scenes: list[dict[str, Any]],
    *,
    asset_id: str,
    source_image_url: str,
    new_image_url: str,
    generation_reference_url: str | None,
    replacement_metadata: dict[str, Any],
) -> None:
    for scene in scenes:
        shot = scene.get("shot_description")
        if isinstance(shot, dict) and isinstance(shot.get("mentions"), list):
            for mention in shot["mentions"]:
                if not isinstance(mention, dict):
                    continue
                mention_id = str(mention.get("asset_id") or mention.get("assetId") or mention.get("id") or "")
                if mention_id != asset_id:
                    continue
                mention["image_url"] = new_image_url
                if generation_reference_url:
                    mention["generation_reference_url"] = generation_reference_url
                else:
                    mention.pop("generation_reference_url", None)
                for key, value in replacement_metadata.items():
                    if value is not None:
                        mention[key] = value
        image_urls = scene.get("image_urls")
        if isinstance(image_urls, list):
            scene["image_urls"] = [
                new_image_url if str(url) == source_image_url else url
                for url in image_urls
            ]


def _clear_asset_image(asset: dict[str, Any]) -> None:
    for key in ("three_view_images", "images"):
        if key in asset:
            asset[key] = []
    for key in (
        "image_url",
        "url",
        "generation_reference_url",
        "third_asset_id",
        "replacement_asset_id",
        "replacement_asset_type",
        "replacement_source",
        "image_analysis_markdown",
    ):
        if key in {"image_url", "url"}:
            asset[key] = ""
        else:
            asset.pop(key, None)


def _remove_asset_references(
    scenes: list[dict[str, Any]],
    *,
    asset_id: str,
    source_image_url: str,
) -> None:
    for scene in scenes:
        references = scene.get("reference_asset_ids")
        if isinstance(references, list):
            scene["reference_asset_ids"] = [item for item in references if str(item) != asset_id]
        shot = scene.get("shot_description")
        if isinstance(shot, dict) and isinstance(shot.get("mentions"), list):
            shot["mentions"] = [
                mention
                for mention in shot["mentions"]
                if not isinstance(mention, dict)
                or str(mention.get("asset_id") or mention.get("assetId") or mention.get("id") or "") != asset_id
            ]
        image_urls = scene.get("image_urls")
        if isinstance(image_urls, list):
            scene["image_urls"] = [
                url
                for url in image_urls
                if str(url) != source_image_url
            ]
