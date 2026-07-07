"""视频场景参考图生成：props / scenes 支持用户上传图参考生图，其余资产仍走文生图。"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

from pixelflow.generate.image_prepare import TEXT_TO_IMAGE_MODEL, filter_image_materials

TEXT_TO_IMAGE_ENDPOINT = "/api/picture/text_to_image"
REFERENCE_IMAGE_ENDPOINT = "/api/picture/multi_reference_image_generation"
MIXED_IMAGE_ENDPOINT = "/api/picture/mixed"
MAX_REFERENCE_IMAGES = 9
REFERENCE_IMAGE_MODEL = TEXT_TO_IMAGE_MODEL
REFERENCE_IMAGE_QUALITY = "2K"
PROP_REFERENCE_PROMPT_SUFFIX = "以参考图中的产品/商品外观为准，保持包装、颜色、材质和比例一致，干净背景，无文字水印。"
SCENE_REFERENCE_PROMPT_SUFFIX = "如果图片是背景墙、天花板、地板等场景元素，以参考图中的场景风格和环境氛围为准，保持空间布局、色调和光影一致，干净画面，无文字水印。如果是产品图，生成的场景图必须包含该产品。"
# 兼容旧命名
MAX_PROP_REFERENCE_IMAGES = MAX_REFERENCE_IMAGES
PROP_REFERENCE_IMAGE_MODEL = REFERENCE_IMAGE_MODEL
PROP_REFERENCE_IMAGE_QUALITY = REFERENCE_IMAGE_QUALITY


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


async def generate_scene_assets(
    *,
    image_skill: ImageSkill,
    global_assets: dict[str, Any],
    scene_packages: list[dict[str, Any]],
    materials: list[dict[str, Any]] | None = None,
    image_size: str = "1080p",
    model: str | None = None,
    quota_checker: Any,
) -> dict[str, Any]:
    """生成场景参考图；props / scenes 在用户有上传图片时走参考生图。"""
    enriched = [dict(scene) for scene in scene_packages if isinstance(scene, dict)]
    assets = dict(global_assets) if global_assets else {}
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
        generation_modes.add(generation_mode)
        if use_reference:
            result = await image_skill.reference_image(
                reference_images=reference_urls,
                prompt=_enhance_reference_prompt(prompt, asset_type),
                ratio=ratio,
                size=REFERENCE_IMAGE_QUALITY,
                model=REFERENCE_IMAGE_MODEL,
                max_images=1,
            )
            endpoint = REFERENCE_IMAGE_ENDPOINT
            if not result.ok:
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
                    if fallback.ok:
                        result = fallback
                        generation_mode = "text_to_image_fallback"
                        endpoint = TEXT_TO_IMAGE_ENDPOINT
                    else:
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
            quota_insufficient = quota_checker(getattr(result, "raw", None)) or quota_checker(getattr(result, "error", None))
            failed_assets.append(
                {
                    **context,
                    "generation_mode": generation_mode,
                    "reference_urls": (reference_urls if use_reference else []),
                    "error": result.error or "图片生成失败",
                    "quota_insufficient": quota_insufficient,
                    "raw": getattr(result, "raw", None),
                }
            )
            return [], quota_insufficient, endpoint
        urls = _extract_image_urls(result)
        if not urls:
            failed_assets.append(
                {
                    **context,
                    "generation_mode": generation_mode,
                    "reference_urls": (reference_urls if use_reference else []),
                    "error": "图片生成结果没有URL",
                    "raw": getattr(result, "raw", None),
                }
            )
        return urls, False, endpoint

    asset_jobs: list[tuple[dict[str, Any], str, str, str, dict[str, Any]]] = []
    if assets:
        for character in _list_of_dicts(assets.get("characters")):
            prompt = str(character.get("three_view_prompt") or character.get("image_prompt") or character.get("description") or "").strip()
            if prompt:
                asset_jobs.append((character, "three_view_images", prompt, "1:1", {"asset_id": character.get("asset_id"), "asset_type": "character"}))
        for scene_image in _list_of_dicts(assets.get("scenes")):
            prompt = str(scene_image.get("image_prompt") or scene_image.get("description") or "").strip()
            if prompt:
                asset_jobs.append((scene_image, "images", prompt, "9:16", {"asset_id": scene_image.get("asset_id"), "asset_type": "scene_image"}))
        for prop_image in _list_of_dicts(assets.get("props")):
            prompt = str(prop_image.get("image_prompt") or prop_image.get("description") or prop_image.get("name") or "").strip()
            if prompt:
                asset_jobs.append((prop_image, "images", prompt, "1:1", {"asset_id": prop_image.get("asset_id"), "asset_type": "prop_image"}))
    else:
        for scene in enriched:
            scene_id = str(scene.get("scene_id") or "")
            for character in _list_of_dicts(scene.get("characters")):
                prompt = str(character.get("three_view_prompt") or character.get("image_prompt") or character.get("description") or "").strip()
                if prompt:
                    asset_jobs.append((character, "three_view_images", prompt, "1:1", {"scene_id": scene_id, "asset_type": "character"}))
            for scene_image in _list_of_dicts(scene.get("scene_images")):
                prompt = str(scene_image.get("image_prompt") or scene_image.get("description") or "").strip()
                if prompt:
                    asset_jobs.append((scene_image, "images", prompt, "9:16", {"scene_id": scene_id, "asset_type": "scene_image"}))
            for prop_image in _list_of_dicts(scene.get("prop_images")):
                prompt = str(prop_image.get("image_prompt") or prop_image.get("description") or prop_image.get("name") or "").strip()
                if prompt:
                    asset_jobs.append((prop_image, "images", prompt, "1:1", {"scene_id": scene_id, "asset_type": "prop_image"}))

    for target, field_name, prompt, ratio, context in asset_jobs:
        urls, quota_insufficient, _endpoint = await generate_asset(prompt, ratio, context)
        target[field_name] = urls
        if quota_insufficient:
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
