"""PixelFlow v2 智能 PPT 生成 API。"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pixelflow.skills import PptGenerationResult, get_ppt_skill
from pixelflow.skills.base import is_quota_insufficient, quota_resume_message

router = APIRouter(prefix="/agent/flows/ppt", tags=["pixelflow-flows"])

_PPT_JOBS: dict[str, dict[str, Any]] = {}
_MAX_PPT_JOBS = 100
_PPT_ATTACHMENT_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx", ".pdf"}


class PptJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class PptJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    message: str = ""


class PptSummaryJobStartRequest(BaseModel):
    ppt_topic: str
    ppt_style: str
    attachments: list[dict[str, Any] | str] = Field(default_factory=list)
    smart_ppt_project_id: int | None = None


class PptUpdateSummaryJobStartRequest(BaseModel):
    original_outline: str
    modification_opinion: str
    smart_ppt_project_id: int


class PptContentJsonJobStartRequest(BaseModel):
    original_outline: str
    ppt_style: str
    smart_ppt_project_id: int


class PptImagesJobStartRequest(BaseModel):
    content_json: Any
    smart_ppt_project_id: int


class PptRegenerateImageJobStartRequest(BaseModel):
    page_index: int = Field(ge=1)
    page_json: dict[str, Any] = Field(default_factory=dict)
    smart_ppt_project_id: int


class PptFileJobStartRequest(BaseModel):
    file_urls: list[str]
    smart_ppt_project_id: int


@router.post("/summary/start", response_model=PptJobStartResponse)
async def start_ppt_summary(body: PptSummaryJobStartRequest) -> PptJobStartResponse:
    file_urls = _extract_office_file_urls(body.attachments)
    if not body.ppt_topic.strip():
        raise HTTPException(status_code=422, detail="PPT主题不能为空")
    if not body.ppt_style.strip():
        raise HTTPException(status_code=422, detail="PPT风格不能为空")
    job_id = _create_job("ppt_summary")
    asyncio.create_task(_run_summary_job(job_id, body, file_urls))
    return PptJobStartResponse(ok=True, job_id=job_id, status="running", message="已开始生成 PPT 大纲。")


@router.post("/summary/update/start", response_model=PptJobStartResponse)
async def start_update_ppt_summary(body: PptUpdateSummaryJobStartRequest) -> PptJobStartResponse:
    job_id = _create_job("ppt_summary_update")
    asyncio.create_task(_run_summary_update_job(job_id, body))
    return PptJobStartResponse(ok=True, job_id=job_id, status="running", message="已开始更新 PPT 大纲。")


@router.post("/content-json/start", response_model=PptJobStartResponse)
async def start_ppt_content_json(body: PptContentJsonJobStartRequest) -> PptJobStartResponse:
    job_id = _create_job("ppt_content_json")
    asyncio.create_task(_run_content_json_job(job_id, body))
    return PptJobStartResponse(ok=True, job_id=job_id, status="running", message="已开始将 PPT 大纲转换为页面 JSON。")


@router.post("/images/start", response_model=PptJobStartResponse)
async def start_ppt_images(body: PptImagesJobStartRequest) -> PptJobStartResponse:
    pages = _normalize_content_pages(body.content_json)
    if not pages:
        raise HTTPException(status_code=422, detail="PPT页面 JSON 不能为空")
    job_id = _create_job("ppt_images")
    _set_job_result(
        job_id,
        {
            "ok": False,
            "smart_ppt_project_id": body.smart_ppt_project_id,
            "pages": [_page_pending_payload(index, page_json) for index, page_json in enumerate(pages, start=1)],
            "message": "PPT图片生成中。",
        },
    )
    asyncio.create_task(_run_images_job(job_id, body.smart_ppt_project_id, pages))
    return PptJobStartResponse(ok=True, job_id=job_id, status="running", message="已开始生成 PPT 页面图片。")


@router.post("/images/regenerate/start", response_model=PptJobStartResponse)
async def start_regenerate_ppt_image(body: PptRegenerateImageJobStartRequest) -> PptJobStartResponse:
    job_id = _create_job("ppt_image_regenerate")
    _set_job_result(
        job_id,
        {
            "ok": False,
            "smart_ppt_project_id": body.smart_ppt_project_id,
            "page": _page_pending_payload(body.page_index, body.page_json),
            "message": "PPT单页图片重新生成中。",
        },
    )
    asyncio.create_task(_run_regenerate_image_job(job_id, body))
    return PptJobStartResponse(ok=True, job_id=job_id, status="running", message="已开始重新生成 PPT 页面图片。")


@router.post("/file/start", response_model=PptJobStartResponse)
async def start_ppt_file(body: PptFileJobStartRequest) -> PptJobStartResponse:
    file_urls = [url.strip() for url in body.file_urls if url and url.strip()]
    if not file_urls:
        raise HTTPException(status_code=422, detail="请先生成可用的 PPT 页面图片")
    job_id = _create_job("ppt_file")
    asyncio.create_task(_run_file_job(job_id, body.smart_ppt_project_id, file_urls))
    return PptJobStartResponse(ok=True, job_id=job_id, status="running", message="已开始生成 PPT 附件。")


@router.get("/jobs/{job_id}", response_model=PptJobStatusResponse)
async def get_ppt_job(job_id: str) -> PptJobStatusResponse:
    job = _PPT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="PPT job not found")
    return PptJobStatusResponse(
        ok=job.get("status") not in {"failed"},
        job_id=job_id,
        status=job.get("status", "unknown"),
        result=job.get("result"),
        error=job.get("error"),
        message=job.get("message", ""),
    )


async def _run_summary_job(job_id: str, body: PptSummaryJobStartRequest, file_urls: list[str]) -> None:
    try:
        result = await get_ppt_skill().generate_ppt_summary(
            topic=body.ppt_topic,
            ppt_style=body.ppt_style,
            file_urls=file_urls,
            smart_ppt_project_id=body.smart_ppt_project_id,
        )
        _complete_or_pause(job_id, _ppt_result_dict(result, stage="summary"))
    except Exception as exc:  # noqa: BLE001 - async job boundary
        _fail_job(job_id, str(exc))


async def _run_summary_update_job(job_id: str, body: PptUpdateSummaryJobStartRequest) -> None:
    try:
        result = await get_ppt_skill().update_ppt_summary(
            original_outline=body.original_outline,
            modification_opinion=body.modification_opinion,
            smart_ppt_project_id=body.smart_ppt_project_id,
        )
        _complete_or_pause(job_id, _ppt_result_dict(result, stage="summary_update"))
    except Exception as exc:  # noqa: BLE001 - async job boundary
        _fail_job(job_id, str(exc))


async def _run_content_json_job(job_id: str, body: PptContentJsonJobStartRequest) -> None:
    try:
        result = await get_ppt_skill().generate_ppt_content_json(
            original_outline=body.original_outline,
            ppt_style=body.ppt_style,
            smart_ppt_project_id=body.smart_ppt_project_id,
        )
        result_dict = _ppt_result_dict(result, stage="content_json")
        result_dict["pages"] = _normalize_content_pages(result.content_json)
        _complete_or_pause(job_id, result_dict)
    except Exception as exc:  # noqa: BLE001 - async job boundary
        _fail_job(job_id, str(exc))


async def _run_images_job(job_id: str, smart_ppt_project_id: int, pages: list[dict[str, Any]]) -> None:
    async def generate_one(page_index: int, page_json: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        result = await get_ppt_skill().generate_ppt_image(
            json_content=json.dumps(page_json, ensure_ascii=False),
            smart_ppt_project_id=smart_ppt_project_id,
        )
        return page_index, _page_result_payload(page_index, page_json, result)

    tasks = [asyncio.create_task(generate_one(index, page_json)) for index, page_json in enumerate(pages, start=1)]
    by_index: dict[int, dict[str, Any]] = {}
    quota_paused = False
    for completed in asyncio.as_completed(tasks):
        page_index, page_result = await completed
        by_index[page_index] = page_result
        quota_paused = quota_paused or bool(page_result.get("quota_insufficient"))
        _set_job_result(
            job_id,
            {
                "ok": False,
                "smart_ppt_project_id": smart_ppt_project_id,
                "pages": [_merge_page_status(index, page_json, by_index) for index, page_json in enumerate(pages, start=1)],
                "message": "PPT图片生成中。",
            },
        )
    final_pages = [by_index[index] for index in sorted(by_index)]
    all_ok = final_pages and all(page.get("status") == "completed" for page in final_pages)
    result = {
        "ok": all_ok,
        "smart_ppt_project_id": smart_ppt_project_id,
        "pages": final_pages,
        "message": "PPT 页面图片已生成。" if all_ok else "部分 PPT 页面图片生成失败。",
        "quota_insufficient": quota_paused,
    }
    _complete_or_pause(job_id, result)


async def _run_regenerate_image_job(job_id: str, body: PptRegenerateImageJobStartRequest) -> None:
    try:
        result = await get_ppt_skill().generate_ppt_image(
            json_content=json.dumps(body.page_json, ensure_ascii=False),
            smart_ppt_project_id=body.smart_ppt_project_id,
        )
        result_dict = {
            "ok": result.ok,
            "smart_ppt_project_id": result.smart_ppt_project_id or body.smart_ppt_project_id,
            "page": _page_result_payload(body.page_index, body.page_json, result),
            "quota_insufficient": result.quota_insufficient or is_quota_insufficient(result.raw),
            "message": "PPT 单页图片已重新生成。" if result.ok else (result.error or "PPT 单页图片生成失败。"),
        }
        _complete_or_pause(job_id, result_dict)
    except Exception as exc:  # noqa: BLE001 - async job boundary
        _fail_job(job_id, str(exc))


async def _run_file_job(job_id: str, smart_ppt_project_id: int, file_urls: list[str]) -> None:
    try:
        result = await get_ppt_skill().generate_ppt_file(file_urls=file_urls, smart_ppt_project_id=smart_ppt_project_id)
        _complete_or_pause(job_id, _ppt_result_dict(result, stage="file"))
    except Exception as exc:  # noqa: BLE001 - async job boundary
        _fail_job(job_id, str(exc))


def _create_job(kind: str) -> str:
    while len(_PPT_JOBS) >= _MAX_PPT_JOBS:
        _PPT_JOBS.pop(next(iter(_PPT_JOBS)))
    job_id = str(uuid.uuid4())
    _PPT_JOBS[job_id] = {"status": "running", "kind": kind, "result": None, "error": None, "message": ""}
    return job_id


def _set_job_result(job_id: str, result: dict[str, Any]) -> None:
    if job_id in _PPT_JOBS:
        _PPT_JOBS[job_id]["result"] = result


def _complete_or_pause(job_id: str, result: dict[str, Any]) -> None:
    if job_id not in _PPT_JOBS:
        return
    quota_insufficient = bool(result.get("quota_insufficient")) or is_quota_insufficient(result)
    if quota_insufficient:
        result["quota_insufficient"] = True
        result["message"] = quota_resume_message(str(result.get("message") or result.get("error") or "额度不足"))
        _PPT_JOBS[job_id].update(status="quota_paused", result=result, error=None, message=result["message"])
        return
    _PPT_JOBS[job_id].update(status="completed", result=result, error=None, message=str(result.get("message") or ""))


def _fail_job(job_id: str, error: str) -> None:
    if job_id in _PPT_JOBS:
        _PPT_JOBS[job_id].update(status="failed", error=error, message=error)


def _ppt_result_dict(result: PptGenerationResult, *, stage: str) -> dict[str, Any]:
    quota_insufficient = result.quota_insufficient or is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    return {
        "ok": result.ok,
        "stage": stage,
        "task_id": result.task_id,
        "smart_ppt_project_id": result.smart_ppt_project_id,
        "summary": result.summary,
        "content_json": result.content_json,
        "image_url": result.image_url,
        "ppt_url": result.ppt_url,
        "filename": result.filename,
        "slide_count": result.slide_count,
        "error": result.error,
        "quota_insufficient": quota_insufficient,
        "message": _ppt_stage_message(stage, result),
        "raw": result.raw,
    }


def _ppt_stage_message(stage: str, result: PptGenerationResult) -> str:
    if not result.ok:
        return result.error or "SmartPPT 任务失败。"
    messages = {
        "summary": "PPT 大纲已生成，请确认是否需要修改。",
        "summary_update": "PPT 大纲已更新，请确认是否继续修改。",
        "content_json": "PPT 页面结构已生成，开始进入 PPT 图片生成。",
        "file": "PPT 附件已生成，请下载确认。",
    }
    return messages.get(stage, "SmartPPT 任务已完成。")


def _page_pending_payload(page_index: int, page_json: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_index": page_index,
        "title": str(page_json.get("title") or page_json.get("page_title") or f"第 {page_index} 页"),
        "json_content": page_json,
        "status": "running",
        "image_url": None,
        "task_id": None,
        "error": None,
    }


def _page_result_payload(page_index: int, page_json: dict[str, Any], result: PptGenerationResult) -> dict[str, Any]:
    return {
        "page_index": page_index,
        "title": str(page_json.get("title") or page_json.get("page_title") or f"第 {page_index} 页"),
        "json_content": page_json,
        "status": "completed" if result.ok and result.image_url else "failed",
        "image_url": result.image_url,
        "task_id": result.task_id,
        "error": result.error,
        "quota_insufficient": result.quota_insufficient or is_quota_insufficient(result.raw),
        "raw": result.raw,
    }


def _merge_page_status(page_index: int, page_json: dict[str, Any], by_index: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return by_index.get(page_index) or _page_pending_payload(page_index, page_json)


def _normalize_content_pages(content_json: Any) -> list[dict[str, Any]]:
    payload = content_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = [{"title": "PPT页面", "content": payload}]
    if isinstance(payload, dict):
        for key in ("pages", "slides", "items", "content", "page_list"):
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        return []
    pages: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        page = item if isinstance(item, dict) else {"content": item}
        page.setdefault("page_index", index)
        pages.append(page)
    return pages


def _extract_office_file_urls(attachments: list[dict[str, Any] | str]) -> list[str]:
    file_urls: list[str] = []
    for attachment in attachments:
        url = attachment if isinstance(attachment, str) else _attachment_url(attachment)
        if not url:
            continue
        suffix = PurePosixPath(urlparse(url).path).suffix.lower()
        if suffix not in _PPT_ATTACHMENT_EXTENSIONS:
            raise HTTPException(status_code=422, detail="PPT附件仅支持 Word、Excel、PDF 文件")
        file_urls.append(url)
    if not file_urls:
        raise HTTPException(status_code=422, detail="请上传至少一个 Word、Excel 或 PDF 附件")
    return file_urls


def _attachment_url(attachment: dict[str, Any]) -> str:
    for key in ("url", "fileUrl", "file_url", "path", "download_url", "downloadUrl"):
        value = attachment.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
