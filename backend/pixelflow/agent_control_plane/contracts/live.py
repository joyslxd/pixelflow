"""live Turn、人工响应与公开中断投影合同。"""

from datetime import datetime
from math import isfinite
from typing import Any
from uuid import UUID

from pydantic import Field, JsonValue, field_validator

from .base import ContractModel
from .enums import AgentAction, AgentIntent


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
        if type(value) is list:
            for item in value:
                _validate_strict_json(item, ancestors)
            return
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON 对象键必须是字符串")
            _validate_strict_json(item, ancestors)
    finally:
        ancestors.remove(identity)


def validate_strict_json_object(value: Any) -> Any:
    """在 Pydantic 转换前拒绝非普通字典及其非法 JSON 子值。"""

    if type(value) is not dict:
        raise ValueError("必须是普通 JSON 对象")
    _validate_strict_json(value, set())
    return value


def validate_strict_json_array(value: Any) -> Any:
    """在 Pydantic 转换前拒绝非列表及其非法 JSON 子值。"""

    if type(value) is not list:
        raise ValueError("必须是 JSON 数组")
    _validate_strict_json(value, set())
    return value


class ExplicitActionSignal(ContractModel):
    """保存按钮或人工决策控件提交的结构化动作。"""

    action: AgentAction
    intent: AgentIntent | None = None
    workflow_id: str | None = Field(default=None, min_length=1)
    stage: str | None = Field(default=None, min_length=1)
    artifact_ref: str | None = Field(default=None, min_length=1)
    patch: dict[str, JsonValue] = Field(default_factory=dict)

    _validate_patch = field_validator("patch", mode="before")(
        validate_strict_json_object
    )


class InterruptResponseValue(ContractModel):
    """恢复原 Turn 时交给 Graph 的权威人工响应值。"""

    content: str = Field(min_length=1)
    materials: list[dict[str, JsonValue]] = Field(default_factory=list)
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    explicit_action: ExplicitActionSignal | None = None

    _validate_materials = field_validator("materials", mode="before")(
        validate_strict_json_array
    )


class InterruptResponseRequest(ContractModel):
    """保持 M12 外壳并按响应 ID 幂等恢复原 Turn。"""

    client_response_id: UUID
    value: InterruptResponseValue


class AgentInterruptProjection(ContractModel):
    """Snapshot 与 SSE 对外公开的中断投影。"""

    interrupt_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    workflow_id: str | None = Field(default=None, min_length=1)
    turn_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    opened_at: datetime

    _validate_payload = field_validator("payload", mode="before")(
        validate_strict_json_object
    )


__all__ = [
    "AgentInterruptProjection",
    "ExplicitActionSignal",
    "InterruptResponseRequest",
    "InterruptResponseValue",
]
