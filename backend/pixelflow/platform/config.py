"""读取 PixelFlow 自有的运行配置，不泄漏或持久化敏感值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HarnessSidecarSettings:
    """Gateway 调用 Sidecar 所需的最小配置快照，类似受控 Client 配置。"""

    base_url: str
    jwt_signing_key: str
    instance_id: str
    request_timeout_seconds: float

    @classmethod
    def from_env(cls) -> HarnessSidecarSettings | None:
        """从环境读取完整配置；任一必填项缺失时返回 None 并拒绝启用新 Run。"""

        base_url = os.environ.get("PIXELFLOW_HARNESS_SIDECAR_BASE_URL", "").strip()
        signing_key = os.environ.get("PIXELFLOW_GATEWAY_JWT_SIGNING_KEY", "").strip()
        instance_id = os.environ.get("PIXELFLOW_GATEWAY_INSTANCE_ID", "").strip()
        if not base_url or not signing_key or not instance_id:
            return None
        # Docker Compose 内的固定服务名只在受控私有网络解析；其余明文 HTTP 仍拒绝。
        normalized_base_url = base_url.rstrip("/")
        is_loopback = normalized_base_url.startswith("http://127.0.0.1:")
        is_compose_sidecar = normalized_base_url == "http://harness-sidecar:8090"
        if not base_url.startswith("https://") and not is_loopback and not is_compose_sidecar:
            raise ValueError("Sidecar 地址必须使用 HTTPS，M0 loopback 或受控 Compose 服务例外")
        if len(signing_key) < 32:
            raise ValueError("Gateway 服务 JWT 签名材料长度不足")
        raw_timeout = os.environ.get("PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS", "10").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as error:
            raise ValueError("Sidecar 请求超时必须是正数秒") from error
        if timeout <= 0 or timeout > 300:
            raise ValueError("Sidecar 请求超时必须在 0 到 300 秒之间")
        return cls(
            base_url=normalized_base_url,
            jwt_signing_key=signing_key,
            instance_id=instance_id,
            request_timeout_seconds=timeout,
        )


@dataclass(frozen=True, slots=True)
class GatewayRuntimeSettings:
    """Gateway 自有启动配置，不依赖 DeerFlow AppConfig。"""

    log_level: str
    database_backend: str
    database_url: str
    database_echo_sql: bool
    database_pool_size: int
    database_sqlite_dir: str

    @classmethod
    def from_env(cls) -> GatewayRuntimeSettings:
        """读取启动期环境变量；缺失时使用本地 SQLite，生产应显式覆盖。"""

        backend = os.environ.get("PIXELFLOW_DATABASE_BACKEND", "sqlite").strip()
        if backend not in {"memory", "sqlite", "postgres"}:
            raise ValueError("PIXELFLOW_DATABASE_BACKEND 仅支持 memory、sqlite 或 postgres")
        sqlite_dir = os.environ.get(
            "PIXELFLOW_DATABASE_SQLITE_DIR",
            "../.pixelflow/data",
        ).strip()
        raw_url = os.environ.get("PIXELFLOW_DATABASE_URL", "").strip()
        if backend == "postgres" and not raw_url:
            raise ValueError("Postgres 后端必须配置 PIXELFLOW_DATABASE_URL")
        if backend == "sqlite" and not raw_url:
            raw_url = f"sqlite+aiosqlite:///{Path(sqlite_dir).expanduser() / 'pixelflow.db'}"
        return cls(
            log_level=os.environ.get("PIXELFLOW_LOG_LEVEL", "INFO").strip().upper() or "INFO",
            database_backend=backend,
            database_url=raw_url,
            database_echo_sql=os.environ.get("PIXELFLOW_DATABASE_ECHO_SQL", "false").strip().lower() == "true",
            database_pool_size=int(os.environ.get("PIXELFLOW_DATABASE_POOL_SIZE", "5")),
            database_sqlite_dir=sqlite_dir,
        )


__all__ = ["GatewayRuntimeSettings", "HarnessSidecarSettings"]
