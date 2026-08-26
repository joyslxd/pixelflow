"""验证 Gateway 容器探针不依赖终端用户鉴权。"""

from __future__ import annotations

import httpx
import pytest

from app.gateway.app import create_app


@pytest.mark.asyncio
async def test_gateway_live_and_ready_are_public_and_safe() -> None:
    """存活探针始终可用，Run Bridge 装配后就绪探针返回固定公开状态。"""

    app = create_app()
    app.state.pixelflow_harness_run_bridge = object()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        live = await client.get("/live")
        ready = await client.get("/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "live", "service": "pixelflow-gateway"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "service": "pixelflow-gateway"}
