"""第三方剪映草稿 HTTP Provider 实现。"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .models import JianyingDraftRequest, JianyingDraftResult, JianyingDraftStatus
from .skill import JianyingDraftCapability

logger = logging.getLogger(__name__)

_CREATE_PATH = "/api/jianying/draft/tasks"
_RESULT_PATH = "/api/jianying/draft/tasks/result"
_PROCESSING_CODES = {20201, 20202}
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
_DEFAULT_CREATE_READ_TIMEOUT_SECONDS = 30.0
_DEFAULT_QUERY_READ_TIMEOUT_SECONDS = 15.0
_DEFAULT_DOWNLOAD_READ_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
_SENSITIVE_BUSINESS_MESSAGE_PATTERN = re.compile(
    r"https?://|\b(?:bearer|token|authorization|api[_ -]?key|secret)\b|密钥|鉴权|凭据",
    flags=re.IGNORECASE,
)


class _ProviderRequestError(RuntimeError):
    """第三方网络请求耗尽重试后的内部异常。"""


class _SourceFileError(RuntimeError):
    """第三方返回的草稿 ZIP 不满足下载或文件合同。"""


def _https_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value.strip()
    return value.strip() if address.is_global else None


def _safe_file_stem(value: str | None) -> str:
    candidate = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", str(value or "").strip(), flags=re.UNICODE)
    candidate = candidate.strip(".-_")[:60]
    return candidate or "PixelFlow"


def _single_provider_zip_url(value: object) -> str:
    """兼容第三方单字符串及单元素数组两种 ZIP URL 包装。"""

    candidate: object = value
    if isinstance(value, list):
        if len(value) != 1:
            raise _SourceFileError("provider must return exactly one ZIP URL")
        candidate = value[0]
    url = _https_url(candidate)
    if url is None:
        raise _SourceFileError("provider ZIP URL must use public HTTPS")
    return url


def _public_business_message(prefix: str, body: dict[str, Any]) -> str:
    """只公开第三方业务文案，不暴露响应体、凭据或异常细节。"""

    message = re.sub(r"[\r\n\t\x00-\x1f\x7f]+", " ", str(body.get("message") or "")).strip()
    message = message[:160]
    if _SENSITIVE_BUSINESS_MESSAGE_PATTERN.search(message):
        return prefix
    return f"{prefix}：{message}" if message else prefix


def _default_uploader(path: str) -> dict[str, Any]:
    """复用 content-app `/api/upload`，由当前用户 Authorization 上传到 TOS。"""

    from pixelflow.skills.borgrise.run_generation import upload_file

    return upload_file(path)


class HttpJianyingDraftSkill:
    """创建第三方任务、轮询 ZIP 结果并原样上传 TOS。"""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        poll_interval_seconds: float = 2.0,
        max_retries: int = 2,
        connect_timeout_seconds: float = _DEFAULT_CONNECT_TIMEOUT_SECONDS,
        create_read_timeout_seconds: float = _DEFAULT_CREATE_READ_TIMEOUT_SECONDS,
        query_read_timeout_seconds: float = _DEFAULT_QUERY_READ_TIMEOUT_SECONDS,
        download_read_timeout_seconds: float = _DEFAULT_DOWNLOAD_READ_TIMEOUT_SECONDS,
        retry_backoff_seconds: float = 0.5,
        max_archive_bytes: int = _DEFAULT_MAX_ARCHIVE_BYTES,
        http_client: httpx.AsyncClient | None = None,
        uploader: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        normalized_base_url = str(base_url or "").rstrip("/")
        if normalized_base_url and _https_url(normalized_base_url) is None:
            raise ValueError("jianying draft base_url must be a public HTTPS URL")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._base_url = normalized_base_url
        self._token = str(token or "").strip()
        self._poll_interval_seconds = poll_interval_seconds
        self._max_retries = max_retries
        self._connect_timeout_seconds = connect_timeout_seconds
        self._create_read_timeout_seconds = create_read_timeout_seconds
        self._query_read_timeout_seconds = query_read_timeout_seconds
        self._download_read_timeout_seconds = download_read_timeout_seconds
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._max_archive_bytes = max_archive_bytes
        self._client = http_client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = http_client is None
        self._uploader = uploader or _default_uploader

    async def capability(self) -> JianyingDraftCapability:
        """配置完整即开放能力；具体连通性在异步 job 中给出结果。"""

        return JianyingDraftCapability(
            available=bool(self._base_url and self._token),
            reason="" if self._base_url and self._token else "剪映草稿服务待接入",
            poll_interval_seconds=self._poll_interval_seconds,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        """完成创建、轮询、ZIP 下载校验和 TOS 上传的完整 Provider 流程。"""

        try:
            created = await self._post_provider(
                _CREATE_PATH,
                [{"videoUrl": str(scene.video_url), "videoOrder": order} for order, scene in enumerate(request.scenes, start=1)],
                read_timeout_seconds=self._create_read_timeout_seconds,
            )
        except _ProviderRequestError:
            return self._failed("第三方剪映草稿任务创建失败")

        if self._business_code(created) != 200:
            return self._failed(_public_business_message("第三方剪映草稿任务创建失败", created))
        provider_task_id = created.get("data")
        if not isinstance(provider_task_id, str) or not provider_task_id.strip():
            return self._failed("第三方剪映草稿任务未返回任务编号")
        provider_task_id = provider_task_id.strip()

        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                queried = await self._post_provider(
                    _RESULT_PATH,
                    {"taskId": provider_task_id},
                    read_timeout_seconds=self._query_read_timeout_seconds,
                )
            except _ProviderRequestError:
                return self._failed("第三方剪映草稿结果查询失败", provider_task_id=provider_task_id)

            code = self._business_code(queried)
            if code in _PROCESSING_CODES:
                continue
            if code != 200:
                return self._failed(
                    _public_business_message("第三方剪映草稿任务处理失败", queried),
                    provider_task_id=provider_task_id,
                )

            try:
                return await self._download_and_upload(
                    request=request,
                    provider_task_id=provider_task_id,
                    source_url=_single_provider_zip_url(queried.get("data")),
                )
            except (_ProviderRequestError, _SourceFileError):
                return self._failed("剪映草稿文件归档失败", provider_task_id=provider_task_id)
            except Exception as exc:  # noqa: BLE001 - 上传实现是外部边界，统一收敛成可重试终态
                logger.error(
                    "[pixelflow] jianying draft archive failed provider_task_id=%s error_type=%s",
                    provider_task_id,
                    type(exc).__name__,
                )
                return self._failed("剪映草稿文件归档失败", provider_task_id=provider_task_id)

    async def _download_and_upload(
        self,
        *,
        request: JianyingDraftRequest,
        provider_task_id: str,
        source_url: object,
    ) -> JianyingDraftResult:
        file_name = f"{_safe_file_stem(request.project_name)}-剪映草稿.zip"
        with tempfile.TemporaryDirectory(prefix="pixelflow-jianying-") as temp_dir:
            archive_path = Path(temp_dir) / file_name
            await self._download_zip(source_url, archive_path)
            self._validate_zip(archive_path)
            upload_task = asyncio.create_task(asyncio.to_thread(self._uploader, str(archive_path)))
            upload_result = await self._await_non_cancellable_upload(upload_task)
        if not isinstance(upload_result, dict) or upload_result.get("error") or upload_result.get("success") is not True:
            raise _ProviderRequestError("content-app upload failed")
        download_url = _https_url(upload_result.get("url"))
        if download_url is None:
            raise _ProviderRequestError("content-app upload did not return an HTTPS URL")
        return JianyingDraftResult(
            status=JianyingDraftStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            download_url=download_url,
            file_name=file_name,
            message="剪映草稿已生成",
        )

    @staticmethod
    async def _await_non_cancellable_upload(upload_task: asyncio.Task[dict[str, Any]]) -> dict[str, Any]:
        """上传线程无法安全中断；收到取消时继续等待，避免提前删除它正在读取的 ZIP。"""

        while True:
            try:
                return await asyncio.shield(upload_task)
            except asyncio.CancelledError:
                if upload_task.done():
                    return upload_task.result()

    async def _download_zip(self, source_url: object, destination: Path) -> None:
        url = _https_url(source_url)
        if url is None:
            raise _SourceFileError("source URL must use public HTTPS")

        for attempt in range(self._max_retries + 1):
            try:
                timeout = self._timeout(self._download_read_timeout_seconds)
                async with self._client.stream("GET", url, timeout=timeout) as response:
                    if response.status_code >= 500:
                        if attempt < self._max_retries:
                            await self._retry_sleep(attempt)
                            continue
                        raise _ProviderRequestError("source download returned 5xx")
                    if response.status_code != 200:
                        raise _SourceFileError("source download returned non-200")
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > self._max_archive_bytes:
                        raise _SourceFileError("source archive exceeds size limit")
                    downloaded_bytes = 0
                    with destination.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > self._max_archive_bytes:
                                raise _SourceFileError("source archive exceeds size limit")
                            output.write(chunk)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt < self._max_retries:
                    await self._retry_sleep(attempt)
                    continue
                raise _ProviderRequestError("source download network failure") from None
            except ValueError as exc:
                raise _SourceFileError("invalid content-length") from exc
            return
        raise _ProviderRequestError("source download retries exhausted")

    @staticmethod
    def _validate_zip(path: Path) -> None:
        if not zipfile.is_zipfile(path):
            raise _SourceFileError("source file is not a ZIP archive")
        with zipfile.ZipFile(path) as archive:
            if not archive.infolist():
                raise _SourceFileError("source ZIP archive is empty")

    async def _post_provider(
        self,
        path: str,
        payload: object,
        *,
        read_timeout_seconds: float,
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}{path}",
                    json=payload,
                    headers={"Content-Type": "application/json", "token": self._token},
                    timeout=self._timeout(read_timeout_seconds),
                )
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt < self._max_retries:
                    await self._retry_sleep(attempt)
                    continue
                raise _ProviderRequestError("provider network failure") from None

            if response.status_code >= 500 and attempt < self._max_retries:
                await self._retry_sleep(attempt)
                continue
            try:
                body = response.json()
            except ValueError as exc:
                raise _ProviderRequestError("provider returned invalid JSON") from exc
            if not isinstance(body, dict):
                raise _ProviderRequestError("provider returned invalid envelope")
            if response.status_code >= 500:
                return body
            if response.status_code != 200:
                return body if "code" in body else {"code": response.status_code, "data": None}
            return body
        raise _ProviderRequestError("provider retries exhausted")

    def _timeout(self, read_timeout_seconds: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=self._connect_timeout_seconds,
        )

    async def _retry_sleep(self, attempt: int) -> None:
        delay = self._retry_backoff_seconds * (2**attempt)
        if delay:
            await asyncio.sleep(delay)

    @staticmethod
    def _business_code(body: dict[str, Any]) -> int | None:
        value = body.get("code")
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _failed(message: str, *, provider_task_id: str | None = None) -> JianyingDraftResult:
        return JianyingDraftResult(
            status=JianyingDraftStatus.FAILED,
            provider_task_id=provider_task_id,
            message=message,
        )
