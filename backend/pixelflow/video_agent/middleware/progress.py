"""把 Tool / 回答进度映射为公开原生事件。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from pixelflow.video_agent.events.publisher import NativeAgentEventPublisher
from pixelflow.video_agent.tool_runtime_context import get_tool_runtime_context

logger = logging.getLogger(__name__)

_FRAMEWORK_TOOLS = frozenset({"ask_clarification", "update_video_plan"})


class VideoProgressMiddleware(AgentMiddleware[AgentState]):
    """在 Tool 生命周期发出 agent.tool.* 公开事件。"""

    def __init__(
        self,
        *,
        runtime_repository: object | None = None,
        clock=None,
    ) -> None:
        super().__init__()
        self.runtime_repository = runtime_repository
        self._clock = clock
        self._started_at: dict[str, float] = {}

    def _publisher(self) -> NativeAgentEventPublisher | None:
        repository = self.runtime_repository
        if repository is None or not hasattr(repository, "create_event"):
            return None
        runtime = get_tool_runtime_context() or {}
        user_id = str(runtime.get("user_id") or "").strip()
        conversation_id = str(runtime.get("conversation_id") or "").strip()
        turn_id = str(runtime.get("turn_id") or "").strip()
        if not user_id or not conversation_id or not turn_id:
            return None
        return NativeAgentEventPublisher(
            repository=repository,  # type: ignore[arg-type]
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            clock=self._clock,
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[object]],
    ) -> object:
        tool_name = str(request.tool_call.get("name") or "").strip()
        tool_call_id = str(request.tool_call.get("id") or "").strip() or "missing"
        if tool_name in _FRAMEWORK_TOOLS:
            return await handler(request)

        publisher = self._publisher()
        runtime = get_tool_runtime_context() or {}
        plan_id = runtime.get("plan_id")
        step_id = runtime.get("step_id")
        self._started_at[tool_call_id] = time.monotonic()
        if publisher is not None:
            try:
                await publisher.tool_started(
                    tool_name=tool_name or "unknown_tool",
                    tool_call_id=tool_call_id,
                    plan_id=str(plan_id) if isinstance(plan_id, str) else None,
                    step_id=str(step_id) if isinstance(step_id, str) else None,
                    title=tool_name or None,
                )
            except Exception:  # noqa: BLE001
                logger.exception("emit agent.tool.started failed")

        try:
            result = await handler(request)
        except Exception as exc:
            if publisher is not None:
                try:
                    await publisher.tool_failed(
                        tool_name=tool_name or "unknown_tool",
                        tool_call_id=tool_call_id,
                        public_summary="工具执行失败，请稍后重试",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("emit agent.tool.failed failed")
            raise exc

        public_summary = _public_summary_from_tool_result(result)
        artifact_refs = _artifact_refs_from_tool_result(result)
        started = self._started_at.pop(tool_call_id, None)
        duration_ms = (
            int((time.monotonic() - started) * 1000) if started is not None else None
        )
        failed = isinstance(result, ToolMessage) and getattr(result, "status", None) == "error"
        if publisher is not None:
            try:
                if failed:
                    await publisher.tool_failed(
                        tool_name=tool_name or "unknown_tool",
                        tool_call_id=tool_call_id,
                        public_summary=public_summary or "工具执行失败，请稍后重试",
                    )
                else:
                    await publisher.tool_completed(
                        tool_name=tool_name or "unknown_tool",
                        tool_call_id=tool_call_id,
                        public_summary=public_summary or f"已完成 {tool_name}",
                        artifact_refs=artifact_refs,
                        duration_ms=duration_ms,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("emit agent.tool terminal event failed")
        return result


def _public_summary_from_tool_result(result: object) -> str:
    if isinstance(result, ToolMessage):
        content = result.content
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                return content.strip()[:2_000]
            if isinstance(payload, dict):
                summary = payload.get("public_summary")
                if isinstance(summary, str) and summary.strip():
                    return summary.strip()[:2_000]
            return content.strip()[:2_000]
    return ""


def _artifact_refs_from_tool_result(result: object) -> tuple[str, ...]:
    if not isinstance(result, ToolMessage):
        return ()
    content = result.content
    if not isinstance(content, str):
        return ()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, dict):
        return ()
    refs = payload.get("artifact_refs")
    if not isinstance(refs, list):
        return ()
    return tuple(
        str(item)
        for item in refs
        if isinstance(item, str) and item.startswith("artifact:")
    )[:32]
