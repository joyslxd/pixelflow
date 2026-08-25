"""定义框架无关的聊天模型 Port。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import ChatCompletion, ChatModelRequest


@runtime_checkable
class ChatModelPort(Protocol):
    """类似 Java Client 接口：领域层不依赖 LangChain Message 或模型类。"""

    async def complete(self, request: ChatModelRequest) -> ChatCompletion:
        """执行单次已清洗文本生成，并返回最小安全结果。"""

    async def aclose(self) -> None:
        """关闭本 Client 自己持有的网络资源。"""


__all__ = ["ChatModelPort"]
