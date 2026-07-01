"""PixelFlow v2 图片生成准备 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pixelflow.generate.image_prepare import ImageMethod, prepare_image_generation
from pixelflow.skills import get_image_skill
from pixelflow.skills.base import is_quota_insufficient, quota_resume_message

router = APIRouter(prefix="/agent/flows/image", tags=["pixelflow-flows"])


class ImagePrepareRequest(BaseModel):
    form_values: dict[str, Any] = Field(default_factory=dict)
    plan_markdown: str = ""
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    revision_feedback: str | None = None
    intake_context: dict[str, Any] = Field(default_factory=dict)


class ImagePrepareResponse(BaseModel):
    ok: bool
    method: ImageMethod
    endpoint: str
    prompt: str
    negative_prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    images: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""
    review_timeout_sec: int = 30


class ImageGenerateRequest(BaseModel):
    method: ImageMethod
    prompt: str = ""
    negative_prompt: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class ImageGenerateResponse(BaseModel):
    ok: bool
    method: ImageMethod
    endpoint: str
    task_id: str | None = None
    images: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class ImageAssetEditRequest(BaseModel):
    asset_id: str
    asset_name: str = ""
    asset_group: str
    source_image_url: str
    prompt: str
    ratio: str = "1:1"
    size: str = "4K"
    model: str | None = "gpt-image-2"


class ImageAssetEditResponse(BaseModel):
    ok: bool
    method: ImageMethod = "image_edit"
    endpoint: str = "/api/picture/image_edit"
    source_image_url: str
    edited_image: dict[str, Any] = Field(default_factory=dict)
    asset_id: str
    asset_group: str
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


@router.post("/prepare", response_model=ImagePrepareResponse)
async def prepare_image(body: ImagePrepareRequest) -> ImagePrepareResponse:
    result = prepare_image_generation(
        body.form_values,
        body.plan_markdown,
        body.selected_direction,
        body.materials,
        body.revision_feedback,
        body.intake_context,
    )
    return ImagePrepareResponse(**result.to_dict())


@router.post("/generate", response_model=ImageGenerateResponse)
async def generate_image(body: ImageGenerateRequest) -> ImageGenerateResponse:
    skill = get_image_skill()
    requested_count = _requested_image_count(body.params)
    images: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []
    task_ids: list[str] = []
    last_result = None
    for _index in range(requested_count):
        params = _single_image_params(body.method, body.params)
        result = await _generate_image_once(skill, body, params)
        last_result = result
        raw_results.append(result.raw)
        if result.task_id:
            task_ids.append(result.task_id)
        if result.images:
            images.extend(result.images)
        quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
        if not result.ok or quota_insufficient:
            message = quota_resume_message(result.error) if quota_insufficient else (result.error or "图片生成失败。")
            return ImageGenerateResponse(
                ok=False,
                method=body.method,
                endpoint=_endpoint_for(body.method, result.raw),
                task_id=task_ids[0] if task_ids else result.task_id,
                images=images,
                error=result.error,
                message=message,
                quota_insufficient=quota_insufficient,
                raw=_aggregate_raw(raw_results, requested_count),
            )
        if len(images) >= requested_count:
            break
    if last_result is None:
        raise RuntimeError("图片生成未执行")
    return ImageGenerateResponse(
        ok=True,
        method=body.method,
        endpoint=_endpoint_for(body.method, last_result.raw),
        task_id=task_ids[0] if task_ids else last_result.task_id,
        images=images[:requested_count],
        message="图片生成完成。",
        quota_insufficient=False,
        raw=_aggregate_raw(raw_results, requested_count),
    )


@router.post("/edit-asset", response_model=ImageAssetEditResponse)
async def edit_image_asset(body: ImageAssetEditRequest) -> ImageAssetEditResponse:
    source_image_url = body.source_image_url.strip()
    prompt = body.prompt.strip()
    if not source_image_url:
        raise HTTPException(status_code=400, detail="source_image_url不能为空")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt不能为空")

    result = await get_image_skill().image_edit(
        image_url=source_image_url,
        prompt=prompt,
        model=_optional_str(body.model),
        ratio=body.ratio or "1:1",
        size=body.size or "4K",
        max_images=1,
    )
    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    edited_image = result.images[0] if result.images else {}
    edited_url = _optional_str(edited_image.get("url") or edited_image.get("download_url")) if edited_image else None
    ok = result.ok and bool(edited_url)
    if ok:
        message = "素材图片编辑完成。"
    elif quota_insufficient:
        message = quota_resume_message(result.error)
    else:
        message = result.error or "图片编辑结果没有URL。"
    return ImageAssetEditResponse(
        ok=ok,
        endpoint=_endpoint_for("image_edit", result.raw),
        source_image_url=source_image_url,
        edited_image=edited_image,
        asset_id=body.asset_id,
        asset_group=body.asset_group,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )


async def _generate_image_once(skill: Any, body: ImageGenerateRequest, params: dict[str, Any]):
    if body.method == "image_edit":
        model = _optional_str(params.get("model"))
        return await skill.image_edit(
            image_url=_first_image_url(params),
            prompt=body.prompt or str(params.get("prompt") or ""),
            model=model,
            ratio=_ratio_from_params(params),
            size=_image_size_from_params(params, model),
            max_images=int(params.get("max_images") or params.get("num_images") or params.get("num") or 1),
        )
    if body.method == "multi_reference_image_generation":
        model = _optional_str(params.get("model"))
        return await skill.reference_image(
            reference_images=_image_urls_from_params(params, primary_keys=("reference_image_urls", "referenceImageUrls")),
            prompt=body.prompt or str(params.get("prompt") or ""),
            ratio=_ratio_from_params(params),
            size=_image_size_from_params(params, model),
            model=model,
            max_images=int(params.get("max_images") or params.get("num_images") or 1),
        )
    if body.method == "multi_image_fusion":
        model = _optional_str(params.get("model"))
        return await skill.multi_image_fusion(
            image_urls=_image_urls_from_params(params, primary_keys=("image_urls", "imageUrls")),
            prompt=body.prompt or str(params.get("prompt") or ""),
            ratio=_ratio_from_params(params),
            size=_image_size_from_params(params, model),
            model=model,
            num_images=int(params.get("num_images") or params.get("num") or 1),
        )
    model = _optional_str(params.get("model"))
    return await skill.text_to_image(
        prompt=body.prompt or str(params.get("prompt") or ""),
        ratio=str(params.get("ratio") or "1:1"),
        size=_image_size_from_params(params, model),
        model=model,
        num_images=int(params.get("num_images") or params.get("num") or 1),
    )


def _requested_image_count(params: dict[str, Any]) -> int:
    try:
        count = int(params.get("max_images") or params.get("num_images") or params.get("num") or 1)
    except (TypeError, ValueError):
        count = 1
    return max(1, min(10, count))


def _single_image_params(method: ImageMethod, params: dict[str, Any]) -> dict[str, Any]:
    single = dict(params)
    if method in {"image_edit", "multi_reference_image_generation"}:
        single["max_images"] = 1
    else:
        single["num_images"] = 1
        single["num"] = 1
    return single


def _aggregate_raw(raw_results: list[dict[str, Any]], requested_count: int) -> dict[str, Any]:
    if len(raw_results) == 1:
        raw = dict(raw_results[0])
        raw["requested_images"] = requested_count
        return raw
    endpoint = next((raw.get("endpoint") for raw in raw_results if isinstance(raw.get("endpoint"), str)), None)
    return {
        "endpoint": endpoint,
        "requested_images": requested_count,
        "results": raw_results,
    }


def _endpoint_for(method: ImageMethod, raw: dict[str, Any]) -> str:
    endpoint = raw.get("endpoint")
    if isinstance(endpoint, str) and endpoint:
        return endpoint
    return {
        "text_to_image": "/api/picture/text_to_image",
        "multi_reference_image_generation": "/api/picture/multi_reference_image_generation",
        "image_edit": "/api/picture/image_edit",
        "multi_image_fusion": "/api/picture/multi_image_fusion",
    }[method]


def _ratio_from_params(params: dict[str, Any]) -> str:
    ratio = params.get("ratio")
    if isinstance(ratio, str) and ratio:
        return ratio
    width = params.get("width")
    height = params.get("height")
    if width and height:
        return f"{width}:{height}"
    return "1:1"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _image_size_from_params(params: dict[str, Any], model: str | None) -> str:
    explicit = params.get("imageSize") or params.get("size")
    if explicit:
        return str(explicit)
    if model == "gpt-image-2":
        return "4K"
    return "1080p"


def _first_image_url(params: dict[str, Any]) -> str:
    explicit = _optional_str(params.get("image_url") or params.get("imageUrl") or params.get("url"))
    if explicit:
        return explicit
    urls = _image_urls_from_params(
        params,
        primary_keys=("reference_image_urls", "referenceImageUrls", "image_urls", "imageUrls", "images", "materials"),
    )
    return urls[0] if urls else ""


def _image_urls_from_params(params: dict[str, Any], *, primary_keys: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    for key in primary_keys:
        urls.extend(_urls_from_value(params.get(key)))
    return list(dict.fromkeys(urls))


def _urls_from_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        url = _optional_str(
            value.get("url")
            or value.get("image_url")
            or value.get("imageUrl")
            or value.get("download_url")
            or value.get("downloadUrl")
            or value.get("src")
        )
        return [url] if url else []
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_urls_from_value(item))
        return urls
    return []
