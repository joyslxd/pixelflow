"""P0-5：旧视频 Job HTTP 路由已物理删除。"""

from __future__ import annotations

import importlib

import pytest

from app.gateway.app import create_app


def test_pixelflow_video_job_routes_not_mounted() -> None:
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    forbidden_prefixes = (
        "/agent/flows/video/prepare-scene-packages",
        "/agent/flows/video/generate-scene-assets",
        "/agent/flows/video/generate-scenes",
        "/agent/flows/video/generate-direct",
        "/agent/flows/video/merge",
        "/agent/flows/video/quality-review",
        "/agent/flows/video/update-scene-package-asset",
        "/agent/flows/video/analyze-storyboards",
    )
    mounted = {path for path in paths if any(path.startswith(p) for p in forbidden_prefixes)}
    assert not mounted, f"旧视频 Job 路由仍挂载: {sorted(mounted)}"


def test_pixelflow_video_router_module_deleted() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.gateway.routers.pixelflow_video")
