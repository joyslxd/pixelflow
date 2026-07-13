"""认证兼容包。

PixelFlow 运行时登录已经统一交给 content-app，网关只接收
``Authorization: Bearer <content-app-jwt>``。这里仅保留错误响应和用户模型等
少量兼容导出，避免 ``app.gateway.auth.errors`` 这类 import 顺手加载旧的本地
密码登录、SQLite 用户仓库或 reset-admin 逻辑。

不要在新代码里从本包恢复本地登录/注册/cookie session；需要当前用户时请走
``app.gateway.content_app_auth`` 或 ``app.gateway.deps.get_current_user_from_request``。
"""

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse, TokenError
from app.gateway.auth.models import User, UserResponse
from app.gateway.auth.jwt import create_access_token, decode_token
from app.gateway.auth.password import hash_password, verify_password

__all__ = [
    # 错误响应：AuthMiddleware/deps 仍复用这些结构返回稳定 JSON。
    "AuthErrorCode",
    "AuthErrorResponse",
    "TokenError",
    # 用户模型：authz.AuthContext 仍把它作为类型使用。
    "User",
    "UserResponse",
    # 与原 auth 包兼容：单测和部分历史模块仍从该入口导入认证原语。
    "create_access_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
