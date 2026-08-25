"""Agent 控制面合同与请求处理器。"""

from .public_contracts import AgentSnapshotV1, PublicAgentEventV1

__all__ = [
    "AgentRunBridge",
    "AgentSnapshotV1",
    "PublicAgentEventV1",
]


def __getattr__(name: str) -> object:
    """按需加载依赖 Repository 的控制面服务，避免 ORM 注册时形成循环导入。"""

    if name == "AgentRunBridge":
        from .run_bridge import AgentRunBridge

        return AgentRunBridge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
