"""验证 LangGraph 兼容入口复用 content-app Authorization。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langgraph_sdk import Auth

from app.gateway.content_app_auth import ContentAppAuthError, ContentAppUser
from app.gateway.langgraph_auth import add_owner_filter, authenticate


def _request(authorization: str | None = None):
    """构造只包含 LangGraph 鉴权所需字段的请求。"""
    headers = {} if authorization is None else {"Authorization": authorization}
    return SimpleNamespace(headers=headers)


def test_missing_authorization_raises_401():
    """缺少 Authorization 时保持 content-app 的未认证语义。"""
    with pytest.raises(Auth.exceptions.HTTPException) as exc_info:
        asyncio.run(authenticate(_request()))

    assert exc_info.value.status_code == 401


def test_valid_authorization_returns_content_app_user_id():
    """有效 Authorization 直接返回 content-app 用户 ID。"""
    user = ContentAppUser(id="user-1", username="user-1")
    authenticate_header = AsyncMock(return_value=user)

    with patch("app.gateway.langgraph_auth.authenticate_authorization_header", authenticate_header):
        result = asyncio.run(authenticate(_request("Bearer user-token")))

    assert result == "user-1"
    authenticate_header.assert_awaited_once_with("Bearer user-token")


def test_content_app_error_preserves_status_code():
    """content-app 拒绝用户时不能降级成本地登录或其他状态码。"""
    authenticate_header = AsyncMock(
        side_effect=ContentAppAuthError(
            status_code=403,
            code="user_disabled",
            message="该账号已被禁用",
        )
    )

    with patch("app.gateway.langgraph_auth.authenticate_authorization_header", authenticate_header):
        with pytest.raises(Auth.exceptions.HTTPException) as exc_info:
            asyncio.run(authenticate(_request("Bearer disabled-token")))

    assert exc_info.value.status_code == 403
    assert "禁用" in str(exc_info.value.detail)


class _FakeUser:
    """提供 LangGraph AuthContext 所需的最小用户结构。"""

    def __init__(self, identity: str):
        self.identity = identity
        self.is_authenticated = True
        self.display_name = identity


def _context(user_id: str):
    """构造 owner filter 使用的最小鉴权上下文。"""
    return Auth.types.AuthContext(
        resource="threads",
        action="create",
        user=_FakeUser(user_id),
        permissions=[],
    )


def test_owner_filter_injects_user_id():
    """新建资源时写入当前 content-app 用户。"""
    value = {}

    result = asyncio.run(add_owner_filter(_context("user-a"), value))

    assert value["metadata"]["user_id"] == "user-a"
    assert result == {"user_id": "user-a"}


def test_owner_filter_preserves_other_metadata():
    """用户隔离字段不能覆盖标题等业务元数据。"""
    value = {"metadata": {"title": "hello"}}

    asyncio.run(add_owner_filter(_context("user-a"), value))

    assert value["metadata"] == {"title": "hello", "user_id": "user-a"}


def test_owner_filter_overrides_forged_user_id():
    """调用方伪造的 owner 必须由当前登录用户覆盖。"""
    value = {"metadata": {"user_id": "attacker"}}

    asyncio.run(add_owner_filter(_context("real-owner"), value))

    assert value["metadata"]["user_id"] == "real-owner"


def test_langgraph_json_uses_shared_auth_handler():
    """LangGraph 配置继续指向共享 content-app 鉴权模块。"""
    config = json.loads((Path(__file__).parent.parent / "langgraph.json").read_text(encoding="utf-8"))

    assert "auth" in config
    assert "langgraph_auth" in config["auth"]["path"]


def test_auth_handler_registers_authenticate_and_owner_filter():
    """兼容入口必须同时注册身份校验和 owner filter。"""
    from app.gateway.langgraph_auth import auth

    assert auth._authenticate_handler is not None
    assert len(auth._global_handlers) == 1
