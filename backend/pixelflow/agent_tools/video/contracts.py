"""视频 Capability Tool 的框架无关 DTO、成本与恢复合同。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential
from pixelflow.video.contracts import VideoToolResult, VideoWorkspace


class VideoToolCostLevel(StrEnum):
    """定义 Tool 的副作用级别，确认与费用策略必须以此为准。"""

    NONE = "none"
    EXTERNAL_READ = "external_read"
    BILLABLE = "billable"
    DESTRUCTIVE = "destructive"


class VideoToolIdempotencyMode(StrEnum):
    """定义 Tool 的幂等身份归属，避免付费调用被模型重试放大。"""

    READ_ONLY = "read_only"
    REQUEST = "request"
    OPERATION = "operation"


class VideoToolRecoveryMode(StrEnum):
    """定义 Tool 失败后的恢复边界，业务恢复不依赖 Harness Session。"""

    INLINE = "inline"
    REPLAY = "replay"
    OPERATION = "operation"


class VideoToolValidationError(ValueError):
    """表示用户或规划器可通过修正参数恢复的 Tool 调用错误。"""


class VideoToolExecutionError(RuntimeError):
    """表示 Tool 执行失败且必须收敛为固定公开摘要。"""


@dataclass(frozen=True)
class VideoToolSpec:
    """冻结单个视频 Tool 的输入、费用、恢复与 Workspace 变更声明。"""

    name: str
    description: str
    input_model: type[BaseModel]
    cost_level: VideoToolCostLevel
    confirmation_required: bool
    idempotency_mode: VideoToolIdempotencyMode
    recovery_mode: VideoToolRecoveryMode
    workspace_mutations: tuple[str, ...]
    # 仅这些字段可以进入 model_observation；Broker 还会施加总字节预算。
    model_observation_keys: tuple[str, ...] = ()

    @property
    def input_schema(self) -> dict[str, object]:
        """生成提供给 Agent Engine 的 JSON Schema 副本。"""

        return self.input_model.model_json_schema()


@dataclass(frozen=True)
class VideoToolContext:
    """表示 Gateway 从权威 Workspace 构造的 Tool 上下文，模型参数不得伪造。"""

    user_id: str
    workspace: VideoWorkspace
    # Run 与 Tool Call 来自 Gateway 的冻结 binding；计费批次不得信任模型参数生成身份。
    run_id: str | None = None
    tool_call_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    credential: TransientVideoAgentCredential | None = None
    report_progress: object | None = None
    report_thinking: object | None = None

    def __post_init__(self) -> None:
        """验证 owner 与计划步骤配对，防止 Tool 逃逸到错误的业务范围。"""

        if not self.user_id.strip():
            raise ValueError("工具上下文必须包含用户标识")
        if (self.run_id is None) != (self.tool_call_id is None):
            raise ValueError("工具上下文的 run_id 与 tool_call_id 必须同时提供")
        if self.run_id is not None and (
            not self.run_id.startswith("hrun_")
            or not self.tool_call_id
            or not self.tool_call_id.strip()
        ):
            raise ValueError("工具上下文的冻结 Run 或 Tool Call 标识无效")
        if (self.plan_id is None) != (self.step_id is None):
            raise ValueError("工具上下文的 plan_id 与 step_id 必须同时提供")
        if self.plan_id is not None and (
            not self.plan_id.strip() or not self.step_id or not self.step_id.strip()
        ):
            raise ValueError("工具上下文的计划与步骤标识不能为空")

    async def emit_progress(self, message: str, *, phase: str) -> None:
        """向会话推送当前步骤的公开阶段文案；无回调时静默跳过。"""

        reporter = self.report_progress
        if reporter is None:
            return
        text = message.strip()
        phase_key = phase.strip()
        if not text or not phase_key:
            return
        await reporter(text, phase=phase_key)  # type: ignore[operator]

    async def emit_thinking_delta(self, text: str) -> None:
        """向会话推送模型思考流增量；无回调时静默跳过。"""

        reporter = self.report_thinking
        if reporter is None:
            return
        piece = text.strip("\x00")
        if not piece:
            return
        await reporter(piece)  # type: ignore[operator]


class VideoTool(Protocol):
    """视频 Tool Handler 的稳定领域 Port。"""

    @property
    def spec(self) -> VideoToolSpec: ...

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult: ...


__all__ = [
    "VideoTool",
    "VideoToolContext",
    "VideoToolCostLevel",
    "VideoToolExecutionError",
    "VideoToolIdempotencyMode",
    "VideoToolRecoveryMode",
    "VideoToolSpec",
    "VideoToolValidationError",
]
