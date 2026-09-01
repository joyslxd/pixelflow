"""提供仅供 PixelFlow Gateway 调用的 Sidecar HTTP/SSE API。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from .config import SidecarSettings
from .auth import ServiceJwtValidationError, validate_service_jwt
from .contracts import HarnessRunRequest, HarnessRunState, RunStatus
from .deepseek_engine import DeepSeekHarnessEngine
from .event_store import RunRequestConflictError, SqliteRunEventStore
from .run_service import RunActivationError, RunService


logger = logging.getLogger(__name__)


def _cordis_path() -> str:
    """返回随 Sidecar 源码交付的安全 Composition 文件路径。"""

    from pathlib import Path

    return str(
        Path(__file__).parents[2]
        / "engines/deepseek/cordis/m0-safe.cordis.yml",
    )


def create_app(settings: SidecarSettings | None = None) -> FastAPI:
    """创建 Sidecar 应用；配置仅在建应用时读取，避免请求间漂移。"""

    resolved = settings or SidecarSettings.from_env()
    store = SqliteRunEventStore(resolved.run_store_path)
    engine = DeepSeekHarnessEngine(resolved, Path(_cordis_path()))
    service = RunService(store, engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await service.reconcile_interrupted_runs()
            yield
        finally:
            await service.aclose()

    app = FastAPI(
        title="PixelFlow Agent Harness Sidecar",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.sidecar_settings = resolved
    app.state.run_service = service

    @app.get("/live")
    async def live() -> dict[str, str]:
        """进程存活检查，不泄漏任何运行配置。"""

        return {"status": "live"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        """启动准入检查，未配置凭据时明确拒绝接流量。"""

        error = resolved.readiness_error()
        if error:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": error})
        return {"status": "ready"}

    def require_service(authorization: str | None) -> None:
        """为内部 Run API 统一校验 Gateway 服务凭据。"""

        try:
            validate_service_jwt(
                authorization,
                verify_key=resolved.gateway_jwt_verify_key,
                issuer=resolved.gateway_jwt_issuer,
                audience=resolved.gateway_jwt_audience,
            )
        except ServiceJwtValidationError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "service_authentication_failed"})

    @app.post("/internal/v1/runs", response_model=HarnessRunState, status_code=status.HTTP_202_ACCEPTED)
    async def create_run(
        body: HarnessRunRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> HarnessRunState:
        """接收 Gateway 冻结的 Run 请求并异步启动真实模型执行。"""

        require_service(authorization)
        if idempotency_key != body.run_request_key:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "idempotency_key_mismatch"})
        try:
            return await service.create_run(body)
        except RunRequestConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "run_request_conflict"}) from exc
        except ValueError as exc:
            # 这里只记录本服务主动定义的协议/配置拒绝原因；不记录请求正文、
            # Tool 参数、模型响应或任何下游异常，以便 Gateway 503 可排障。
            logger.warning(
                "sidecar_run_rejected reason_code=%s",
                _safe_run_rejection_code(exc),
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "run_request_rejected"}) from exc

    @app.post("/internal/v1/runs/{run_id}/activate", response_model=HarnessRunState)
    async def activate_run(
        run_id: str,
        authorization: str | None = Header(default=None),
    ) -> HarnessRunState:
        """仅在 Gateway 已写入权威 binding 后激活已接受 Run。"""

        require_service(authorization)
        try:
            result = await service.activate_run(run_id)
        except RunActivationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "run_activation_unavailable"},
            ) from exc
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "run_not_found"})
        return result

    @app.get("/internal/v1/runs/{run_id}", response_model=HarnessRunState)
    async def get_run(run_id: str, authorization: str | None = Header(default=None)) -> HarnessRunState:
        """查询公开 Run 状态，不返回原始 Harness Session 数据。"""

        require_service(authorization)
        result = await service.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "run_not_found"})
        return result

    @app.post("/internal/v1/runs/{run_id}/cancel", response_model=HarnessRunState)
    async def cancel_run(run_id: str, authorization: str | None = Header(default=None)) -> HarnessRunState:
        """取消当前 Harness 模型循环；不会取消已提交给外部 Provider 的业务操作。"""

        require_service(authorization)
        result = await service.cancel_run(run_id)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "run_not_found"})
        return result

    @app.get("/internal/v1/runs/{run_id}/events")
    async def stream_events(
        run_id: str,
        request: Request,
        after_sequence: int = 0,
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        """按 sequence 提供断线可恢复的公开 SSE 流。"""

        require_service(authorization)
        if after_sequence < 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "after_sequence_invalid"})
        if await service.get_run(run_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "run_not_found"})
        if not await service.has_event_cursor(run_id, after_sequence):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "after_sequence_unknown"})

        async def events() -> AsyncIterator[str]:
            cursor = after_sequence
            while True:
                for event in await service.events_after(run_id, cursor):
                    cursor = event.sequence
                    yield f"id: {event.event_id}\ndata: {event.model_dump_json()}\n\n"
                current = await service.get_run(run_id)
                if current is not None and current.status in {
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.08)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _safe_run_rejection_code(error: ValueError) -> str:
    """将 Sidecar 主动定义的 Run 拒绝语义收敛为固定日志码。"""

    reason = str(error)
    if reason == "模型档案与 Sidecar 启动配置不匹配":
        return "model_profile_name_mismatch"
    if reason == "模型档案摘要与 Sidecar 启动配置不匹配":
        return "model_profile_digest_mismatch"
    if reason.startswith("Run 限制"):
        return "run_limits_mismatch"
    if reason.startswith("Skill "):
        return "skill_snapshot_invalid"
    return "run_request_rejected"
