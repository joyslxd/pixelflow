"""原生 Video Agent 工厂装配合同测试。"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import FakeListChatModel

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

from pixelflow.video_agent.agent import (
    VIDEO_AGENT_NAME,
    create_video_agent,
    describe_video_agent_assembly,
    extract_bound_tool_names,
)
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
from pixelflow.video_agent.tools.inspect_workspace import InspectVideoWorkspaceTool
from pixelflow.video_agent.tools.registry import VideoToolRegistry

_FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "bash",
        "ls",
        "glob",
        "grep",
        "read_file",
        "write_file",
        "str_replace",
        "web_search",
        "web_fetch",
        "image_search",
        "task",
        "skill_manage",
        "present_files",
        "view_image",
    }
)
_FRAMEWORK_TOOL_ALLOWLIST = frozenset({"ask_clarification", "update_video_plan"})


def _registry() -> VideoToolRegistry:
    return VideoToolRegistry([InspectVideoWorkspaceTool()])


def _stubs() -> dict[str, object]:
    return {
        "executor": SimpleNamespace(),
        "video_repository": SimpleNamespace(),
        "runtime_repository": SimpleNamespace(),
        "skill_catalog": SimpleNamespace(),
        "memory_config": MemoryConfig(enabled=False),
    }


def test_create_video_agent_binds_only_registry_and_framework_tools() -> None:
    registry = _registry()
    model = FakeListChatModel(responses=["已读取工作区"])

    agent = create_video_agent(model=model, registry=registry, **_stubs())

    bound = extract_bound_tool_names(agent)
    assert "inspect_video_workspace" in bound
    business = bound - _FRAMEWORK_TOOL_ALLOWLIST
    assert business <= set(registry.names())
    assert bound.isdisjoint(_FORBIDDEN_TOOL_NAMES)


def test_create_video_agent_assembles_expected_middleware_and_prompt() -> None:
    registry = _registry()
    model = FakeListChatModel(responses=["ok"])

    agent = create_video_agent(model=model, registry=registry, **_stubs())
    assert agent.name == VIDEO_AGENT_NAME

    assembly = describe_video_agent_assembly(
        model=model,
        registry=registry,
        **_stubs(),
    )
    assert assembly["name"] == VIDEO_AGENT_NAME
    assert assembly["system_prompt"] == VIDEO_AGENT_SYSTEM_PROMPT
    assert assembly["state_schema"] is VideoAgentState
    middleware_types = assembly["middleware_types"]
    for expected in (
        DanglingToolCallMiddleware,
        ToolErrorHandlingMiddleware,
        DynamicContextMiddleware,
        VideoWorkspaceContextMiddleware,
        VideoPlanMiddleware,
        VideoToolCommitmentMiddleware,
        VideoToolGatewayMiddleware,
        VideoProgressMiddleware,
        LoopDetectionMiddleware,
        VideoConfirmationAwaitMiddleware,
        VideoLoopLimitMiddleware,
        MemoryMiddleware,
        ClarificationMiddleware,
    ):
        assert expected in middleware_types, expected.__name__
    # 确认等待剥离必须排在 LoopDetection 之后，保证 after_model 先剥再计数。
    assert middleware_types.index(VideoConfirmationAwaitMiddleware) > middleware_types.index(
        LoopDetectionMiddleware
    )
    assert assembly["memory_agent_name"] == VIDEO_AGENT_NAME
    assert assembly["loop_limit_max_business_tools"] == 3
