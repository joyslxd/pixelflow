"""PixelFlow v2 采集表单与创意方向 API。

这个 router 是采集阶段的 Controller：返回表单 schema、校验补表结果、生成
确定性的 3 个创意方向草稿。真正流程编排后续仍应落在 LangGraph node/Agent，
这里先提供前端和第三方可调用的 `/agent` 契约。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.pixelflow_memory import concise_result_summary, current_user_id, power_mem_service, record_power_mem_background, search_power_mem
from pixelflow.agent_workflows.video.live_capabilities import (
    generate_application_directions as draft_creative_directions_with_llm,
)
from pixelflow.agent_workflows.video.live_capabilities import (
    validate_video_application_form as validate_form,
)
from pixelflow.intake.context import IntakeContext as StandardIntakeContext
from pixelflow.intake.context import normalize_intake_context
from pixelflow.intake.forms import CreationIntent, get_form_schema
from pixelflow.intake.industry_profile import resolve_industry_profile
from pixelflow.intake.llm import IntakeIntent, recognize_intent_with_llm
from pixelflow.memory import build_memory_query, with_semantic_memory

router = APIRouter(prefix="/agent/flows/intake", tags=["pixelflow-flows"])


class IntakeValidateRequest(BaseModel):
    intent: CreationIntent
    values: dict[str, Any] = Field(default_factory=dict)
    intake_rounds: int = Field(default=0, ge=0)

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
            "generate_ppt": "ppt",
            "ppt_generation": "ppt",
            "smart_ppt": "ppt",
            "生成ppt": "ppt",
            "制作ppt": "ppt",
        }
        return aliases.get(normalized, value)


class IntentAnalyzeRequest(BaseModel):
    prompt: str
    materials: list[dict[str, Any]] = Field(default_factory=list)


class IntentAnalyzeResponse(BaseModel):
    intent: IntakeIntent
    confidence: float = 0
    reason: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    intake_context: dict[str, Any] = Field(default_factory=dict)
    llm_used: bool = False
    model_name: str = "deepseek-v4-pro"
    error: str | None = None


class IntentAnalyzeJobStartResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str = "running"
    message: str = ""


class IntentAnalyzeJobStatusResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str
    result: IntentAnalyzeResponse | None = None
    error: str | None = None
    message: str = ""


class IntakeValidationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    intent: CreationIntent
    form_schema: dict[str, Any] = Field(alias="schema")
    values: dict[str, Any]
    missing_fields: list[str] = Field(default_factory=list)
    intake_rounds: int = 0
    is_complete: bool = False
    terminated: bool = False
    message: str = ""
    creative_directions: list[dict[str, Any]] = Field(default_factory=list)


class CreativeDirectionsRequest(IntakeValidateRequest):
    product_creative_profile: dict[str, Any] = Field(default_factory=dict)
    intake_context: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)


class CreativeDirectionsResponse(BaseModel):
    validation: IntakeValidationResponse
    creative_directions: list[dict[str, Any]] = Field(default_factory=list)
    intake_context: dict[str, Any] = Field(default_factory=dict)


class CreativeDirectionsJobStartResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str = "running"
    message: str = ""


class CreativeDirectionsJobStatusResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str
    result: CreativeDirectionsResponse | None = None
    error: str | None = None
    message: str = ""


_INTAKE_ANALYZE_JOBS: dict[str, dict[str, Any]] = {}
_MAX_INTAKE_ANALYZE_JOBS = 200
_CREATIVE_DIRECTION_JOBS: dict[str, dict[str, Any]] = {}
_MAX_CREATIVE_DIRECTION_JOBS = 200


@router.get("/forms/{intent}")
async def get_intake_form(intent: CreationIntent) -> dict[str, Any]:
    return get_form_schema(intent).to_dict()


@router.post("/analyze", response_model=IntentAnalyzeResponse)
async def analyze_intake_intent(body: IntentAnalyzeRequest, request: Request) -> IntentAnalyzeResponse:
    return await _analyze_intake_intent(body, request)


@router.post("/analyze/start", response_model=IntentAnalyzeJobStartResponse)
async def start_analyze_intake_intent(body: IntentAnalyzeRequest, request: Request) -> IntentAnalyzeJobStartResponse:
    _trim_intake_analyze_jobs()
    user_id = await current_user_id(request)
    job_id = uuid.uuid4().hex
    _INTAKE_ANALYZE_JOBS[job_id] = {"status": "running", "result": None, "error": None, "user_id": user_id}
    asyncio.create_task(_run_intake_analyze_job(job_id, body, power_mem_service(request), user_id))
    return IntentAnalyzeJobStartResponse(ok=True, job_id=job_id, status="running", message="采集 Agent 意图识别任务已启动。")


@router.get("/analyze/jobs/{job_id}", response_model=IntentAnalyzeJobStatusResponse)
async def get_analyze_intake_intent_job(job_id: str, request: Request) -> IntentAnalyzeJobStatusResponse:
    job = _INTAKE_ANALYZE_JOBS.get(job_id)
    user_id = await current_user_id(request)
    if job is None or job.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Intake analyze job not found")
    result = job.get("result")
    if isinstance(result, IntentAnalyzeResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = IntentAnalyzeResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return IntentAnalyzeJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message=_intake_analyze_job_message(status),
    )


async def _analyze_intake_intent(
    body: IntentAnalyzeRequest,
    request: Request | None = None,
    *,
    power_mem: Any = None,
    user_id: str | None = None,
) -> IntentAnalyzeResponse:
    result = await recognize_intent_with_llm(body.prompt, body.materials)
    data = result.to_dict()
    if request is not None:
        user_id, memories = await search_power_mem(
            request,
            source_agent="intake_agent",
            query_values=[body.prompt, body.materials, data.get("values"), data.get("intake_context")],
            categories=["preference", "brand", "skill"],
        )
    else:
        memories = await _search_intake_memories(
            power_mem,
            user_id=user_id,
            query_values=[body.prompt, body.materials, data.get("values"), data.get("intake_context")],
        )
    if memories:
        context, profile = with_semantic_memory(data.get("intake_context"), memories)
        data["intake_context"] = context
        values = dict(data.get("values") or {})
        values["semantic_memory_context"] = context.get("semantic_memory")
        data["values"] = values
        if profile:
            context["product_creative_profile"] = profile
    service = power_mem or (power_mem_service(request) if request is not None else None)
    record_power_mem_background(
        service,
        user_id=user_id,
        content=concise_result_summary("采集 Agent 完成意图识别", {"intent": data.get("intent"), "message": data.get("reason"), "ok": True}),
        category="experience",
        source_agent="intake_agent",
        metadata={"source": "intake_analyze", "intent": data.get("intent")},
        memory_type="experience",
        infer=False,
    )
    if data.get("intake_context"):
        record_power_mem_background(
            service,
            user_id=user_id,
            content=_brand_memory_summary(data["intake_context"]),
            category="brand",
            source_agent="intake_agent",
            metadata={"source": "intake_analyze", "intent": data.get("intent")},
            # memory_type 必须和 category 一致：PowerMem 服务端会用 memory_type 覆写
            # metadata.category，若这里写成 "fact"，brand 记忆会被存成 category=fact，
            # 之后 creative_directions 用 filters.category=brand 检索时就永远搜不到。
            memory_type="brand",
            infer=False,
        )
    return IntentAnalyzeResponse(**data)


@router.post("/validate", response_model=IntakeValidationResponse)
async def validate_intake_form(body: IntakeValidateRequest) -> IntakeValidationResponse:
    result = validate_form(body.intent, body.values, body.intake_rounds)
    data = result.to_dict()
    data["form_schema"] = data.pop("schema")
    return IntakeValidationResponse(**data, creative_directions=[])


@router.post("/directions", response_model=CreativeDirectionsResponse)
async def create_creative_directions(body: CreativeDirectionsRequest, request: Request) -> CreativeDirectionsResponse:
    return await _create_creative_directions(body, request)


@router.post("/directions/start", response_model=CreativeDirectionsJobStartResponse)
async def start_creative_directions(body: CreativeDirectionsRequest, request: Request) -> CreativeDirectionsJobStartResponse:
    _trim_creative_direction_jobs()
    job_id = uuid.uuid4().hex
    _CREATIVE_DIRECTION_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_creative_direction_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return CreativeDirectionsJobStartResponse(ok=True, job_id=job_id, status="running", message="创意方向生成任务已启动。")


@router.get("/directions/jobs/{job_id}", response_model=CreativeDirectionsJobStatusResponse)
async def get_creative_directions_job(job_id: str) -> CreativeDirectionsJobStatusResponse:
    job = _CREATIVE_DIRECTION_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Creative directions job not found")
    result = job.get("result")
    if isinstance(result, dict):
        result = CreativeDirectionsResponse(**result)
    return CreativeDirectionsJobStatusResponse(
        ok=job.get("status") != "failed",
        job_id=job_id,
        status=str(job.get("status") or "running"),
        result=result if isinstance(result, CreativeDirectionsResponse) else None,
        error=job.get("error"),
        message=str(job.get("error") or ""),
    )


async def _create_creative_directions(
    body: CreativeDirectionsRequest,
    request: Request | None = None,
    *,
    memories: list[Any] | None = None,
    power_mem: Any = None,
    user_id: str | None = None,
) -> CreativeDirectionsResponse:
    validation = validate_form(body.intent, body.values, body.intake_rounds)
    data = validation.to_dict()
    data["form_schema"] = data.pop("schema")
    validation_response = IntakeValidationResponse(**data, creative_directions=[])
    if not validation.is_complete or validation.terminated:
        return CreativeDirectionsResponse(validation=validation_response, creative_directions=[], intake_context=body.intake_context)
    context = _context_for_directions(body, validation.values)
    product_creative_profile = dict(body.product_creative_profile)
    if not product_creative_profile:
        profile_result = await resolve_industry_profile(
            industry_type=context.industry_type,
            source_prompt=context.source_prompt,
            form_values=context.form_values,
            materials=body.materials,
        )
        product_creative_profile = profile_result.profile
    if body.materials:
        product_creative_profile["materials"] = body.materials
    if memories is None and request is not None:
        user_id, memories = await search_power_mem(
            request,
            source_agent="creative_direction_agent",
            query_values=[context.to_dict(), validation.values, product_creative_profile, body.materials],
            categories=["preference", "brand", "skill", "experience"],
        )
    elif memories is None:
        memories = await _search_creative_direction_memories(
            power_mem,
            user_id=user_id,
            query_values=[context.to_dict(), validation.values, product_creative_profile, body.materials],
        )
    memories = memories or []
    memory_context, product_creative_profile = with_semantic_memory(
        context.to_dict(),
        memories,
        product_creative_profile=product_creative_profile,
    )
    context = StandardIntakeContext(
        source_prompt=context.source_prompt,
        intent=context.intent,
        product_subject=context.product_subject,
        creation_goal=context.creation_goal,
        industry_type=context.industry_type,
        requested_output_count=context.requested_output_count,
        form_values=context.form_values,
        product_creative_profile=product_creative_profile,
    )
    context_dict = {**context.to_dict(), **{key: value for key, value in memory_context.items() if key == "semantic_memory"}}
    directions = [
        direction.to_dict()
        for direction in await draft_creative_directions_with_llm(
            body.intent,
            context.form_values,
            product_creative_profile,
        )
    ]
    record_power_mem_background(
        power_mem or (power_mem_service(request) if request is not None else None),
        user_id=user_id,
        content=concise_result_summary(
            "创意方向 Agent 生成方向",
            {"intent": body.intent, "message": f"directions={len(directions)}", "ok": True},
        ),
        category="experience",
        source_agent="creative_direction_agent",
        metadata={"source": "creative_directions", "intent": body.intent},
        memory_type="experience",
        infer=False,
    )
    return CreativeDirectionsResponse(validation=validation_response, creative_directions=directions, intake_context=context_dict)


async def _run_creative_direction_job(
    job_id: str,
    body: CreativeDirectionsRequest,
    power_mem: Any = None,
    user_id: str | None = None,
) -> None:
    try:
        result = await _create_creative_directions(body, power_mem=power_mem, user_id=user_id)
        _CREATIVE_DIRECTION_JOBS[job_id] = {"status": "completed", "result": result, "error": None}
    except Exception as exc:
        _CREATIVE_DIRECTION_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}


async def _run_intake_analyze_job(
    job_id: str,
    body: IntentAnalyzeRequest,
    power_mem: Any = None,
    user_id: str | None = None,
) -> None:
    try:
        result = await _analyze_intake_intent(body, power_mem=power_mem, user_id=user_id)
        _INTAKE_ANALYZE_JOBS[job_id] = {"status": "completed", "result": result, "error": None, "user_id": user_id}
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("采集 Agent 完成异步意图识别", result.model_dump()),
            category="experience",
            source_agent="intake_agent",
            metadata={"source": "intake_analyze_job", "job_id": job_id, "intent": result.intent},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _INTAKE_ANALYZE_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc), "user_id": user_id}
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=f"采集 Agent 异步意图识别失败；error={str(exc)[:300]}",
            category="experience",
            source_agent="intake_agent",
            metadata={"source": "intake_analyze_job", "job_id": job_id, "status": "failed"},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )


async def _search_intake_memories(
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
        categories=["preference", "brand", "skill"],
        source_agent=None,
    )


async def _search_creative_direction_memories(
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


def _trim_intake_analyze_jobs() -> None:
    overflow = len(_INTAKE_ANALYZE_JOBS) - _MAX_INTAKE_ANALYZE_JOBS + 1
    if overflow <= 0:
        return
    for job_id in list(_INTAKE_ANALYZE_JOBS.keys())[:overflow]:
        _INTAKE_ANALYZE_JOBS.pop(job_id, None)


def _trim_creative_direction_jobs() -> None:
    overflow = len(_CREATIVE_DIRECTION_JOBS) - _MAX_CREATIVE_DIRECTION_JOBS + 1
    if overflow <= 0:
        return
    for job_id in list(_CREATIVE_DIRECTION_JOBS.keys())[:overflow]:
        _CREATIVE_DIRECTION_JOBS.pop(job_id, None)


def _intake_analyze_job_message(status: str) -> str:
    if status == "completed":
        return "采集 Agent 意图识别完成。"
    if status == "failed":
        return "采集 Agent 意图识别失败。"
    return "采集 Agent 正在识别意图。"


def _context_for_directions(body: CreativeDirectionsRequest, values: dict[str, Any]) -> StandardIntakeContext:
    if body.intake_context:
        source_prompt = str(body.intake_context.get("source_prompt") or "")
        extracted = {
            **body.intake_context,
            "values": values,
            "requested_output_count": body.intake_context.get("requested_output_count") or values.get("image_count"),
        }
        return normalize_intake_context(intent=body.intent, source_prompt=source_prompt, extracted=extracted)
    return normalize_intake_context(
        intent=body.intent,
        source_prompt="",
        extracted={
            "product_creative_profile": body.product_creative_profile,
            "values": values,
            "requested_output_count": values.get("image_count"),
        },
    )


def _brand_memory_summary(intake_context: dict[str, Any]) -> str:
    subject = str(intake_context.get("product_subject") or "").strip()
    goal = str(intake_context.get("creation_goal") or "").strip()
    industry = str(intake_context.get("industry_type") or "").strip()
    if not any([subject, goal, industry]):
        return ""
    return f"用户创作上下文：产品/品牌主体={subject or '未识别'}；创作目标={goal or '未识别'}；行业={industry or 'general'}"
