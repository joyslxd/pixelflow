"""VideoAgent 单次执行使用的临时凭据。"""

from __future__ import annotations


class VideoAgentCredentialUnavailableError(RuntimeError):
    """临时凭据缺失或已离开当前请求边界。"""


class TransientVideoAgentCredential:
    """只在当前 Controller 调用链中提供 Authorization，禁止持久化。"""

    __slots__ = ("_authorization", "_discarded")

    def __init__(self, authorization: str) -> None:
        normalized = authorization.strip() if isinstance(authorization, str) else ""
        if not normalized:
            raise ValueError("VideoAgent 当前请求缺少临时 Authorization")
        self._authorization = normalized
        self._discarded = False

    def borrow_authorization(self) -> str:
        """在当前同步执行边界借用凭据，不转移所有权。"""

        if self._discarded or not self._authorization:
            raise VideoAgentCredentialUnavailableError(
                "VideoAgent 临时 Authorization 已不可用"
            )
        return self._authorization

    def discard(self) -> None:
        """请求结束时主动清除对象持有的凭据引用。"""

        self._authorization = ""
        self._discarded = True

    def __copy__(self) -> None:
        raise TypeError("VideoAgent 临时凭据禁止复制")

    def __deepcopy__(self, _memo: dict[int, object]) -> None:
        raise TypeError("VideoAgent 临时凭据禁止复制")

    def __getstate__(self) -> None:
        raise TypeError("VideoAgent 临时凭据禁止序列化")

    def __reduce_ex__(self, _protocol: int) -> None:
        raise TypeError("VideoAgent 临时凭据禁止序列化")

    def __repr__(self) -> str:
        state = "已清理" if self._discarded else "可用"
        return f"TransientVideoAgentCredential(state={state})"
