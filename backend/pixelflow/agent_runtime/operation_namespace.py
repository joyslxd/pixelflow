"""M06 外部任务完成恢复使用的稳定命名空间。"""

from __future__ import annotations

from dataclasses import dataclass


def _validate_identifier(value: str) -> str:
    """拒绝会导致恢复身份歧义的空白或分隔符标识。"""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or ":" in value
    ):
        raise ValueError("恢复标识必须非空、无首尾空白且不含冒号")
    return value


@dataclass(frozen=True, slots=True)
class OperationExecutionNamespace:
    """封装同一工作流Operation完成恢复的稳定身份。"""

    thread_id: str
    checkpoint_ns: str = ""


def workflow_operation_namespace(
    conversation_id: str,
    workflow_id: str,
) -> OperationExecutionNamespace:
    """按会话和工作流生成V2 Operation恢复命名空间。"""

    conversation = _validate_identifier(conversation_id)
    workflow = _validate_identifier(workflow_id)
    return OperationExecutionNamespace(
        thread_id=f"pf:conversation:{conversation}:operation:{workflow}:v2",
    )


__all__ = ["OperationExecutionNamespace", "workflow_operation_namespace"]
