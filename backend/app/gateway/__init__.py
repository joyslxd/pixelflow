from .config import GatewayConfig, get_gateway_config

__all__ = ["app", "create_app", "GatewayConfig", "get_gateway_config"]


def __getattr__(name: str):
    """懒加载 FastAPI 应用实例，避免导入配置/测试子模块时提前启动网关。"""
    if name in {"app", "create_app"}:
        from .app import app, create_app

        return app if name == "app" else create_app
    raise AttributeError(name)
