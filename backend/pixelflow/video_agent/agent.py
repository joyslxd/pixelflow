"""PixelFlow 原生 Video Agent 装配入口。

打开本文件应能直接看到：模型、Prompt、Tools、Middleware 与 State 的装配关系。
本模块不实现 LangGraph Agent loop，只调用 DeerFlow SDK 级工厂 create_deerflow_agent。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deerflow.agents.factory import create_deerflow_agent
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.dangling_tool_call_middleware import (
    DanglingToolCallMiddleware,
)
from deerflow.agents.middlewares.dynamic_context_middleware import (
    DynamicContextMiddleware,
)
from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import (
    ToolErrorHandlingMiddleware,
)
from deerflow.config.memory_config import MemoryConfig
from deerflow.tools.builtins import ask_clarification_tool
from langchain.agents.middleware import AgentMiddleware
from langgraph.graph.state import CompiledStateGraph

from pixelflow.video_agent.middleware import (
    VideoConfirmationAwaitMiddleware,
    VideoLoopLimitMiddleware,
    VideoPlanMiddleware,
    VideoProgressMiddleware,
    VideoToolCommitmentMiddleware,
    VideoToolGatewayMiddleware,
    VideoWorkspaceContextMiddleware,
)
from pixelflow.video_agent.prompts import VIDEO_AGENT_SYSTEM_PROMPT
from pixelflow.video_agent.state import VideoAgentState
from pixelflow.video_agent.tool_adapter import build_video_agent_tools
from pixelflow.video_agent.tool_gateway import VideoToolGateway
from pixelflow.video_agent.tools.plan import build_update_video_plan_tool
from pixelflow.video_agent.tools.registry import VideoToolRegistry

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from deerflow.config.app_config import AppConfig

VIDEO_AGENT_NAME = "pixelflow-video-agent"
_DEFAULT_MAX_BUSINESS_TOOLS = 3


def create_video_agent(
    *,
    model: BaseChatModel,
    registry: VideoToolRegistry,
    executor: object,
    video_repository: object,
    runtime_repository: object,
    skill_catalog: object,
    checkpointer: BaseCheckpointSaver | None = None,
    app_config: AppConfig | None = None,
    memory_config: MemoryConfig | None = None,
    max_business_tools: int = _DEFAULT_MAX_BUSINESS_TOOLS,
) -> CompiledStateGraph:
    """构造唯一原生 Video Agent 图。

    executor / repository / skill_catalog 由后续 Gateway 与 Tool 执行链路使用；
    P0-1.1 先完成可定位的装配壳，并拒绝注入 Sandbox/File/Bash/MCP 等通用能力。
    """

    assembly = _build_assembly(
        model=model,
        registry=registry,
        executor=executor,
        video_repository=video_repository,
        runtime_repository=runtime_repository,
        skill_catalog=skill_catalog,
        checkpointer=checkpointer,
        app_config=app_config,
        memory_config=memory_config,
        max_business_tools=max_business_tools,
    )
    return create_deerflow_agent(
        model=assembly["model"],
        tools=assembly["tools"],
        system_prompt=assembly["system_prompt"],
        middleware=assembly["middleware"],
        state_schema=assembly["state_schema"],
        checkpointer=assembly["checkpointer"],
        name=assembly["name"],
    )


def describe_video_agent_assembly(
    *,
    model: BaseChatModel,
    registry: VideoToolRegistry,
    executor: object,
    video_repository: object,
    runtime_repository: object,
    skill_catalog: object,
    checkpointer: BaseCheckpointSaver | None = None,
    app_config: AppConfig | None = None,
    memory_config: MemoryConfig | None = None,
    max_business_tools: int = _DEFAULT_MAX_BUSINESS_TOOLS,
) -> dict[str, Any]:
    """返回装配摘要，供合同测试检查 Middleware / Prompt / Memory 隔离，不编译图。"""

    assembly = _build_assembly(
        model=model,
        registry=registry,
        executor=executor,
        video_repository=video_repository,
        runtime_repository=runtime_repository,
        skill_catalog=skill_catalog,
        checkpointer=checkpointer,
        app_config=app_config,
        memory_config=memory_config,
        max_business_tools=max_business_tools,
    )
    memory_mw = next(
        mw for mw in assembly["middleware"] if isinstance(mw, MemoryMiddleware)
    )
    loop_mw = next(
        mw for mw in assembly["middleware"] if isinstance(mw, VideoLoopLimitMiddleware)
    )
    return {
        "name": assembly["name"],
        "system_prompt": assembly["system_prompt"],
        "state_schema": assembly["state_schema"],
        "middleware_types": tuple(type(mw) for mw in assembly["middleware"]),
        "tool_names": tuple(tool.name for tool in assembly["tools"]),
        "memory_agent_name": memory_mw._agent_name,
        "loop_limit_max_business_tools": loop_mw.max_business_tools,
    }


def extract_bound_tool_names(agent: CompiledStateGraph) -> set[str]:
    """从已编译 Agent 图读取绑定的 Tool 名称。"""

    tools_node = agent.nodes.get("tools")
    if tools_node is None:
        return set()
    bound = getattr(tools_node, "bound", None)
    tools_by_name = getattr(bound, "_tools_by_name", None)
    if not isinstance(tools_by_name, dict):
        return set()
    return set(tools_by_name)


def _build_assembly(
    *,
    model: BaseChatModel,
    registry: VideoToolRegistry,
    executor: object,
    video_repository: object,
    runtime_repository: object,
    skill_catalog: object,
    checkpointer: BaseCheckpointSaver | None,
    app_config: AppConfig | None,
    memory_config: MemoryConfig | None,
    max_business_tools: int,
) -> dict[str, Any]:
    if model is None:
        raise ValueError("model 不能为空")
    if not isinstance(registry, VideoToolRegistry):
        raise TypeError("registry 必须是 VideoToolRegistry")
    for name, value in {
        "executor": executor,
        "video_repository": video_repository,
        "runtime_repository": runtime_repository,
        "skill_catalog": skill_catalog,
    }.items():
        if value is None:
            raise ValueError(f"{name} 不能为空")

    resolved_memory = memory_config
    if resolved_memory is None and app_config is not None:
        resolved_memory = app_config.memory
    if resolved_memory is None:
        resolved_memory = MemoryConfig()

    # 依赖先挂到 Gateway middleware，便于后续 Task 接线且避免未使用参数告警。
    plan_mw = VideoPlanMiddleware()
    tool_gateway = VideoToolGateway(
        registry=registry,
        executor=executor,
        plan_middleware=plan_mw,
        video_repository=video_repository,
        runtime_repository=runtime_repository,
    )
    gateway = VideoToolGatewayMiddleware(gateway=tool_gateway)

    workspace_mw = VideoWorkspaceContextMiddleware(
        video_repository=video_repository,
        skill_catalog=skill_catalog,
    )
    progress_mw = VideoProgressMiddleware(
        runtime_repository=runtime_repository,
    )

    middleware: list[AgentMiddleware] = [
        DanglingToolCallMiddleware(),
        ToolErrorHandlingMiddleware(),
        DynamicContextMiddleware(
            agent_name=VIDEO_AGENT_NAME,
            app_config=app_config,
        ),
        workspace_mw,
        plan_mw,
        # 思考口述 Tool 名却未发 tool_calls 时，补发白名单只读调用。
        VideoToolCommitmentMiddleware(),
        gateway,
        progress_mw,
        LoopDetectionMiddleware(),
        # 须在 LoopDetection 之后：after_model 反向执行，先剥离确认后重复 tool_calls。
        VideoConfirmationAwaitMiddleware(),
        VideoLoopLimitMiddleware(max_business_tools=max_business_tools),
        MemoryMiddleware(
            agent_name=VIDEO_AGENT_NAME,
            memory_config=resolved_memory,
        ),
        ClarificationMiddleware(),
    ]

    tools = [
        *build_video_agent_tools(registry, gateway=tool_gateway),
        build_update_video_plan_tool(plan_mw),
        ask_clarification_tool,
    ]

    return {
        "name": VIDEO_AGENT_NAME,
        "model": model,
        "tools": tools,
        "system_prompt": VIDEO_AGENT_SYSTEM_PROMPT,
        "middleware": middleware,
        "state_schema": VideoAgentState,
        "checkpointer": checkpointer,
    }
