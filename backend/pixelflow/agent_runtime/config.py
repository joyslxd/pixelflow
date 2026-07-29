"""Agent Runtime 的启动配置合同。"""

from __future__ import annotations

import json
import os
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

AgentRuntimeMode = Literal["off", "shadow", "assist", "primary"]
AgentRuntimeIntent = Literal["video", "image", "ppt", "video_analysis"]
TOKENS_PER_K = 1024


class ContextBudgetConfig(BaseModel):
    """所有 Agent 节点共享的上下文预算配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    effective_context_k: int = Field(
        default=896,
        gt=0,
        description="用途：设置统一有效上下文窗口，单位 K（1K=1024 tokens）；影响：所有当前和未来 Agent 节点按该上限计算预算。",
    )
    output_reserve_k: int = Field(
        default=32,
        gt=0,
        description="用途：预留模型输出空间，单位 K（1K=1024 tokens）；影响：该值会从有效上下文中扣除，避免输出被输入挤占。",
    )
    safety_reserve_k: int = Field(
        default=32,
        gt=0,
        description="用途：预留协议、估算误差和供应商波动空间，单位 K（1K=1024 tokens）；影响：该值会从有效上下文中扣除并降低溢出风险。",
    )
    require_verified_model_profile: bool = Field(
        default=True,
        description="用途：要求模型能力档案已验证；影响：true 时实际流程缺少已验证档案会启动失败，不再静默使用 128K 兜底。",
    )

    @model_validator(mode="after")
    def validate_usable_input(self) -> Self:
        """保证输出与安全预留后仍有可用输入空间。"""

        if self.usable_input_tokens <= 0:
            raise ValueError("统一上下文预算扣除输出和安全预留后必须保留可用输入")
        return self

    @property
    def effective_context_tokens(self) -> int:
        return self.effective_context_k * TOKENS_PER_K

    @property
    def output_reserve_tokens(self) -> int:
        return self.output_reserve_k * TOKENS_PER_K

    @property
    def safety_reserve_tokens(self) -> int:
        return self.safety_reserve_k * TOKENS_PER_K

    @property
    def usable_input_tokens(self) -> int:
        return (
            self.effective_context_tokens
            - self.output_reserve_tokens
            - self.safety_reserve_tokens
        )


class AgentRuntimeConfig(BaseModel):
    """进程启动时冻结的 Agent Runtime 开关。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: AgentRuntimeMode = Field(
        default="off",
        description="用途：选择会话编排模式；影响：off 保持现有 v2 流程，其他值只在后续获批发布阶段启用。",
    )
    enabled_intents: tuple[AgentRuntimeIntent, ...] = Field(
        default=(),
        description="用途：限定 primary 可接管的业务 intent；影响：空列表不允许任何业务进入 primary。",
    )
    new_conversation_rollout_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="用途：控制新建对话进入新 Runtime 的比例；影响：0 表示全部新对话继续使用现有 v2。",
    )
    context_compaction_enabled: bool = Field(
        default=False,
        description="用途：控制 Agent 上下文压缩能力；影响：false 时不会启动新 Runtime 的压缩流程。",
    )
    context_budget: ContextBudgetConfig = Field(
        default_factory=ContextBudgetConfig,
        description="用途：集中配置所有 Agent 节点的上下文、输出与安全预算；影响：修改后统一改变新进程中的预算计算。",
    )
    compaction_retry_backoff_seconds: int = Field(
        default=30,
        gt=0,
        description="用途：设置压缩失败后的持久化重试退避秒数；影响：退避期内 Snapshot、SSE 和轮询只读状态而不重新执行压缩。",
    )


def _parse_enabled_intents(raw_value: str) -> tuple[str, ...]:
    value = raw_value.strip()
    if not value:
        raise ValueError("enabled_intents 显式配置不能为空")
    if value.startswith("["):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("enabled_intents 必须是字符串数组")
        return tuple(item.strip() for item in parsed)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_rollout_percent(raw_value: str) -> int:
    value = raw_value.strip()
    if re.fullmatch(r"(?:0|[1-9][0-9]*)", value, flags=re.ASCII) is None:
        raise ValueError("new_conversation_rollout_percent 必须是十进制整数")
    return int(value)


def _parse_bool(raw_value: str, *, field_name: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"{field_name} 必须是布尔值")


def _parse_positive_int(raw_value: str, *, field_name: str) -> int:
    value = raw_value.strip()
    if re.fullmatch(r"[1-9][0-9]*", value, flags=re.ASCII) is None:
        raise ValueError(f"{field_name} 必须是正十进制整数")
    return int(value)


def load_agent_runtime_config_from_env() -> AgentRuntimeConfig:
    """读取并校验进程启动环境；缺省值保持新 Runtime 完全关闭。"""

    try:
        enabled_intents_value = os.getenv("PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS")
        return AgentRuntimeConfig(
            mode=os.getenv("PIXELFLOW_AGENT_RUNTIME_MODE", "off").strip(),
            enabled_intents=(
                ()
                if enabled_intents_value is None
                else _parse_enabled_intents(enabled_intents_value)
            ),
            new_conversation_rollout_percent=_parse_rollout_percent(
                os.getenv(
                    "PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT",
                    "0",
                ),
            ),
            context_compaction_enabled=_parse_bool(
                os.getenv("PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED", "false"),
                field_name="context_compaction_enabled",
            ),
            context_budget=ContextBudgetConfig(
                effective_context_k=_parse_positive_int(
                    os.getenv(
                        "PIXELFLOW_AGENT_RUNTIME_CONTEXT_EFFECTIVE_K",
                        "896",
                    ),
                    field_name="effective_context_k",
                ),
                output_reserve_k=_parse_positive_int(
                    os.getenv(
                        "PIXELFLOW_AGENT_RUNTIME_CONTEXT_OUTPUT_RESERVE_K",
                        "32",
                    ),
                    field_name="output_reserve_k",
                ),
                safety_reserve_k=_parse_positive_int(
                    os.getenv(
                        "PIXELFLOW_AGENT_RUNTIME_CONTEXT_SAFETY_RESERVE_K",
                        "32",
                    ),
                    field_name="safety_reserve_k",
                ),
                require_verified_model_profile=_parse_bool(
                    os.getenv(
                        "PIXELFLOW_AGENT_RUNTIME_CONTEXT_REQUIRE_VERIFIED_MODEL_PROFILE",
                        "true",
                    ),
                    field_name="require_verified_model_profile",
                ),
            ),
            compaction_retry_backoff_seconds=_parse_positive_int(
                os.getenv(
                    "PIXELFLOW_AGENT_RUNTIME_COMPACTION_RETRY_BACKOFF_SECONDS",
                    "30",
                ),
                field_name="compaction_retry_backoff_seconds",
            ),
        )
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ValueError(f"Agent Runtime 配置无效：{exc}") from None


def validate_agent_runtime_startup_config() -> AgentRuntimeConfig:
    """在 Gateway 导入路由前完成 fail-closed 启动校验。"""

    return load_agent_runtime_config_from_env()


__all__ = [
    "AgentRuntimeConfig",
    "AgentRuntimeIntent",
    "AgentRuntimeMode",
    "ContextBudgetConfig",
    "TOKENS_PER_K",
    "load_agent_runtime_config_from_env",
    "validate_agent_runtime_startup_config",
]
