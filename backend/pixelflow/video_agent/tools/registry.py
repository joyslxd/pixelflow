"""VideoAgent 受控工具合同、参数校验与注册表。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ValidationError

from pixelflow.video_agent.contracts import VideoToolResult, VideoWorkspace


class VideoToolCostLevel(StrEnum):
    NONE = "none"
    EXTERNAL_READ = "external_read"
    BILLABLE = "billable"
    DESTRUCTIVE = "destructive"


class VideoToolIdempotencyMode(StrEnum):
    READ_ONLY = "read_only"
    REQUEST = "request"
    OPERATION = "operation"


class VideoToolRecoveryMode(StrEnum):
    INLINE = "inline"
    REPLAY = "replay"
    OPERATION = "operation"


class VideoToolValidationError(ValueError):
    """表示用户或规划器可以通过修正参数恢复的工具调用错误。"""


@dataclass(frozen=True)
class VideoToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    cost_level: VideoToolCostLevel
    confirmation_required: bool
    idempotency_mode: VideoToolIdempotencyMode
    recovery_mode: VideoToolRecoveryMode
    workspace_mutations: tuple[str, ...]

    @property
    def input_schema(self) -> dict[str, object]:
        """生成提供给规划模型的 JSON Schema 副本。"""
        return self.input_model.model_json_schema()


@dataclass(frozen=True)
class VideoToolContext:
    user_id: str
    workspace: VideoWorkspace

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("工具上下文必须包含用户标识")


class VideoTool(Protocol):
    @property
    def spec(self) -> VideoToolSpec: ...

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult: ...


class VideoToolRegistry:
    """只解析启动时显式注册的工具，并在执行前统一校验参数。"""

    def __init__(self, tools: Iterable[VideoTool]) -> None:
        registered: dict[str, VideoTool] = {}
        for tool in tools:
            name = tool.spec.name.strip()
            if not name:
                raise ValueError("工具名称不能为空")
            if name in registered:
                raise ValueError(f"工具名称重复：{name}")
            registered[name] = tool
        self._tools = registered

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def resolve(self, name: str) -> VideoTool | None:
        return self._tools.get(name)

    def specs(self) -> tuple[VideoToolSpec, ...]:
        return tuple(self._tools[name].spec for name in self.names())

    async def execute(
        self,
        context: VideoToolContext,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        tool = self.resolve(tool_name)
        if tool is None:
            raise VideoToolValidationError("规划器选择了未注册工具")
        try:
            validated = tool.spec.input_model.model_validate(dict(arguments))
        except ValidationError:
            return VideoToolResult(
                tool_name=tool.spec.name,
                public_summary="工具参数无效，请修正后重试",
            )
        try:
            return await tool.execute(
                context,
                validated.model_dump(mode="json"),
            )
        except VideoToolValidationError:
            return VideoToolResult(
                tool_name=tool.spec.name,
                public_summary="工具参数无效，请修正后重试",
            )
