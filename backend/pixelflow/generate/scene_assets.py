"""视频场景参考图生成：props / scenes 支持用户上传图参考生图，其余资产仍走文生图。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlparse

from pixelflow.creative.scene_blueprint import asset_requirement_entity_quality_issues
from pixelflow.generate.image_prepare import filter_image_materials

SceneAssetProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

TEXT_TO_IMAGE_ENDPOINT = "/api/picture/text_to_image"
REFERENCE_IMAGE_ENDPOINT = "/api/picture/multi_reference_image_generation"
MIXED_IMAGE_ENDPOINT = "/api/picture/mixed"
MAX_REFERENCE_IMAGES = 9
# 全局素材手动编辑仍保留原默认值；场景包资产生成不会使用这个常量。
REFERENCE_IMAGE_QUALITY = "2K"
PROP_REFERENCE_PROMPT_SUFFIX = "以参考图中的产品/商品外观为准，保持包装、颜色、材质和比例一致，干净背景，无文字水印。"
SCENE_REFERENCE_PROMPT_SUFFIX = "如果图片是背景墙、天花板、地板等场景元素，以参考图中的场景风格和环境氛围为准，保持空间布局、色调和光影一致，干净画面，无文字水印。如果是产品图，生成的场景图必须包含该产品。"
PROP_MULTI_SCENE_GRID_PROMPT_SUFFIX = "根据道具使用场景，生成一张道具在多场景应用的 4 宫格图。"
# 兼容旧命名
MAX_PROP_REFERENCE_IMAGES = MAX_REFERENCE_IMAGES


class ImageSkill(Protocol):
    async def text_to_image(
        self,
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        num_images: int = 1,
    ) -> Any: ...

    async def reference_image(
        self,
        reference_images: list[str],
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        max_images: int = 1,
    ) -> Any: ...


def collect_uploaded_reference_image_urls(materials: list[dict[str, Any]] | None) -> list[str]:
    """从用户上传 materials 收集参考图 URL，排除 scene_global_asset 引用。"""
    urls: list[str] = []
    for material in filter_image_materials(materials or []):
        if _is_generated_scene_asset(material):
            continue
        url = str(material.get("url") or "").strip()
        if _is_reference_image_url(url) and url not in urls:
            urls.append(url)
    return urls[:MAX_REFERENCE_IMAGES]


def global_asset_edit_ratio(asset_group: str) -> str:
    if asset_group == "scenes":
        return "9:16"
    return "1:1"


def enhance_global_asset_edit_prompt(prompt: str, asset_group: str) -> str:
    if asset_group == "scenes":
        return enhance_scene_reference_prompt(prompt)
    if asset_group == "props":
        return enhance_prop_reference_prompt(prompt)
    return prompt.strip()


def collect_prop_reference_image_urls(
    materials: list[dict[str, Any]] | None,
    scene_packages: list[dict[str, Any]] | None,
) -> list[str]:
    """收集 props / scenes 参考生图可用的 http(s) 图片 URL。"""
    urls: list[str] = []
    for material in filter_image_materials(materials or []):
        if _is_generated_scene_asset(material):
            continue
        url = str(material.get("url") or "").strip()
        if _is_reference_image_url(url):
            urls.append(url)
    for scene in scene_packages or []:
        if not isinstance(scene, dict):
            continue
        for url in scene.get("image_urls") or []:
            normalized = str(url).strip()
            if _is_reference_image_url(normalized) and normalized not in urls:
                urls.append(normalized)
    return urls[:MAX_REFERENCE_IMAGES]


def enhance_scene_reference_prompt(prompt: str) -> str:
    cleaned = prompt.strip()
    if not cleaned:
        return SCENE_REFERENCE_PROMPT_SUFFIX
    if SCENE_REFERENCE_PROMPT_SUFFIX in cleaned:
        return cleaned
    return f"{cleaned}。{SCENE_REFERENCE_PROMPT_SUFFIX}"


def _enhance_reference_prompt(prompt: str, asset_type: str) -> str:
    if asset_type == "scene_image":
        return enhance_scene_reference_prompt(prompt)
    return enhance_prop_reference_prompt(prompt)


def _uses_reference_image(asset_type: str, reference_urls: list[str]) -> bool:
    return asset_type in {"prop_image", "scene_image"} and bool(reference_urls)


def enhance_prop_reference_prompt(prompt: str) -> str:
    cleaned = prompt.strip()
    if not cleaned:
        return PROP_REFERENCE_PROMPT_SUFFIX
    if PROP_REFERENCE_PROMPT_SUFFIX in cleaned:
        return cleaned
    return f"{cleaned}。{PROP_REFERENCE_PROMPT_SUFFIX}"


def enhance_prop_multi_scene_grid_prompt(prompt: str) -> str:
    """道具生图追加四宫格多场景应用要求（单张图，不改多图契约）。"""
    cleaned = prompt.strip()
    if not cleaned:
        return PROP_MULTI_SCENE_GRID_PROMPT_SUFFIX
    if PROP_MULTI_SCENE_GRID_PROMPT_SUFFIX in cleaned:
        return cleaned
    return f"{cleaned}。{PROP_MULTI_SCENE_GRID_PROMPT_SUFFIX}"


def _asset_generation_prompt(asset: dict[str, Any], *prompt_fields: str) -> str:
    """将 Plan 的正式名称、文字说明和生图要求一起传给供应商。"""
    name = str(asset.get("name") or "").strip()
    description = str(asset.get("description") or "").strip()
    image_prompt = ""
    for field in prompt_fields:
        image_prompt = str(asset.get(field) or "").strip()
        if image_prompt:
            break
    if not description and not image_prompt:
        return ""
    parts: list[str] = []
    seen_values: set[str] = set()
    for label, value in (("资产名称", name), ("Plan文字说明", description), ("生图要求", image_prompt)):
        if value and value not in seen_values:
            parts.append(f"{label}：{value}")
            seen_values.add(value)
    if len(parts) == 1 and image_prompt:
        return image_prompt
    return "；".join(parts)


def resolve_scene_asset_endpoint(generation_modes: set[str]) -> str:
    if not generation_modes or generation_modes == {"text_to_image"}:
        return TEXT_TO_IMAGE_ENDPOINT
    if generation_modes == {"reference_image"}:
        return REFERENCE_IMAGE_ENDPOINT
    return MIXED_IMAGE_ENDPOINT


def _is_generated_scene_asset(material: dict[str, Any]) -> bool:
    return str(material.get("source") or "").strip() == "scene_global_asset"


def _is_reference_image_url(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    return not _is_probable_video_url(url)


def _is_probable_video_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    video_extensions = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".m3u8")
    image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")
    if path.endswith(video_extensions):
        return True
    if path.endswith(image_extensions):
        return False
    video_hosts = ("douyin.com", "xhslink.com", "xiaohongshu.com", "bilibili.com", "b23.tv", "kuaishou.com")
    return any(host == domain or host.endswith(f".{domain}") for domain in video_hosts) or bool(parsed.scheme and parsed.netloc)


def _extract_image_urls(result: Any) -> list[str]:
    images = getattr(result, "images", None) or []
    urls: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        url = str(image.get("url") or image.get("download_url") or "").strip()
        if url:
            urls.append(url)
    return urls


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _failure_detail_parts(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        parts: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            parts.extend(_failure_detail_parts(child, child_prefix))
        return parts
    if isinstance(value, list):
        return _failure_detail_parts("、".join(str(item) for item in value if item), prefix)
    if value in (None, "", True, False):
        return []
    text = str(value).strip()
    return [f"{prefix}：{text}" if prefix else text] if text else []


def _failure_validation_details(value: Any) -> Any:
    """提取 content-app 返回的字段级校验详情。

    HTTP 层会把业务响应放到 ``details`` 中，因此需要同时兼容
    ``data`` 以及 ``details.data`` 两种结构。
    """
    if not isinstance(value, dict):
        return None
    for key in ("data", "errors", "fieldErrors", "detail"):
        candidate = value.get(key)
        if not isinstance(candidate, (dict, list)) or not candidate:
            continue
        nested = _failure_validation_details(candidate)
        return nested or candidate
    nested_details = value.get("details")
    if isinstance(nested_details, dict):
        return _failure_validation_details(nested_details)
    return None


def _failure_reason(result: Any, default: str = "图片生成失败") -> str:
    raw = getattr(result, "raw", None)
    raw_record = raw if isinstance(raw, dict) else {}
    candidates = [
        getattr(result, "error", None),
        raw_record.get("message"),
        raw_record.get("msg"),
    ]
    parts: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and text not in parts:
            parts.append(text)
    details = _failure_validation_details(raw_record)
    for detail in _failure_detail_parts(details):
        if detail not in parts:
            parts.append(detail)
    return "；".join(parts)[:1000] or default


def _failure_attempt(result: Any, endpoint: str) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "task_id": getattr(result, "task_id", None),
        "error": _failure_reason(result),
        "raw": getattr(result, "raw", None),
    }


def _asset_context(
    target: dict[str, Any],
    *,
    asset_type: str,
    scene_packages: list[dict[str, Any]],
    scene_id: str = "",
    scene_index: Any = None,
) -> dict[str, Any]:
    asset_id = str(target.get("asset_id") or target.get("id") or "").strip()
    asset_name = str(target.get("name") or target.get("title") or target.get("description") or asset_id).strip()
    related_scenes = [scene for scene in scene_packages if asset_id and asset_id in [str(item) for item in scene.get("reference_asset_ids") or []]]
    if not related_scenes and scene_id:
        related_scenes = [scene for scene in scene_packages if str(scene.get("scene_id") or "") == scene_id]
    if not related_scenes and len(scene_packages) == 1:
        related_scenes = scene_packages[:1]
    related_scene_ids = [str(scene.get("scene_id") or "") for scene in related_scenes if scene.get("scene_id")]
    related_scene_indexes = [scene.get("scene_index") for scene in related_scenes if scene.get("scene_index") is not None]
    resolved_scene_id = scene_id or (related_scene_ids[0] if related_scene_ids else "")
    resolved_scene_index = scene_index if scene_index is not None else (related_scene_indexes[0] if related_scene_indexes else None)
    return {
        "asset_id": asset_id,
        "asset_name": asset_name or "未命名参考图",
        "asset_type": asset_type,
        "scene_id": resolved_scene_id,
        "scene_index": resolved_scene_index,
        "related_scene_ids": related_scene_ids,
        "related_scene_indexes": related_scene_indexes,
    }


def _scene_asset_target_key(value: dict[str, Any]) -> tuple[str, str] | None:
    asset_id = str(value.get("asset_id") or "").strip()
    asset_type = str(value.get("asset_type") or "").strip()
    if not asset_id or asset_type not in {"character", "scene_image", "prop_image"}:
        return None
    return asset_type, asset_id


def _validate_scene_asset_entity_names(
    global_assets: dict[str, Any],
    scene_packages: list[dict[str, Any]],
) -> None:
    """在实际生图边界再次拒绝被旧对话保存下来的创作元信息资产。"""

    collections: dict[str, list[str]] = {"characters": [], "scenes": [], "props": []}

    def collect(target: str, value: Any) -> None:
        for item in _list_of_dicts(value):
            name = str(item.get("name") or item.get("label") or item.get("asset_name") or "").strip()
            if name and name not in collections[target]:
                collections[target].append(name)

    if global_assets:
        collect("characters", global_assets.get("characters"))
        collect("scenes", global_assets.get("scenes"))
        collect("props", global_assets.get("props"))
    else:
        for scene in scene_packages:
            collect("characters", scene.get("characters"))
            collect("scenes", scene.get("scene_images"))
            collect("props", scene.get("prop_images"))

    if any(collections.values()):
        issues = asset_requirement_entity_quality_issues(
            [{"scene_index": 1, "asset_requirements": collections}]
        )
        if issues:
            raise ValueError("；".join(issues))


async def generate_scene_assets(
    *,
    image_skill: ImageSkill,
    global_assets: dict[str, Any],
    scene_packages: list[dict[str, Any]],
    materials: list[dict[str, Any]] | None = None,
    image_ratio: str = "1:1",
    image_size: str = "4K",
    model: str | None = None,
    quota_checker: Any,
    target_assets: list[dict[str, Any]] | None = None,
    on_progress: SceneAssetProgressCallback | None = None,
) -> dict[str, Any]:
    """生成场景参考图；props / scenes 在用户有上传图片时走参考生图。

    on_progress 在每张参考图尝试结束后触发，供异步 Job 回写轮询进度。
    """
    from pixelflow.skills.borgrise.run_generation import (
        default_image_quality_for_model,
        normalize_image_quality,
    )

    resolved_model = str(model or "gpt-image-2").strip() or "gpt-image-2"
    quality = normalize_image_quality(image_size)
    if resolved_model.casefold() == "gpt-image-2" and quality.casefold() == "1080p":
        image_size = default_image_quality_for_model("gpt-image-2")
    else:
        image_size = quality or default_image_quality_for_model(resolved_model)
    model = resolved_model
    enriched = [dict(scene) for scene in scene_packages if isinstance(scene, dict)]
    assets = dict(global_assets) if global_assets else {}
    _validate_scene_asset_entity_names(assets, enriched)
    failed_assets: list[dict[str, Any]] = []
    generation_modes: set[str] = set()
    reference_urls = collect_prop_reference_image_urls(materials, enriched)

    async def generate_asset(
        prompt: str,
        ratio: str,
        context: dict[str, Any],
    ) -> tuple[list[str], bool, str]:
        asset_type = str(context.get("asset_type") or "")
        use_reference = _uses_reference_image(asset_type, reference_urls)
        generation_mode = "reference_image" if use_reference else "text_to_image"
        attempts: list[dict[str, Any]] = []
        generation_modes.add(generation_mode)
        if use_reference:
            result = await image_skill.reference_image(
                reference_images=reference_urls,
                prompt=_enhance_reference_prompt(prompt, asset_type),
                ratio=ratio,
                size=image_size,
                model=model,
                max_images=1,
            )
            endpoint = REFERENCE_IMAGE_ENDPOINT
            if not result.ok:
                attempts.append(_failure_attempt(result, endpoint))
                quota_insufficient = quota_checker(getattr(result, "raw", None)) or quota_checker(getattr(result, "error", None))
                if not quota_insufficient:
                    generation_modes.add("text_to_image")
                    fallback = await image_skill.text_to_image(
                        prompt=prompt,
                        ratio=ratio,
                        size=image_size,
                        model=model,
                        num_images=1,
                    )
                    endpoint = TEXT_TO_IMAGE_ENDPOINT
                    generation_mode = "text_to_image_fallback"
                    if fallback.ok:
                        result = fallback
                    else:
                        attempts.append(_failure_attempt(fallback, endpoint))
                        result = fallback
        else:
            result = await image_skill.text_to_image(
                prompt=prompt,
                ratio=ratio,
                size=image_size,
                model=model,
                num_images=1,
            )
            endpoint = TEXT_TO_IMAGE_ENDPOINT
            if not result.ok:
                attempts.append(_failure_attempt(result, endpoint))
        if not result.ok:
            quota_insufficient = quota_checker(getattr(result, "raw", None)) or quota_checker(getattr(result, "error", None))
            failed_assets.append(
                {
                    **context,
                    "generation_mode": generation_mode,
                    "endpoint": endpoint,
                    "model": model,
                    "ratio": ratio,
                    "size": image_size,
                    "reference_urls": (reference_urls if use_reference else []),
                    "error": _failure_reason(result),
                    "attempts": attempts or [_failure_attempt(result, endpoint)],
                    "quota_insufficient": quota_insufficient,
                    "raw": getattr(result, "raw", None),
                }
            )
            return [], quota_insufficient, endpoint
        urls = _extract_image_urls(result)
        if not urls:
            no_url_error = "图片生成结果没有URL"
            failed_assets.append(
                {
                    **context,
                    "generation_mode": generation_mode,
                    "endpoint": endpoint,
                    "model": model,
                    "ratio": ratio,
                    "size": image_size,
                    "reference_urls": (reference_urls if use_reference else []),
                    "error": no_url_error,
                    "attempts": [
                        {
                            "endpoint": endpoint,
                            "task_id": getattr(result, "task_id", None),
                            "error": no_url_error,
                            "raw": getattr(result, "raw", None),
                        }
                    ],
                    "quota_insufficient": False,
                    "raw": getattr(result, "raw", None),
                }
            )
        # Plan 资产清单与图片严格一对一；即使供应商意外返回多张，也只绑定第一张。
        return urls[:1], False, endpoint

    asset_jobs: list[tuple[dict[str, Any], str, str, str, dict[str, Any]]] = []

    def queue_asset(
        target: dict[str, Any],
        field_name: str,
        prompt: str,
        ratio: str,
        context: dict[str, Any],
    ) -> None:
        if prompt:
            asset_jobs.append((target, field_name, prompt, ratio, context))
            return
        # 已有可用图片的素材无需再生成；无图且无提示词时必须显式报错。
        if target.get(field_name):
            return
        failed_assets.append(
            {
                **context,
                "generation_mode": "not_started",
                "endpoint": "",
                "model": model,
                "ratio": ratio,
                "size": image_size,
                "reference_urls": [],
                "error": "素材缺少图片生成提示词",
                "attempts": [],
                "quota_insufficient": False,
                "raw": None,
            }
        )

    if assets:
        for character in _list_of_dicts(assets.get("characters")):
            prompt = _asset_generation_prompt(character, "three_view_prompt", "image_prompt")
            queue_asset(character, "three_view_images", prompt, image_ratio, _asset_context(character, asset_type="character", scene_packages=enriched))
        for scene_image in _list_of_dicts(assets.get("scenes")):
            prompt = _asset_generation_prompt(scene_image, "image_prompt")
            queue_asset(scene_image, "images", prompt, image_ratio, _asset_context(scene_image, asset_type="scene_image", scene_packages=enriched))
        for prop_image in _list_of_dicts(assets.get("props")):
            prompt = enhance_prop_multi_scene_grid_prompt(_asset_generation_prompt(prop_image, "image_prompt"))
            queue_asset(prop_image, "images", prompt, image_ratio, _asset_context(prop_image, asset_type="prop_image", scene_packages=enriched))
    else:
        for scene in enriched:
            scene_id = str(scene.get("scene_id") or "")
            scene_index = scene.get("scene_index")
            for character in _list_of_dicts(scene.get("characters")):
                prompt = _asset_generation_prompt(character, "three_view_prompt", "image_prompt")
                queue_asset(
                    character,
                    "three_view_images",
                    prompt,
                    image_ratio,
                    _asset_context(character, asset_type="character", scene_packages=enriched, scene_id=scene_id, scene_index=scene_index),
                )
            for scene_image in _list_of_dicts(scene.get("scene_images")):
                prompt = _asset_generation_prompt(scene_image, "image_prompt")
                queue_asset(
                    scene_image,
                    "images",
                    prompt,
                    image_ratio,
                    _asset_context(scene_image, asset_type="scene_image", scene_packages=enriched, scene_id=scene_id, scene_index=scene_index),
                )
            for prop_image in _list_of_dicts(scene.get("prop_images")):
                prompt = enhance_prop_multi_scene_grid_prompt(_asset_generation_prompt(prop_image, "image_prompt"))
                queue_asset(
                    prop_image,
                    "images",
                    prompt,
                    image_ratio,
                    _asset_context(prop_image, asset_type="prop_image", scene_packages=enriched, scene_id=scene_id, scene_index=scene_index),
                )

    if target_assets is not None:
        requested_targets: dict[tuple[str, str], dict[str, Any]] = {}
        for target_asset in target_assets:
            if not isinstance(target_asset, dict):
                continue
            target_key = _scene_asset_target_key(target_asset)
            if target_key is not None:
                requested_targets.setdefault(target_key, target_asset)
        available_target_keys = {
            target_key
            for *_job, context in asset_jobs
            if (target_key := _scene_asset_target_key(context)) is not None
        }
        available_target_keys.update(
            target_key
            for failure in failed_assets
            if (target_key := _scene_asset_target_key(failure)) is not None
        )
        failed_assets = [
            failure
            for failure in failed_assets
            if (target_key := _scene_asset_target_key(failure)) is not None and target_key in requested_targets
        ]
        for target_key, target_asset in requested_targets.items():
            if target_key in available_target_keys:
                continue
            failed_assets.append(
                {
                    "asset_id": target_asset.get("asset_id"),
                    "asset_type": target_asset.get("asset_type"),
                    "error": "指定的失败素材不存在或缺少生成提示词",
                    "error_code": "scene_asset_retry_target_not_found",
                    "quota_insufficient": False,
                }
            )
        asset_jobs = [
            job
            for job in asset_jobs
            if (target_key := _scene_asset_target_key(job[4])) is not None and target_key in requested_targets
        ]

    total = len(asset_jobs)

    async def emit_progress(
        *,
        completed: int,
        context: dict[str, Any],
        ok: bool,
        quota_insufficient: bool = False,
    ) -> None:
        if on_progress is None:
            return
        payload = {
            "completed": completed,
            "total": total,
            "asset_id": str(context.get("asset_id") or ""),
            "asset_name": str(context.get("asset_name") or "未命名参考图"),
            "asset_type": str(context.get("asset_type") or ""),
            "ok": ok,
            "quota_insufficient": quota_insufficient,
            "global_assets": assets,
            "scene_packages": enriched,
            "failed_assets": list(failed_assets),
        }
        maybe_awaitable = on_progress(payload)
        if maybe_awaitable is not None:
            await maybe_awaitable

    for job_index, (target, field_name, prompt, ratio, context) in enumerate(asset_jobs):
        urls, quota_insufficient, _endpoint = await generate_asset(prompt, ratio, context)
        if urls:
            target[field_name] = urls
        await emit_progress(
            completed=job_index + 1,
            context=context,
            ok=bool(urls),
            quota_insufficient=quota_insufficient,
        )
        if quota_insufficient:
            failed_target_keys = {
                target_key
                for failure in failed_assets
                if (target_key := _scene_asset_target_key(failure)) is not None
            }
            for *_pending_job, pending_context in asset_jobs[job_index + 1 :]:
                pending_key = _scene_asset_target_key(pending_context)
                if pending_key is not None and pending_key in failed_target_keys:
                    continue
                if pending_key is not None:
                    failed_target_keys.add(pending_key)
                failed_assets.append(
                    {
                        **pending_context,
                        "error": "本轮因额度不足尚未生成",
                        "error_code": "scene_asset_retry_pending",
                        "quota_insufficient": True,
                        "retry_pending": True,
                    }
                )
            return {
                "ok": False,
                "endpoint": resolve_scene_asset_endpoint(generation_modes),
                "global_assets": assets,
                "scene_packages": enriched,
                "failed_assets": failed_assets,
                "quota_insufficient": True,
            }

    return {
        "ok": not failed_assets,
        "endpoint": resolve_scene_asset_endpoint(generation_modes),
        "global_assets": assets,
        "scene_packages": enriched,
        "failed_assets": failed_assets,
        "quota_insufficient": False,
    }
