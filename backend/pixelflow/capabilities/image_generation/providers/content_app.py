"""Content-App 图片生成 start/status 的稳定防腐 Adapter。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
from pydantic import JsonValue

from pixelflow.generation_jobs.providers import (
    ProviderJobMappingError,
    ProviderJobOutcome,
    ProviderJobSnapshot,
    ProviderResponseDiagnostics,
)
from pixelflow.platform.content_app_authorization import TransientContentAppAuthorizationStore
from pixelflow.platform.content_app_url import optional_content_app_base_url

_ENDPOINTS = {
    "text_to_image": "/picture/text_to_image",
    "reference_image": "/picture/multi_reference_image_generation",
}
_POLLING = {"queued", "pending", "created", "submitted", "waiting", "running", "processing", "in_progress"}
_SUCCESS = {"success", "succeeded", "completed", "done"}
_FAILED = {"failed", "error", "cancelled"}
# Content-App 的 DTO 需要实际像素值。此表采用 Seedream 支持的 1K-4K 区间，避免把工作区比例 9:16 误传成 9×16 像素。
_RATIO_PIXEL_DIMENSIONS: dict[str, tuple[int, int]] = {
    "1:1": (2048, 2048),
    "4:3": (2304, 1728),
    "3:4": (1728, 2304),
    "3:2": (2496, 1664),
    "2:3": (1664, 2496),
    "16:9": (2560, 1440),
    "9:16": (1440, 2560),
    "21:9": (3024, 1296),
    "9:21": (1296, 3024),
}


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
        base_url = optional_content_app_base_url(os.environ.get("BORGRISE_BASE_URL", ""))
        if base_url is None:
            return None
        return cls(
            base_url=base_url,
            provider_id=os.environ.get("PIXELFLOW_M06_IMAGE_PROVIDER_ID", "content-app-image"),
            profile_version=os.environ.get("PIXELFLOW_M06_IMAGE_PROVIDER_PROFILE_VERSION", "v1"),
            # 用途：复用 content-app 图片生成的项目计费上下文；影响：缺省与当前 admin 图片创作页一致。
            project_id=os.environ.get("PIXELFLOW_M06_IMAGE_PROJECT_ID", "1").strip() or "1",
        )


class ContentAppImageGenerationAdapter:
    """将图片生成请求映射到 Content-App；start 与 status 都使用当前用户 Authorization。"""

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
        self._authorization_store = authorization_store or TransientContentAppAuthorizationStore()
        self._owns_authorization_store = authorization_store is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(connect=settings.connect_timeout_seconds, read=settings.read_timeout_seconds, write=settings.read_timeout_seconds, pool=settings.connect_timeout_seconds))

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
            payload, diagnostics = _require_json_object(response)
            snapshot = _to_snapshot(payload, expected_job_id=None, diagnostics=diagnostics)
            await self._bind_status_authorization(snapshot, authorization)
            return snapshot
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402:
                return _quota_snapshot()
            raise RuntimeError("content_app_image_start_failed") from exc
        except ProviderJobMappingError:
            # 200 响应但字段不符合 Provider 合同，必须保留受控原因码供 Worker 投影诊断。
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("content_app_image_start_failed") from exc

    async def status(
        self,
        provider_job_id: str,
        *,
        user_id: str,
        conversation_id: str,
        authorization: str = "",
    ) -> ProviderJobSnapshot:
        del conversation_id
        token = ""
        try:
            token = await self._status_authorization(provider_job_id, user_id=user_id, authorization=authorization)
            response = await self._client.get(
                f"{self._settings.base_url}/task/{provider_job_id}/status",
                headers={"Authorization": token},
            )
            if response.status_code == 402:
                return _quota_snapshot(provider_job_id)
            if response.status_code == 404:
                await self._authorization_store.discard_job(provider_job_id=provider_job_id)
                return ProviderJobSnapshot(provider_job_id=provider_job_id, outcome=ProviderJobOutcome.EXPIRED, reason_code="provider_job_expired", message="供应商原任务已过期，需要用户手动重新发起。")
            response.raise_for_status()
            payload, diagnostics = _require_json_object(response)
            snapshot = _to_snapshot(payload, expected_job_id=provider_job_id, diagnostics=diagnostics)
            if snapshot.outcome is not ProviderJobOutcome.POLLING:
                await self._authorization_store.discard_job(provider_job_id=provider_job_id)
            return snapshot
        except LookupError as exc:
            raise ProviderJobMappingError("provider_status_authorization_unavailable") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("content_app_image_status_failed") from exc
        finally:
            token = ""

    async def _bind_status_authorization(self, snapshot: ProviderJobSnapshot, authorization: str) -> None:
        """把创建任务时的用户授权绑到 Provider Job，供后续 status 轮询。"""

        if snapshot.provider_job_id and snapshot.outcome is ProviderJobOutcome.POLLING:
            await self._authorization_store.put_job(
                provider_job_id=snapshot.provider_job_id,
                authorization=_bearer(authorization),
            )

    async def _status_authorization(self, provider_job_id: str, *, user_id: str, authorization: str) -> str:
        """优先使用 Worker 传入的任务凭据，缺失时回退到当前用户浏览器授权。"""

        if authorization.strip():
            return _bearer(authorization)
        return await self._authorization_store.borrow(provider_job_id=provider_job_id, user_id=user_id)

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


def _ratio_dimensions(ratio: str) -> tuple[int, int]:
    """将工作区比例转换为 Content-App DTO 所需的实际像素宽高。"""

    parts = ratio.strip().split(":", 1)
    if len(parts) != 2 or not all(part.isdigit() and 0 < int(part) <= 99 for part in parts):
        raise ValueError("image_generation_ratio_invalid")
    numerator, denominator = (int(part) for part in parts)
    divisor = _greatest_common_divisor(numerator, denominator)
    normalized = f"{numerator // divisor}:{denominator // divisor}"
    if dimensions := _RATIO_PIXEL_DIMENSIONS.get(normalized):
        return dimensions
    if max(numerator, denominator) / min(numerator, denominator) > 3:
        raise ValueError("image_generation_ratio_invalid")
    # 非预设比例以约 3.9MP 生成，保持在 Provider 允许的 1K-4K 面积范围内；16 像素对齐便于下游处理。
    scale = int((3_932_160 / (numerator * denominator)) ** 0.5) // 16 * 16
    width, height = numerator * scale, denominator * scale
    if width < 1024 or height < 1024 or width * height > 4_194_304:
        raise ValueError("image_generation_ratio_invalid")
    return width, height


def _greatest_common_divisor(left: int, right: int) -> int:
    while right:
        left, right = right, left % right
    return left


def _to_snapshot(
    payload: Mapping[str, object],
    *,
    expected_job_id: str | None,
    diagnostics: ProviderResponseDiagnostics | None = None,
) -> ProviderJobSnapshot:
    # 保留响应根层，兼容 taskId 在根层、data 层或 data.task 层的 Provider DTO。
    source = payload
    job_id = _first_nested(source, "taskId", "task_id", "jobId", "job_id", "id") or expected_job_id
    if not job_id:
        raise ProviderJobMappingError("image_provider_job_id_missing", diagnostics=diagnostics)
    if expected_job_id and job_id != expected_job_id:
        raise ProviderJobMappingError("image_provider_job_id_mismatch", diagnostics=diagnostics)
    status = (_first_nested(source, "status", "taskStatus", "state") or "").lower()
    result = _image_result(source)
    if status in {"quota_insufficient", "payment_required", "paused_quota"}:
        return _quota_snapshot(job_id)
    if status in _FAILED:
        return ProviderJobSnapshot(provider_job_id=job_id, outcome=ProviderJobOutcome.FAILED, reason_code="provider_business_failed", message="供应商任务执行失败。")
    if status in _SUCCESS or (not status and result is not None):
        if result is None:
            raise ProviderJobMappingError("image_result_url_missing", diagnostics=diagnostics)
        return ProviderJobSnapshot(provider_job_id=job_id, outcome=ProviderJobOutcome.SUCCEEDED, result=result, reason_code="provider_succeeded", message="供应商任务已完成。")
    return ProviderJobSnapshot(provider_job_id=job_id, outcome=ProviderJobOutcome.POLLING, reason_code="provider_polling", message="供应商任务处理中。")


def _image_result(source: Mapping[str, object]) -> dict[str, JsonValue] | None:
    """从 data/task/result.data 等受控层取出第一张 HTTPS 图，不保留查询串到 Artifact。"""

    url = _first_image_url(source)
    if url is None:
        return None
    name = url.rsplit("/", 1)[-1][:180] or "image"
    return {"image_url": url, "artifact_ref": "artifact:image:" + name}


def _first_image_url(value: object, *, depth: int = 0) -> str | None:
    """兼容 content-app 把图片放在 result.data / images / imageUrl 的成功 DTO。"""

    if depth > 4:
        return None
    if isinstance(value, str):
        return _canonical_https_image_url(value)
    if isinstance(value, list):
        for item in value[:8]:
            found = _first_image_url(item, depth=depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, Mapping):
        return None
    result = value.get("result")
    if isinstance(result, Mapping) and "data" in result:
        found = _first_image_url(result.get("data"), depth=depth + 1)
        if found:
            return found
    for key in (
        "images",
        "imageUrls",
        "image_urls",
        "imageUrl",
        "image_url",
        "url",
        "urls",
        "fileUrl",
        "file_url",
        "src",
        "output",
    ):
        if key not in value:
            continue
        found = _first_image_url(value.get(key), depth=depth + 1)
        if found:
            return found
    for key in ("data", "task", "job", "payload", "result"):
        nested = value.get(key)
        if isinstance(nested, (Mapping, list, str)):
            found = _first_image_url(nested, depth=depth + 1)
            if found:
                return found
    return None


def _canonical_https_image_url(value: str) -> str | None:
    """只接受无用户信息的 HTTPS 图地址，去掉查询串以免 Snapshot 安全校验拒绝。"""

    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None or parsed.password is not None:
        return None
    return urlunparse(("https", parsed.netloc, parsed.path or "/", "", "", ""))


def _quota_snapshot(provider_job_id: str | None = None) -> ProviderJobSnapshot:
    return ProviderJobSnapshot(provider_job_id=provider_job_id, outcome=ProviderJobOutcome.PAUSED_QUOTA, reason_code="provider_quota_insufficient", message="额度不足，当前任务已暂停，可在充值后继续。")


def _require_json_object(response: httpx.Response) -> tuple[Mapping[str, object], ProviderResponseDiagnostics]:
    """把 Content-App 成功响应收成对象，映射失败只保留受控原因码。"""

    diagnostics = _response_diagnostics(response)
    try:
        payload = response.json()
    except ValueError:
        raise ProviderJobMappingError("provider_response_not_json", diagnostics=diagnostics) from None
    if not isinstance(payload, Mapping):
        raise ProviderJobMappingError("provider_response_not_object", diagnostics=diagnostics)
    return payload, _response_diagnostics(response, payload)


def _response_diagnostics(
    response: httpx.Response,
    payload: object | None = None,
) -> ProviderResponseDiagnostics:
    """只提取响应元数据和字段路径，不保留响应值。"""

    return ProviderResponseDiagnostics(
        status_code=response.status_code,
        content_type=response.headers.get("content-type", "unknown"),
        response_length=len(response.content),
        field_paths=_field_paths(payload),
    )


def _field_paths(value: object, *, max_depth: int = 4) -> tuple[str, ...]:
    """以有限深度提取 JSON 字段路径，完全忽略字段值。"""

    paths: list[str] = []

    def visit(current: object, prefix: str, depth: int) -> None:
        if depth >= max_depth:
            return
        if isinstance(current, Mapping):
            for key, nested in current.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                path = f"{prefix}.{key.strip()}" if prefix else key.strip()
                if not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]{1,256}", path):
                    continue
                paths.append(path)
                visit(nested, path, depth + 1)
        elif isinstance(current, list) and current:
            path = f"{prefix}[]"
            if re.fullmatch(r"[A-Za-z0-9_.:\[\]-]{1,256}", path):
                paths.append(path)
                visit(current[0], path, depth + 1)

    visit(value, "", 0)
    return tuple(dict.fromkeys(paths))[:128]


def _first(value: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _first_nested(value: Mapping[str, object], *keys: str) -> str | None:
    """在 Content-App 常见的 data/task/job 包装层中读取文本字段。"""

    for layer in _mapping_layers(value):
        found = _first(layer, *keys)
        if found:
            return found
    return None


def _mapping_layers(value: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """返回有限深度的响应对象层，避免对 Provider 返回内容做无界猜测。"""

    layers: list[Mapping[str, object]] = []
    pending: list[tuple[Mapping[str, object], int]] = [(value, 0)]
    nested_keys = ("data", "task", "job", "payload")
    seen: set[int] = set()
    while pending:
        current, depth = pending.pop(0)
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        layers.append(current)
        if depth >= 2:
            continue
        for key in nested_keys:
            nested = current.get(key)
            if isinstance(nested, Mapping):
                pending.append((nested, depth + 1))
    return tuple(layers)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _bearer(value: str) -> str:
    text = value.strip()
    return text if text.lower().startswith("bearer ") else f"Bearer {text}"
