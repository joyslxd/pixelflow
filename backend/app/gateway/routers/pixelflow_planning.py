"""PixelFlow v2 策划 plan.md API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.gateway.pixelflow_memory import concise_result_summary, power_mem_service, record_power_mem_background, search_power_mem
from pixelflow.creative.plan_markdown import CreationIntent, build_plan_markdown
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
    result = build_plan_markdown(
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
