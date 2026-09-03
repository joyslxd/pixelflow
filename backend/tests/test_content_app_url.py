"""验证 Content-App API 根地址会补齐 /api，避免打到站点 HTML。"""

from pixelflow.platform.content_app_url import (
    normalize_content_app_base_url,
    optional_content_app_base_url,
)


def test_normalize_content_app_base_url_appends_api_to_site_root() -> None:
    assert normalize_content_app_base_url("https://test-video.borgrise.com") == (
        "https://test-video.borgrise.com/api"
    )


def test_normalize_content_app_base_url_collapses_space_before_api() -> None:
    assert normalize_content_app_base_url("https://test-video.borgrise.com/ /api") == (
        "https://test-video.borgrise.com/api"
    )


def test_normalize_content_app_base_url_keeps_existing_api_suffix() -> None:
    assert normalize_content_app_base_url("https://test-video.borgrise.com/api/") == (
        "https://test-video.borgrise.com/api"
    )


def test_optional_content_app_base_url_treats_blank_as_unconfigured() -> None:
    assert optional_content_app_base_url("   ") is None


def test_normalize_content_app_base_url_allows_registered_http_vitamazing_host() -> None:
    assert normalize_content_app_base_url("http://creator.vitamazing.top/api") == (
        "http://creator.vitamazing.top/api"
    )


def test_normalize_content_app_base_url_rejects_public_http_ip() -> None:
    try:
        normalize_content_app_base_url("http://115.191.36.147/api")
    except ValueError as exc:
        assert "已登记" in str(exc)
    else:
        raise AssertionError("公网 IP 不应作为 content-app 根")
