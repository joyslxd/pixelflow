"""Content-App API 根地址规范化；登录校验与 Provider 必须打到同一 /api 前缀。"""

from __future__ import annotations

from urllib.parse import urlparse

# 用途：公网 HTTP 仅放行已验证的 content-app 站点；影响：其它 http 主机仍不能作为生图/生视频根。
_ALLOWED_HTTP_HOSTS = ("vitamazing.top",)
_ALLOWED_HTTP_HOST_SUFFIXES = (".vitamazing.top",)


def _is_allowed_content_app_base(base_url: str) -> bool:
    """HTTPS、本机回环或已登记的 vitamazing 站点才可作为 Provider 根。"""

    if base_url.startswith("https://"):
        return True
    if base_url.startswith("http://127.0.0.1:"):
        return True
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "http" or parsed.username or parsed.password or not host:
        return False
    return host in _ALLOWED_HTTP_HOSTS or any(host.endswith(suffix) for suffix in _ALLOWED_HTTP_HOST_SUFFIXES)


def normalize_content_app_base_url(raw: str) -> str:
    """去掉空白、折叠路径双斜杠，并保证根地址以 /api 结尾。"""

    compact = "".join(raw.split())
    if "://" not in compact:
        raise ValueError("M06 Provider 必须配置受控 HTTPS、loopback 或已登记 content-app 地址")
    scheme, rest = compact.split("://", 1)
    base_url = f"{scheme}://{rest.replace('//', '/')}".rstrip("/")
    if not _is_allowed_content_app_base(base_url):
        raise ValueError("M06 Provider 必须配置受控 HTTPS、loopback 或已登记 content-app 地址")
    if not base_url.endswith("/api"):
        base_url = f"{base_url}/api"
    return base_url


def optional_content_app_base_url(raw: str) -> str | None:
    """空配置表示未装配 Provider；非空则规范化。"""

    if not "".join(raw.split()):
        return None
    return normalize_content_app_base_url(raw)
