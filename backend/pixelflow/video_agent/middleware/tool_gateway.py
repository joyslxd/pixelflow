"""Tool 调用前的鉴权 / 确认 / 额度 / revision 闸门 Middleware。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from copy import deepcopy

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime

from pixelflow.video_agent.tool_gateway import VideoToolGateway
from pixelflow.video_agent.tool_runtime_context import get_tool_runtime_context


def tools_awaiting_confirmation(messages: list[object]) -> set[str]:
    """从历史 ToolMessage 中收集已弹出确认、等待用户点击的工具名。"""

    awaiting: set[str] = set()
    for item in messages:
        if not isinstance(item, ToolMessage):
            continue
        content = item.content
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("requires_confirmation") is not True:
            continue
        name = str(payload.get("tool_name") or getattr(item, "name", "") or "").strip()
        if name:
            awaiting.add(name)
    return awaiting


def tools_soft_failed(messages: list[object]) -> set[str]:
    """本轮已业务失败（非确认闸门）的工具，禁止同轮盲重试以免撞递归上限。"""

    failed: set[str] = set()
    for item in messages:
        if not isinstance(item, ToolMessage):
            continue
        name = str(getattr(item, "name", "") or "").strip()
        content = item.content
        summary = ""
        if isinstance(content, str) and content.strip():
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                summary = content
            else:
                if isinstance(payload, dict):
                    if payload.get("requires_confirmation") is True:
                        continue
                    name = str(payload.get("tool_name") or name).strip() or name
                    public = payload.get("public_summary")
                    if isinstance(public, str):
                        summary = public
                else:
                    summary = content
        if getattr(item, "status", None) == "error":
            if name:
                failed.add(name)
            continue
        if name and any(
            token in summary
            for token in ("执行失败", "合并失败", "缺少临时授权", "参数无效", "启动失败")
        ):
            failed.add(name)
    return failed


def strip_blocked_tool_retries(state: AgentState) -> dict | None:
    """确认等待或业务失败后，剥离模型对同一 Tool 的再次 tool_calls。"""

    messages = list(state.get("messages") or [])
    if not messages:
        return None
    last = messages[-1]
    if not isinstance(last, AIMessage):
        return None
    tool_calls = list(getattr(last, "tool_calls", None) or [])
    if not tool_calls:
        return None
    history = messages[:-1]
    blocked = tools_awaiting_confirmation(history) | tools_soft_failed(history)
    if not blocked:
        return None
    kept = [
        call
        for call in tool_calls
        if str(call.get("name") or "").strip() not in blocked
    ]
    if len(kept) == len(tool_calls):
        return None
    if tools_soft_failed(history):
        hint = (
            "该工具本轮已失败，请勿再次调用；"
            "请用一两句话说明失败结果，然后结束本轮。"
        )
    else:
        hint = (
            "确认单已发出，请勿再次调用同一计费工具；"
            "直接提示用户在界面点击确认，然后结束本轮。"
        )
    content = last.content
    if isinstance(content, str):
        next_content = f"{content.rstrip()}\n\n{hint}".strip() if content.strip() else hint
    elif isinstance(content, list):
        next_content = [*content, {"type": "text", "text": hint}]
    else:
        next_content = hint
    update: dict[str, object] = {
        "tool_calls": kept,
        "content": next_content,
    }
    additional_kwargs = dict(getattr(last, "additional_kwargs", {}) or {})
    if not kept:
        additional_kwargs.pop("tool_calls", None)
        additional_kwargs.pop("function_call", None)
        response_metadata = deepcopy(getattr(last, "response_metadata", {}) or {})
        if response_metadata.get("finish_reason") == "tool_calls":
            response_metadata["finish_reason"] = "stop"
        update["response_metadata"] = response_metadata
    update["additional_kwargs"] = additional_kwargs
    return {"messages": [last.model_copy(update=update)]}


# 兼容旧测试名
strip_repeat_while_awaiting_confirmation = strip_blocked_tool_retries


class VideoToolGatewayMiddleware(AgentMiddleware[AgentState]):
    """在 Tool 生命周期注入 tool_call_id，并把 Gateway 闸门结果回写。

    实际确认/额度/revision 裁决在 VideoToolGateway.invoke；本 Middleware
    只保证 tool_call_id 进入 runtime context。同轮防重复盲调见
    ``VideoConfirmationAwaitMiddleware``（须注册在 LoopDetection 之后，
    以便 after_model 反向链路上先剥离再计数）。
    """

    def __init__(self, gateway: VideoToolGateway | None = None) -> None:
        super().__init__()
        self.gateway = gateway

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[object]],
    ) -> object:
        tool_call_id = str(request.tool_call.get("id") or "").strip()
        runtime = dict(get_tool_runtime_context() or {})
        if tool_call_id and not runtime.get("tool_call_id"):
            # StructuredTool coroutine 走 Gateway；此处补充 call id 供闸门绑定。
            from pixelflow.video_agent.tool_runtime_context import bind_tool_runtime_context

            runtime["tool_call_id"] = tool_call_id
            with bind_tool_runtime_context(runtime):
                result = await handler(request)
        else:
            result = await handler(request)

        if isinstance(result, ToolMessage) and isinstance(result.content, str):
            try:
                payload = json.loads(result.content)
            except json.JSONDecodeError:
                return result
            if isinstance(payload, dict) and payload.get("requires_confirmation") is True:
                # 已发卡；内容保持 Gateway 安全摘要，不改写。
                return result
        return result


class VideoConfirmationAwaitMiddleware(AgentMiddleware[AgentState]):
    """确认等待或业务失败后，阻止同轮再调同一计费 Tool。

    必须排在 ``LoopDetectionMiddleware`` 之后注册：LangGraph after_model 按
    注册顺序反向执行，这样才能先剥离 tool_calls，再让 LoopDetection 计数。
    """

    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return strip_blocked_tool_retries(state)

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return strip_blocked_tool_retries(state)
