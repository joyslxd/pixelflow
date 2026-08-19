"""把「思考里口述要调 Tool」落成原生 tool_calls，避免 ReAct 假规划。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from uuid import uuid4

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage

# 可强制补发的工具。计费类必须仍走 Gateway confirmation_required 闸门，
# 这里只把「口述却不 Call」落成原生 tool_calls，禁止静默跳过确认。
_FORCEABLE_TOOLS = frozenset(
    {
        "inspect_video_workspace",
        "compose_or_export_video",
    }
)

# 模型常编造的别名 → 已注册 Tool
_TOOL_ALIASES: dict[str, str] = {
    "inspect_video_workspace": "inspect_video_workspace",
    "compose_or_export_video": "compose_or_export_video",
    "merge_videos": "compose_or_export_video",
    "merge_video": "compose_or_export_video",
}

_DEFAULT_ARGS: dict[str, dict[str, object]] = {
    "compose_or_export_video": {"output_type": "mp4"},
}

_INTENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE | re.UNICODE), tool)
    for pattern, tool in (
        (
            r"(?:调用|先(?:检查|查看|确认)|让我|需要|准备)(?:一下)?(?:调用)?\s*"
            r"[`「\"']?inspect_video_workspace",
            "inspect_video_workspace",
        ),
        (
            r"inspect_video_workspace\s*(?:来|查看|检查|确认|读取)",
            "inspect_video_workspace",
        ),
        (r"调用\s*inspect_video_workspace", "inspect_video_workspace"),
        (
            r"(?:调用|启动|执行|需要)(?:一下)?(?:调用)?\s*[`「\"']?"
            r"(?:merge_videos?|compose_or_export_video)",
            "compose_or_export_video",
        ),
        (
            r"(?:merge_videos?|compose_or_export_video)\s*(?:来|合并|合成|导出)?",
            "compose_or_export_video",
        ),
        (
            r"(?:调用|启动|执行).{0,12}(?:合并|合成).{0,8}(?:视频|成片|工具)",
            "compose_or_export_video",
        ),
        (r"合并(?:这些|全部|所有)?(?:分镜)?视频", "compose_or_export_video"),
    )
)

_USER_MERGE_INTENT = re.compile(
    r"(?:合并|合成).{0,8}(?:视频|成片)|导出(?:成片|mp4)|merge\s*videos?",
    re.IGNORECASE | re.UNICODE,
)

_USER_GENERATE_SCENES_INTENT = re.compile(
    r"确认并生成分镜视频|重新生成已修改的分镜视频|继续生成失败的分镜视频|"
    r"^(?:请)?(?:帮我)?生成(?:全部|所有)?(?:的)?(?:分镜)?视频",
    re.IGNORECASE | re.UNICODE,
)


def _message_blob(message: AIMessage) -> str:
    parts: list[str] = []
    content = message.content
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                for key in ("text", "thinking", "reasoning", "reasoning_content"):
                    value = block.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value)
    additional = getattr(message, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = additional.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
    return "\n".join(parts)


def _latest_human_text(request: ModelRequest | None) -> str:
    if request is None:
        return ""
    messages = getattr(request, "messages", None)
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if not isinstance(item, HumanMessage):
            continue
        content = item.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                block
                for block in content
                if isinstance(block, str) and block.strip()
            ]
            if texts:
                return "\n".join(texts)
            for block in content:
                if isinstance(block, Mapping):
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        return text
    return ""


def narrated_forceable_tool(
    message: AIMessage,
    *,
    request: ModelRequest | None = None,
) -> str | None:
    """若正文/思考口述了可强制补发的 Tool 且未带 tool_calls，返回工具名。"""

    if getattr(message, "tool_calls", None):
        return None
    human = _latest_human_text(request)
    # 用户明确要生成/重跑分镜视频时，禁止把「成片/合并」口述强制成 compose。
    if human and _USER_GENERATE_SCENES_INTENT.search(human):
        return None
    blob = _message_blob(message)
    if not blob.strip():
        return None
    for pattern, tool_name in _INTENT_PATTERNS:
        if pattern.search(blob):
            return tool_name
    for alias, canonical in _TOOL_ALIASES.items():
        if alias in blob and re.search(r"(调用|查看|检查|读取|确认|启动|执行|合并)", blob):
            return canonical
    # 用户明确说合并，模型只口头答应却不 Call
    if human and _USER_MERGE_INTENT.search(human) and re.search(
        r"(合并|合成|成片|交付|导出)",
        blob,
    ):
        return "compose_or_export_video"
    return None


def default_forced_tool_args(tool_name: str) -> dict[str, object]:
    return dict(_DEFAULT_ARGS.get(tool_name) or {})


def _awaiting_confirmation_tools(request: ModelRequest | None) -> set[str]:
    """本轮消息里已返回 requires_confirmation 或业务失败的工具，禁止再强制补发。"""

    if request is None:
        return set()
    messages = getattr(request, "messages", None)
    if not isinstance(messages, list):
        return set()
    from pixelflow.video_agent.middleware.tool_gateway import (
        tools_awaiting_confirmation,
        tools_soft_failed,
    )

    return tools_awaiting_confirmation(messages) | tools_soft_failed(messages)


class VideoToolCommitmentMiddleware(AgentMiddleware[AgentState]):
    """思考模型常把「我要调 X」写进 reasoning 却不发原生 tool_calls。

    对白名单 Tool（含需确认的交付），检测到口述意图后强制补一条 tool_call，
    让 ReAct 真正进 tools 节点；计费仍由 Gateway 确认闸门裁决。
    若该 Tool 本轮已 requires_confirmation，不再强制补发，避免确认后连打。
    """

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        response = handler(request)
        return self._maybe_force_tool(response, request=request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], object],
    ) -> ModelCallResult:
        response = await handler(request)  # type: ignore[misc]
        return self._maybe_force_tool(response, request=request)  # type: ignore[arg-type]

    def _maybe_force_tool(
        self,
        response: ModelResponse | AIMessage,
        *,
        request: ModelRequest | None = None,
    ) -> ModelCallResult:
        result = getattr(response, "result", None)
        messages: list[object]
        if isinstance(result, list) and result:
            messages = list(result)
        elif isinstance(response, AIMessage):
            messages = [response]
        else:
            return response  # type: ignore[return-value]

        blocked = _awaiting_confirmation_tools(request)
        patched: list[object] = []
        changed = False
        for item in messages:
            if not isinstance(item, AIMessage):
                patched.append(item)
                continue
            tool_name = narrated_forceable_tool(item, request=request)
            if (
                tool_name is None
                or tool_name not in _FORCEABLE_TOOLS
                or tool_name in blocked
            ):
                patched.append(item)
                continue
            call_id = f"forced_{uuid4().hex[:16]}"
            patched.append(
                item.model_copy(
                    update={
                        "tool_calls": [
                            {
                                "name": tool_name,
                                "args": default_forced_tool_args(tool_name),
                                "id": call_id,
                                "type": "tool_call",
                            }
                        ],
                        # 清空可能误导前端的「我已调用」口述，真正结果等 Tool Result。
                        "content": "",
                    }
                )
            )
            changed = True
        if not changed:
            return response  # type: ignore[return-value]
        if hasattr(response, "override"):
            return response.override(result=patched)  # type: ignore[return-value]
        if len(patched) == 1 and isinstance(patched[0], AIMessage):
            return patched[0]  # type: ignore[return-value]
        return response  # type: ignore[return-value]
