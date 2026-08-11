"""DeepSeek VideoAgent 的严格结构化模型边界。"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.skills import SkillManifest
from pixelflow.video_agent.tools import VideoToolSpec

_PLAN_OUTPUT_EXAMPLE = (
    '{"public_goal":"导入成熟脚本","steps":'
    '[{"tool_name":"import_script","title":"导入脚本","arguments":{}}]}'
)


class _PlanningContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class VideoPlanStepProposal(_PlanningContract):
    tool_name: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class VideoPlanProposal(_PlanningContract):
    public_goal: str = Field(min_length=1, max_length=2_000)
    steps: tuple[VideoPlanStepProposal, ...] = Field(min_length=1, max_length=3)


class VideoAgentPlanningContext(_PlanningContract):
    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    turn_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=20_000)
    artifact_refs: tuple[str, ...] = ()
    materials: tuple[dict[str, JsonValue], ...] = ()
    workspace: VideoWorkspace
    workspace_digest: dict[str, JsonValue] = Field(default_factory=dict)
    operation_summaries: tuple[dict[str, JsonValue], ...] = ()
    blocking_confirmation: dict[str, JsonValue] | None = None


class VideoPlanningModel(Protocol):
    async def propose(
        self,
        context: VideoAgentPlanningContext,
        tool_specs: Sequence[VideoToolSpec],
        skill_manifests: Sequence[SkillManifest],
        feedback: str | None,
    ) -> VideoPlanProposal: ...


def _bind_structured_planner(model: Any) -> Any:
    """优先 json_schema；供应商不支持时回退默认绑定。"""

    binder = getattr(model, "with_structured_output", None)
    if binder is None:
        raise TypeError("规划模型缺少 with_structured_output")
    try:
        return binder(VideoPlanProposal, method="json_schema")
    except TypeError:
        return binder(VideoPlanProposal)
    except Exception:
        return binder(VideoPlanProposal)


def _coerce_plan_proposal(result: Any) -> VideoPlanProposal:
    """把结构化输出结果收敛为 VideoPlanProposal；形态不对时抛 ValidationError 供修复重试。"""

    if isinstance(result, VideoPlanProposal):
        return result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            # 交给 model_validate 生成统一的 ValidationError
            pass
    return VideoPlanProposal.model_validate(result)


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
        structured = _bind_structured_planner(model)
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
            "你是 PixelFlow VideoAgent 规划器。只输出短计划 JSON 对象，不输出思维链、"
            "不输出数组、不输出 Markdown。"
            "每轮最多 3 个可执行步骤，只能选择给定已注册工具。"
            "输出必须严格符合："
            f"{_PLAN_OUTPUT_EXAMPLE}。"
            "不要一次性铺开完整脚本流水线或成片流程；优先最小可推进的下一步。"
            "用户贴入成熟脚本或要求导入脚本时，优先 import_script。"
            "若存在 blocking_confirmation，只能规划 inspect_video_workspace 或公开澄清，"
            "不得绕过确认、额度或权限闸门。"
            "计费和破坏性步骤仍由服务端确认闸门控制。"
            "用户确认脚本并生成资产包时优先 prepare_scene_packages；"
            "确认生图模型或继续生成失败参考图时用 generate_scene_assets；"
            "若 workspace_digest.failed_scene_asset_count>0 且用户说继续/重试失败参考图，"
            "应规划 generate_scene_assets（可由后续确认闸门执行）。"
            "用户修改第 N 镜/指定分镜时优先 patch_scene；"
            "确认生成或重生成已修改分镜时用 generate_scenes，"
            "且应只覆盖 dirty_scene_ids 或用户明确点名的镜头，禁止整包重做未改镜头。"
        )
        user_payload = {
            "request": context.content,
            "artifact_refs": list(context.artifact_refs),
            "materials": list(context.materials),
            "workspace_id": context.workspace.workspace_id,
            "workspace_digest": context.workspace_digest,
            "operation_summaries": list(context.operation_summaries),
            "blocking_confirmation": context.blocking_confirmation,
            "tools": tools,
            "skills": skills,
            "repair_feedback": feedback,
            "output_schema": VideoPlanProposal.model_json_schema(),
            "output_example": json.loads(_PLAN_OUTPUT_EXAMPLE),
        }
        result = await structured.ainvoke(
            [
                ("system", system_prompt),
                ("human", json.dumps(user_payload, ensure_ascii=False)),
            ]
        )
        return _coerce_plan_proposal(result)
