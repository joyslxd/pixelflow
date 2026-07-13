"""content-app 登录态查询接口。

pixelflow 不再提供本地登录、注册、初始化管理员等能力。登录统一发生在
content-app，前端访问 pixelflow 时只需要在请求头携带
``Authorization: Bearer <content-app-jwt>``。
"""

from __future__ import annotations

from dataclasses import dataclass
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.gateway.deps import get_current_user_from_request

router = APIRouter(prefix="/agent/auth", tags=["auth"])


class CurrentUserResponse(BaseModel):
    """当前 content-app 登录用户信息。"""

    authenticated: bool = True
    id: str
    username: str


@dataclass
class _RateEntry:
    failures: int = 0


_login_attempts: dict[str, int] = {}


COMMON_PASSWORDS = {
    "password",
    "password1",
    "qwerty123",
    "letmein1",
    "iloveyou",
}


class RegisterRequest(BaseModel):
    """Compatibility request model used in legacy auth tests."""

    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def _block_common(cls, value: str) -> str:
        if len(value) < 8:
            return value
        if value.strip().lower() in COMMON_PASSWORDS:
            raise ValueError("too common")
        return value


class LoginResponse(BaseModel):
    """Compatibility login response shape."""

    expires_in: int = 3600
    needs_setup: bool = False


class ChangePasswordRequest(BaseModel):
    """Compatibility password-change request shape."""

    current_password: str
    new_password: str = Field(min_length=8)
    new_email: str | None = None

    @field_validator("new_password")
    @classmethod
    def _block_common_new(cls, value: str) -> str:
        if len(value) < 8:
            return value
        if value.strip().lower() in COMMON_PASSWORDS:
            raise ValueError("too common")
        return value


def _record_login_failure(ip: str) -> None:
    _login_attempts[ip] = _login_attempts.get(ip, 0) + 1


def _record_login_success(ip: str) -> None:
    _login_attempts.pop(ip, None)


def _check_rate_limit(ip: str, max_attempts: int = 5) -> None:
    attempts = _login_attempts.get(ip, 0)
    if attempts >= max_attempts:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Please try again later.",
        )


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(request: Request) -> CurrentUserResponse:
    """返回当前用户。

    Java 类比：这相当于一个只读的 ``/current-user`` Controller。它不会创建
    pixelflow session，也不会刷新 token，只是复用全局 AuthMiddleware 已校验过的
    content-app 登录态。
    """
    user = await get_current_user_from_request(request)
    return CurrentUserResponse(id=str(user.id), username=getattr(user, "username", str(user.id)))
