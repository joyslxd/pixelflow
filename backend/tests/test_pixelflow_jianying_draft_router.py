"""P0-5：旧剪映 Job HTTP 路由已物理删除。"""

from __future__ import annotations

import importlib

import pytest

from app.gateway.app import create_app


def test_jianying_draft_router_not_mounted() -> None:
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    forbidden = {
        "/agent/flows/video/jianying-draft/capability",
        "/agent/flows/video/jianying-draft/start",
    }
    assert forbidden.isdisjoint(paths)
    assert not any(
        "/agent/flows/video/jianying-draft" in getattr(route, "path", "")
        for route in app.routes
    )


def test_jianying_draft_router_module_deleted() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.gateway.routers.pixelflow_jianying_draft")
