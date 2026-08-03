"""视频 live Handler 与 v2 Router 共享的 Application 能力端口。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
import secrets
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from types import CoroutineType
from typing import Any, Protocol
from weakref import WeakKeyDictionary

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

logger = logging.getLogger(__name__)
_BACKGROUND_MEMORY_RECORD_TASKS: set[asyncio.Future[Any]] = set()


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

    def record_background(
        self,
        *,
        summary: str,
        category: str,
        metadata: Mapping[str, Any],
    ) -> None: ...


class Clock(Protocol):
    """提供可测试时间的最小端口。"""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class TransientTurnCredential:
    """只在当前 Turn 的付费 Skill 边界短暂使用的登录凭据。"""

    authorization: _OpaqueAuthorization = field(repr=False)

    def __init__(self, authorization: str) -> None:
        if not isinstance(authorization, str) or not authorization.strip():
            raise ValueError("当前 Turn 缺少临时 Authorization")
        handle = secrets.token_urlsafe(24)
        object.__setattr__(self, "authorization", _OpaqueAuthorization(handle))
        with _TRANSIENT_CREDENTIAL_LOCK:
            _TRANSIENT_CREDENTIAL_SECRETS[self] = authorization.strip()

    def discard(self) -> None:
        """显式清理尚未消费的临时凭据。"""

        _discard_transient_credential(self)

    def __copy__(self) -> None:
        raise TypeError("临时凭据禁止复制")

    def __deepcopy__(self, _memo: dict[int, Any]) -> None:
        raise TypeError("临时凭据禁止复制")

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
        self._record_background(
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
        self._record_background(
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
        self._record_background(
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
        self._record_background(
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
        contract = state.scene_package.creation_contract
        from app.gateway.content_app_auth_context import (
            reset_current_content_app_auth,
            set_current_content_app_auth,
        )

        authorization = _consume_authorization_for_skill_boundary(credential)
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
            self._record_background(
                summary="视频 live 能力执行场景资产生成失败",
                metadata={"stage": "generate_scene_assets", "ok": False},
            )
            raise RuntimeError("场景资产生成失败") from None
        try:
            safe_result = _safe_json_projection(result, authorization=authorization)
        except Exception:  # noqa: BLE001 - DTO 转换异常也不得越过安全投影边界
            self._record_background(
                summary="视频 live 能力拒绝了不安全的场景资产结果",
                metadata={"stage": "generate_scene_assets", "ok": False},
            )
            raise RuntimeError("场景资产结果未通过安全校验") from None
        self._record_background(
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
        except Exception as error:  # noqa: BLE001 - PowerMem 按既有合同 fail-open
            _log_memory_failure("search", error)
            memories = []
        raw_context = dict(intake_context)
        raw_profile = raw_context.get("product_creative_profile")
        profile = dict(raw_profile) if isinstance(raw_profile, Mapping) else {}
        return with_semantic_memory(
            raw_context,
            memories,
            product_creative_profile=profile,
        )

    def _record_background(self, *, summary: str, metadata: Mapping[str, Any]) -> None:
        try:
            safe_metadata = dict(metadata)
            safe_metadata["recorded_at"] = self._clock.now().isoformat()
            schedule_result = self._memory_record.record_background(
                summary=summary,
                category="experience",
                metadata=safe_metadata,
            )
            if schedule_result is not None:
                if inspect.isawaitable(schedule_result):
                    _schedule_memory_record_awaitable(schedule_result)
                    return
                raise TypeError("PowerMem 后台记录端口返回了非 awaitable 的非空值")
        except Exception as error:  # noqa: BLE001 - PowerMem 记录失败不得阻断主流程
            _log_memory_failure("record_background", error)


def _log_memory_failure(operation: str, error: BaseException) -> None:
    """只记录安全诊断维度，不包含异常文本或业务内容。"""

    logger.warning(
        "PowerMem 操作失败：operation=%s exception_type=%s",
        operation,
        type(error).__name__,
    )


def _schedule_memory_record_awaitable(value: Any) -> None:
    """把误返回的 awaitable 纳入当前事件循环，用户路径不等待结果。"""

    try:
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(value, loop=loop)
    except Exception as error:  # noqa: BLE001 - 调度失败按安全元数据 fail-open
        if type(value) is CoroutineType:
            value.close()
        _log_memory_failure("record_background_schedule", error)
        return
    _BACKGROUND_MEMORY_RECORD_TASKS.add(task)
    task.add_done_callback(_consume_memory_record_result)


def _consume_memory_record_result(task: asyncio.Future[Any]) -> None:
    """消费后台结果和异常，避免 Task exception 泄漏到事件循环。"""

    _BACKGROUND_MEMORY_RECORD_TASKS.discard(task)
    try:
        if task.cancelled():
            return
        error = task.exception()
    except asyncio.CancelledError:
        return
    except BaseException as error:  # noqa: BLE001 - done callback 不得向事件循环抛错
        _log_memory_callback_failure(error)
        return
    if error is not None:
        _log_memory_callback_failure(error)


def _log_memory_callback_failure(error: BaseException) -> None:
    """保证 done callback 的安全诊断本身不会逃逸到事件循环。"""

    try:
        _log_memory_failure("record_background_async", error)
    except BaseException:  # noqa: BLE001 - 日志基础设施异常也不得破坏回调
        return


def _context_materials(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = context.get("materials")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


class _OpaqueAuthorization:
    """通用转换只能看到固定脱敏文本的不透明 Authorization。"""

    __slots__ = ("_handle",)

    def __init__(self, handle: str) -> None:
        object.__setattr__(self, "_handle", handle)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise TypeError("临时凭据禁止修改")

    def __str__(self) -> str:
        return "[已脱敏临时凭据]"

    def __repr__(self) -> str:
        return "[已脱敏临时凭据]"

    def __format__(self, _format_spec: str) -> str:
        return "[已脱敏临时凭据]"

    def __copy__(self) -> None:
        raise TypeError("临时凭据禁止复制")

    def __deepcopy__(self, _memo: dict[int, Any]) -> None:
        raise TypeError("临时凭据禁止复制")

    def __getstate__(self) -> None:
        raise TypeError("临时凭据禁止序列化")

    def __reduce_ex__(self, _protocol: int) -> None:
        raise TypeError("临时凭据禁止序列化")


_TRANSIENT_CREDENTIAL_LOCK = RLock()
_TRANSIENT_CREDENTIAL_SECRETS: WeakKeyDictionary[TransientTurnCredential, str] = (
    WeakKeyDictionary()
)


def _consume_authorization_for_skill_boundary(
    credential: TransientTurnCredential,
) -> str:
    """在付费 Skill 边界原子消费一次临时 Authorization。"""

    with _TRANSIENT_CREDENTIAL_LOCK:
        authorization = _TRANSIENT_CREDENTIAL_SECRETS.pop(credential, None)
    if authorization is None:
        raise RuntimeError("当前 Turn 临时凭据不可用")
    return authorization


def _consume_authorization_for_quota_resume_boundary(
    credential: TransientTurnCredential,
) -> str:
    """仅为原 Provider job 的 quota resume 原子消费一次 Authorization。"""

    if not isinstance(credential, TransientTurnCredential):
        raise TypeError("credential 必须是 TransientTurnCredential")
    with _TRANSIENT_CREDENTIAL_LOCK:
        authorization = _TRANSIENT_CREDENTIAL_SECRETS.pop(credential, None)
    if authorization is None:
        raise RuntimeError("当前 Turn 临时凭据不可用")
    return authorization


def _borrow_authorization_for_operation_boundary(
    credential: TransientTurnCredential,
) -> str:
    """仅在 M06 start 调用期间借用凭据，由调用方在结束后立即清理。"""

    if not isinstance(credential, TransientTurnCredential):
        raise TypeError("credential 必须是 TransientTurnCredential")
    with _TRANSIENT_CREDENTIAL_LOCK:
        authorization = _TRANSIENT_CREDENTIAL_SECRETS.get(credential)
    if authorization is None:
        raise RuntimeError("当前 Turn 临时凭据不可用")
    return authorization


def _discard_transient_credential(credential: TransientTurnCredential) -> None:
    """清理未消费的临时 Authorization，重复调用保持幂等。"""

    with _TRANSIENT_CREDENTIAL_LOCK:
        _TRANSIENT_CREDENTIAL_SECRETS.pop(credential, None)


class _UnsafeCapabilityOutput(ValueError):
    """标记供应商结果无法安全投影；异常文本不得携带原始值。"""


_PROTOCOL_KEY_WORD_PATTERN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+",
)
_PROTOCOL_KEY_ALLOWED_PATTERN = re.compile(r"[A-Za-z0-9_ -]*")
_CREDENTIAL_METADATA_SUFFIXES = (
    ("expires", "at"),
    ("expiry", "at"),
    ("count",),
    ("usage",),
    ("limit",),
    ("ttl",),
    ("expires",),
    ("expiry",),
    ("status",),
    ("enabled",),
)
_CREDENTIAL_WORDS = frozenset(
    {
        "authorization",
        "auth",
        "bearer",
        "oauth",
        "token",
        "secret",
        "cookie",
        "credential",
        "password",
        "session",
    }
)
_CREDENTIAL_WORD_PLURALS = {
    "tokens": "token",
    "secrets": "secret",
    "credentials": "credential",
    "cookies": "cookie",
    "passwords": "password",
    "sessions": "session",
    "keys": "key",
}
_CREDENTIAL_KEY_QUALIFIERS = frozenset(
    {
        "api",
        "private",
        "public",
        "access",
        "client",
        "account",
        "subscription",
        "service",
        "provider",
        "signing",
        "encryption",
        "ssh",
    }
)
_CREDENTIAL_DECORATION_SUFFIXES = ("header", "value", "id", "hash", "material")
_CREDENTIAL_COMPACT_METADATA_SUFFIXES = (
    "expiresat",
    "expiryat",
    "count",
    "usage",
    "limit",
    "ttl",
    "expires",
    "expiry",
    "status",
    "enabled",
)
_BEARER_PATTERN = re.compile(r"(?i)(?:^|[\s\"'])bearer\s+[A-Za-z0-9._~+/=-]{6,}")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_API_SECRET_PATTERN = re.compile(r"\b(?:sk|rk|pk|api)[-_][A-Za-z0-9_-]{12,}\b", re.IGNORECASE)


def _normalize_protocol_key(key: str) -> str:
    """规范协议字段字符域，避免 Unicode 混淆键绕过词法检测。"""

    if type(key) is not str:
        raise _UnsafeCapabilityOutput("场景资产结果包含非字符串字段")
    normalized_key = unicodedata.normalize("NFKC", key)
    if not normalized_key.isascii() or _PROTOCOL_KEY_ALLOWED_PATTERN.fullmatch(normalized_key) is None:
        raise _UnsafeCapabilityOutput("场景资产结果包含非法字段")
    return normalized_key


def _normalize_protocol_word(word: str) -> str:
    return _CREDENTIAL_WORD_PLURALS.get(word, word)


def _protocol_key_words(key: str) -> tuple[str, ...]:
    """按连接符和大小写边界拆分协议字段词，不依赖有限字段名枚举。"""

    words: list[str] = []
    for component in re.split(r"[^A-Za-z0-9]+", key):
        words.extend(match.group(0).casefold() for match in _PROTOCOL_KEY_WORD_PATTERN.finditer(component))

    merged_words: list[str] = []
    index = 0
    while index < len(words):
        if words[index : index + 2] == ["o", "auth"]:
            merged_words.append("oauth")
            index += 2
            continue
        merged_words.append(words[index])
        index += 1
    return tuple(_normalize_protocol_word(word) for word in merged_words)


def _normalize_fused_credential_plural(component: str) -> str:
    for plural, singular in _CREDENTIAL_WORD_PLURALS.items():
        if component.endswith(plural):
            return f"{component[: -len(plural)]}{singular}"
    return component


def _has_fused_credential_semantics(component: str) -> bool:
    component = _normalize_fused_credential_plural(component)
    if any(component.endswith(word) for word in _CREDENTIAL_WORDS):
        return True
    if any(component.endswith(f"{qualifier}key") for qualifier in _CREDENTIAL_KEY_QUALIFIERS):
        return True
    for suffix in _CREDENTIAL_DECORATION_SUFFIXES:
        if component.endswith(suffix):
            undecorated = component[: -len(suffix)]
            if undecorated == "key" or _has_fused_credential_semantics(undecorated):
                return True
    return False


def _has_credential_metadata_suffix(words: tuple[str, ...]) -> bool:
    if any(
        len(words) >= len(suffix) and words[-len(suffix) :] == suffix
        for suffix in _CREDENTIAL_METADATA_SUFFIXES
    ):
        return True
    if not words:
        return False
    compact_component = words[-1]
    return any(
        compact_component.endswith(suffix)
        and _has_fused_credential_semantics(compact_component[: -len(suffix)])
        for suffix in _CREDENTIAL_COMPACT_METADATA_SUFFIXES
    )


def _is_sensitive_protocol_key(key: str) -> bool:
    words = _protocol_key_words(key)
    if not words or _has_credential_metadata_suffix(words):
        return False
    word_set = frozenset(words)
    if word_set & _CREDENTIAL_WORDS:
        return True
    if words == ("key",):
        return True
    if "key" in word_set and word_set & _CREDENTIAL_KEY_QUALIFIERS:
        return True
    if any(
        first == "key" and second in _CREDENTIAL_DECORATION_SUFFIXES
        for first, second in zip(words, words[1:], strict=False)
    ):
        return True
    return any(_has_fused_credential_semantics(word) for word in words)


def _safe_json_projection(value: Any, *, authorization: str) -> Any:
    """将结果投影为安全 JSON；无法完整验证时固定失败。"""

    secret_values = {authorization}
    bearer_match = re.fullmatch(r"(?i)bearer\s+(.+)", authorization.strip())
    if bearer_match is not None:
        secret_values.add(bearer_match.group(1))

    def project(item: Any) -> Any:
        item_type = type(item)
        if item_type is dict:
            projected: dict[str, Any] = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise _UnsafeCapabilityOutput("场景资产结果包含非字符串字段")
                normalized_key = _normalize_protocol_key(key)
                if _is_sensitive_protocol_key(normalized_key):
                    raise _UnsafeCapabilityOutput("场景资产结果包含敏感字段")
                projected[key] = project(child)
            return projected
        if item_type is list or item_type is tuple:
            return [project(child) for child in item]
        if item is None or item_type is bool or item_type is int:
            return item
        if item_type is float:
            if not math.isfinite(item):
                raise _UnsafeCapabilityOutput("场景资产结果包含非有限数值")
            return item
        if item_type is str:
            if any(secret and secret in item for secret in secret_values):
                raise _UnsafeCapabilityOutput("场景资产结果包含当前临时凭据")
            if _BEARER_PATTERN.search(item) or _JWT_PATTERN.search(item) or _API_SECRET_PATTERN.search(item):
                raise _UnsafeCapabilityOutput("场景资产结果包含疑似凭据")
            return item
        raise _UnsafeCapabilityOutput("场景资产结果包含不支持的对象")

    return project(value)


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
