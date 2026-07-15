"""PixelFlow v2 图片生成准备 API。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.pixelflow_memory import concise_result_summary, current_user_id, power_mem_service, record_power_mem_background, search_power_mem
from pixelflow.generate.image_prepare import (
    IMAGE_EDIT_MODEL,
    ImageMethod,
    prepare_image_generation,
    validate_final_image_contract,
)
from pixelflow.generate.scene_assets import (
    REFERENCE_IMAGE_QUALITY,
    collect_uploaded_reference_image_urls,
    enhance_global_asset_edit_prompt,
    global_asset_edit_ratio,
)
from pixelflow.memory import with_semantic_memory
from pixelflow.skills import get_image_skill
from pixelflow.skills.base import is_quota_insufficient, quota_resume_message

router = APIRouter(prefix="/agent/flows/image", tags=["pixelflow-flows"])

_IMAGE_GENERATION_JOBS: dict[str, dict[str, Any]] = {}
_MAX_IMAGE_GENERATION_JOBS = 100
_IMAGE_ASSET_EDIT_JOBS: dict[str, dict[str, Any]] = {}
_MAX_IMAGE_ASSET_EDIT_JOBS = 100
_IMAGE_ASSET_FUSION_JOBS: dict[str, dict[str, Any]] = {}
_MAX_IMAGE_ASSET_FUSION_JOBS = 100


class ImagePrepareRequest(BaseModel):
    form_values: dict[str, Any] = Field(default_factory=dict)
    plan_markdown: str = ""
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    creation_contract: dict[str, Any] | None = None
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
    review_timeout_sec: int = 60


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


class ImageGenerateJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class ImageGenerateJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: ImageGenerateResponse | None = None
    error: str | None = None
    message: str = ""


class ImageAssetEditRequest(BaseModel):
    asset_id: str
    asset_name: str = ""
    asset_group: str
    source_image_url: str
    prompt: str
    materials: list[dict[str, Any]] = Field(default_factory=list)
    reference_image_urls: list[str] = Field(default_factory=list)
    ratio: str = "1:1"
    size: str = REFERENCE_IMAGE_QUALITY
    model: str | None = IMAGE_EDIT_MODEL


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


class ImageAssetEditJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class ImageAssetEditJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: ImageAssetEditResponse | None = None
    error: str | None = None
    message: str = ""


class ImageAssetFusionRequest(BaseModel):
    asset_id: str
    asset_name: str = ""
    asset_group: str
    source_image_url: str
    prompt: str
    materials: list[dict[str, Any]] = Field(default_factory=list)
    reference_image_urls: list[str] = Field(default_factory=list)
    ratio: str = "1:1"
    size: str = REFERENCE_IMAGE_QUALITY
    model: str | None = IMAGE_EDIT_MODEL


class ImageAssetFusionResponse(BaseModel):
    ok: bool
    method: ImageMethod = "multi_image_fusion"
    endpoint: str = "/api/picture/multi_image_fusion"
    source_image_url: str
    fused_image: dict[str, Any] = Field(default_factory=dict)
    asset_id: str
    asset_group: str
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class ImageAssetFusionJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class ImageAssetFusionJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: ImageAssetFusionResponse | None = None
    error: str | None = None
    message: str = ""


@router.post("/prepare", response_model=ImagePrepareResponse)
async def prepare_image(body: ImagePrepareRequest, request: Request) -> ImagePrepareResponse:
    try:
        validate_final_image_contract(body.creation_contract)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user_id, memories = await search_power_mem(
        request,
        source_agent="image_prepare_agent",
        query_values=[
            body.form_values,
            body.plan_markdown,
            body.selected_direction,
            body.creation_contract,
            body.materials,
            body.revision_feedback,
            body.intake_context,
        ],
        categories=["preference", "brand", "skill", "experience"],
    )
    intake_context, _profile = with_semantic_memory(body.intake_context, memories)
    try:
        result = prepare_image_generation(
            body.form_values,
            body.plan_markdown,
            body.selected_direction,
            body.materials,
            body.revision_feedback,
            intake_context,
            body.creation_contract,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_power_mem_background(
        power_mem_service(request),
        user_id=user_id,
        content=concise_result_summary("图片准备 Agent 选择生成接口", {"method": result.method, "endpoint": result.endpoint, "message": result.message, "ok": result.ok}),
        category="experience",
        source_agent="image_prepare_agent",
        metadata={"source": "image_prepare", "method": result.method, "endpoint": result.endpoint},
        memory_type="experience",
        infer=False,
    )
    return ImagePrepareResponse(**result.to_dict())


@router.post("/generate", response_model=ImageGenerateResponse)
async def generate_image(body: ImageGenerateRequest, request: Request) -> ImageGenerateResponse:
    result = await _generate_image_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("图片生成 Agent 完成同步生成", result.model_dump()),
        category="experience",
        source_agent="image_generation_agent",
        metadata={"source": "image_generate", "method": body.method, "image_count": len(result.images)},
        memory_type="experience",
        infer=False,
    )
    return result


@router.post("/generate/start", response_model=ImageGenerateJobStartResponse)
async def start_generate_image(body: ImageGenerateRequest, request: Request) -> ImageGenerateJobStartResponse:
    _trim_image_generation_jobs()
    job_id = uuid.uuid4().hex
    _IMAGE_GENERATION_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_image_generation_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return ImageGenerateJobStartResponse(ok=True, job_id=job_id, status="running", message="图片生成任务已启动。")


@router.get("/generate/jobs/{job_id}", response_model=ImageGenerateJobStatusResponse)
async def get_generate_image_job(job_id: str) -> ImageGenerateJobStatusResponse:
    job = _IMAGE_GENERATION_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="图片生成任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, ImageGenerateResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = ImageGenerateResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return ImageGenerateJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message=_image_job_message(status, "图片生成"),
    )


async def _generate_image_response(body: ImageGenerateRequest) -> ImageGenerateResponse:
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
async def edit_image_asset(body: ImageAssetEditRequest, request: Request) -> ImageAssetEditResponse:
    result = await _edit_image_asset_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("素材图片编辑 Agent 完成同步编辑", result.model_dump()),
        category="experience",
        source_agent="scene_global_asset_edit_agent",
        metadata={"source": "image_edit_asset", "asset_id": body.asset_id, "asset_group": body.asset_group},
        memory_type="experience",
        infer=False,
    )
    return result


@router.post("/edit-asset/start", response_model=ImageAssetEditJobStartResponse)
async def start_edit_image_asset(body: ImageAssetEditRequest, request: Request) -> ImageAssetEditJobStartResponse:
    _trim_image_asset_edit_jobs()
    job_id = uuid.uuid4().hex
    _IMAGE_ASSET_EDIT_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_image_asset_edit_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return ImageAssetEditJobStartResponse(ok=True, job_id=job_id, status="running", message="素材图片编辑任务已启动。")


@router.get("/edit-asset/jobs/{job_id}", response_model=ImageAssetEditJobStatusResponse)
async def get_edit_image_asset_job(job_id: str) -> ImageAssetEditJobStatusResponse:
    job = _IMAGE_ASSET_EDIT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="素材图片编辑任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, ImageAssetEditResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = ImageAssetEditResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return ImageAssetEditJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message=_image_job_message(status, "素材图片编辑"),
    )


@router.post("/fuse-asset", response_model=ImageAssetFusionResponse)
async def fuse_image_asset(body: ImageAssetFusionRequest, request: Request) -> ImageAssetFusionResponse:
    result = await _fuse_image_asset_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("素材图片融合 Agent 完成同步融合", result.model_dump()),
        category="experience",
        source_agent="scene_global_asset_fusion_agent",
        metadata={"source": "image_fuse_asset", "asset_id": body.asset_id, "asset_group": body.asset_group},
        memory_type="experience",
        infer=False,
    )
    return result


@router.post("/fuse-asset/start", response_model=ImageAssetFusionJobStartResponse)
async def start_fuse_image_asset(body: ImageAssetFusionRequest, request: Request) -> ImageAssetFusionJobStartResponse:
    _trim_image_asset_fusion_jobs()
    job_id = uuid.uuid4().hex
    _IMAGE_ASSET_FUSION_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_image_asset_fusion_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return ImageAssetFusionJobStartResponse(ok=True, job_id=job_id, status="running", message="素材图片融合任务已启动。")


@router.get("/fuse-asset/jobs/{job_id}", response_model=ImageAssetFusionJobStatusResponse)
async def get_fuse_image_asset_job(job_id: str) -> ImageAssetFusionJobStatusResponse:
    job = _IMAGE_ASSET_FUSION_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="素材图片融合任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, ImageAssetFusionResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = ImageAssetFusionResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return ImageAssetFusionJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message=_image_job_message(status, "素材图片融合"),
    )


def _collect_global_asset_edit_reference_urls(body: ImageAssetEditRequest) -> list[str]:
    urls = collect_uploaded_reference_image_urls(body.materials)
    for url in body.reference_image_urls or []:
        normalized = str(url).strip()
        if normalized.startswith(("http://", "https://")) and normalized not in urls:
            urls.append(normalized)
    return urls[:9]


def _global_asset_image_edit_kwargs(body: ImageAssetEditRequest, *, source_image_url: str, prompt: str) -> dict[str, Any]:
    return {
        "image_url": source_image_url,
        "prompt": prompt,
        "model": _optional_str(body.model) or IMAGE_EDIT_MODEL,
        "ratio": body.ratio or "1:1",
        "size": body.size or REFERENCE_IMAGE_QUALITY,
        "max_images": 1,
    }


def _collect_global_asset_fusion_image_urls(body: ImageAssetFusionRequest, *, source_image_url: str) -> list[str]:
    urls: list[str] = []

    def append(url: str) -> None:
        normalized = url.strip()
        if normalized.startswith(("http://", "https://")) and normalized not in urls:
            urls.append(normalized)

    append(source_image_url)
    for material in body.materials or []:
        url = _material_image_url(material)
        if url and _is_valid_uploaded_image_material(material, url):
            append(url)
    for url in body.reference_image_urls or []:
        normalized = str(url).strip()
        if _is_valid_image_url_suffix(normalized):
            append(normalized)
    return urls[:9]


def _material_image_url(material: dict[str, Any]) -> str:
    for key in ("url", "image_url", "imageUrl", "download_url", "downloadUrl", "path", "src", "artifact_url", "artifactUrl"):
        value = material.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_valid_uploaded_image_material(material: dict[str, Any], url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    kind = _first_material_text(material, "type", "kind", "media_type", "mediaType", "mime_type", "mimeType").lower()
    return (
        kind in {"image", "picture", "reference_image"}
        or kind.startswith("image/")
        or kind.startswith("image")
        or _is_valid_image_url_suffix(url)
    )


def _first_material_text(material: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = material.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_valid_image_url_suffix(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    path = urlparse(url).path.lower()
    return path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


async def _edit_image_asset_response(body: ImageAssetEditRequest) -> ImageAssetEditResponse:
    source_image_url = body.source_image_url.strip()
    prompt = body.prompt.strip()
    if not source_image_url:
        raise HTTPException(status_code=400, detail="source_image_url不能为空")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt不能为空")

    reference_urls = _collect_global_asset_edit_reference_urls(body)
    skill = get_image_skill()
    method: ImageMethod = "image_edit"
    if reference_urls:
        method = "multi_reference_image_generation"
        ratio = body.ratio or global_asset_edit_ratio(body.asset_group)
        result = await skill.reference_image(
            reference_images=reference_urls,
            prompt=enhance_global_asset_edit_prompt(prompt, body.asset_group),
            model=_optional_str(body.model) or IMAGE_EDIT_MODEL,
            ratio=ratio,
            size=body.size or REFERENCE_IMAGE_QUALITY,
            max_images=1,
        )
        quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
        if not result.ok and not quota_insufficient:
            method = "image_edit"
            result = await skill.image_edit(
                **_global_asset_image_edit_kwargs(body, source_image_url=source_image_url, prompt=prompt),
            )
    else:
        result = await skill.image_edit(
            **_global_asset_image_edit_kwargs(body, source_image_url=source_image_url, prompt=prompt),
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
        method=method,
        endpoint=_endpoint_for(method, result.raw),
        source_image_url=source_image_url,
        edited_image=edited_image,
        asset_id=body.asset_id,
        asset_group=body.asset_group,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )


async def _fuse_image_asset_response(body: ImageAssetFusionRequest) -> ImageAssetFusionResponse:
    source_image_url = body.source_image_url.strip()
    prompt = body.prompt.strip()
    if not source_image_url:
        raise HTTPException(status_code=400, detail="source_image_url不能为空")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt不能为空")

    image_urls = _collect_global_asset_fusion_image_urls(body, source_image_url=source_image_url)
    if len(image_urls) < 2:
        raise HTTPException(status_code=400, detail="素材图片融合至少需要引用素材图和 1 张有效上传图片")

    skill = get_image_skill()
    ratio = body.ratio or global_asset_edit_ratio(body.asset_group)
    result = await skill.multi_image_fusion(
        image_urls=image_urls,
        prompt=enhance_global_asset_edit_prompt(prompt, body.asset_group),
        model=_optional_str(body.model) or IMAGE_EDIT_MODEL,
        ratio=ratio,
        size=body.size or REFERENCE_IMAGE_QUALITY,
        num_images=1,
    )

    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    fused_image = result.images[0] if result.images else {}
    fused_url = _optional_str(fused_image.get("url") or fused_image.get("download_url")) if fused_image else None
    ok = result.ok and bool(fused_url)
    if ok:
        message = "素材图片融合完成。"
    elif quota_insufficient:
        message = quota_resume_message(result.error)
    else:
        message = result.error or "图片融合结果没有URL。"
    return ImageAssetFusionResponse(
        ok=ok,
        method="multi_image_fusion",
        endpoint=_endpoint_for("multi_image_fusion", result.raw),
        source_image_url=source_image_url,
        fused_image=fused_image,
        asset_id=body.asset_id,
        asset_group=body.asset_group,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )


async def _run_image_generation_job(job_id: str, body: ImageGenerateRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _generate_image_response(body)
        _IMAGE_GENERATION_JOBS[job_id] = {
            "status": "quota_paused" if result.quota_insufficient else "completed",
            "result": result,
            "error": None,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("图片生成 Agent 完成异步生成", result.model_dump()),
            category="experience",
            source_agent="image_generation_agent",
            metadata={"source": "image_generate_job", "job_id": job_id, "method": body.method, "image_count": len(result.images)},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _IMAGE_GENERATION_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=f"图片生成 Agent 异步生成失败；method={body.method}；error={str(exc)[:300]}",
            category="experience",
            source_agent="image_generation_agent",
            metadata={"source": "image_generate_job", "job_id": job_id, "method": body.method, "status": "failed"},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )


async def _run_image_asset_edit_job(job_id: str, body: ImageAssetEditRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _edit_image_asset_response(body)
        _IMAGE_ASSET_EDIT_JOBS[job_id] = {
            "status": "quota_paused" if result.quota_insufficient else "completed",
            "result": result,
            "error": None,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("素材图片编辑 Agent 完成异步编辑", result.model_dump()),
            category="experience",
            source_agent="scene_global_asset_edit_agent",
            metadata={"source": "image_edit_asset_job", "job_id": job_id, "asset_id": body.asset_id, "asset_group": body.asset_group},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _IMAGE_ASSET_EDIT_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=f"素材图片编辑 Agent 异步编辑失败；asset_id={body.asset_id}；error={str(exc)[:300]}",
            category="experience",
            source_agent="scene_global_asset_edit_agent",
            metadata={"source": "image_edit_asset_job", "job_id": job_id, "asset_id": body.asset_id, "status": "failed"},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )


async def _run_image_asset_fusion_job(job_id: str, body: ImageAssetFusionRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _fuse_image_asset_response(body)
        _IMAGE_ASSET_FUSION_JOBS[job_id] = {
            "status": "quota_paused" if result.quota_insufficient else "completed",
            "result": result,
            "error": None,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("素材图片融合 Agent 完成异步融合", result.model_dump()),
            category="experience",
            source_agent="scene_global_asset_fusion_agent",
            metadata={"source": "image_fuse_asset_job", "job_id": job_id, "asset_id": body.asset_id, "asset_group": body.asset_group},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _IMAGE_ASSET_FUSION_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=f"素材图片融合 Agent 异步融合失败：asset_id={body.asset_id}，error={str(exc)[:300]}",
            category="experience",
            source_agent="scene_global_asset_fusion_agent",
            metadata={"source": "image_fuse_asset_job", "job_id": job_id, "asset_id": body.asset_id, "status": "failed"},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )


def _trim_image_generation_jobs() -> None:
    if len(_IMAGE_GENERATION_JOBS) < _MAX_IMAGE_GENERATION_JOBS:
        return
    for job_id in list(_IMAGE_GENERATION_JOBS.keys())[: len(_IMAGE_GENERATION_JOBS) - _MAX_IMAGE_GENERATION_JOBS + 1]:
        _IMAGE_GENERATION_JOBS.pop(job_id, None)


def _trim_image_asset_edit_jobs() -> None:
    if len(_IMAGE_ASSET_EDIT_JOBS) < _MAX_IMAGE_ASSET_EDIT_JOBS:
        return
    for job_id in list(_IMAGE_ASSET_EDIT_JOBS.keys())[: len(_IMAGE_ASSET_EDIT_JOBS) - _MAX_IMAGE_ASSET_EDIT_JOBS + 1]:
        _IMAGE_ASSET_EDIT_JOBS.pop(job_id, None)


def _trim_image_asset_fusion_jobs() -> None:
    if len(_IMAGE_ASSET_FUSION_JOBS) < _MAX_IMAGE_ASSET_FUSION_JOBS:
        return
    for job_id in list(_IMAGE_ASSET_FUSION_JOBS.keys())[: len(_IMAGE_ASSET_FUSION_JOBS) - _MAX_IMAGE_ASSET_FUSION_JOBS + 1]:
        _IMAGE_ASSET_FUSION_JOBS.pop(job_id, None)


def _image_job_message(status: str, label: str) -> str:
    if status == "completed":
        return f"{label}完成。"
    if status == "quota_paused":
        return f"{label}因额度不足暂停。"
    if status == "failed":
        return f"{label}失败。"
    return f"{label}中。"


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
    content_app_size = params.get("size")
    if _looks_like_ratio(content_app_size):
        return str(content_app_size)
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
    explicit = params.get("imageSize") or params.get("image_quality")
    if explicit:
        return str(explicit)
    legacy_size = params.get("size")
    if legacy_size and not _looks_like_ratio(legacy_size):
        return str(legacy_size)
    defaults = {
        "gpt-image-2": "4K",
        "seeddream-4.5": "2K",
        "seeddream-5.0": "2K",
        "nanobanana-pro": "1080p",
        "nano-banana": "1080p",
    }
    if model in defaults:
        return defaults[model]
    return "1080p"


def _looks_like_ratio(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    left, sep, right = text.partition(":")
    return bool(sep and left.isdigit() and right.isdigit())


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
