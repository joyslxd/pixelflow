"""Content-App 图片生成 start/status 的稳定防腐 Adapter。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from pydantic import JsonValue

from pixelflow.generation_jobs.providers import (
    ProviderJobMappingError,
    ProviderJobOutcome,
    ProviderJobSnapshot,
)
from pixelflow.platform.content_app_authorization import (
    TransientContentAppAuthorizationStore,
)

_ENDPOINTS = {
    "text_to_image": "/picture/text_to_image",
    "reference_image": "/picture/multi_reference_image_generation",
}
_POLLING = {"queued", "pending", "created", "submitted", "waiting", "running", "processing", "in_progress"}
_SUCCESS = {"success", "succeeded", "completed", "done"}
_FAILED = {"failed", "error", "cancelled"}


@dataclass(frozen=True)
class ContentAppImageProviderSettings:
    base_url: str
    provider_id: str
    profile_version: str
    connect_timeout_seconds: float = 10
    read_timeout_seconds: float = 30
    project_id: str = "1"

    @classmethod
    def from_env(cls) -> ContentAppImageProviderSettings | None:
        enabled = os.environ.get("PIXELFLOW_M06_IMAGE_PROVIDER_ENABLED", "").strip().lower()
        if enabled in {"0", "false", "no", "off"}:
            return None
        base_url = os.environ.get("BORGRISE_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            return None
        if not base_url.startswith(("https://", "http://127.0.0.1:")):
            raise ValueError("M06 图片 Provider 必须配置受控 HTTPS 或 loopback content-app 地址")
        return cls(
            base_url=base_url,
            provider_id=os.environ.get("PIXELFLOW_M06_IMAGE_PROVIDER_ID", "content-app-image"),
            profile_version=os.environ.get("PIXELFLOW_M06_IMAGE_PROVIDER_PROFILE_VERSION", "v1"),
            # 用途：复用 content-app 图片生成的项目计费上下文；影响：缺省与当前 admin 图片创作页一致。
            project_id=os.environ.get("PIXELFLOW_M06_IMAGE_PROJECT_ID", "1").strip() or "1",
        )


class ContentAppImageGenerationAdapter:
    """将图片生成请求映射到 Content-App，Authorization 仅用于当前 start。"""

    def __init__(
        self,
        settings: ContentAppImageProviderSettings,
        *,
        client: httpx.AsyncClient | None = None,
        authorization_store: TransientContentAppAuthorizationStore | None = None,
    ) -> None:
        self._settings = settings
        self.provider_id = settings.provider_id
        self.profile_version = settings.profile_version
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(connect=settings.connect_timeout_seconds, read=settings.read_timeout_seconds, write=settings.read_timeout_seconds, pool=settings.connect_timeout_seconds))
        self._authorization_store = authorization_store or TransientContentAppAuthorizationStore()
        self._owns_authorization_store = authorization_store is None

    def prepare_operation_request(self, request: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        mode = str(request.get("generation_mode") or "").strip()
        if mode not in _ENDPOINTS:
            raise ProviderJobMappingError("image_generation_mode_unsupported")
        return {**dict(request), "provider_id": self.provider_id, "provider_profile_version": self.profile_version}

    async def start(self, request: Mapping[str, JsonValue], *, authorization: str, idempotency_key: str) -> ProviderJobSnapshot:
        normalized = self.prepare_operation_request(request)
        mode = str(normalized["generation_mode"])
        body = _start_payload(normalized)
        try:
            response = await self._client.post(
                f"{self._settings.base_url}{_ENDPOINTS[mode]}",
                params={"projectId": self._settings.project_id},
                headers={
                    "Authorization": _bearer(authorization),
                    "Idempotency-Key": idempotency_key,
                    "ModelType": str(normalized["model"]),
                    "billType": "2",
                    "duration": "1",
                    "apiModelParamObj": json.dumps({"size": str(normalized["size"])}, separators=(",", ":")),
                },
                json=body,
            )
            if response.status_code == 402:
                return _quota_snapshot()
            response.raise_for_status()
            snapshot = _to_snapshot(response.json(), expected_job_id=None)
            if snapshot.provider_job_id is not None and snapshot.outcome is ProviderJobOutcome.POLLING:
                await self._authorization_store.put_job(
                    provider_job_id=snapshot.provider_job_id,
                    authorization=_bearer(authorization),
                )
            return snapshot
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402:
                return _quota_snapshot()
            raise RuntimeError("content_app_image_start_failed") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("content_app_image_start_failed") from exc

    async def status(self, provider_job_id: str, *, user_id: str, conversation_id: str) -> ProviderJobSnapshot:
        del conversation_id
        try:
            authorization = await self._authorization_store.borrow(
                provider_job_id=provider_job_id,
                user_id=user_id,
            )
            response = await self._client.get(
                f"{self._settings.base_url}/task/{provider_job_id}/status",
                headers={"Authorization": authorization},
            )
            if response.status_code == 402:
                return _quota_snapshot(provider_job_id)
            if response.status_code == 404:
                await self._authorization_store.discard_job(provider_job_id=provider_job_id)
                return ProviderJobSnapshot(provider_job_id=provider_job_id, outcome=ProviderJobOutcome.EXPIRED, reason_code="provider_job_expired", message="供应商原任务已过期，需要用户手动重新发起。")
            response.raise_for_status()
            return _to_snapshot(response.json(), expected_job_id=provider_job_id)
        except httpx.HTTPError as exc:
            raise RuntimeError("content_app_image_status_failed") from exc

    async def aclose(self) -> None:
        if self._owns_authorization_store:
            await self._authorization_store.aclose()
        if self._owns_client:
            await self._client.aclose()

def _start_payload(request: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """映射 content-app 文生图 DTO，比例以 width/height 传递而非 ratio。"""

    mode = str(request["generation_mode"])
    width, height = _ratio_dimensions(str(request["ratio"]))
    model = str(request["model"])
    payload: dict[str, JsonValue] = {
        "prompt": str(request["prompt"]),
        "model": model,
        "model_version": model,
        "width": width,
        "height": height,
        "imageSize": str(request["size"]),
        "num": 1,
        "oldFileOrderList": [],
    }
    if mode == "reference_image":
        payload["reference_image_urls"] = list(_strings(request.get("reference_image_urls")))
    return payload


def _ratio_dimensions(ratio: str) -> tuple[str, str]:
    """将工作区比例转换为 content-app 图片接口的 width/height 字符串。"""

    parts = ratio.strip().split(":", 1)
    if len(parts) != 2 or not all(part.isdigit() and 0 < int(part) <= 99 for part in parts):
        raise ValueError("image_generation_ratio_invalid")
    return parts[0], parts[1]


def _to_snapshot(payload: Mapping[str, object], *, expected_job_id: str | None) -> ProviderJobSnapshot:
    source = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    assert isinstance(source, Mapping)
    job_id = _first(source, "taskId", "task_id", "jobId", "job_id") or expected_job_id
    if not job_id:
        raise ProviderJobMappingError("image_provider_job_id_missing")
    if expected_job_id and job_id != expected_job_id:
        raise ProviderJobMappingError("image_provider_job_id_mismatch")
    status = (_first(source, "status", "taskStatus", "state") or "").lower()
    result = _image_result(source)
    if status in {"quota_insufficient", "payment_required", "paused_quota"}:
        return _quota_snapshot(job_id)
    if status in _SUCCESS or (not status and result is not None):
        return ProviderJobSnapshot(provider_job_id=job_id, outcome=ProviderJobOutcome.SUCCEEDED, result=result, reason_code="provider_succeeded", message="供应商任务已完成。")
    if status in _FAILED:
        return ProviderJobSnapshot(provider_job_id=job_id, outcome=ProviderJobOutcome.FAILED, reason_code="provider_business_failed", message="供应商任务执行失败。")
    return ProviderJobSnapshot(provider_job_id=job_id, outcome=ProviderJobOutcome.POLLING, reason_code="provider_polling", message="供应商任务处理中。")


def _image_result(source: Mapping[str, object]) -> dict[str, JsonValue] | None:
    values = source.get("images") or source.get("imageUrls") or source.get("image_urls") or source.get("result")
    if isinstance(values, str) and values.startswith("https://"):
        return {"image_url": values, "artifact_ref": "artifact:image:" + values.rsplit("/", 1)[-1][:180]}
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value.startswith("https://"):
                return {"image_url": value, "artifact_ref": "artifact:image:" + value.rsplit("/", 1)[-1][:180]}
            if isinstance(value, Mapping):
                url = _first(value, "url", "image_url", "src")
                if url and url.startswith("https://"):
                    return {"image_url": url, "artifact_ref": "artifact:image:" + url.rsplit("/", 1)[-1][:180]}
    return None


def _quota_snapshot(provider_job_id: str | None = None) -> ProviderJobSnapshot:
    return ProviderJobSnapshot(provider_job_id=provider_job_id, outcome=ProviderJobOutcome.PAUSED_QUOTA, reason_code="provider_quota_insufficient", message="额度不足，当前任务已暂停，可在充值后继续。")


def _first(value: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _bearer(value: str) -> str:
    text = value.strip()
    return text if text.lower().startswith("bearer ") else f"Bearer {text}"
