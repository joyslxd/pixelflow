"""读取 Sidecar 进程配置，所有敏感值只允许由环境或 Secret Manager 注入。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SidecarSettings:
    """保存启动后不可变的 Sidecar 最小运行配置。"""

    agent_home: Path
    run_store_path: Path
    gateway_jwt_verify_key: str
    gateway_jwt_issuer: str
    gateway_jwt_audience: str
    tool_broker_base_url: str
    tool_broker_jwt_signing_key: str
    tool_broker_jwt_issuer: str
    tool_broker_jwt_audience: str
    sidecar_instance_id: str
    model_profile_name: str
    model_id: str
    request_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "SidecarSettings":
        """从环境读取配置；缺少敏感值时 readiness 失败而不是回退默认凭据。"""

        agent_home_raw = os.environ.get("PIXELFLOW_AGENT_HOME", "").strip()
        agent_home = Path(agent_home_raw).expanduser() if agent_home_raw else Path()
        run_store_raw = os.environ.get("PIXELFLOW_HARNESS_RUN_STORE", "").strip()
        run_store = (
            Path(run_store_raw).expanduser()
            if run_store_raw
            else agent_home / "run-events" / "runs.sqlite3"
        )
        return cls(
            agent_home=agent_home,
            run_store_path=run_store,
            gateway_jwt_verify_key=os.environ.get("PIXELFLOW_GATEWAY_JWT_VERIFY_KEY", "").strip(),
            gateway_jwt_issuer=os.environ.get("PIXELFLOW_GATEWAY_JWT_ISSUER", "pixelflow-gateway").strip(),
            gateway_jwt_audience=os.environ.get("PIXELFLOW_GATEWAY_JWT_AUDIENCE", "pixelflow-harness-sidecar").strip(),
            tool_broker_base_url=os.environ.get("PIXELFLOW_TOOL_BROKER_BASE_URL", "").strip().rstrip("/"),
            tool_broker_jwt_signing_key=os.environ.get("PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY", "").strip(),
            tool_broker_jwt_issuer=os.environ.get("PIXELFLOW_TOOL_BROKER_JWT_ISSUER", "pixelflow-harness-sidecar").strip(),
            tool_broker_jwt_audience=os.environ.get("PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE", "pixelflow-tool-broker").strip(),
            sidecar_instance_id=os.environ.get("PIXELFLOW_SIDECAR_INSTANCE_ID", "").strip(),
            model_profile_name=os.environ.get(
                "PIXELFLOW_HARNESS_MODEL_PROFILE",
                "deepseek-v4-pro",
            ).strip(),
            model_id=os.environ.get(
                "PIXELFLOW_HARNESS_MODEL_ID",
                "deepseek-v4-pro-ga-260813",
            ).strip(),
            request_timeout_seconds=float(
                os.environ.get("PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS", "90"),
            ),
        )

    def readiness_error(self) -> str | None:
        """返回固定安全错误码，禁止把密钥、路径或底层异常暴露到健康检查。"""

        if not self.agent_home_raw_is_configured:
            return "agent_home_unconfigured"
        if not self.gateway_jwt_verify_key:
            return "gateway_jwt_verify_key_unconfigured"
        if not self.gateway_jwt_issuer or not self.gateway_jwt_audience:
            return "gateway_jwt_contract_unconfigured"
        if not self._tool_broker_url_is_safe:
            return "tool_broker_endpoint_unconfigured"
        if len(self.tool_broker_jwt_signing_key) < 32:
            return "tool_broker_jwt_signing_key_unconfigured"
        if not self.tool_broker_jwt_issuer or not self.tool_broker_jwt_audience or not self.sidecar_instance_id:
            return "tool_broker_jwt_contract_unconfigured"
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            return "model_credential_unconfigured"
        if not os.environ.get("DEEPSEEK_BASE_URL", "").strip():
            return "model_endpoint_unconfigured"
        if not self.model_profile_name or not self.model_id:
            return "model_profile_unconfigured"
        return None

    @property
    def agent_home_raw_is_configured(self) -> bool:
        """确认 Agent Home 来自显式环境变量，避免静默使用当前工作目录。"""

        return bool(os.environ.get("PIXELFLOW_AGENT_HOME", "").strip())

    @property
    def _tool_broker_url_is_safe(self) -> bool:
        """生产只接受 HTTPS；M0 loopback 真实测试允许固定 127.0.0.1 地址。"""

        return (
            self.tool_broker_base_url.startswith("https://")
            or self.tool_broker_base_url.startswith("http://127.0.0.1:")
            or self.tool_broker_base_url.startswith("http://gateway:")
        )
