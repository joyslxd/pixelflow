"""视频 live Handler 与 v2 Router 共享的 Application 能力端口。"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from pixelflow.agent_runtime.contracts import WorkflowStatus
from pixelflow.creative.plan_llm import PLAN_LLM_MODEL_NAME
from pixelflow.creative.plan_markdown import (
    PlanMarkdownResult,
    build_plan_markdown_with_llm,
    restore_plan_version,
    revise_plan_markdown_with_llm,
)
from pixelflow.intake.forms import CreationIntent, CreativeDirection, FormValidationResult, validate_form
from pixelflow.intake.llm import INTAKE_LLM_MODEL_NAME, draft_creative_directions_with_llm
from pixelflow.memory import with_semantic_memory
from pixelflow.skills.base import ImageGenerationSkill, is_quota_insufficient

from .planning import VideoPlanningStage, VideoPlanningWorkflowService, VideoPlanningWorkflowState
from .scene_packages import (
    VideoScenePackageStage,
    VideoScenePackageWorkflowService,
    VideoScenePackageWorkflowState,
)


class ChatModelFactory(Protocol):
    """创建现有聊天模型 Client 的最小端口。"""

    def __call__(self, model_name: str, *, attach_tracing: bool = False) -> Any: ...


class SceneAssetImageSkill(ImageGenerationSkill, Protocol):
    """场景资产生成实际需要的图片 Skill 端口。"""


class MemorySearchPort(Protocol):
    """读取 live Turn 可复用语义记忆的最小端口。"""

    async def search(
        self,
        *,
        query_values: Sequence[Any],
        categories: Sequence[str],
    ) -> Sequence[Any]: ...


class MemoryRecordPort(Protocol):
    """记录安全阶段摘要的最小端口。"""

    def record(
        self,
        *,
        summary: str,
        category: str,
        metadata: Mapping[str, Any],
    ) -> Awaitable[None] | None: ...


class Clock(Protocol):
    """提供可测试时间的最小端口。"""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class TransientTurnCredential:
    """只在当前 Turn 的付费 Skill 边界短暂使用的登录凭据。"""

    authorization: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization", _NonSerializableAuthorization(self.authorization))

    def __getstate__(self) -> None:
        raise TypeError("临时凭据禁止序列化")

    def __reduce_ex__(self, _protocol: int) -> None:
        raise TypeError("临时凭据禁止序列化")


class TurnCredentialProvider(Protocol):
    """按 Turn 读取临时凭据，不提供枚举或持久化能力。"""

    def get(self, turn_id: str) -> TransientTurnCredential | None: ...


class VideoLiveCapabilityPort(Protocol):
    """Task 7 live Handler 只依赖的稳定视频能力合同。"""

    async def validate_intake(
        self,
        form_values: Mapping[str, Any],
        *,
        intake_rounds: int,
    ) -> FormValidationResult: ...

    async def generate_directions(
        self,
        form_values: Mapping[str, Any],
        intake_context: Mapping[str, Any],
    ) -> list[CreativeDirection]: ...

    async def generate_initial_plan(
        self,
        *,
        form_values: Mapping[str, Any],
        selected_direction: Mapping[str, Any],
        intake_context: Mapping[str, Any],
        materials: Sequence[Mapping[str, Any]],
    ) -> PlanMarkdownResult: ...

    async def revise_plan(
        self,
        state: VideoPlanningWorkflowState,
        *,
        revision_feedback: str,
    ) -> PlanMarkdownResult: ...

    async def restore_plan(
        self,
        state: VideoPlanningWorkflowState,
        *,
        plan_version: int,
    ) -> PlanMarkdownResult: ...

    async def generate_scene_assets(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        credential: TransientTurnCredential,
    ) -> Mapping[str, Any]: ...


def validate_video_application_form(
    intent: CreationIntent,
    values: dict[str, Any] | None,
    intake_rounds: int = 0,
) -> FormValidationResult:
    """共享表单 Application 函数，保持原 v2 调用签名。"""

    return validate_form(intent, values, intake_rounds)


async def generate_application_directions(
    intent: CreationIntent,
    values: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    *,
    model_name: str = INTAKE_LLM_MODEL_NAME,
    model_factory: Callable[..., Any] | None = None,
) -> list[CreativeDirection]:
    """共享创意方向 Application 函数，保持原 v2 调用签名。"""

    return await draft_creative_directions_with_llm(
        intent,
        values,
        product_creative_profile,
        model_name=model_name,
        model_factory=model_factory,
    )


async def generate_application_plan(
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
    *,
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: Callable[..., Any] | None = None,
) -> PlanMarkdownResult:
    """共享初始 Plan Application 函数，保持原 v2 调用签名。"""

    return await build_plan_markdown_with_llm(
        intent,
        form_values,
        selected_direction,
        product_creative_profile,
        materials,
        intake_context,
        model_name=model_name,
        model_factory=model_factory,
    )


async def revise_application_plan(**kwargs: Any) -> PlanMarkdownResult:
    """共享 Plan 修订 Application 函数，参数与现有核心 Service 一致。"""

    return await revise_plan_markdown_with_llm(**kwargs)


def restore_application_plan(**kwargs: Any) -> PlanMarkdownResult:
    """共享 Plan 历史恢复 Application 函数，参数与现有核心 Service 一致。"""

    return restore_plan_version(**kwargs)


async def generate_application_scene_assets(**kwargs: Any) -> dict[str, Any]:
    """共享场景资产 Application 函数，参数与现有核心 Service 一致。"""

    from pixelflow.generate.scene_assets import generate_scene_assets

    return await generate_scene_assets(**kwargs)


class DefaultVideoLiveCapabilities(VideoLiveCapabilityPort):
    """组合现有视频 Service，并隔离模型、Skill、记忆和时间依赖。"""

    def __init__(
        self,
        *,
        model_factory: ChatModelFactory,
        scene_asset_skill: SceneAssetImageSkill,
        memory_search: MemorySearchPort,
        memory_record: MemoryRecordPort,
        clock: Clock,
    ) -> None:
        self._model_factory = model_factory
        self._scene_asset_skill = scene_asset_skill
        self._memory_search = memory_search
        self._memory_record = memory_record
        self._clock = clock

    async def validate_intake(
        self,
        form_values: Mapping[str, Any],
        *,
        intake_rounds: int,
    ) -> FormValidationResult:
        return validate_video_application_form("video", dict(form_values), intake_rounds)

    async def generate_directions(
        self,
        form_values: Mapping[str, Any],
        intake_context: Mapping[str, Any],
    ) -> list[CreativeDirection]:
        values = dict(form_values)
        validation = validate_video_application_form("video", values, 0)
        if not validation.is_complete or validation.terminated:
            raise ValueError("视频需求表单不完整，不能生成创意方向")
        _context, profile = await self._memory_context(
            intake_context,
            query_values=[values, intake_context],
        )
        directions = await generate_application_directions(
            "video",
            validation.values,
            profile,
            model_factory=self._model_factory,
        )
        if len(directions) != 3:
            raise ValueError("视频创意方向必须恰好为 3 个")
        await self._record(
            summary="视频 live 能力已生成 3 个创意方向",
            metadata={"stage": "direction_generation", "direction_count": len(directions)},
        )
        return directions

    async def generate_initial_plan(
        self,
        *,
        form_values: Mapping[str, Any],
        selected_direction: Mapping[str, Any],
        intake_context: Mapping[str, Any],
        materials: Sequence[Mapping[str, Any]],
    ) -> PlanMarkdownResult:
        validation = validate_video_application_form("video", dict(form_values), 0)
        if not validation.is_complete or validation.terminated:
            raise ValueError("视频需求表单不完整，不能生成 Plan")
        direction = dict(selected_direction)
        if not direction:
            raise ValueError("生成 Plan 前必须选择创意方向")
        context, profile = await self._memory_context(
            intake_context,
            query_values=[validation.values, direction, intake_context, materials],
        )
        result = await generate_application_plan(
            "video",
            validation.values,
            direction,
            profile,
            [dict(item) for item in materials],
            context,
            model_factory=self._model_factory,
        )
        await self._record(
            summary="视频 live 能力已生成初始 Plan",
            metadata={
                "stage": "plan_generation",
                "plan_version": result.plan_version,
                "ok": result.error is None,
            },
        )
        return result

    async def revise_plan(
        self,
        state: VideoPlanningWorkflowState,
        *,
        revision_feedback: str,
    ) -> PlanMarkdownResult:
        VideoPlanningWorkflowService().validate_state(state)
        if state.current_stage is not VideoPlanningStage.PLAN_REVIEW or state.active_plan is None:
            raise ValueError("只有等待人工审核的 Plan 才能修订")
        feedback = revision_feedback.strip()
        if not feedback:
            raise ValueError("Plan 修订意见不能为空")
        context, profile = await self._memory_context(
            state.intake_context,
            query_values=[state.form_values, state.selected_direction, feedback],
        )
        active = state.active_plan
        result = await revise_application_plan(
            intent="video",
            form_values=state.form_values,
            selected_direction=state.selected_direction,
            current_plan_markdown=active.plan_markdown,
            current_plan_version=active.plan_version,
            plan_history=active.plan_history,
            revision_feedback=feedback,
            creation_contract=active.creation_contract,
            current_scene_blueprints=active.scene_blueprints,
            current_asset_manifest=active.asset_manifest,
            product_creative_profile=profile,
            materials=_context_materials(context),
            intake_context=context,
            model_factory=self._model_factory,
        )
        await self._record(
            summary="视频 live 能力已执行 Plan 修订",
            metadata={
                "stage": "plan_revision",
                "plan_version": result.plan_version,
                "ok": result.error is None,
            },
        )
        return result

    async def restore_plan(
        self,
        state: VideoPlanningWorkflowState,
        *,
        plan_version: int,
    ) -> PlanMarkdownResult:
        VideoPlanningWorkflowService().validate_state(state)
        if state.current_stage is not VideoPlanningStage.PLAN_REVIEW or state.active_plan is None:
            raise ValueError("只有等待人工审核的 Plan 才能恢复历史版本")
        active = state.active_plan
        result = restore_application_plan(
            intent="video",
            current_plan_markdown=active.plan_markdown,
            current_plan_version=active.plan_version,
            plan_history=active.plan_history,
            restore_version=plan_version,
            creation_contract=active.creation_contract,
            scene_durations_sec=active.scene_durations_sec,
            scene_blueprints=active.scene_blueprints,
            asset_manifest=active.asset_manifest,
        )
        await self._record(
            summary="视频 live 能力已恢复 Plan 历史版本",
            metadata={"stage": "plan_restore", "plan_version": result.plan_version},
        )
        return result

    async def generate_scene_assets(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        credential: TransientTurnCredential,
    ) -> Mapping[str, Any]:
        VideoScenePackageWorkflowService().to_workflow_record(state)
        if (
            state.current_stage is not VideoScenePackageStage.GENERATE_SCENE_ASSETS
            or state.status is not WorkflowStatus.RUNNING
        ):
            raise ValueError("只有场景资产生成阶段可以调用图片 Skill")
        authorization = credential.authorization.strip()
        if not authorization:
            raise ValueError("当前 Turn 缺少临时 Authorization")
        contract = state.scene_package.creation_contract
        from app.gateway.content_app_auth_context import (
            reset_current_content_app_auth,
            set_current_content_app_auth,
        )

        context_token = set_current_content_app_auth(authorization, username="")
        skill_failed = False
        result: dict[str, Any] | None = None
        try:
            try:
                result = await generate_application_scene_assets(
                    image_skill=self._scene_asset_skill,
                    global_assets=state.scene_package.global_assets,
                    scene_packages=state.scene_package.scene_packages,
                    materials=[
                        {"type": "image", "url": url}
                        for url in state.scene_package.material_image_urls
                    ],
                    image_ratio=str(contract.get("scene_image_ratio") or "1:1"),
                    image_size=str(contract.get("scene_image_size") or "1080p"),
                    model=str(contract.get("image_model") or "") or None,
                    quota_checker=is_quota_insufficient,
                    target_assets=None,
                )
            except Exception:  # noqa: BLE001 - 供应商异常必须在临时凭据边界固定化
                skill_failed = True
        finally:
            reset_current_content_app_auth(context_token)
        if skill_failed or result is None:
            await self._record(
                summary="视频 live 能力执行场景资产生成失败",
                metadata={"stage": "generate_scene_assets", "ok": False},
            )
            raise RuntimeError("场景资产生成失败") from None
        safe_result = _without_credential(result, authorization)
        await self._record(
            summary="视频 live 能力已执行场景资产生成",
            metadata={
                "stage": "generate_scene_assets",
                "ok": bool(safe_result.get("ok")),
                "failed_count": len(safe_result.get("failed_assets") or []),
            },
        )
        return safe_result

    async def _memory_context(
        self,
        intake_context: Mapping[str, Any],
        *,
        query_values: Sequence[Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            memories = list(
                await self._memory_search.search(
                    query_values=query_values,
                    categories=["preference", "brand", "skill", "experience"],
                )
            )
        except Exception:  # noqa: BLE001 - PowerMem 按既有合同 fail-open
            memories = []
        raw_context = dict(intake_context)
        raw_profile = raw_context.get("product_creative_profile")
        profile = dict(raw_profile) if isinstance(raw_profile, Mapping) else {}
        return with_semantic_memory(
            raw_context,
            memories,
            product_creative_profile=profile,
        )

    async def _record(self, *, summary: str, metadata: Mapping[str, Any]) -> None:
        safe_metadata = dict(metadata)
        safe_metadata["recorded_at"] = self._clock.now().isoformat()
        try:
            pending = self._memory_record.record(
                summary=summary,
                category="experience",
                metadata=safe_metadata,
            )
            if inspect.isawaitable(pending):
                await pending
        except Exception:  # noqa: BLE001 - PowerMem 记录失败不得阻断主流程
            return


def _context_materials(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = context.get("materials")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


class _NonSerializableAuthorization(str):
    """保留字符串调用合同，但拒绝 dataclass 深拷贝序列化。"""

    def __deepcopy__(self, _memo: dict[int, Any]) -> None:
        raise TypeError("临时凭据禁止序列化")

    def __reduce_ex__(self, _protocol: int) -> None:
        raise TypeError("临时凭据禁止序列化")


def _without_credential(value: Any, authorization: str) -> Any:
    """深度复制输出并剔除任何凭据键或意外回显。"""

    sensitive_keys = {"authorization", "token", "access_token", "api_key", "secret"}
    if isinstance(value, Mapping):
        return {
            str(key): _without_credential(item, authorization)
            for key, item in value.items()
            if str(key).strip().lower() not in sensitive_keys
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_without_credential(item, authorization) for item in value]
    if isinstance(value, str):
        return value.replace(authorization, "[已移除敏感凭据]")
    return copy.deepcopy(value)


__all__ = [
    "ChatModelFactory",
    "Clock",
    "DefaultVideoLiveCapabilities",
    "MemoryRecordPort",
    "MemorySearchPort",
    "SceneAssetImageSkill",
    "TransientTurnCredential",
    "TurnCredentialProvider",
    "VideoLiveCapabilityPort",
    "generate_application_directions",
    "generate_application_plan",
    "generate_application_scene_assets",
    "restore_application_plan",
    "revise_application_plan",
    "validate_video_application_form",
]
