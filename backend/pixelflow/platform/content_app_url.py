"""Content-App API 根地址规范化；登录校验与 Provider 必须打到同一 /api 前缀。"""

from __future__ import annotations


def normalize_content_app_base_url(raw: str) -> str:
    """去掉空白、折叠路径双斜杠，并保证根地址以 /api 结尾。"""

    compact = "".join(raw.split())
    if "://" not in compact:
        raise ValueError("M06 Provider 必须配置受控 HTTPS 或 loopback content-app 地址")
    scheme, rest = compact.split("://", 1)
    base_url = f"{scheme}://{rest.replace('//', '/')}".rstrip("/")
    if not base_url.startswith(("https://", "http://127.0.0.1:")):
        raise ValueError("M06 Provider 必须配置受控 HTTPS 或 loopback content-app 地址")
    if not base_url.endswith("/api"):
        base_url = f"{base_url}/api"
    return base_url


def optional_content_app_base_url(raw: str) -> str | None:
    """空配置表示未装配 Provider；非空则规范化。"""

    if not "".join(raw.split()):
        return None
    return normalize_content_app_base_url(raw)
