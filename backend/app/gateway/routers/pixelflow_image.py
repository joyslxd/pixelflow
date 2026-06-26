"""PixelFlow v2 图片生成准备 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from pixelflow.generate.image_prepare import ImageMethod, prepare_image_generation
from pixelflow.skills import get_image_skill

router = APIRouter(prefix="/agent/flows/image", tags=["pixelflow-flows"])


class ImagePrepareRequest(BaseModel):
    form_values: dict[str, Any] = Field(default_factory=dict)
    plan_markdown: str = ""
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    revision_feedback: str | None = None


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
    raw: dict[str, Any] = Field(default_factory=dict)


@router.post("/prepare", response_model=ImagePrepareResponse)
async def prepare_image(body: ImagePrepareRequest) -> ImagePrepareResponse:
    result = prepare_image_generation(
        body.form_values,
        body.plan_markdown,
        body.selected_direction,
        body.materials,
        body.revision_feedback,
    )
    return ImagePrepareResponse(**result.to_dict())


@router.post("/generate", response_model=ImageGenerateResponse)
async def generate_image(body: ImageGenerateRequest) -> ImageGenerateResponse:
    skill = get_image_skill()
    if body.method == "image_edit":
        model = _optional_str(body.params.get("model"))
        result = await skill.image_edit(
            image_url=_first_image_url(body.params),
            prompt=body.prompt or str(body.params.get("prompt") or ""),
            model=model,
            ratio=_ratio_from_params(body.params),
            size=_image_size_from_params(body.params, model),
            max_images=int(body.params.get("max_images") or body.params.get("num_images") or body.params.get("num") or 1),
        )
    elif body.method == "multi_reference_image_generation":
        model = _optional_str(body.params.get("model"))
        result = await skill.reference_image(
            reference_images=_image_urls_from_params(body.params, primary_keys=("reference_image_urls", "referenceImageUrls")),
            prompt=body.prompt or str(body.params.get("prompt") or ""),
            ratio=_ratio_from_params(body.params),
            size=_image_size_from_params(body.params, model),
            model=model,
            max_images=int(body.params.get("max_images") or body.params.get("num_images") or 1),
        )
    elif body.method == "multi_image_fusion":
        model = _optional_str(body.params.get("model"))
        result = await skill.multi_image_fusion(
            image_urls=_image_urls_from_params(body.params, primary_keys=("image_urls", "imageUrls")),
            prompt=body.prompt or str(body.params.get("prompt") or ""),
            ratio=_ratio_from_params(body.params),
            size=_image_size_from_params(body.params, model),
            model=model,
            num_images=int(body.params.get("num_images") or body.params.get("num") or 1),
        )
    else:
        model = _optional_str(body.params.get("model"))
        result = await skill.text_to_image(
            prompt=body.prompt or str(body.params.get("prompt") or ""),
            ratio=str(body.params.get("ratio") or "1:1"),
            size=_image_size_from_params(body.params, model),
            model=model,
            num_images=int(body.params.get("num_images") or body.params.get("num") or 1),
        )
    return ImageGenerateResponse(
        ok=result.ok,
        method=body.method,
        endpoint=_endpoint_for(body.method, result.raw),
        task_id=result.task_id,
        images=result.images,
        error=result.error,
        message="图片生成完成。" if result.ok else (result.error or "图片生成失败。"),
        raw=result.raw,
    )


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
