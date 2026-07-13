"""Typed error definitions for auth module.

AuthErrorCode: exhaustive enum of all auth failure conditions.
TokenError: exhaustive enum of JWT decode failures.
AuthErrorResponse: structured error payload for HTTP responses.
"""

from enum import StrEnum

from pydantic import BaseModel


class AuthErrorCode(StrEnum):
    """认证错误码。

    这里保留旧本地登录错误码，是为了兼容少量测试和历史返回结构；新的网关主路径
    使用 content-app Authorization，重点看 ``NOT_AUTHENTICATED``、``TOKEN_*``、
    ``USER_DISABLED`` 和 ``AUTH_SERVICE_UNAVAILABLE``。
    """

    INVALID_CREDENTIALS = "invalid_credentials"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_INVALID = "token_invalid"
    USER_NOT_FOUND = "user_not_found"
    EMAIL_ALREADY_EXISTS = "email_already_exists"
    PROVIDER_NOT_FOUND = "provider_not_found"
    NOT_AUTHENTICATED = "not_authenticated"
    USER_DISABLED = "user_disabled"
    AUTH_SERVICE_UNAVAILABLE = "auth_service_unavailable"
    SYSTEM_ALREADY_INITIALIZED = "system_already_initialized"


class TokenError(StrEnum):
    """Exhaustive list of JWT decode failure reasons."""

    EXPIRED = "expired"
    INVALID_SIGNATURE = "invalid_signature"
    MALFORMED = "malformed"


class AuthErrorResponse(BaseModel):
    """Structured error response — replaces bare `detail` strings."""

    code: AuthErrorCode
    message: str


def token_error_to_code(err: TokenError) -> AuthErrorCode:
    """Map TokenError to AuthErrorCode — single source of truth."""
    if err == TokenError.EXPIRED:
        return AuthErrorCode.TOKEN_EXPIRED
    return AuthErrorCode.TOKEN_INVALID
