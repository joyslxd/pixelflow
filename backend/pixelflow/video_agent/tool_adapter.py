"""把 VideoToolRegistry 映射为 LangChain StructuredTool。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from pixelflow.video_agent.tool_gateway import VideoToolGateway
from pixelflow.video_agent.tool_runtime_context import get_tool_runtime_context
from pixelflow.video_agent.tools.registry import VideoToolRegistry, VideoToolSpec

# 这些字段只允许由 Runtime context 注入，绝不能进入模型可见 args schema。
_HIDDEN_CONTEXT_FIELD_NAMES = frozenset(
    {
        "user_id",
        "workspace_id",
        "workspace",
        "plan_id",
        "step_id",
        "authorization",
        "credential",
        "revision",
        "runtime",
    }
)


def build_video_agent_tools(
    registry: VideoToolRegistry,
    *,
    gateway: VideoToolGateway | None = None,
) -> list[BaseTool]:
    """将注册表 Spec 映射为 StructuredTool；coroutine 统一进入 Gateway。"""

    if not isinstance(registry, VideoToolRegistry):
        raise TypeError("registry 必须是 VideoToolRegistry")
    resolved_gateway = gateway or VideoToolGateway(registry=registry)
    if resolved_gateway.registry is not registry:
        raise ValueError("gateway.registry 必须与传入的 registry 为同一实例")

    tools: list[BaseTool] = []
    for spec in registry.specs():
        tools.append(
            _structured_tool_from_spec(spec, gateway=resolved_gateway)
        )
    return tools


def _structured_tool_from_spec(
    spec: VideoToolSpec,
    *,
    gateway: VideoToolGateway,
) -> StructuredTool:
    tool_name = spec.name
    description = spec.description
    input_model = spec.input_model
    schema = input_model.model_json_schema()
    properties = schema.get("properties")
    if isinstance(properties, dict):
        leaked = _HIDDEN_CONTEXT_FIELD_NAMES.intersection(properties)
        if leaked:
            raise ValueError(
                f"工具 {tool_name} 的 args schema 不得暴露上下文字段：{sorted(leaked)}"
            )

    async def _run(**kwargs: Any) -> str:
        return await gateway.invoke(
            tool_name,
            kwargs,
            runtime_context=get_tool_runtime_context(),
        )

    return StructuredTool.from_function(
        coroutine=_run,
        name=tool_name,
        description=description,
        args_schema=input_model,
    )
