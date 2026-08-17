"""在每次模型调用前注入 VideoWorkspace 安全摘要。"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import HumanMessage

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.workspace.digest import build_workspace_digest
from pixelflow.video_agent.tool_runtime_context import get_tool_runtime_context

logger = logging.getLogger(__name__)

_WORKSPACE_CONTEXT_KEY = "video_workspace_context_reminder"
_MAX_DIGEST_CHARS = 2_400


def format_workspace_context_reminder(
    workspace: VideoWorkspace,
    *,
    skill_hints: tuple[str, ...] = (),
    extra: Mapping[str, Any] | None = None,
) -> str:
    """把 Workspace 摘要格式化为模型可读的 system-reminder（不含凭证与长正文）。"""

    digest = build_workspace_digest(workspace)
    payload: dict[str, Any] = {
        "workspace": digest,
        "priority_rule": (
            "当长期记忆偏好与当前 Workspace 或本轮明确指令冲突时，"
            "以 Workspace 与本轮指令为准。"
        ),
    }
    if skill_hints:
        payload["skill_hints"] = list(skill_hints)[:8]
    if extra:
        payload["runtime"] = {
            key: value
            for key, value in extra.items()
            if key
            in {
                "turn_id",
                "conversation_id",
                "plan_id",
                "step_id",
                "revision",
            }
            and value is not None
        }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(body) > _MAX_DIGEST_CHARS:
        body = body[: _MAX_DIGEST_CHARS - 3] + "..."
    return (
        "<system-reminder>\n"
        "<video_workspace_context>\n"
        f"{body}\n"
        "</video_workspace_context>\n"
        "</system-reminder>"
    )


class VideoWorkspaceContextMiddleware(AgentMiddleware[AgentState]):
    """每次模型调用前追加 Workspace 安全摘要；不写入凭证或完整脚本正文。"""

    def __init__(
        self,
        *,
        video_repository: object | None = None,
        skill_catalog: object | None = None,
    ) -> None:
        super().__init__()
        self.video_repository = video_repository
        self.skill_catalog = skill_catalog

    def _skill_hints(self) -> tuple[str, ...]:
        catalog = self.skill_catalog
        if catalog is None:
            return ()
        names = getattr(catalog, "names", None)
        if callable(names):
            try:
                values = names()
            except Exception:  # noqa: BLE001
                return ()
            return tuple(str(item) for item in values if str(item).strip())[:8]
        specs = getattr(catalog, "specs", None)
        if callable(specs):
            try:
                values = specs()
            except Exception:  # noqa: BLE001
                return ()
            hints: list[str] = []
            for item in values:
                name = getattr(item, "name", None) or getattr(item, "skill_id", None)
                if name:
                    hints.append(str(name))
            return tuple(hints[:8])
        return ()

    def _resolve_workspace(self, request: ModelRequest) -> VideoWorkspace | None:
        runtime_context = get_tool_runtime_context() or {}
        workspace = runtime_context.get("workspace")
        if isinstance(workspace, VideoWorkspace):
            return workspace

        state = request.state if isinstance(request.state, Mapping) else {}
        workspace_id = state.get("workspace_id") or runtime_context.get("workspace_id")
        user_id = runtime_context.get("user_id")
        repository = self.video_repository
        getter = getattr(repository, "get_workspace", None)
        if (
            callable(getter)
            and isinstance(user_id, str)
            and user_id.strip()
            and isinstance(workspace_id, str)
            and workspace_id.strip()
        ):
            # 同步中间件路径不 await；仓库若仅提供 async，则跳过刷新。
            logger.debug("workspace context 跳过异步仓库刷新 workspace_id=%s", workspace_id)
        return None

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        workspace = self._resolve_workspace(request)
        if workspace is None:
            return request
        runtime_context = get_tool_runtime_context() or {}
        reminder = format_workspace_context_reminder(
            workspace,
            skill_hints=self._skill_hints(),
            extra=runtime_context,
        )
        message = HumanMessage(
            content=reminder,
            name="video_workspace_context",
            additional_kwargs={
                "hide_from_ui": True,
                _WORKSPACE_CONTEXT_KEY: True,
            },
        )
        return request.override(messages=[*request.messages, message])

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))
