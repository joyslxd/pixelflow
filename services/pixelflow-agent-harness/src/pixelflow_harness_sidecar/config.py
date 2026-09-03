"""读取 Sidecar 进程配置，所有敏感值只允许由环境或 Secret Manager 注入。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .model_profile import HarnessModelProfile


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
    model_profile: HarnessModelProfile
    tool_manifest_digest: str
    request_timeout_seconds: float
    run_limit_profiles_json: str = ""

    @classmethod
    def from_env(cls) -> SidecarSettings:
        """从环境读取配置；缺少敏感值时 readiness 失败而不是回退默认凭据。"""

        agent_home_raw = os.environ.get("PIXELFLOW_AGENT_HOME", "").strip()
        agent_home = Path(agent_home_raw).expanduser() if agent_home_raw else Path()
        run_store_raw = os.environ.get("PIXELFLOW_HARNESS_RUN_STORE", "").strip()
        run_store = (
            Path(run_store_raw).expanduser()
            if run_store_raw
            else agent_home / "run-events" / "runs.sqlite3"
        )
        model_profile = HarnessModelProfile.from_env()
        tool_manifest_digest = os.environ.get("PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST", "").strip()
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
            model_profile=model_profile,
            tool_manifest_digest=tool_manifest_digest,
            request_timeout_seconds=float(
                os.environ.get("PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS", "90"),
            ),
            run_limit_profiles_json=os.environ.get("PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES", "").strip(),
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
        if not self.model_profile.digest.startswith("sha256:"):
            return "model_profile_unconfigured"
        if not self.tool_manifest_digest.startswith("sha256:") or len(self.tool_manifest_digest) != 71:
            return "tool_manifest_unconfigured"
        if not self.run_limit_profiles_json:
            return "run_limit_profiles_unconfigured"
        try:
            self._limit_profiles()
        except (TypeError, ValueError):
            return "run_limit_profiles_invalid"
        try:
            from .skill_snapshot import snapshot_skill_root

            snapshot_skill_root(self.agent_home / "skills")
        except ValueError:
            return "skill_snapshot_invalid"
        return None

    @property
    def run_limits_digest(self) -> str:
        """返回完整限制发布配置的公开摘要，供 Gateway 预检比较。"""

        payload = {
            name: {
                "deadline_seconds": profile["deadline_seconds"],
                "max_model_steps": profile["max_model_steps"],
                "max_business_tools": profile["max_business_tools"],
                "max_billable_batch_starts": profile["max_billable_batch_starts"],
            }
            for name, profile in self._limit_profiles().items()
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def validate_run_limits(self, limits: object) -> None:
        """校验 Gateway 冻结的档案、数值和 digest 与本地配置完全一致。"""

        profile_name = getattr(limits, "profile", None)
        profile = self._limit_profiles().get(profile_name)
        if profile is None:
            raise ValueError("Run 限制档案未获 Sidecar 授权")
        expected = {
            "profile": profile_name,
            "max_model_steps": getattr(limits, "max_model_steps", None),
            "max_business_tools": getattr(limits, "max_business_tools", None),
            "max_billable_batch_starts": getattr(limits, "max_billable_batch_starts", None),
            "deadline_seconds": getattr(limits, "deadline_seconds", None),
        }
        if profile != expected:
            raise ValueError("Run 限制数值与 Sidecar 档案不一致")
        encoded = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        if getattr(limits, "digest", None) != "sha256:" + hashlib.sha256(encoded).hexdigest():
            raise ValueError("Run 限制摘要与 Sidecar 档案不一致")

    def _limit_profiles(self) -> dict[str, dict[str, int | str]]:
        try:
            value = json.loads(self.run_limit_profiles_json)
        except json.JSONDecodeError as error:
            raise ValueError("Run 限制档案不是 JSON") from error
        if not isinstance(value, dict):
            raise TypeError("Run 限制档案不是对象")
        required = {"deadline_seconds", "max_model_steps", "max_business_tools", "max_billable_batch_starts"}
        profiles: dict[str, dict[str, int | str]] = {}
        for name, raw in value.items():
            if not isinstance(name, str) or not isinstance(raw, dict) or set(raw) != required:
                raise ValueError("Run 限制档案字段无效")
            if any(isinstance(item, bool) or not isinstance(item, int) for item in raw.values()):
                raise ValueError("Run 限制档案必须为整数")
            profiles[name] = {"profile": name, **raw}
        return profiles

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
