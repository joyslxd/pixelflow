"""PixelFlow v2 策划 plan.md API。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.gateway.pixelflow_memory import (
    concise_result_summary,
    current_user_id,
    power_mem_service,
    record_power_mem_background,
    search_power_mem,
)
from pixelflow.creative.plan_markdown import (
    CreationIntent,
    build_plan_markdown_with_llm,
    restore_plan_version,
    revise_plan_markdown_with_llm,
)
from pixelflow.creative.revision_contract import build_manual_plan_revision_feedback
from pixelflow.memory import build_memory_query, with_semantic_memory

router = APIRouter(prefix="/agent/flows/planning", tags=["pixelflow-flows"])

_PLAN_GENERATION_JOBS: dict[str, dict[str, Any]] = {}
_PLAN_REVISION_JOBS: dict[str, dict[str, Any]] = {}
_MAX_PLAN_GENERATION_JOBS = 200
_MAX_PLAN_REVISION_JOBS = 200


class PlanMarkdownRequest(BaseModel):
    intent: CreationIntent
    form_values: dict[str, Any] = Field(default_factory=dict)
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    product_creative_profile: dict[str, Any] = Field(default_factory=dict)
    intake_context: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    collection: dict[str, Any] = Field(default_factory=dict)

    @field_validator("intent", mode="before")
    @classmethod
    def normalize_intent_alias(cls, value: Any) -> Any:
        normalized = str(value or "").strip().lower()
        aliases = {
            "generate_video": "video",
            "video_generation": "video",
            "生成视频": "video",
            "generate_image": "image",
            "image_generation": "image",
            "生成图片": "image",
        }
        return aliases.get(normalized, value)

    @model_validator(mode="after")
    def expand_collection_payload(self) -> PlanMarkdownRequest:
        collection = self.collection if isinstance(self.collection, dict) else {}
        if not self.form_values and isinstance(collection.get("form_values"), dict):
            self.form_values = collection["form_values"]
        if not self.product_creative_profile and isinstance(collection.get("product_creative_profile"), dict):
            self.product_creative_profile = collection["product_creative_profile"]
        if not self.intake_context and isinstance(collection.get("intake_context"), dict):
            self.intake_context = collection["intake_context"]
        if not self.materials and isinstance(collection.get("materials"), list):
            self.materials = collection["materials"]
        return self


class PlanMarkdownResponse(BaseModel):
    output_type: CreationIntent
    plan_markdown: str
    template_path: str
    consistency_issues: list[str] = Field(default_factory=list)
    review_timeout_sec: int | None = None
    plan_version: int = 1
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    creation_contract: dict[str, Any] = Field(default_factory=dict)
    scene_durations_sec: list[int] = Field(default_factory=list)
    scene_blueprints: list[dict[str, Any]] = Field(default_factory=list)
    asset_manifest: dict[str, list[dict[str, str]]] = Field(
        default_factory=lambda: {"characters": [], "scenes": [], "props": []}
    )
    llm_used: bool = False
    model_name: str = "deepseek-v4-pro"
    error: str | None = None
    restored_from_version: int | None = None


class PlanJobStartResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str = "running"
    message: str = ""


class PlanJobStatusResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str
    result: PlanMarkdownResponse | None = None
    error: str | None = None
    message: str = ""


class PlanRevisionRequest(PlanMarkdownRequest):
    current_plan_markdown: str
    current_plan_version: int = Field(default=1, ge=1)
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    revision_feedback: str = Field(min_length=1)
    creation_contract: dict[str, Any] = Field(default_factory=dict)
    scene_blueprints: list[dict[str, Any]] = Field(default_factory=list)
    asset_manifest: dict[str, list[dict[str, str]]] = Field(
        default_factory=lambda: {"characters": [], "scenes": [], "props": []}
    )


class PlanRestoreRequest(BaseModel):
    intent: CreationIntent
    current_plan_markdown: str
    current_plan_version: int = Field(ge=1)
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    restore_version: int = Field(ge=1)
    creation_contract: dict[str, Any] = Field(default_factory=dict)
    scene_durations_sec: list[int] = Field(default_factory=list)
    scene_blueprints: list[dict[str, Any]] = Field(default_factory=list)
    asset_manifest: dict[str, list[dict[str, str]]] = Field(
        default_factory=lambda: {"characters": [], "scenes": [], "props": []}
    )

    @field_validator("intent", mode="before")
    @classmethod
    def normalize_intent_alias(cls, value: Any) -> Any:
        return PlanMarkdownRequest.normalize_intent_alias(value)


class PlanManualEditRequest(PlanMarkdownRequest):
    current_plan_markdown: str = Field(min_length=1, max_length=100_000)
    edited_plan_markdown: str = Field(min_length=1, max_length=100_000)
    current_plan_version: int = Field(default=1, ge=1)
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    creation_contract: dict[str, Any] = Field(default_factory=dict)
    scene_blueprints: list[dict[str, Any]] = Field(default_factory=list)
    asset_manifest: dict[str, list[dict[str, str]]] = Field(
        default_factory=lambda: {"characters": [], "scenes": [], "props": []}
    )


@router.post("/plan", response_model=PlanMarkdownResponse)
async def create_plan_markdown(body: PlanMarkdownRequest, request: Request) -> PlanMarkdownResponse:
    return await _create_plan_markdown(body, request=request)


@router.post("/plan/start", response_model=PlanJobStartResponse)
async def start_create_plan_markdown(body: PlanMarkdownRequest, request: Request) -> PlanJobStartResponse:
    _trim_plan_jobs(_PLAN_GENERATION_JOBS, _MAX_PLAN_GENERATION_JOBS)
    job_id = uuid.uuid4().hex
    user_id = await current_user_id(request)
    _PLAN_GENERATION_JOBS[job_id] = {
        "status": "running",
        "result": None,
        "error": None,
        "user_id": user_id,
    }
    asyncio.create_task(
        _run_plan_generation_job(
            job_id,
            body,
            power_mem=power_mem_service(request),
            user_id=user_id,
        )
    )
    return PlanJobStartResponse(job_id=job_id, message="Plan generation job started.")


@router.get("/plan/jobs/{job_id}", response_model=PlanJobStatusResponse)
async def get_create_plan_markdown_job(job_id: str, request: Request) -> PlanJobStatusResponse:
    return await _plan_job_status(
        _PLAN_GENERATION_JOBS,
        job_id,
        request,
        not_found_detail="Plan generation job not found",
        running_message="Plan is being generated.",
        completed_message="Plan generation completed.",
        failed_message="Plan generation failed.",
    )


async def _create_plan_markdown(
    body: PlanMarkdownRequest,
    request: Request | None = None,
    *,
    power_mem: Any = None,
    user_id: str | None = None,
    run_id: str | None = None,
) -> PlanMarkdownResponse:
    if request is not None:
        user_id, memories = await search_power_mem(
            request,
            source_agent="planning_agent",
            query_values=[body.form_values, body.selected_direction, body.product_creative_profile, body.intake_context, body.materials],
            categories=["preference", "brand", "skill", "experience"],
        )
        service = power_mem_service(request)
    else:
        service = power_mem
        memories = await _search_planning_memories(
            service,
            user_id=user_id,
            query_values=[body.form_values, body.selected_direction, body.product_creative_profile, body.intake_context, body.materials],
        )
    intake_context, product_creative_profile = with_semantic_memory(
        body.intake_context,
        memories,
        product_creative_profile=body.product_creative_profile,
    )
    result = await build_plan_markdown_with_llm(
        body.intent,
        body.form_values,
        body.selected_direction,
        product_creative_profile,
        body.materials,
        intake_context,
    )
    record_power_mem_background(
        service,
        user_id=user_id,
        content=concise_result_summary(
            "策划 Agent 生成 plan.md",
            {"intent": body.intent, "message": f"issues={len(result.consistency_issues)}", "ok": not result.consistency_issues},
        ),
        category="experience",
        source_agent="planning_agent",
        metadata={"source": "planning_plan", "intent": body.intent, "consistency_issues": result.consistency_issues},
        memory_type="experience",
        run_id=run_id,
        infer=False,
    )
    return PlanMarkdownResponse(**result.to_dict())


@router.post("/plan/revise", response_model=PlanMarkdownResponse)
async def revise_plan_markdown(body: PlanRevisionRequest, request: Request) -> PlanMarkdownResponse:
    return await _revise_plan_markdown(body, request=request)


@router.post("/plan/revise/start", response_model=PlanJobStartResponse)
async def start_revise_plan_markdown(body: PlanRevisionRequest, request: Request) -> PlanJobStartResponse:
    _trim_plan_jobs(_PLAN_REVISION_JOBS, _MAX_PLAN_REVISION_JOBS)
    job_id = uuid.uuid4().hex
    user_id = await current_user_id(request)
    _PLAN_REVISION_JOBS[job_id] = {
        "status": "running",
        "result": None,
        "error": None,
        "user_id": user_id,
    }
    asyncio.create_task(
        _run_plan_revision_job(
            job_id,
            body,
            power_mem=power_mem_service(request),
            user_id=user_id,
        )
    )
    return PlanJobStartResponse(job_id=job_id, message="Plan revision job started.")


@router.get("/plan/revise/jobs/{job_id}", response_model=PlanJobStatusResponse)
async def get_revise_plan_markdown_job(job_id: str, request: Request) -> PlanJobStatusResponse:
    return await _plan_job_status(
        _PLAN_REVISION_JOBS,
        job_id,
        request,
        not_found_detail="Plan revision job not found",
        running_message="Plan is being revised.",
        completed_message="Plan revision completed.",
        failed_message="Plan revision failed.",
    )


async def _revise_plan_markdown(
    body: PlanRevisionRequest,
    request: Request | None = None,
    *,
    power_mem: Any = None,
    user_id: str | None = None,
    run_id: str | None = None,
) -> PlanMarkdownResponse:
    query_values = [
        body.form_values,
        body.selected_direction,
        body.product_creative_profile,
        body.intake_context,
        body.materials,
        body.current_plan_markdown,
        body.revision_feedback,
    ]
    if request is not None:
        user_id, memories = await search_power_mem(
            request,
            source_agent="planning_agent",
            query_values=query_values,
            categories=["preference", "brand", "skill", "experience"],
        )
        service = power_mem_service(request)
    else:
        service = power_mem
        memories = await _search_planning_memories(service, user_id=user_id, query_values=query_values)
    intake_context, product_creative_profile = with_semantic_memory(
        body.intake_context,
        memories,
        product_creative_profile=body.product_creative_profile,
    )
    result = await revise_plan_markdown_with_llm(
        intent=body.intent,
        form_values=body.form_values,
        selected_direction=body.selected_direction,
        current_plan_markdown=body.current_plan_markdown,
        current_plan_version=body.current_plan_version,
        plan_history=body.plan_history,
        revision_feedback=body.revision_feedback,
        creation_contract=body.creation_contract,
        current_scene_blueprints=body.scene_blueprints,
        current_asset_manifest=body.asset_manifest,
        product_creative_profile=product_creative_profile,
        materials=body.materials,
        intake_context=intake_context,
    )
    record_power_mem_background(
        service,
        user_id=user_id,
        content=concise_result_summary(
            "策划 Agent 修订 plan.md",
            {"intent": body.intent, "message": f"version={result.plan_version}", "ok": not result.error},
        ),
        category="experience",
        source_agent="planning_agent",
        metadata={"source": "planning_plan_revision", "intent": body.intent, "plan_version": result.plan_version},
        memory_type="experience",
        run_id=run_id,
        infer=False,
    )
    return PlanMarkdownResponse(**result.to_dict())


async def _run_plan_generation_job(
    job_id: str,
    body: PlanMarkdownRequest,
    *,
    power_mem: Any = None,
    user_id: str | None = None,
) -> None:
    try:
        result = await _create_plan_markdown(
            body,
            power_mem=power_mem,
            user_id=user_id,
            run_id=job_id,
        )
        _PLAN_GENERATION_JOBS[job_id] = {
            "status": "completed",
            "result": result,
            "error": None,
            "user_id": user_id,
        }
    except Exception as exc:  # noqa: BLE001 - background boundary persists failure for polling clients
        _PLAN_GENERATION_JOBS[job_id] = {
            "status": "failed",
            "result": None,
            "error": str(exc),
            "user_id": user_id,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary(
                "策划 Agent 异步生成 plan.md 失败",
                {"intent": body.intent, "ok": False},
            ),
            category="experience",
            source_agent="planning_agent",
            metadata={"source": "planning_plan_job", "job_id": job_id, "status": "failed", "intent": body.intent},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )


async def _run_plan_revision_job(
    job_id: str,
    body: PlanRevisionRequest,
    *,
    power_mem: Any = None,
    user_id: str | None = None,
) -> None:
    try:
        result = await _revise_plan_markdown(
            body,
            power_mem=power_mem,
            user_id=user_id,
            run_id=job_id,
        )
        _PLAN_REVISION_JOBS[job_id] = {
            "status": "completed",
            "result": result,
            "error": None,
            "user_id": user_id,
        }
    except Exception as exc:  # noqa: BLE001 - background boundary persists failure for polling clients
        _PLAN_REVISION_JOBS[job_id] = {
            "status": "failed",
            "result": None,
            "error": str(exc),
            "user_id": user_id,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary(
                "策划 Agent 异步修订 plan.md 失败",
                {"intent": body.intent, "ok": False},
            ),
            category="experience",
            source_agent="planning_agent",
            metadata={"source": "planning_plan_revision_job", "job_id": job_id, "status": "failed", "intent": body.intent},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )


async def _plan_job_status(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    request: Request,
    *,
    not_found_detail: str,
    running_message: str,
    completed_message: str,
    failed_message: str,
) -> PlanJobStatusResponse:
    job = jobs.get(job_id)
    user_id = await current_user_id(request)
    if job is None or job.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail=not_found_detail)
    raw_result = job.get("result")
    if isinstance(raw_result, PlanMarkdownResponse):
        result = raw_result
    elif isinstance(raw_result, dict):
        result = PlanMarkdownResponse(**raw_result)
    else:
        result = None
    status = str(job.get("status") or "running")
    error = str(job.get("error")) if job.get("error") else None
    message = completed_message if status == "completed" else failed_message if status == "failed" else running_message
    return PlanJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result,
        error=error,
        message=message,
    )


async def _search_planning_memories(
    power_mem: Any,
    *,
    user_id: str | None,
    query_values: list[Any],
) -> list[Any]:
    if power_mem is None or not hasattr(power_mem, "search"):
        return []
    query = build_memory_query(*query_values)
    if not query:
        return []
    return await power_mem.search(
        user_id=user_id,
        query=query,
        categories=["preference", "brand", "skill", "experience"],
        source_agent=None,
    )


def _trim_plan_jobs(jobs: dict[str, dict[str, Any]], max_jobs: int) -> None:
    overflow = len(jobs) - max_jobs + 1
    if overflow <= 0:
        return
    for job_id in list(jobs.keys())[:overflow]:
        jobs.pop(job_id, None)


@router.post("/plan/restore", response_model=PlanMarkdownResponse)
async def restore_plan_markdown(body: PlanRestoreRequest) -> PlanMarkdownResponse:
    result = restore_plan_version(
        intent=body.intent,
        current_plan_markdown=body.current_plan_markdown,
        current_plan_version=body.current_plan_version,
        plan_history=body.plan_history,
        restore_version=body.restore_version,
        creation_contract=body.creation_contract,
        scene_durations_sec=body.scene_durations_sec,
        scene_blueprints=body.scene_blueprints,
        asset_manifest=body.asset_manifest,
    )
    return PlanMarkdownResponse(**result.to_dict())


@router.post("/plan/save-edit", response_model=PlanMarkdownResponse)
async def save_manual_plan_edit(body: PlanManualEditRequest, request: Request) -> PlanMarkdownResponse:
    user_id, memories = await search_power_mem(
        request,
        source_agent="planning_agent",
        query_values=[
            body.form_values,
            body.selected_direction,
            body.product_creative_profile,
            body.intake_context,
            body.materials,
            body.current_plan_markdown,
            body.edited_plan_markdown,
        ],
        categories=["preference", "brand", "skill", "experience"],
    )
    intake_context, product_creative_profile = with_semantic_memory(
        body.intake_context,
        memories,
        product_creative_profile=body.product_creative_profile,
    )
    result = await revise_plan_markdown_with_llm(
        intent=body.intent,
        form_values=body.form_values,
        selected_direction=body.selected_direction,
        current_plan_markdown=body.current_plan_markdown,
        current_plan_version=body.current_plan_version,
        plan_history=body.plan_history,
        revision_feedback=build_manual_plan_revision_feedback(body.current_plan_markdown, body.edited_plan_markdown),
        creation_contract=body.creation_contract,
        current_scene_blueprints=body.scene_blueprints,
        current_asset_manifest=body.asset_manifest,
        product_creative_profile=product_creative_profile,
        materials=body.materials,
        intake_context=intake_context,
        change_source="manual_edit",
    )
    record_power_mem_background(
        power_mem_service(request),
        user_id=user_id,
        content=concise_result_summary(
            "用户手工发布 plan.md",
            {"intent": body.intent, "message": f"version={result.plan_version}", "ok": not result.error},
        ),
        category="experience",
        source_agent="planning_agent",
        metadata={"source": "planning_plan_manual_edit", "intent": body.intent, "plan_version": result.plan_version},
        memory_type="experience",
        infer=False,
    )
    return PlanMarkdownResponse(**result.to_dict())
