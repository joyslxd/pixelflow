"""验证 PixelFlow 自有请求级用户上下文的 owner 隔离语义。"""

from dataclasses import dataclass

import pytest

from pixelflow.platform.auth_context import (
    DEFAULT_USER_ID,
    get_effective_user_id,
    require_current_user,
    reset_current_user,
    set_current_user,
)


@dataclass(frozen=True)
class _User:
    id: str


def test_context_user_is_scoped_and_resettable() -> None:
    """认证中间件必须能在 finally 中恢复默认用户桶。"""

    assert get_effective_user_id() == DEFAULT_USER_ID
    token = set_current_user(_User(id="user-1"))
    try:
        assert require_current_user().id == "user-1"
        assert get_effective_user_id() == "user-1"
    finally:
        reset_current_user(token)
    assert get_effective_user_id() == DEFAULT_USER_ID


def test_require_current_user_rejects_missing_context() -> None:
    """没有认证上下文时禁止 Repository 隐式读取 owner。"""

    with pytest.raises(RuntimeError, match="用户上下文"):
        require_current_user()
