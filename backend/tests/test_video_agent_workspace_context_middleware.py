"""VideoWorkspaceContextMiddleware 合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.middleware.workspace_context import (
    VideoWorkspaceContextMiddleware,
    format_workspace_context_reminder,
)
from pixelflow.video_agent.tool_runtime_context import bind_tool_runtime_context

T0 = datetime(2026, 8, 12, tzinfo=UTC)


def test_format_workspace_context_reminder_is_safe_and_bounded() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        revision=3,
        payload={
            "script": {
                "content": "很长的脚本" * 200,
                "status": "draft",
                "source": "import",
            },
            "authorization": "Bearer secret-token",
            "dirty_scene_ids": ["scene-1"],
            "product_info": {"name": "水杯", "token": "should-drop"},
            "latest_input": "继续",
        },
        created_at=T0,
        updated_at=T0,
    )
    text = format_workspace_context_reminder(
        workspace,
        skill_hints=("seedance-prompt",),
        extra={"turn_id": "turn-1", "authorization": "leak"},
    )
    assert "<video_workspace_context>" in text
    assert "Bearer" not in text
    assert "secret-token" not in text
    assert "很长的脚本很长的脚本" not in text
    assert "registered_scene_asset_image_models" in text
    assert "gpt-image-2" in text
    assert "seeddream-5.0" in text
    assert "workspace-1" in text
    assert "seedance-prompt" in text
    assert "leak" not in text


def test_format_workspace_context_reminder_includes_safe_target_scene() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        payload={},
        created_at=T0,
        updated_at=T0,
    )

    text = format_workspace_context_reminder(
        workspace,
        extra={
            "turn_id": "turn-1",
            "target_scene": {
                "scene_id": "scene-2",
                "title": "手机特写",
                "shot_description": {"text": "安然攥着手机", "mentions": []},
                "authorization": "Bearer must-not-leak",
            },
        },
    )

    assert '"target_scene"' in text
    assert '"scene_id":"scene-2"' in text
    assert "安然攥着手机" in text
    assert "must-not-leak" not in text


def test_workspace_middleware_appends_reminder_via_wrap_model_call() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        payload={"script": {"content": "短脚本", "status": "ready"}},
        created_at=T0,
        updated_at=T0,
    )
    middleware = VideoWorkspaceContextMiddleware(
        skill_catalog=SimpleNamespace(names=lambda: ("seedance-prompt",)),
    )
    model = FakeListChatModel(responses=["ok"])
    request = ModelRequest(
        model=model,
        messages=[HumanMessage(content="看看状态")],
        system_message=None,
        tool_choice=None,
        tools=[],
        response_format=None,
        state={},
        runtime=SimpleNamespace(),
        model_settings={},
    )
    captured: dict[str, object] = {}

    def handler(req: ModelRequest):
        captured["messages"] = req.messages
        return SimpleNamespace()

    with bind_tool_runtime_context(
        {"user_id": "user-1", "workspace": workspace, "turn_id": "turn-1"}
    ):
        middleware.wrap_model_call(request, handler)

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 2
    assert isinstance(messages[-1], HumanMessage)
    assert "video_workspace_context" in str(messages[-1].content)
    assert messages[-1].additional_kwargs.get("hide_from_ui") is True
