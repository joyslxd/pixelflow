"""工作区缩略图必须能代理已生成图片，而不仅是用户上传素材。"""

from __future__ import annotations

from app.gateway.routers.pixelflow_conversations import (
    _safe_asset_thumbnail_target,
    _workspace_asset_thumbnail_url,
)


def test_thumbnail_url_uses_generated_asset_image_url() -> None:
    payload = {
        "workspace_schema_version": 2,
        "asset_registry": [
            {
                "asset_id": "asset_scene_01",
                "origin": "planned_generation",
                "state": "ready",
                "image_url": "https://bucket.tos-cn-beijing.volces.com/kitchen.jpeg",
            }
        ],
    }

    url = _workspace_asset_thumbnail_url(payload, "asset_scene_01")

    assert url == "https://bucket.tos-cn-beijing.volces.com/kitchen.jpeg"
    assert _safe_asset_thumbnail_target(url) == url


def test_thumbnail_url_uses_uploaded_material_not_generated_field() -> None:
    payload = {
        "workspace_schema_version": 2,
        "asset_registry": [
            {
                "asset_id": "asset_material_1",
                "origin": "existing_material",
                "state": "ready",
                "source_material_id": "material-1",
                "image_url": "https://bucket.tos-cn-beijing.volces.com/should-not-use.jpeg",
            }
        ],
        "materials": [
            {
                "material_id": "material-1",
                "kind": "image",
                "url": "https://cdn.vitamazing.top/upload.png",
            }
        ],
    }

    assert _workspace_asset_thumbnail_url(payload, "asset_material_1") == "https://cdn.vitamazing.top/upload.png"


def test_thumbnail_url_ignores_generating_assets_without_ready_image() -> None:
    payload = {
        "workspace_schema_version": 2,
        "asset_registry": [
            {
                "asset_id": "asset_character_01",
                "origin": "planned_generation",
                "state": "generating",
                "image_url": "https://bucket.tos-cn-beijing.volces.com/pending.jpeg",
            }
        ],
    }

    assert _workspace_asset_thumbnail_url(payload, "asset_character_01") is None


def test_thumbnail_rejects_non_allowlisted_host() -> None:
    assert _safe_asset_thumbnail_target("https://evil.example/kitchen.jpeg") is None
    assert _safe_asset_thumbnail_target("http://bucket.tos-cn-beijing.volces.com/kitchen.jpeg") is None
