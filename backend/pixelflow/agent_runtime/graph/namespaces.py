"""统一 Agent Runtime 的 thread 与 checkpoint namespace 生成器。"""

from __future__ import annotations

from dataclasses import dataclass

ROOT_CHECKPOINT_NS = ""


def _validate_identifier(value: str) -> str:
    """拒绝会导致 namespace 歧义的空白或分隔符标识。"""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or ":" in value
    ):
        raise ValueError("图标识必须是非空、无首尾空白且不含冒号的字符串")
    return value


@dataclass(frozen=True, slots=True)
class GraphExecutionNamespace:
    """封装一次图执行所需的 LangGraph 可配置命名空间。"""

    thread_id: str
    checkpoint_ns: str

    def as_runnable_config(self) -> dict[str, dict[str, str]]:
        """生成可直接传给 LangGraph invoke 的配置。"""

        return {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": self.checkpoint_ns,
            }
        }


def supervisor_namespace(conversation_id: str) -> GraphExecutionNamespace:
    """生成会话级 Supervisor 的版本化执行命名空间。"""

    normalized_conversation_id = _validate_identifier(conversation_id)
    return GraphExecutionNamespace(
        thread_id=f"pf:conversation:{normalized_conversation_id}:supervisor:v1",
        checkpoint_ns=ROOT_CHECKPOINT_NS,
    )


def workflow_namespace(
    conversation_id: str,
    workflow_id: str,
) -> GraphExecutionNamespace:
    """生成会话内单个 Workflow 的版本化执行命名空间。"""

    normalized_conversation_id = _validate_identifier(conversation_id)
    normalized_workflow_id = _validate_identifier(workflow_id)
    return GraphExecutionNamespace(
        thread_id=(
            f"pf:conversation:{normalized_conversation_id}:"
            f"workflow:{normalized_workflow_id}:v1"
        ),
        checkpoint_ns=ROOT_CHECKPOINT_NS,
    )
