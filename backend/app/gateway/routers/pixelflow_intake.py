"""PixelFlow v2 采集表单与创意方向 API。

这个 router 是采集阶段的 Controller：返回表单 schema、校验补表结果、生成
确定性的 3 个创意方向草稿。真正流程编排后续仍应落在 LangGraph node/Agent，
这里先提供前端和第三方可调用的 `/agent` 契约。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.pixelflow_memory import concise_result_summary, power_mem_service, record_power_mem_background, search_power_mem
from pixelflow.intake.context import IntakeContext as StandardIntakeContext
from pixelflow.intake.context import normalize_intake_context
from pixelflow.intake.forms import CreationIntent, get_form_schema, validate_form
from pixelflow.intake.industry_profile import resolve_industry_profile
from pixelflow.intake.llm import IntakeIntent, draft_creative_directions_with_llm, recognize_intent_with_llm
from pixelflow.memory import with_semantic_memory

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


@router.get("/forms/{intent}")
async def get_intake_form(intent: CreationIntent) -> dict[str, Any]:
    return get_form_schema(intent).to_dict()


@router.post("/analyze", response_model=IntentAnalyzeResponse)
async def analyze_intake_intent(body: IntentAnalyzeRequest, request: Request) -> IntentAnalyzeResponse:
    result = await recognize_intent_with_llm(body.prompt, body.materials)
    data = result.to_dict()
    user_id, memories = await search_power_mem(
        request,
        source_agent="intake_agent",
        query_values=[body.prompt, body.materials, data.get("values"), data.get("intake_context")],
        categories=["preference", "brand", "skill"],
    )
    if memories:
        context, profile = with_semantic_memory(data.get("intake_context"), memories)
        data["intake_context"] = context
        values = dict(data.get("values") or {})
        values["semantic_memory_context"] = context.get("semantic_memory")
        data["values"] = values
        if profile:
            context["product_creative_profile"] = profile
    service = power_mem_service(request)
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
    user_id, memories = await search_power_mem(
        request,
        source_agent="creative_direction_agent",
        query_values=[context.to_dict(), validation.values, product_creative_profile, body.materials],
        categories=["preference", "brand", "skill", "experience"],
    )
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
        power_mem_service(request),
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
