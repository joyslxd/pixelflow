"""仅供 Sidecar 调用的 Capability Tool Broker HTTP Adapter。"""

from __future__ import annotations

import os

import jwt
from fastapi import APIRouter, Header, HTTPException, Request, status

from pixelflow.agent_tools.broker import AgentToolBroker
from pixelflow.agent_tools.contracts import ToolCallRequest, ToolCallResponse, ToolManifestResponse
from pixelflow.agent_tools.manifest import manifest
from pixelflow.agent_tools.repository import AgentToolBindingConflictError

router = APIRouter(
    prefix="/agent/internal/agent-tools",
    tags=["internal-agent-tools"],
    include_in_schema=False,
)


def _require_service_identity(authorization: str | None) -> None:
    """校验 Sidecar→Tool Broker 短期服务 JWT，缺失或漂移时失败关闭。"""

    verify_key = os.environ.get("PIXELFLOW_TOOL_BROKER_JWT_VERIFY_KEY", "").strip()
    issuer = os.environ.get("PIXELFLOW_TOOL_BROKER_JWT_ISSUER", "pixelflow-harness-sidecar").strip()
    audience = os.environ.get("PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE", "pixelflow-tool-broker").strip()
    scheme, _, token = (authorization or "").partition(" ")
    if not verify_key or not issuer or not audience or scheme != "Bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "agent_tool_service_authentication_failed"},
        )
    try:
        claims = jwt.decode(
            token,
            verify_key,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "iss", "aud", "service_instance_id"]},
        )
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "agent_tool_service_authentication_failed"},
        ) from error
    instance_id = claims.get("service_instance_id")
    if not isinstance(instance_id, str) or not instance_id.strip() or len(instance_id) > 128:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "agent_tool_service_authentication_failed"},
        )


def _broker(request: Request) -> AgentToolBroker:
    """读取 Gateway 生命周期装配的真实 Broker，禁止临时构造内存实现。"""

    value = getattr(request.app.state, "pixelflow_agent_tool_broker", None)
    if not isinstance(value, AgentToolBroker):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "agent_tool_broker_unavailable"},
        )
    return value


@router.get("/manifest", response_model=ToolManifestResponse)
async def get_manifest(authorization: str | None = Header(default=None)) -> ToolManifestResponse:
    """返回当前只读 Manifest；Sidecar readiness 会据此核对冻结摘要。"""

    _require_service_identity(authorization)
    return manifest()


@router.post("/calls", response_model=ToolCallResponse)
async def call_tool(
    body: ToolCallRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ToolCallResponse:
    """把结构化 Tool Call 委派给 Broker，不读取用户 Authorization 或直接处理 Provider。"""

    _require_service_identity(authorization)
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "agent_tool_idempotency_key_missing"},
        )
    try:
        return await _broker(request).call(body, idempotency_key=idempotency_key)
    except AgentToolBindingConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "agent_tool_call_conflict"},
        ) from error
