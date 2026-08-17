"""VideoAgent 受控工具合同、参数校验与注册表。"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ValidationError

from pixelflow.video_agent.contracts import VideoToolResult, VideoWorkspace
from pixelflow.video_agent.credentials import TransientVideoAgentCredential

logger = logging.getLogger(__name__)


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


class VideoToolExecutionError(RuntimeError):
    """表示工具执行失败且必须收敛为固定公开摘要。"""


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
    plan_id: str | None = None
    step_id: str | None = None
    credential: TransientVideoAgentCredential | None = None
    report_progress: object | None = None
    report_thinking: object | None = None

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("工具上下文必须包含用户标识")
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
        """向会话推送真 LLM 思考流增量；无回调时静默跳过。"""

        reporter = self.report_thinking
        if reporter is None:
            return
        piece = text.strip("\x00")
        if not piece:
            return
        await reporter(piece)  # type: ignore[operator]


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
            # 必须 exclude_unset：否则 Optional 字段被 dump 成 null，工具内二次校验会误报
            # 「镜头补丁不能把字段写为 null」→「镜头补丁参数无效」。
            result = await tool.execute(
                context,
                validated.model_dump(mode="json", exclude_unset=True),
            )
        except VideoToolValidationError as exc:
            # 业务校验文案已面向用户（如缺画幅），不要吞成笼统「工具参数无效」。
            detail = str(exc).strip()
            return VideoToolResult(
                tool_name=tool.spec.name,
                public_summary=(
                    detail[:280]
                    if detail
                    else "工具参数无效，请修正后重试"
                ),
            )
        except VideoToolExecutionError as exc:
            # 业务侧已写好的中文失败原因可公开；其余只带工具名，避免泄漏供应商细节。
            detail = str(exc).strip()
            logger.warning(
                "video tool business failure name=%s error_type=%s detail_prefix=%s",
                tool.spec.name,
                type(exc).__name__,
                (detail[:80] if detail else ""),
            )
            if detail and (
                detail.startswith(
                    (
                        "场景包",
                        "参考图",
                        "generate_scene_assets",
                        "prepare_scene_packages",
                        "视频交付",
                        "视频合并",
                        "剪映交付",
                    )
                )
                or "不是可生成实体" in detail
                or "缺少计划身份" in detail
                or "缺少临时授权" in detail
                or "尚未装配" in detail
                or "校验失败" in detail
            ):
                summary = detail[:280]
            else:
                summary = f"{tool.spec.name} 执行失败，请稍后重试"
            return VideoToolResult(
                tool_name=tool.spec.name,
                public_summary=summary,
            )
        allowed_roots = {
            mutation.split(".", maxsplit=1)[0]
            for mutation in tool.spec.workspace_mutations
        }
        patch_roots = set(result.workspace_patch)
        if result.tool_name != tool.spec.name or not patch_roots.issubset(allowed_roots):
            undeclared = sorted(patch_roots - allowed_roots)
            # 用途：根键白名单失败时留下可诊断线索，不把完整 patch 打进公开文案。
            logger.warning(
                "工具结果根键校验失败 tool=%s undeclared=%s",
                tool.spec.name,
                undeclared,
            )
            raise VideoToolExecutionError("工具结果无效，请稍后重试")
        return result
