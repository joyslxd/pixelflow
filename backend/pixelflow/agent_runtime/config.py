"""Agent Runtime 的启动配置合同。"""

from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

AgentRuntimeMode = Literal["off", "shadow", "assist", "primary"]
AgentRuntimeIntent = Literal["video", "image", "ppt", "video_analysis"]


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


def _parse_enabled_intents(raw_value: str) -> tuple[str, ...]:
    value = raw_value.strip()
    if not value:
        return ()
    if value.startswith("["):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("enabled_intents 必须是字符串数组")
        return tuple(item.strip() for item in parsed)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError("context_compaction_enabled 必须是布尔值")


def load_agent_runtime_config_from_env() -> AgentRuntimeConfig:
    """读取并校验进程启动环境；缺省值保持新 Runtime 完全关闭。"""

    try:
        return AgentRuntimeConfig(
            mode=os.getenv("PIXELFLOW_AGENT_RUNTIME_MODE", "off").strip(),
            enabled_intents=_parse_enabled_intents(
                os.getenv("PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS", ""),
            ),
            new_conversation_rollout_percent=os.getenv(
                "PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT",
                "0",
            ),
            context_compaction_enabled=_parse_bool(
                os.getenv("PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED", "false"),
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
    "load_agent_runtime_config_from_env",
    "validate_agent_runtime_startup_config",
]
