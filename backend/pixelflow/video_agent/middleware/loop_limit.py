"""业务 Tool 次数上限：每个 invocation 最多 N 次，防止一次跑完整 Workflow。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt.tool_node import ToolCallRequest


_FRAMEWORK_TOOLS = frozenset({"ask_clarification", "update_video_plan"})


class VideoLoopLimitMiddleware(AgentMiddleware[AgentState]):
    """统计业务 Tool 次数；达到上限后剥离后续 tool_calls 并提示总结。"""

    def __init__(self, *, max_business_tools: int = 3) -> None:
        if max_business_tools < 1:
            raise ValueError("max_business_tools 必须 >= 1")
        super().__init__()
        self.max_business_tools = max_business_tools
        self._business_tool_count = 0
        self._limit_reached = False

    def reset(self) -> None:
        self._business_tool_count = 0
        self._limit_reached = False

    def before_agent(self, state: AgentState, runtime) -> dict | None:
        self.reset()
        return None

    async def abefore_agent(self, state: AgentState, runtime) -> dict | None:
        self.reset()
        return None

    def _is_business_tool(self, name: str) -> bool:
        cleaned = name.strip()
        return bool(cleaned) and cleaned not in _FRAMEWORK_TOOLS

    def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[object]],
    ):
        return self._wrap_tool_call_async(request, handler)

    async def _wrap_tool_call_async(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[object]],
    ):
        name = str(request.tool_call.get("name") or "")
        if self._is_business_tool(name):
            if self._business_tool_count >= self.max_business_tools:
                self._limit_reached = True
                from langchain_core.messages import ToolMessage

                return ToolMessage(
                    content=(
                        f"本轮业务 Tool 已达上限（{self.max_business_tools}）。"
                        "请总结当前结果；如需继续请等待下一轮 Turn。"
                    ),
                    tool_call_id=str(request.tool_call.get("id") or "missing"),
                    name=name,
                    status="error",
                )
            self._business_tool_count += 1
            if self._business_tool_count >= self.max_business_tools:
                self._limit_reached = True
        return await handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], object],
    ):
        name = str(request.tool_call.get("name") or "")
        if self._is_business_tool(name):
            if self._business_tool_count >= self.max_business_tools:
                self._limit_reached = True
                from langchain_core.messages import ToolMessage

                return ToolMessage(
                    content=(
                        f"本轮业务 Tool 已达上限（{self.max_business_tools}）。"
                        "请总结当前结果；如需继续请等待下一轮 Turn。"
                    ),
                    tool_call_id=str(request.tool_call.get("id") or "missing"),
                    name=name,
                    status="error",
                )
            self._business_tool_count += 1
            if self._business_tool_count >= self.max_business_tools:
                self._limit_reached = True
        return handler(request)

    def _strip_business_tool_calls(self, response: ModelResponse) -> ModelResponse:
        if not self._limit_reached:
            return response
        # ModelResponse 通常含 message；尽量剥离后续业务 tool_calls。
        message = getattr(response, "result", None) or getattr(response, "message", None)
        if message is None and isinstance(response, AIMessage):
            message = response
        # langchain ModelResponse: .result is list of messages
        result = getattr(response, "result", None)
        if isinstance(result, list) and result:
            patched: list[object] = []
            for item in result:
                if isinstance(item, AIMessage) and item.tool_calls:
                    kept = [
                        call
                        for call in item.tool_calls
                        if not self._is_business_tool(str(call.get("name") or ""))
                    ]
                    if len(kept) != len(item.tool_calls):
                        item = item.model_copy(
                            update={
                                "tool_calls": kept,
                                "content": (
                                    (item.content or "")
                                    + (
                                        "\n本轮业务 Tool 已达上限，请总结当前结果。"
                                        if not kept
                                        else ""
                                    )
                                ).strip(),
                            }
                        )
                patched.append(item)
            if hasattr(response, "override"):
                return response.override(result=patched)  # type: ignore[return-value]
        return response

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        if self._limit_reached:
            request = request.override(
                messages=[
                    *request.messages,
                    HumanMessage(
                        content=(
                            f"本轮业务 Tool 已达上限（{self.max_business_tools}）。"
                            "请给出简洁总结，不要再调用业务 Tool。"
                        ),
                        name="video_loop_limit",
                    ),
                ]
            )
        response = handler(request)
        if isinstance(response, ModelResponse):
            return self._strip_business_tool_calls(response)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        if self._limit_reached:
            request = request.override(
                messages=[
                    *request.messages,
                    HumanMessage(
                        content=(
                            f"本轮业务 Tool 已达上限（{self.max_business_tools}）。"
                            "请给出简洁总结，不要再调用业务 Tool。"
                        ),
                        name="video_loop_limit",
                    ),
                ]
            )
        response = await handler(request)
        if isinstance(response, ModelResponse):
            return self._strip_business_tool_calls(response)
        return response
