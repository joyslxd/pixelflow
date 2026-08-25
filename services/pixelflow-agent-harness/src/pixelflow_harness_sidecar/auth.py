"""校验 Gateway 发给 Sidecar 的短期服务 JWT。"""

from __future__ import annotations

from typing import Any

import jwt


class ServiceJwtValidationError(ValueError):
    """表示服务身份 JWT 不满足固定内部调用合同。"""


def validate_service_jwt(
    authorization: str | None,
    *,
    verify_key: str,
    issuer: str,
    audience: str,
) -> dict[str, Any]:
    """校验签名、期限、issuer、audience 与非空实例身份，不返回原始 token。"""

    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or not token or not verify_key:
        raise ServiceJwtValidationError("service_jwt_missing")
    try:
        claims = jwt.decode(
            token,
            verify_key,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "iss", "aud", "service_instance_id"]},
        )
    except jwt.PyJWTError as error:
        raise ServiceJwtValidationError("service_jwt_invalid") from error
    instance_id = claims.get("service_instance_id")
    if not isinstance(instance_id, str) or not instance_id.strip() or len(instance_id) > 128:
        raise ServiceJwtValidationError("service_jwt_instance_invalid")
    return claims
