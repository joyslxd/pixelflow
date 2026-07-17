"""第三方剪映草稿 HTTP Provider 实现。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

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
_DEFAULT_MAX_SOURCE_FILES = 500
_DEFAULT_MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024
_DEFAULT_MAX_TOTAL_SOURCE_BYTES = 200 * 1024 * 1024


class _ProviderRequestError(RuntimeError):
    """第三方网络请求耗尽重试后的内部异常。"""


class _SourceFileError(RuntimeError):
    """第三方返回的草稿源文件不满足下载或 JSON 合同。"""


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


def _source_archive_name(source_url: object, *, index: int, used_names: set[str]) -> str:
    """保留第三方草稿文件名；非法或重名时生成稳定、安全的 JSON 名称。"""

    url = _https_url(source_url)
    if url is None:
        raise _SourceFileError("source URL must use public HTTPS")
    original_name = unquote(Path(urlsplit(url).path).name)
    original_stem = Path(original_name).stem if original_name.lower().endswith(".json") else original_name
    safe_stem = _safe_file_stem(original_stem) if original_stem else f"draft-{index:03d}"
    candidate = f"{safe_stem}.json"
    if candidate in used_names:
        candidate = f"{safe_stem}-{index:03d}.json"
    collision = 1
    while candidate in used_names:
        candidate = f"{safe_stem}-{index:03d}-{collision}.json"
        collision += 1
    used_names.add(candidate)
    return candidate


def _default_uploader(path: str) -> dict[str, Any]:
    """复用 content-app `/api/upload`，由当前用户 Authorization 上传到 TOS。"""

    from pixelflow.skills.borgrise.run_generation import upload_file

    return upload_file(path)


class HttpJianyingDraftSkill:
    """创建第三方任务、轮询 JSON 结果并打包上传 TOS。"""

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
        max_source_files: int = _DEFAULT_MAX_SOURCE_FILES,
        max_source_file_bytes: int = _DEFAULT_MAX_SOURCE_FILE_BYTES,
        max_total_source_bytes: int = _DEFAULT_MAX_TOTAL_SOURCE_BYTES,
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
        self._max_source_files = max_source_files
        self._max_source_file_bytes = max_source_file_bytes
        self._max_total_source_bytes = max_total_source_bytes
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
        """完成创建、轮询、源文件归档和 TOS 上传的完整 Provider 流程。"""

        try:
            created = await self._post_provider(
                _CREATE_PATH,
                [{"videoUrl": str(scene.video_url), "videoOrder": order} for order, scene in enumerate(request.scenes, start=1)],
                read_timeout_seconds=self._create_read_timeout_seconds,
            )
        except _ProviderRequestError:
            return self._failed("第三方剪映草稿任务创建失败")

        if self._business_code(created) != 200:
            return self._failed("第三方剪映草稿任务创建失败")
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
                return self._failed("第三方剪映草稿任务处理失败", provider_task_id=provider_task_id)

            source_urls = queried.get("data")
            if not isinstance(source_urls, list) or not source_urls:
                return self._failed("第三方剪映草稿结果为空", provider_task_id=provider_task_id)
            if len(source_urls) > self._max_source_files:
                return self._failed("第三方剪映草稿结果文件过多", provider_task_id=provider_task_id)
            try:
                return await self._package_and_upload(
                    request=request,
                    provider_task_id=provider_task_id,
                    source_urls=source_urls,
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

    async def _package_and_upload(
        self,
        *,
        request: JianyingDraftRequest,
        provider_task_id: str,
        source_urls: list[object],
    ) -> JianyingDraftResult:
        total_bytes = 0
        source_files: list[tuple[str, bytes]] = []
        used_names: set[str] = set()
        file_name = f"{_safe_file_stem(request.project_name)}-剪映草稿.zip"
        for index, source_url in enumerate(source_urls, start=1):
            archive_name = _source_archive_name(source_url, index=index, used_names=used_names)
            content = await self._download_json(source_url)
            total_bytes += len(content)
            if total_bytes > self._max_total_source_bytes:
                raise _SourceFileError("source files exceed total size limit")
            source_files.append((archive_name, content))

        upload_result = await asyncio.to_thread(
            self._archive_and_upload,
            file_name,
            source_files,
        )
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

    def _archive_and_upload(
        self,
        file_name: str,
        source_files: list[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """在线程内同时管理 ZIP 和上传，协程取消后也由线程完成清理。"""

        with tempfile.TemporaryDirectory(prefix="pixelflow-jianying-") as temp_dir:
            archive_path = Path(temp_dir) / file_name
            with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for archive_name, content in source_files:
                    archive.writestr(archive_name, content)
            return self._uploader(str(archive_path))

    async def _download_json(self, source_url: object) -> bytes:
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
                    if content_length and int(content_length) > self._max_source_file_bytes:
                        raise _SourceFileError("source file exceeds size limit")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self._max_source_file_bytes:
                            raise _SourceFileError("source file exceeds size limit")
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt < self._max_retries:
                    await self._retry_sleep(attempt)
                    continue
                raise _ProviderRequestError("source download network failure") from None
            except ValueError as exc:
                raise _SourceFileError("invalid content-length") from exc
            try:
                json.loads(content.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _SourceFileError("source file is not valid UTF-8 JSON") from exc
            return bytes(content)
        raise _ProviderRequestError("source download retries exhausted")

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
