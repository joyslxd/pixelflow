"""DeepSeek VideoAgent 的严格结构化模型边界。"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.skills import SkillManifest
from pixelflow.video_agent.tools import VideoToolSpec


class _PlanningContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class VideoPlanStepProposal(_PlanningContract):
    tool_name: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class VideoPlanProposal(_PlanningContract):
    public_goal: str = Field(min_length=1, max_length=2_000)
    steps: tuple[VideoPlanStepProposal, ...] = Field(min_length=1, max_length=8)


class VideoAgentPlanningContext(_PlanningContract):
    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    turn_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=20_000)
    artifact_refs: tuple[str, ...] = ()
    materials: tuple[dict[str, JsonValue], ...] = ()
    workspace: VideoWorkspace


class VideoPlanningModel(Protocol):
    async def propose(
        self,
        context: VideoAgentPlanningContext,
        tool_specs: Sequence[VideoToolSpec],
        skill_manifests: Sequence[SkillManifest],
        feedback: str | None,
    ) -> VideoPlanProposal: ...


class DeepSeekVideoPlanningModel:
    """通过项目模型工厂调用 deepseek-v4-pro 的结构化输出。"""

    def __init__(
        self,
        *,
        app_config: object | None = None,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._app_config = app_config
        self._model_factory = model_factory

    async def propose(
        self,
        context: VideoAgentPlanningContext,
        tool_specs: Sequence[VideoToolSpec],
        skill_manifests: Sequence[SkillManifest],
        feedback: str | None,
    ) -> VideoPlanProposal:
        model_factory = self._model_factory
        if model_factory is None:
            from deerflow.models import create_chat_model

            model_factory = create_chat_model
        model = model_factory(
            name="deepseek-v4-pro",
            thinking_enabled=False,
            app_config=self._app_config,
        )
        structured = model.with_structured_output(VideoPlanProposal)
        tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "cost_level": spec.cost_level.value,
                "confirmation_required": spec.confirmation_required,
            }
            for spec in tool_specs
        ]
        skills = [
            {
                "name": manifest.name,
                "description": manifest.description,
                "allowed_tools": manifest.allowed_tools,
            }
            for manifest in skill_manifests
        ]
        system_prompt = (
            "你是 PixelFlow VideoAgent 规划器。只输出短计划，不输出思维链。"
            "只能选择给定工具，最多八步；计费和破坏性步骤仍由服务端确认闸门控制。"
        )
        user_payload = {
            "request": context.content,
            "artifact_refs": list(context.artifact_refs),
            "materials": list(context.materials),
            "workspace_id": context.workspace.workspace_id,
            "workspace_product_info": (
                context.workspace.payload.get("product_info")
                if isinstance(context.workspace.payload, dict)
                else None
            ),
            "tools": tools,
            "skills": skills,
            "repair_feedback": feedback,
        }
        result = await structured.ainvoke(
            [
                ("system", system_prompt),
                ("human", json.dumps(user_payload, ensure_ascii=False)),
            ]
        )
        return VideoPlanProposal.model_validate(result)
