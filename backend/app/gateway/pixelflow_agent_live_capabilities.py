"""把 Gateway 的公开依赖适配为视频 live Runtime 端口。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pixelflow.agent_runtime.contracts import ContextEnvelope
from pixelflow.agent_runtime.supervisor.classifier import DecisionModel
from pixelflow.agent_runtime.supervisor.decision_service import SupervisorAnswerPort
from pixelflow.agent_workflows.video.live_capabilities import (
    Clock,
    DefaultVideoLiveCapabilities,
)
from pixelflow.memory import build_memory_query

from .pixelflow_agent_live_providers import VIDEO_LIVE_HANDLER_NOT_READY

logger = logging.getLogger(__name__)
_VIDEO_LIVE_MEMORY_SOURCE = "video_live_handler"
_VIDEO_LIVE_USER_SCOPE_MISSING = "video_live_user_scope_missing"


class _LazyDecisionModel(DecisionModel):
    """首次真实分类时再创建模型，Gateway 启动阶段不触发模型调用。"""

    def __init__(self, *, model_factory: Callable[..., Any], model_name: str) -> None:
        self._model_factory = model_factory
        self._model_name = model_name
        self._model: Any | None = None

    async def ainvoke(self, messages: Sequence[tuple[str, str]]) -> object:
        model = self._get_model()
        result = await model.ainvoke(list(messages))
        return _decision_output(result)

    def _get_model(self) -> Any:
        if self._model is None:
            self._model = self._model_factory(
                self._model_name,
                attach_tracing=False,
            )
        return self._model


class _LazySupervisorAnswerPort(SupervisorAnswerPort):
    """复用现有聊天模型生成只读回答，不暴露任何工具调用入口。"""

    def __init__(self, *, model_factory: Callable[..., Any], model_name: str) -> None:
        self._model_factory = model_factory
        self._model_name = model_name
        self._model: Any | None = None

    async def answer(self, context: ContextEnvelope) -> str:
        model = self._get_model()
        payload = context.model_dump(mode="json")
        result = await model.ainvoke(
            [
                (
                    "system",
                    "你是 PixelFlow 的只读回答助手。只能根据给定上下文回答，不得调用工具或声称已执行工作流。",
                ),
                (
                    "human",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            ]
        )
        answer = _text_output(result)
        if not answer:
            raise RuntimeError("supervisor_answer_empty")
        return answer

    def _get_model(self) -> Any:
        if self._model is None:
            self._model = self._model_factory(
                self._model_name,
                attach_tracing=False,
            )
        return self._model


class PowerMemVideoLivePort:
    """在权威 WorkflowCommand 的用户作用域内复用 PowerMemService。"""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._user_id: ContextVar[str | None] = ContextVar(
            f"pixelflow_video_live_user_id_{id(self)}",
            default=None,
        )

    async def search(
        self,
        *,
        query_values: Sequence[Any],
        categories: Sequence[str],
    ) -> Sequence[Any]:
        user_id = self._required_user_id()
        query = build_memory_query(*query_values)
        if not query:
            return ()
        return await self._service.search(
            user_id=user_id,
            query=query,
            categories=list(categories),
            source_agent=None,
            limit=None,
        )

    def record_background(
        self,
        *,
        summary: str,
        category: str,
        metadata: Mapping[str, Any],
    ) -> None:
        # 在创建后台协程前复制当前 ContextVar，避免 dispatch 返回后作用域丢失。
        user_id = self._required_user_id()
        from app.gateway.pixelflow_memory import record_power_mem_background

        record_power_mem_background(
            self._service,
            user_id=user_id,
            content=summary,
            category=category,
            source_agent=_VIDEO_LIVE_MEMORY_SOURCE,
            metadata=dict(metadata),
            memory_type=category,
            infer=False,
        )

    def scope_handler(self, handler: Any) -> Any:
        return _UserScopedLiveHandler(handler=handler, memory_port=self)

    def _required_user_id(self) -> str:
        user_id = self._user_id.get()
        if user_id is None:
            raise RuntimeError(_VIDEO_LIVE_USER_SCOPE_MISSING)
        return user_id


class _UserScopedLiveHandler:
    """以异步任务本地 ContextVar 把命令 owner 传给记忆端口。"""

    def __init__(self, *, handler: Any, memory_port: PowerMemVideoLivePort) -> None:
        self._handler = handler
        self._memory_port = memory_port

    async def dispatch(self, command: Any) -> Any:
        raw_user_id = getattr(command, "user_id", None)
        if not isinstance(raw_user_id, str) or not raw_user_id.strip():
            raise RuntimeError(_VIDEO_LIVE_USER_SCOPE_MISSING)
        token = self._memory_port._user_id.set(raw_user_id.strip())
        try:
            return await self._handler.dispatch(command)
        finally:
            self._memory_port._user_id.reset(token)


@dataclass(frozen=True, slots=True)
class GatewayVideoLiveCapabilities:
    """汇总真实 live 能力端口及其固定 fail-closed 状态。"""

    capabilities: DefaultVideoLiveCapabilities | None
    decision_model: DecisionModel | None
    answer_port: SupervisorAnswerPort | None
    memory_port: PowerMemVideoLivePort | None
    reason_code: str | None

    @property
    def ready(self) -> bool:
        return (
            self.reason_code is None
            and self.capabilities is not None
            and self.decision_model is not None
            and self.answer_port is not None
            and self.memory_port is not None
        )

    def scope_handler(self, handler: Any) -> Any:
        if self.memory_port is None:
            raise RuntimeError(VIDEO_LIVE_HANDLER_NOT_READY)
        return self.memory_port.scope_handler(handler)


def make_pixelflow_agent_live_capabilities(
    *,
    model_factory: Callable[..., Any] | None,
    scene_asset_skill_factory: Callable[[], Any] | None,
    power_mem_service: Any | None,
    clock: Clock | None,
    model_name: str,
) -> GatewayVideoLiveCapabilities:
    """仅在全部公开依赖齐备时构造真实能力；其余情况固定降级。"""

    if (
        not callable(model_factory)
        or not callable(scene_asset_skill_factory)
        or power_mem_service is None
        or not callable(getattr(power_mem_service, "search", None))
        or not callable(getattr(power_mem_service, "record", None))
        or clock is None
        or not callable(getattr(clock, "now", None))
        or not isinstance(model_name, str)
        or not model_name.strip()
    ):
        return _not_ready_bundle()
    try:
        scene_asset_skill = scene_asset_skill_factory()
        if scene_asset_skill is None or any(
            not callable(getattr(scene_asset_skill, method_name, None))
            for method_name in ("reference_image", "text_to_image")
        ):
            return _not_ready_bundle()
        memory_port = PowerMemVideoLivePort(power_mem_service)
        capabilities = DefaultVideoLiveCapabilities(
            model_factory=model_factory,
            scene_asset_skill=scene_asset_skill,
            memory_search=memory_port,
            memory_record=memory_port,
            clock=clock,
        )
        decision_model = _LazyDecisionModel(
            model_factory=model_factory,
            model_name=model_name.strip(),
        )
        answer_port = _LazySupervisorAnswerPort(
            model_factory=model_factory,
            model_name=model_name.strip(),
        )
    except Exception as error:  # noqa: BLE001 - Gateway readiness 只暴露固定原因码
        logger.warning(
            "视频 live 能力构造失败：exception_type=%s",
            type(error).__name__,
        )
        return _not_ready_bundle()
    return GatewayVideoLiveCapabilities(
        capabilities=capabilities,
        decision_model=decision_model,
        answer_port=answer_port,
        memory_port=memory_port,
        reason_code=None,
    )


def _not_ready_bundle() -> GatewayVideoLiveCapabilities:
    return GatewayVideoLiveCapabilities(
        capabilities=None,
        decision_model=None,
        answer_port=None,
        memory_port=None,
        reason_code=VIDEO_LIVE_HANDLER_NOT_READY,
    )


def _decision_output(value: Any) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    content = getattr(value, "content", value)
    if isinstance(content, Mapping):
        return dict(content)
    return _text_output(content)


def _text_output(value: Any) -> str:
    content = getattr(value, "content", value)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(str(block["text"]))
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


__all__ = [
    "GatewayVideoLiveCapabilities",
    "PowerMemVideoLivePort",
    "VIDEO_LIVE_HANDLER_NOT_READY",
    "make_pixelflow_agent_live_capabilities",
]
