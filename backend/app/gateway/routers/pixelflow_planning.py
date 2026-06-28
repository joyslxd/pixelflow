"""PixelFlow v2 策划 plan.md API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator, model_validator

from pixelflow.creative.plan_markdown import CreationIntent, build_plan_markdown

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
    review_timeout_sec: int = 30


@router.post("/plan", response_model=PlanMarkdownResponse)
async def create_plan_markdown(body: PlanMarkdownRequest) -> PlanMarkdownResponse:
    result = build_plan_markdown(
        body.intent,
        body.form_values,
        body.selected_direction,
        body.product_creative_profile,
        body.materials,
        body.intake_context,
    )
    return PlanMarkdownResponse(**result.to_dict())
