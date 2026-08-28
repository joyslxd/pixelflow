"""content-app 视频异步任务的 HTTP 防腐 Adapter。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import JsonValue

from pixelflow.operations.jobs.providers import (
    ProviderJobAdapter,
    ProviderJobMappingError,
    ProviderJobSnapshot,
)

_MODE_ENDPOINTS = {
    "text_to_video": "/video/text-to-video",
    "image_to_video": "/video/image-to-video",
    "two_image_to_video": "/video/two-image-to-video",
    "reference_mode_video": "/video/reference-mode-video",
    "edit_video": "/video/edit-video",
}
_STATUS_ENDPOINT = "/task/{provider_job_id}/status"


class _ContentAppHTTPError(RuntimeError):
    """向通用 M06 Adapter 提供安全的 HTTP 状态分类，不保留下游正文。"""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("content_app_provider_request_failed")


@dataclass(frozen=True)
class ContentAppVideoProviderSettings:
    """部署环境注入的 Provider 连接配置；用户凭据不属于此配置。"""

    base_url: str
    provider_id: str
    profile_version: str
    connect_timeout_seconds: float
    read_timeout_seconds: float

    @classmethod
    def from_env(cls) -> ContentAppVideoProviderSettings | None:
        """显式开关关闭时不装配 Provider；开启但配置缺失则失败关闭。"""

        enabled = os.environ.get("PIXELFLOW_M06_VIDEO_PROVIDER_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        base_url = os.environ.get("BORGRISE_BASE_URL", "").strip().rstrip("/")
        if not base_url.startswith(("https://", "http://127.0.0.1:")):
            raise ValueError("M06 视频 Provider 必须配置受控 HTTPS 或 loopback content-app 地址")
        return cls(
            base_url=base_url,
            provider_id=_env_text("PIXELFLOW_M06_VIDEO_PROVIDER_ID", "content-app-video"),
            profile_version=_env_text("PIXELFLOW_M06_VIDEO_PROVIDER_PROFILE_VERSION", "v1"),
            connect_timeout_seconds=_env_positive_float("PIXELFLOW_M06_VIDEO_PROVIDER_CONNECT_TIMEOUT_SECONDS", 10),
            read_timeout_seconds=_env_positive_float("PIXELFLOW_M06_VIDEO_PROVIDER_READ_TIMEOUT_SECONDS", 30),
        )


class ContentAppVideoGenerationProvider:
    """将稳定场景生成请求映射到 content-app，并用服务凭据恢复轮询。"""

    def __init__(
        self,
        settings: ContentAppVideoProviderSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self.provider_id = settings.provider_id
        self.profile_version = settings.profile_version
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_seconds,
                read=settings.read_timeout_seconds,
                write=settings.read_timeout_seconds,
                pool=settings.connect_timeout_seconds,
            )
        )

    def prepare_operation_request(
        self,
        request: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        """在 M06 计算 request_hash 前冻结 Provider 路由身份。"""

        mode = _required_text(request, "generation_mode")
        if mode not in _MODE_ENDPOINTS:
            raise ProviderJobMappingError("video_generation_mode_unsupported")
        return {
            **dict(request),
            "provider_id": self.provider_id,
            "provider_profile_version": self.profile_version,
        }

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> ProviderJobSnapshot:
        """首次创建只借用用户 Authorization；请求返回后立即丢弃本地引用。"""

        normalized = self.prepare_operation_request(request)
        if (
            normalized.get("provider_id") != self.provider_id
            or normalized.get("provider_profile_version") != self.profile_version
        ):
            raise ProviderJobMappingError("video_provider_route_drift")
        endpoint, body = _start_payload(normalized)
        user_authorization = _bearer(authorization)
        try:
            response = await self._client.post(
                self._url(endpoint),
                headers={
                    "Authorization": user_authorization,
                    "Idempotency-Key": _required_identifier(idempotency_key),
                    # content-app 按这些请求头解析视频模型计费档案；只在 body
                    # 传 model/size 会被服务端判为模型配置缺失。
                    "modelType": _required_text(normalized, "model"),
                    "billType": "3",
                    "duration": str(_required_video_duration(normalized)),
                    "apiModelParamObj": json.dumps(
                        {"size": _required_text(normalized, "size")},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
                json=body,
            )
            return _to_snapshot(_raise_or_json(response), expected_job_id=None)
        except _ContentAppHTTPError as exc:
            if exc.status_code == 402:
                return _quota_snapshot()
            raise
        finally:
            user_authorization = ""

    async def status(
        self,
        provider_job_id: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> ProviderJobSnapshot:
        """轮询只读取部署服务 Authorization，绝不读取或复用用户 Authorization。"""

        del user_id, conversation_id
        job_id = _required_identifier(provider_job_id)
        service_authorization = _status_authorization()
        try:
            response = await self._client.get(
                self._url(_STATUS_ENDPOINT.format(provider_job_id=job_id)),
                headers={"Authorization": service_authorization},
            )
            return _to_snapshot(_raise_or_json(response), expected_job_id=job_id)
        except _ContentAppHTTPError as exc:
            if exc.status_code == 402:
                return _quota_snapshot(provider_job_id=job_id)
            if exc.status_code == 404:
                return _expired_snapshot(job_id)
            raise
        finally:
            service_authorization = ""

    async def aclose(self) -> None:
        """仅关闭本 Adapter 自建的 HTTP Client。"""

        if self._owns_client:
            await self._client.aclose()

    def as_operation_adapter(self) -> ProviderJobAdapter:
        """返回 M06 既有六态 Adapter，保持 Operation Coordinator 不感知 HTTP。"""

        return ProviderJobAdapter(_ContentAppExistingJobService(self))

    def _url(self, path: str) -> str:
        return f"{self._settings.base_url}{path}"


class _ContentAppExistingJobService:
    """把稳定 Provider Port 适配为 M06 通用 ExistingJobService，避免双重状态映射。"""

    def __init__(self, provider: ContentAppVideoGenerationProvider) -> None:
        self._provider = provider

    async def start(self, request: Mapping[str, JsonValue], *, authorization: str, idempotency_key: str) -> object:
        snapshot = await self._provider.start(
            request,
            authorization=authorization,
            idempotency_key=idempotency_key,
        )
        return _snapshot_response(snapshot)

    async def status_scoped(self, provider_job_id: str, *, user_id: str, conversation_id: str) -> object:
        snapshot = await self._provider.status(
            provider_job_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return _snapshot_response(snapshot)


def _snapshot_response(snapshot: ProviderJobSnapshot) -> dict[str, JsonValue]:
    """把稳定六态快照转回通用 M06 Adapter 的输入形状，避免重复 HTTP 映射。"""

    if snapshot.outcome.value == "expired":
        raise _ContentAppHTTPError(404)
    status = (
        "quota_insufficient"
        if snapshot.outcome.value == "paused_quota"
        else snapshot.outcome.value
    )
    return {
        "job_id": snapshot.provider_job_id,
        "status": status,
        "result": snapshot.result,
    }


def _quota_snapshot(provider_job_id: str | None = None) -> ProviderJobSnapshot:
    from pixelflow.operations.jobs.providers import ProviderJobOutcome

    return ProviderJobSnapshot(
        provider_job_id=provider_job_id,
        outcome=ProviderJobOutcome.PAUSED_QUOTA,
        reason_code="provider_quota_insufficient",
        message="额度不足，当前任务已暂停，可在充值后继续。",
    )


def _expired_snapshot(provider_job_id: str) -> ProviderJobSnapshot:
    from pixelflow.operations.jobs.providers import ProviderJobOutcome

    return ProviderJobSnapshot(
        provider_job_id=provider_job_id,
        outcome=ProviderJobOutcome.EXPIRED,
        reason_code="provider_job_expired",
        message="供应商原任务已过期，需要用户手动重新发起。",
    )


def _start_payload(request: Mapping[str, JsonValue]) -> tuple[str, dict[str, JsonValue]]:
    mode = _required_text(request, "generation_mode")
    endpoint = _MODE_ENDPOINTS.get(mode)
    if endpoint is None:
        raise ProviderJobMappingError("video_generation_mode_unsupported")
    common: dict[str, JsonValue] = {
        "prompt": _required_text(request, "prompt"),
        "model": _required_text(request, "model"),
        "ratio": _required_text(request, "ratio"),
        "size": _required_text(request, "size"),
        "duration": request.get("duration"),
        "videoCount": 1,
        # content-app 视频 body 与稳定 Workspace 合同都使用 on/off；模型计费
        # 路由信息则由上层 start 的专用请求头携带。
        "sound": _required_video_sound(_required_text(request, "sound")),
    }
    images = _string_list(request.get("image_urls"))
    videos = _string_list(request.get("video_urls"))
    audios = _string_list(request.get("audio_urls"))
    if mode == "image_to_video":
        if not images:
            raise ProviderJobMappingError("video_image_reference_missing")
        common["image_url"] = images[0]
    elif mode == "two_image_to_video":
        if len(images) < 2:
            raise ProviderJobMappingError("video_two_image_references_missing")
        common["first_frame_image_url"] = images[0]
        common["last_frame_image_url"] = images[1]
    elif mode == "reference_mode_video":
        common["imageUrls"] = images
        common["videoUrls"] = videos
        common["audioUrls"] = audios
    elif mode == "edit_video":
        if not videos:
            raise ProviderJobMappingError("video_edit_reference_missing")
        common["refVideo"] = videos[0]
        if images:
            common["refImage"] = images[0]
    return endpoint, common


def _required_video_sound(value: str) -> str:
    """校验 content-app 视频 body 的稳定 on/off 声音枚举。"""

    normalized = value.strip().casefold()
    if normalized in {"on", "off"}:
        return normalized
    raise ProviderJobMappingError("video_sound_unsupported")


def _required_video_duration(request: Mapping[str, JsonValue]) -> int:
    """校验并返回 content-app 计费路由需要的单镜整数时长。"""

    duration = request.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 15:
        raise ProviderJobMappingError("video_duration_unsupported")
    return duration


def _to_snapshot(payload: Mapping[str, object], *, expected_job_id: str | None) -> ProviderJobSnapshot:
    data = payload.get("data")
    source = data if isinstance(data, Mapping) else payload
    if payload.get("success") is False and not isinstance(data, Mapping):
        raise _ContentAppHTTPError(404)
    job_id = _first_text(source, "taskId", "task_id", "jobId", "job_id") or expected_job_id
    if not job_id:
        raise ProviderJobMappingError("provider_job_id_missing")
    if expected_job_id is not None and job_id != expected_job_id:
        raise ProviderJobMappingError("provider_job_id_mismatch")
    status = _first_text(source, "status", "taskStatus", "state").lower()
    if not status:
        status = "succeeded" if _result_value(source) is not None else "polling"
    if status in {"not_found", "expired"}:
        raise _ContentAppHTTPError(404)
    if status in {"quota_insufficient", "payment_required", "paused_quota"}:
        raise _ContentAppHTTPError(402)
    return ProviderJobSnapshot(
        provider_job_id=job_id,
        outcome=_outcome(status),
        result=_result_projection(_result_value(source), job_id=job_id) if status in {"succeeded", "success", "completed", "done"} else None,
        reason_code=_reason_code(status),
        message=_message(status),
    )


def _raise_or_json(response: httpx.Response) -> Mapping[str, object]:
    if response.status_code in {402, 404}:
        raise _ContentAppHTTPError(response.status_code)
    if response.is_error:
        raise _ContentAppHTTPError(response.status_code)
    try:
        value = response.json()
    except ValueError as exc:
        raise ProviderJobMappingError("provider_response_not_json") from exc
    if not isinstance(value, Mapping):
        raise ProviderJobMappingError("provider_response_not_object")
    return value


def _result_value(source: Mapping[str, object]) -> object:
    value = source.get("result")
    if isinstance(value, Mapping) and isinstance(value.get("data"), (Mapping, list, str)):
        return value["data"]
    return value


def _result_projection(value: object, *, job_id: str) -> dict[str, JsonValue]:
    url = _first_video_url(value)
    if url is None:
        raise ProviderJobMappingError("video_result_url_missing")
    digest = hashlib.sha256(job_id.encode()).hexdigest()[:24]
    return {
        "variant_id": f"variant:{digest}",
        "artifact_ref": f"artifact:video:{digest}",
        "video_url": url,
        "completed_at": datetime.now(UTC).isoformat(),
    }


def _first_video_url(value: object) -> str | None:
    candidates: list[object] = []
    if isinstance(value, Mapping):
        candidates.extend(value.get(key) for key in ("video_url", "videoUrl", "url", "video"))
        candidates.extend(value.get(key) for key in ("videos", "video_urls", "videoUrls"))
    else:
        candidates.append(value)
    for candidate in candidates:
        values = candidate if isinstance(candidate, list) else [candidate]
        for item in values:
            if not isinstance(item, str):
                continue
            parsed = urlparse(item)
            if parsed.scheme == "https" and parsed.netloc and not parsed.query and not parsed.fragment:
                return item
    return None


def _outcome(status: str):
    from pixelflow.operations.jobs.providers import ProviderJobOutcome

    mapping = {
        "queued": ProviderJobOutcome.POLLING,
        "pending": ProviderJobOutcome.POLLING,
        "running": ProviderJobOutcome.POLLING,
        "processing": ProviderJobOutcome.POLLING,
        "succeeded": ProviderJobOutcome.SUCCEEDED,
        "success": ProviderJobOutcome.SUCCEEDED,
        "completed": ProviderJobOutcome.SUCCEEDED,
        "done": ProviderJobOutcome.SUCCEEDED,
        "failed": ProviderJobOutcome.FAILED,
        "error": ProviderJobOutcome.FAILED,
        "timeout": ProviderJobOutcome.TIMEOUT,
        "timed_out": ProviderJobOutcome.TIMEOUT,
    }
    outcome = mapping.get(status)
    if outcome is None:
        raise ProviderJobMappingError("provider_status_unknown")
    return outcome


def _reason_code(status: str) -> str:
    return {
        "queued": "provider_polling", "pending": "provider_polling", "running": "provider_polling", "processing": "provider_polling",
        "succeeded": "provider_succeeded", "success": "provider_succeeded", "completed": "provider_succeeded", "done": "provider_succeeded",
        "failed": "provider_business_failed", "error": "provider_business_failed",
        "timeout": "provider_timeout", "timed_out": "provider_timeout",
    }[status]


def _message(status: str) -> str:
    return {
        "provider_polling": "供应商任务处理中。",
        "provider_succeeded": "供应商任务已完成。",
        "provider_business_failed": "供应商任务执行失败。",
        "provider_timeout": "供应商任务等待超时。",
    }[_reason_code(status)]


def _required_text(request: Mapping[str, JsonValue], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderJobMappingError("video_request_field_missing")
    return value.strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _first_text(source: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _bearer(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized.startswith("Bearer ") or not normalized.removeprefix("Bearer ").strip():
        raise ProviderJobMappingError("provider_start_authorization_missing")
    return normalized


def _status_authorization() -> str:
    value = os.environ.get("PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION", "")
    return _bearer(value)


def _required_identifier(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > 255:
        raise ProviderJobMappingError("provider_identifier_invalid")
    return normalized


def _env_text(key: str, default: str) -> str:
    value = os.environ.get(key, default).strip()
    if not value or len(value) > 120:
        raise ValueError(f"{key} 配置无效")
    return value


def _env_positive_float(key: str, default: float) -> float:
    try:
        value = float(os.environ.get(key, str(default)))
    except ValueError as exc:
        raise ValueError(f"{key} 配置无效") from exc
    if value <= 0 or value > 300:
        raise ValueError(f"{key} 配置无效")
    return value


__all__ = ["ContentAppVideoGenerationProvider", "ContentAppVideoProviderSettings"]
