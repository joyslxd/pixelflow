"""剪映第三方任务到M06 ExistingJobService的持久恢复适配。"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import JsonValue, ValidationError

from .models import JianyingDraftRequest

_CREATE_PATH = "/api/jianying/draft/tasks"
_RESULT_PATH = "/api/jianying/draft/tasks/result"
_INTERNAL_UPLOAD_PATH = "/internal/upload"
_PROCESSING_CODES = frozenset({20201, 20202})
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024


class JianyingProviderContractError(RuntimeError):
    """剪映Provider或content-app内部上传响应不满足安全合同。"""


class JianyingDraftProviderJobService:
    """分离第三方task轮询与归属真实用户的幂等ZIP上传。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        provider_base_url: str,
        provider_token: str,
        content_app_base_url: str,
        service_authorization_provider: Callable[[], str],
        create_timeout_seconds: float = 30.0,
        query_timeout_seconds: float = 15.0,
        download_timeout_seconds: float = 30.0,
        upload_timeout_seconds: float = 300.0,
        max_archive_bytes: int = _MAX_ARCHIVE_BYTES,
    ) -> None:
        if not isinstance(client, httpx.AsyncClient):
            raise TypeError("client必须是httpx.AsyncClient")
        if not callable(service_authorization_provider):
            raise TypeError("service_authorization_provider必须可调用")
        if isinstance(max_archive_bytes, bool) or max_archive_bytes <= 0:
            raise ValueError("剪映ZIP大小上限必须为正整数")
        self._client = client
        self._provider_base_url = _public_https_base_url(provider_base_url)
        self._provider_token = _required_text("provider_token", provider_token)
        self._content_app_base_url = _http_base_url(content_app_base_url)
        self._service_authorization_provider = service_authorization_provider
        self._create_timeout_seconds = _positive_timeout(
            "create_timeout_seconds",
            create_timeout_seconds,
        )
        self._query_timeout_seconds = _positive_timeout(
            "query_timeout_seconds",
            query_timeout_seconds,
        )
        self._download_timeout_seconds = _positive_timeout(
            "download_timeout_seconds",
            download_timeout_seconds,
        )
        self._upload_timeout_seconds = _positive_timeout(
            "upload_timeout_seconds",
            upload_timeout_seconds,
        )
        self._max_archive_bytes = max_archive_bytes

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        """只创建第三方task；用户Authorization不进入Service状态或请求。"""

        _required_text("authorization", authorization)
        normalized_key = _required_text("idempotency_key", idempotency_key)
        request_payload = request.get("request")
        try:
            domain_request = JianyingDraftRequest.model_validate(request_payload)
        except ValidationError as exc:
            raise JianyingProviderContractError("剪映启动请求无效") from exc
        scenes = sorted(domain_request.scenes, key=lambda item: item.scene_index)
        response = await self._client.post(
            urljoin(self._provider_base_url, _CREATE_PATH.lstrip("/")),
            json=[
                {"videoUrl": str(scene.video_url), "videoOrder": order}
                for order, scene in enumerate(scenes, start=1)
            ],
            headers={
                "Content-Type": "application/json",
                "token": self._provider_token,
                "Idempotency-Key": normalized_key,
            },
            timeout=self._create_timeout_seconds,
        )
        response.raise_for_status()
        payload = _response_mapping(response)
        code = _business_code(payload)
        provider_job_id = payload.get("data")
        if code != 200 or not isinstance(provider_job_id, str) or not provider_job_id.strip():
            return {
                "ok": False,
                "job_id": _start_failure_job_id(normalized_key),
                "status": "failed",
                "result": None,
            }
        return {
            "ok": True,
            "job_id": _provider_job_id(provider_job_id),
            "status": "polling",
            "result": None,
        }

    async def status(self, provider_job_id: str) -> object:
        """非作用域查询禁止执行，避免ZIP被错误归属到服务账号。"""

        _provider_job_id(provider_job_id)
        raise JianyingProviderContractError("剪映状态查询缺少目标用户作用域")

    async def status_scoped(
        self,
        provider_job_id: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> object:
        """查询原task，并通过内部幂等上传把ZIP归属到目标用户。"""

        normalized_job_id = _provider_job_id(provider_job_id)
        target_user_id = _scope_text("user_id", user_id)
        _scope_text("conversation_id", conversation_id)
        response = await self._client.post(
            urljoin(self._provider_base_url, _RESULT_PATH.lstrip("/")),
            json={"taskId": normalized_job_id},
            headers={"Content-Type": "application/json", "token": self._provider_token},
            timeout=self._query_timeout_seconds,
        )
        response.raise_for_status()
        payload = _response_mapping(response)
        code = _business_code(payload)
        if code in _PROCESSING_CODES:
            return {
                "ok": True,
                "job_id": normalized_job_id,
                "status": "polling",
                "result": None,
            }
        if code != 200:
            return {
                "ok": False,
                "job_id": normalized_job_id,
                "status": "failed",
                "result": None,
            }
        source_url = _single_public_https_url(payload.get("data"))
        file_name = _stable_file_name(normalized_job_id)
        with tempfile.TemporaryDirectory(prefix="pixelflow-jianying-live-") as directory:
            archive_path = Path(directory) / file_name
            await self._download_archive(source_url, archive_path)
            _validate_zip(archive_path)
            download_url = await self._upload_for_user(
                archive_path,
                provider_job_id=normalized_job_id,
                target_user_id=target_user_id,
            )
        return {
            "ok": True,
            "job_id": normalized_job_id,
            "status": "succeeded",
            "result": {
                "download_url": download_url,
                "file_name": file_name,
                "expire_at": None,
                "message": "剪映草稿已生成",
            },
        }

    async def _download_archive(self, source_url: str, destination: Path) -> None:
        async with self._client.stream(
            "GET",
            source_url,
            timeout=self._download_timeout_seconds,
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise JianyingProviderContractError("剪映ZIP长度无效") from exc
                if declared_size > self._max_archive_bytes:
                    raise JianyingProviderContractError("剪映ZIP超过大小上限")
            downloaded = 0
            with destination.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    downloaded += len(chunk)
                    if downloaded > self._max_archive_bytes:
                        raise JianyingProviderContractError("剪映ZIP超过大小上限")
                    output.write(chunk)

    async def _upload_for_user(
        self,
        archive_path: Path,
        *,
        provider_job_id: str,
        target_user_id: str,
    ) -> str:
        authorization = _service_authorization(
            self._service_authorization_provider()
        )
        idempotency_key = _archive_upload_idempotency_key(
            provider_job_id,
            target_user_id,
        )
        with archive_path.open("rb") as archive:
            response = await self._client.post(
                urljoin(
                    self._content_app_base_url,
                    _INTERNAL_UPLOAD_PATH.lstrip("/"),
                ),
                data={"target_user_id": target_user_id},
                files={
                    "file": (
                        archive_path.name,
                        archive,
                        "application/zip",
                    )
                },
                headers={
                    "Authorization": authorization,
                    "Idempotency-Key": idempotency_key,
                },
                timeout=self._upload_timeout_seconds,
            )
        response.raise_for_status()
        payload = _response_mapping(response)
        if payload.get("success") is False:
            raise JianyingProviderContractError("content-app内部幂等上传失败")
        upload_url = _find_upload_url(payload)
        if upload_url is None:
            raise JianyingProviderContractError("content-app内部上传缺少安全URL")
        return upload_url

    def __repr__(self) -> str:
        return "JianyingDraftProviderJobService(status_scope='target_user')"


def _positive_timeout(field: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field}必须为正数")
    return float(value)


def _required_text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}不能为空")
    return value.strip()


def _scope_text(field: str, value: object) -> str:
    normalized = _required_text(field, value)
    if len(normalized) > 64 or normalized != value:
        raise ValueError(f"{field}格式无效")
    return normalized


def _provider_job_id(value: object) -> str:
    normalized = _required_text("provider_job_id", value)
    if len(normalized) > 128 or re.fullmatch(r"[A-Za-z0-9._:-]+", normalized) is None:
        raise JianyingProviderContractError("剪映provider job ID格式无效")
    return normalized


def _service_authorization(value: object) -> str:
    normalized = _required_text("service_authorization", value)
    if not normalized.startswith("Bearer ") or "\r" in normalized or "\n" in normalized:
        raise JianyingProviderContractError("content-app服务凭据不可用")
    return normalized


def _http_base_url(value: object) -> str:
    normalized = _required_text("content_app_base_url", value).rstrip("/") + "/"
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("content_app_base_url必须是HTTP(S)地址")
    return normalized


def _public_https_base_url(value: object) -> str:
    normalized = _single_public_https_url(value).rstrip("/") + "/"
    return normalized


def _single_public_https_url(value: object) -> str:
    candidate: object = value
    if isinstance(value, list):
        if len(value) != 1:
            raise JianyingProviderContractError("剪映Provider必须返回唯一ZIP URL")
        candidate = value[0]
    normalized = _required_text("https_url", candidate)
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise JianyingProviderContractError("剪映Provider URL必须是公开HTTPS")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise JianyingProviderContractError("剪映Provider URL不是公开地址")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return normalized
    if not address.is_global:
        raise JianyingProviderContractError("剪映Provider URL不是公网地址")
    return normalized


def _find_upload_url(value: object, *, depth: int = 0) -> str | None:
    if depth > 5:
        return None
    if isinstance(value, str):
        try:
            normalized = _single_public_https_url(value)
        except (TypeError, ValueError, JianyingProviderContractError):
            return None
        return normalized if not urlsplit(normalized).query else None
    if isinstance(value, Mapping):
        for key in ("url", "download_url", "downloadUrl", "data", "result"):
            if key in value:
                found = _find_upload_url(value[key], depth=depth + 1)
                if found is not None:
                    return found
    return None


def _response_mapping(response: httpx.Response) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise JianyingProviderContractError("Provider响应不是合法JSON") from exc
    if not isinstance(payload, Mapping):
        raise JianyingProviderContractError("Provider响应必须是JSON对象")
    return payload


def _business_code(payload: Mapping[str, object]) -> int | None:
    value = payload.get("code")
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type] - 第三方业务码可能是数字字符串
    except (TypeError, ValueError):
        return None


def _validate_zip(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise JianyingProviderContractError("剪映Provider返回的文件不是ZIP")
    with zipfile.ZipFile(path) as archive:
        if not archive.infolist():
            raise JianyingProviderContractError("剪映Provider返回了空ZIP")


def _archive_upload_idempotency_key(
    provider_job_id: str,
    target_user_id: str,
) -> str:
    digest = hashlib.sha256(
        f"pixelflow:jianying-archive:v1:{provider_job_id}:{target_user_id}".encode()
    ).hexdigest()
    return f"pf:jianying-upload:{digest}"


def _start_failure_job_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
    return f"jianying-start-failed-{digest}"


def _stable_file_name(provider_job_id: str) -> str:
    digest = hashlib.sha256(provider_job_id.encode()).hexdigest()[:16]
    return f"PixelFlow-剪映草稿-{digest}.zip"


__all__ = [
    "JianyingDraftProviderJobService",
    "JianyingProviderContractError",
]
