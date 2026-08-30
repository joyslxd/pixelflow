"""验证 Composer 上传的图片材料可安全传入视频生成请求。"""

from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolExecutionError
from pixelflow.video.adapters.operations.scenes import (
    _validate_v2_scene_asset_references,
    _workspace_image_material_urls,
)


def test_workspace_image_material_upgrades_content_app_http_url_to_https() -> None:
    """历史测试环境的 HTTP TOS 地址应升级为 Provider 所需 HTTPS，非图片不得混入。"""

    assert _workspace_image_material_urls(
        [
            {"kind": "image", "url": "http://devtos.example.invalid/path/image.jpeg"},
            {"kind": "video", "url": "https://devtos.example.invalid/path/video.mp4"},
            {"kind": "image", "url": "not-a-url"},
        ]
    ) == ["https://devtos.example.invalid/path/image.jpeg"]


def test_workspace_image_material_uses_only_prompt_package_registered_reference() -> None:
    assert _workspace_image_material_urls(
        [
            {"material_id": "m1", "kind": "image", "url": "https://example.invalid/one.jpeg"},
            {"material_id": "m2", "kind": "image", "url": "https://example.invalid/two.jpeg"},
        ],
        only_material_ids={"m2"},
    ) == ["https://example.invalid/two.jpeg"]


def test_v2_scene_rejects_planned_asset_before_video_generation() -> None:
    with pytest.raises(VideoToolExecutionError, match="尚未就绪"):
        _validate_v2_scene_asset_references(
            {"asset_registry": [{"asset_id": "asset-character", "state": "planned", "usable_for_video": False}]},
            {"reference_asset_ids": ["asset-character"]},
        )


def test_v2_scene_selects_only_registered_existing_material() -> None:
    assert _validate_v2_scene_asset_references(
        {"asset_registry": [{
            "asset_id": "asset-product",
            "source_material_id": "m2",
            "state": "ready",
            "usable_for_video": True,
        }]},
        {"reference_asset_ids": ["asset-product"]},
    ) == {"m2"}
