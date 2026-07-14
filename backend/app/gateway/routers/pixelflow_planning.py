"""PixelFlow v2 策划 plan.md API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.gateway.pixelflow_memory import concise_result_summary, current_user_id, power_mem_service, record_power_mem_background, search_power_mem
from pixelflow.creative.plan_markdown import (
    CreationIntent,
    build_plan_markdown_with_llm,
    publish_manual_plan_edit,
    restore_plan_version,
    revise_plan_markdown_with_llm,
)
from pixelflow.memory import with_semantic_memory

router = APIRouter(prefix="/agent/flows/planning", tags=["pixelflow-flows"])


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
    llm_used: bool = False
    model_name: str = "deepseek-v4-pro"
    error: str | None = None
    restored_from_version: int | None = None


class PlanRevisionRequest(PlanMarkdownRequest):
    current_plan_markdown: str
    current_plan_version: int = Field(default=1, ge=1)
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    revision_feedback: str = Field(min_length=1)
    creation_contract: dict[str, Any] = Field(default_factory=dict)


class PlanRestoreRequest(BaseModel):
    intent: CreationIntent
    current_plan_markdown: str
    current_plan_version: int = Field(ge=1)
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    restore_version: int = Field(ge=1)
    creation_contract: dict[str, Any] = Field(default_factory=dict)
    scene_durations_sec: list[int] = Field(default_factory=list)

    @field_validator("intent", mode="before")
    @classmethod
    def normalize_intent_alias(cls, value: Any) -> Any:
        return PlanMarkdownRequest.normalize_intent_alias(value)


class PlanManualEditRequest(BaseModel):
    intent: CreationIntent
    edited_plan_markdown: str = Field(min_length=1, max_length=100_000)
    current_plan_version: int = Field(default=1, ge=1)
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    creation_contract: dict[str, Any] = Field(default_factory=dict)
    scene_durations_sec: list[int] = Field(default_factory=list)

    @field_validator("intent", mode="before")
    @classmethod
    def normalize_intent_alias(cls, value: Any) -> Any:
        return PlanMarkdownRequest.normalize_intent_alias(value)


@router.post("/plan", response_model=PlanMarkdownResponse)
async def create_plan_markdown(body: PlanMarkdownRequest, request: Request) -> PlanMarkdownResponse:
    user_id, memories = await search_power_mem(
        request,
        source_agent="planning_agent",
        query_values=[body.form_values, body.selected_direction, body.product_creative_profile, body.intake_context, body.materials],
        categories=["preference", "brand", "skill", "experience"],
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
        power_mem_service(request),
        user_id=user_id,
        content=concise_result_summary(
            "策划 Agent 生成 plan.md",
            {"intent": body.intent, "message": f"issues={len(result.consistency_issues)}", "ok": not result.consistency_issues},
        ),
        category="experience",
        source_agent="planning_agent",
        metadata={"source": "planning_plan", "intent": body.intent, "consistency_issues": result.consistency_issues},
        memory_type="experience",
        infer=False,
    )
    return PlanMarkdownResponse(**result.to_dict())


@router.post("/plan/revise", response_model=PlanMarkdownResponse)
async def revise_plan_markdown(body: PlanRevisionRequest, request: Request) -> PlanMarkdownResponse:
    user_id, _memories = await search_power_mem(
        request,
        source_agent="planning_agent",
        query_values=[body.form_values, body.selected_direction, body.current_plan_markdown, body.revision_feedback],
        categories=["preference", "brand", "skill", "experience"],
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
    )
    record_power_mem_background(
        power_mem_service(request),
        user_id=user_id,
        content=concise_result_summary(
            "策划 Agent 修订 plan.md",
            {"intent": body.intent, "message": f"version={result.plan_version}", "ok": not result.error},
        ),
        category="experience",
        source_agent="planning_agent",
        metadata={"source": "planning_plan_revision", "intent": body.intent, "plan_version": result.plan_version},
        memory_type="experience",
        infer=False,
    )
    return PlanMarkdownResponse(**result.to_dict())


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
    )
    return PlanMarkdownResponse(**result.to_dict())


@router.post("/plan/save-edit", response_model=PlanMarkdownResponse)
async def save_manual_plan_edit(body: PlanManualEditRequest, request: Request) -> PlanMarkdownResponse:
    result = publish_manual_plan_edit(
        intent=body.intent,
        edited_plan_markdown=body.edited_plan_markdown,
        current_plan_version=body.current_plan_version,
        plan_history=body.plan_history,
        creation_contract=body.creation_contract,
        scene_durations_sec=body.scene_durations_sec,
    )
    user_id = await current_user_id(request)
    record_power_mem_background(
        power_mem_service(request),
        user_id=user_id,
        content=concise_result_summary(
            "用户手工发布 plan.md",
            {"intent": body.intent, "message": f"version={result.plan_version}", "ok": True},
        ),
        category="experience",
        source_agent="planning_agent",
        metadata={"source": "planning_plan_manual_edit", "intent": body.intent, "plan_version": result.plan_version},
        memory_type="experience",
        infer=False,
    )
    return PlanMarkdownResponse(**result.to_dict())
