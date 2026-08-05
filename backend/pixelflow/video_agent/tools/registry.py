"""VideoAgent 受控工具注册与参数校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from pixelflow.video_agent.contracts import VideoToolResult, VideoWorkspace


class VideoToolValidationError(ValueError):
    """用户可修正的工具选择或参数错误。"""


@dataclass(frozen=True)
class VideoToolContext:
    workspace: VideoWorkspace | None


class VideoTool(Protocol):
    name: str
    input_model: type[BaseModel]

    async def execute(
        self,
        context: VideoToolContext,
        arguments: dict[str, object],
    ) -> VideoToolResult: ...


class VideoToolRegistry:
    def __init__(self, tools: list[VideoTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("VideoAgent 工具名称不能重复")

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def resolve(self, name: str) -> VideoTool | None:
        return self._tools.get(name)

    async def execute(
        self,
        name: str,
        context: VideoToolContext,
        arguments: dict[str, object],
    ) -> VideoToolResult:
        tool = self.resolve(name)
        if tool is None:
            raise VideoToolValidationError("未注册的 VideoAgent 工具")
        try:
            validated = tool.input_model.model_validate(arguments)
        except Exception as exc:
            raise VideoToolValidationError("工具参数不合法") from exc
        return await tool.execute(context, validated.model_dump(mode="python"))


class EmptyToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
