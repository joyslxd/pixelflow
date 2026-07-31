"""为 live Runtime 派生跨进程稳定身份。"""

from uuid import NAMESPACE_URL, UUID, uuid5


def _stable_hex(canonical_key: str) -> str:
    return uuid5(NAMESPACE_URL, canonical_key).hex


def conversation_message_id(conversation_id: str, client_input_id: UUID) -> str:
    """按对话与客户端输入生成可重试的可见消息 ID。"""

    key = f"pixelflow-conversation-message:{conversation_id}:{client_input_id}"
    return _stable_hex(key)


def turn_id(conversation_id: str, client_input_id: UUID) -> str:
    """按对话与客户端输入生成可重放的 Turn ID。"""

    key = f"pixelflow-agent-turn:{conversation_id}:{client_input_id}"
    return f"turn_{_stable_hex(key)}"


def workflow_id(conversation_id: str, client_input_id: UUID) -> str:
    """按首个 Turn 输入生成对话隔离的 Workflow ID。"""

    key = f"pixelflow-agent-workflow:{conversation_id}:{client_input_id}"
    return f"wf_{_stable_hex(key)}"


def interrupt_id(turn_id: str, reason_code: str) -> str:
    """按原 Turn 与原因生成重复执行一致的中断 ID。"""

    key = f"pixelflow-agent-interrupt:{turn_id}:{reason_code}"
    return f"interrupt_{_stable_hex(key)}"


def projection_message_id(
    workflow_id: str,
    stage: str,
    stage_version: int,
    action_key: str,
) -> str:
    """按工作流阶段动作生成幂等的助手投影消息 ID。"""

    key = (
        "pixelflow-agent-projection-message:"
        f"{workflow_id}:{stage}:{stage_version}:{action_key}"
    )
    return _stable_hex(key)


__all__ = [
    "conversation_message_id",
    "interrupt_id",
    "projection_message_id",
    "turn_id",
    "workflow_id",
]
