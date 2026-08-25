"""通过短期 JWT 调用 Sidecar，并在 Gateway 持久化 Run binding。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field

from pixelflow.agent_tools.manifest import manifest
from pixelflow.agent_tools.repository import RunBinding, SQLAgentToolRepository

from .contracts import HarnessRunEvent, HarnessRunHandle, HarnessRunRequest


class _StrictModel(BaseModel):
    """拒绝 Sidecar 请求中的未知字段，避免内部协议静默漂移。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GatewayHarnessSidecarError(RuntimeError):
    """表示 Sidecar 请求/绑定失败，不带下游响应正文。"""


class PublicAgentEvent(_StrictModel):
    """Gateway 可对认证用户公开的最小 Sidecar 事件，不泄漏 Harness 私有轨迹。"""

    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    sequence: int = Field(ge=1)
    type: str = Field(pattern=r"^(run|tool|response)\.[a-z_]+$")
    payload: dict[str, Any]


class _SidecarRunEventEnvelope(_StrictModel):
    """识别 Sidecar v1 事件的已知审计字段，转换时不把它们公开给浏览器。"""

    protocol_version: Literal["v1"]
    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    event_id: str = Field(pattern=r"^hevt_[a-f0-9]{32}$")
    sequence: int = Field(ge=1)
    type: str = Field(pattern=r"^(run|tool|response)\.[a-z_]+$")
    occurred_at: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]


class AgentHarnessSidecarClient:
    """类似 Application Service：创建 Sidecar Run 后持久化 Gateway 权威 binding。"""

    def __init__(
        self,
        *,
        base_url: str,
        gateway_jwt_signing_key: str,
        gateway_instance_id: str,
        repository: SQLAgentToolRepository,
        timeout_seconds: float = 10,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """注入仅限内部网络的 Sidecar 地址和短期 JWT 签名材料。"""

        normalized = base_url.rstrip("/")
        if not normalized.startswith("https://") and not normalized.startswith("http://127.0.0.1:"):
            raise ValueError("Sidecar 地址必须使用 HTTPS，M0 仅允许 loopback HTTP")
        if len(gateway_jwt_signing_key) < 32 or not gateway_instance_id:
            raise ValueError("Gateway 服务 JWT 配置无效")
        self._base_url = normalized
        self._signing_key = gateway_jwt_signing_key
        self._instance_id = gateway_instance_id
        self._repository = repository
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, read=None),
        )

    async def create_and_bind(self, request: HarnessRunRequest) -> HarnessRunHandle:
        """创建或回读 Sidecar Run，再按返回 run_id 写入 Gateway binding。"""

        manifest_snapshot = manifest()
        request_body = self._sidecar_request(request, manifest_snapshot.digest)
        try:
            response = await self._client.post(
                f"{self._base_url}/internal/v1/runs",
                headers={
                    "Authorization": f"Bearer {self._service_jwt()}",
                    "Idempotency-Key": request_body["run_request_key"],
                },
                json=request_body,
            )
        except httpx.HTTPError as error:
            raise GatewayHarnessSidecarError("Sidecar Run 创建请求失败") from error
        if response.status_code != httpx.codes.ACCEPTED:
            raise GatewayHarnessSidecarError("Sidecar 拒绝 Run 创建请求")
        try:
            data = response.json()
            run_id = data["run_id"]
            status = data["status"]
            if not isinstance(run_id, str) or not isinstance(status, str):
                raise ValueError("响应字段无效")
        except (ValueError, KeyError, TypeError) as error:
            raise GatewayHarnessSidecarError("Sidecar Run 响应协议无效") from error
        await self._repository.register_run_binding(
            RunBinding(
                run_id=run_id,
                session_id=request_body["session_id"],
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                workspace_id=request.workspace_id,
                workspace_revision=request.workspace_revision,
                context_digest=request.context_digest,
                toolset_version="agent-tools-v1",
                tool_manifest_digest=manifest_snapshot.digest,
                request_digest=request_body["request_digest"],
            ),
        )
        try:
            activation = await self._client.post(
                f"{self._base_url}/internal/v1/runs/{run_id}/activate",
                headers={"Authorization": f"Bearer {self._service_jwt()}"},
            )
        except httpx.HTTPError as error:
            raise GatewayHarnessSidecarError("Sidecar Run 激活请求失败") from error
        if activation.status_code != httpx.codes.OK:
            raise GatewayHarnessSidecarError("Sidecar 拒绝激活已绑定 Run")
        return HarnessRunHandle(run_id=run_id, status=status)

    async def stream_public_events(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        after_sequence: int,
    ) -> AsyncIterator[PublicAgentEvent]:
        """按 binding 校验 owner 后转发 Sidecar 的安全事件，支持断线续传。"""

        async for source_event in self.stream_sidecar_events(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            after_sequence=after_sequence,
        ):
            yield PublicAgentEvent(
                run_id=source_event.run_id,
                sequence=source_event.sequence,
                type=source_event.type,
                payload=source_event.payload,
            )

    async def stream_sidecar_events(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        after_sequence: int,
    ) -> AsyncIterator[HarnessRunEvent]:
        """读取绑定 Run 的完整安全事件 envelope，供 Gateway Outbox 投影使用。"""

        if after_sequence < 0:
            raise ValueError("事件游标不能小于零")
        await self.ensure_run_owner(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        previous = after_sequence
        try:
            async with self._client.stream(
                "GET",
                f"{self._base_url}/internal/v1/runs/{run_id}/events",
                params={"after_sequence": after_sequence},
                headers={
                    "Authorization": f"Bearer {self._service_jwt()}",
                    "Accept": "text/event-stream",
                },
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise GatewayHarnessSidecarError("Sidecar Run 事件流不可用")
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        envelope = _SidecarRunEventEnvelope.model_validate_json(line[6:])
                    except ValueError as error:
                        raise GatewayHarnessSidecarError("Sidecar 事件协议无效") from error
                    event = HarnessRunEvent(
                        run_id=envelope.run_id,
                        event_id=envelope.event_id,
                        sequence=envelope.sequence,
                        type=envelope.type,
                        occurred_at=envelope.occurred_at,
                        payload=envelope.payload,
                    )
                    if event.run_id != run_id or event.sequence <= previous:
                        raise GatewayHarnessSidecarError("Sidecar 事件序列无效")
                    previous = event.sequence
                    yield event
        except httpx.HTTPError as error:
            raise GatewayHarnessSidecarError("Sidecar Run 事件流请求失败") from error

    async def ensure_run_owner(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
    ) -> None:
        """确认公开 Run 查询仍受 Gateway binding 的用户与会话隔离保护。"""

        await self.get_owned_binding(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )

    async def get_owned_binding(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
    ) -> RunBinding:
        """读取已校验归属的不可变 binding，供 Gateway 投影服务使用。"""

        binding = await self._repository.get_run_binding(run_id)
        if (
            binding is None
            or binding.user_id != user_id
            or binding.conversation_id != conversation_id
        ):
            raise LookupError("Run 不存在或不属于当前用户")
        return binding

    async def aclose(self) -> None:
        """关闭 Bridge 自己创建的 HTTP Client。"""

        if self._owns_client:
            await self._client.aclose()

    def _service_jwt(self) -> str:
        """签发 Gateway→Sidecar 五分钟服务 JWT，禁止将用户身份放入 claim。"""

        now = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": "pixelflow-gateway",
                "iss": "pixelflow-gateway",
                "aud": "pixelflow-harness-sidecar",
                "service_instance_id": self._instance_id,
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            self._signing_key,
            algorithm="HS256",
        )

    @staticmethod
    def _sidecar_request(request: HarnessRunRequest, manifest_digest: str) -> dict[str, Any]:
        """构造与外部用户身份隔离的 Sidecar v1 DTO。"""

        session_id = "pfh_" + hashlib.sha256(
            f"v1:{request.trigger_type}:{request.trigger_id}".encode(),
        ).hexdigest()[:32]
        run_request_key = "sha256:" + hashlib.sha256(
            f"dev:{request.trigger_type}:{request.trigger_id}:v1".encode(),
        ).hexdigest()
        digest_input = {
            "workspace_revision": request.workspace_revision,
            "context_digest": request.context_digest,
            "model_profile_digest": request.model_profile_digest,
            "context_budget_digest": request.context_budget_digest,
            "run_limits_digest": request.run_limits_digest,
            "toolset_version": "agent-tools-v1",
            "tool_manifest_digest": manifest_digest,
        }
        request_digest = "sha256:" + hashlib.sha256(
            json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        return {
            "protocol_version": "v1",
            "run_request_key": run_request_key,
            "request_digest": request_digest,
            "session_id": session_id,
            "trigger": {"type": request.trigger_type, "trigger_id": request.trigger_id},
            "binding": {
                "conversation_ref": "opaque:" + hashlib.sha256(request.conversation_id.encode()).hexdigest(),
                "workspace_ref": "opaque:" + hashlib.sha256(request.workspace_id.encode()).hexdigest(),
                "workspace_revision": request.workspace_revision,
                "context_digest": request.context_digest,
            },
            "model": {
                "profile_name": request.model_profile_name,
                "profile_digest": request.model_profile_digest,
                "max_output_tokens": request.max_output_tokens,
            },
            "context_budget": {
                "effective_context_k": 896,
                "output_reserve_k": 32,
                "safety_reserve_k": 32,
                "require_verified_model_profile": True,
                "policy_digest": request.context_budget_digest,
            },
            "limits": {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 90},
            "toolset": {"version": "agent-tools-v1", "manifest_digest": manifest_digest},
            "context": {
                "system_instruction": request.system_instruction,
                "user_input": request.user_input,
                "workspace_projection": request.workspace_projection,
                "conversation_projection": request.conversation_projection,
                "preference_projection": request.preference_projection,
                "brand_profile_projection": request.brand_profile_projection,
                "long_term_memory_projection": request.long_term_memory_projection,
            },
        }


__all__ = [
    "AgentHarnessSidecarClient",
    "GatewayHarnessSidecarError",
    "PublicAgentEvent",
]
