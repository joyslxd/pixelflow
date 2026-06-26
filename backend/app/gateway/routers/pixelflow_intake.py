"""PixelFlow v2 采集表单与创意方向 API。

这个 router 是采集阶段的 Controller：返回表单 schema、校验补表结果、生成
确定性的 3 个创意方向草稿。真正流程编排后续仍应落在 LangGraph node/Agent，
这里先提供前端和第三方可调用的 `/agent` 契约。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pixelflow.intake.forms import CreationIntent, get_form_schema, validate_form
from pixelflow.intake.llm import IntakeIntent, draft_creative_directions_with_llm, recognize_intent_with_llm

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
    materials: list[dict[str, Any]] = Field(default_factory=list)


class CreativeDirectionsResponse(BaseModel):
    validation: IntakeValidationResponse
    creative_directions: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/forms/{intent}")
async def get_intake_form(intent: CreationIntent) -> dict[str, Any]:
    return get_form_schema(intent).to_dict()


@router.post("/analyze", response_model=IntentAnalyzeResponse)
async def analyze_intake_intent(body: IntentAnalyzeRequest) -> IntentAnalyzeResponse:
    result = await recognize_intent_with_llm(body.prompt, body.materials)
    return IntentAnalyzeResponse(**result.to_dict())


@router.post("/validate", response_model=IntakeValidationResponse)
async def validate_intake_form(body: IntakeValidateRequest) -> IntakeValidationResponse:
    result = validate_form(body.intent, body.values, body.intake_rounds)
    data = result.to_dict()
    data["form_schema"] = data.pop("schema")
    return IntakeValidationResponse(**data, creative_directions=[])


@router.post("/directions", response_model=CreativeDirectionsResponse)
async def create_creative_directions(body: CreativeDirectionsRequest) -> CreativeDirectionsResponse:
    validation = validate_form(body.intent, body.values, body.intake_rounds)
    data = validation.to_dict()
    data["form_schema"] = data.pop("schema")
    validation_response = IntakeValidationResponse(**data, creative_directions=[])
    if not validation.is_complete or validation.terminated:
        return CreativeDirectionsResponse(validation=validation_response, creative_directions=[])
    product_creative_profile = dict(body.product_creative_profile)
    if body.materials:
        product_creative_profile["materials"] = body.materials
    directions = [
        direction.to_dict()
        for direction in await draft_creative_directions_with_llm(
            body.intent,
            validation.values,
            product_creative_profile,
        )
    ]
    return CreativeDirectionsResponse(validation=validation_response, creative_directions=directions)
