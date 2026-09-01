"""新会话 Runtime API 与 Port 入口使用的请求合同。"""

from math import isfinite
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator

from .base import ContractModel
from .enums import OrchestrationMode


def _validate_strict_json(value: object, ancestors: set[int]) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("JSON 数字必须是有限值")
        return
    if type(value) not in {list, dict}:
        raise ValueError("只允许 JSON 原生值")
    identity = id(value)
    if identity in ancestors:
        raise ValueError("JSON 值不能包含循环引用")
    ancestors.add(identity)
    try:
        for item in value if type(value) is list else value.values():
            _validate_strict_json(item, ancestors)
    finally:
        ancestors.remove(identity)


def validate_strict_json_object(value: Any) -> Any:
    if type(value) is not dict:
        raise ValueError("必须是普通 JSON 对象")
    _validate_strict_json(value, set())
    return value


def validate_strict_json_array(value: Any) -> Any:
    if type(value) is not list:
        raise ValueError("必须是 JSON 数组")
    _validate_strict_json(value, set())
    return value


class ExplicitActionSignal(ContractModel):
    """浏览器显式工作区命令摘要；不参与 Agent 工具顺序决策。"""

    action: str = Field(min_length=1, max_length=64)
    patch: dict[str, JsonValue] = Field(default_factory=dict)

    _validate_patch = field_validator("patch", mode="before")(validate_strict_json_object)


class ConversationOrchestration(ContractModel):
    """服务端拥有的对话编排归属快照。"""

    orchestration_mode: OrchestrationMode
    orchestration_version: Literal[1] = 1


class TurnStartRequest(ContractModel):
    """原子保存用户输入并创建或复用 Turn 的请求。"""

    client_input_id: UUID
    content: str = Field(min_length=1)
    materials: list[dict[str, JsonValue]] = Field(default_factory=list)
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    expected_context_version: int = Field(ge=0)
    explicit_action: ExplicitActionSignal | None = None

    _validate_materials = field_validator("materials", mode="before")(
        validate_strict_json_array
    )


class WorkspaceCommandRequest(ContractModel):
    """前端提交工作区变更的通用命令，所有业务修改均需携带 revision。"""

    client_command_id: UUID
    workspace_id: str = Field(min_length=1, max_length=64)
    expected_workspace_revision: int = Field(ge=1)
    patch: dict[str, JsonValue]

    _validate_patch = field_validator("patch", mode="before")(
        validate_strict_json_object
    )
