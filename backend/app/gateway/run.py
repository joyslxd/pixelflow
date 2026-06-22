"""Gateway command-line runner.

直接用 ``uvicorn app.gateway.app:app --host ... --port ...`` 时，host/port
必须写在命令行里，无法自动读取 ``config.dev.yml`` / ``config.prod.yml``。
这个很薄的启动器先加载 profile 配置，再把 GatewayConfig 传给 uvicorn，
效果更接近 Spring Boot 根据当前 profile 启动不同端口/配置。
"""

from __future__ import annotations

import argparse

import uvicorn

from app.gateway.config import get_gateway_config
from app.gateway.profile_config import load_profile_config


def main() -> None:
    """从命令行启动 FastAPI Gateway。"""
    parser = argparse.ArgumentParser(description="Run PixelFlow Agent API Gateway")
    parser.add_argument("--reload", action="store_true", help="启用开发热重载")
    args = parser.parse_args()

    # 先加载 YAML profile，再读取 GatewayConfig。命令行临时传入的 env 仍可覆盖 YAML。
    load_profile_config()
    config = get_gateway_config()

    uvicorn.run(
        "app.gateway.app:app",
        host=config.host,
        port=config.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
