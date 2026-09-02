"""content-app 同步视频拼接的 HTTP 防腐 Adapter。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

import httpx
from pydantic import JsonValue

from pixelflow.agent_tools.video.contracts import (
    VideoToolContext,
    VideoToolExecutionError,
)
from pixelflow.agent_tools.video.credentials import VideoAgentCredentialUnavailableError
from pixelflow.agent_tools.video.delivery import DeliveryOperationJob, DeliveryOutputType
from pixelflow.video.workspace.payload import canonicalize_video_model

_MERGE_ENDPOINT = "/video/merge"
_DEFAULT_SCENE_DURATION_SEC = 8
logger = logging.getLogger(__name__)


class ContentAppVideoMergeAdapter:
    """将已选分镜成片同步提交给 content-app `/video/merge`；单镜不发 HTTP。"""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        connect_timeout_seconds: float = 10,
        read_timeout_seconds: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds if read_timeout_seconds is not None else _merge_read_timeout_seconds(),
            write=30,
            pool=connect_timeout_seconds,
        )
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def start_delivery(
        self,
        context: VideoToolContext,
        *,
        output_type: DeliveryOutputType,
        scenes: Sequence[Mapping[str, JsonValue]],
        attempt: int,
    ) -> DeliveryOperationJob:
        if output_type != "mp4":
            raise VideoToolExecutionError("剪映工程包交付尚未装配")
        if not scenes:
            raise VideoToolExecutionError("视频交付缺少可拼接镜头")
        artifact_refs = [str(item.get("artifact_ref") or "") for item in scenes]
        job_id = _delivery_job_id(
            workspace_id=context.workspace.workspace_id,
            output_type=output_type,
            attempt=attempt,
            artifact_refs=artifact_refs,
        )
        urls = _resolve_scene_urls(context.workspace.payload, scenes)
        if len(urls) == 1:
            logger.info("content-app video merge skipped single scene_count=1")
            return _succeeded_job(job_id, output_type, urls[0])
        return await self._merge_many(
            context,
            job_id=job_id,
            output_type=output_type,
            urls=urls,
            scenes=scenes,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _merge_many(
        self,
        context: VideoToolContext,
        *,
        job_id: str,
        output_type: DeliveryOutputType,
        urls: Sequence[str],
        scenes: Sequence[Mapping[str, JsonValue]],
    ) -> DeliveryOperationJob:
        authorization = _borrow_authorization(context)
        model, size, duration = _merge_billing_headers(context.workspace.payload, scenes)
        logger.info("content-app video merge started scene_count=%s", len(urls))
        try:
            response = await self._client.post(
                f"{self._base_url}{_MERGE_ENDPOINT}",
                headers={
                    "Authorization": authorization,
                    "Idempotency-Key": job_id,
                    "modelType": model,
                    "billType": "3",
                    "duration": str(duration),
                    "apiModelParamObj": json.dumps(
                        {"size": size},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
                json={"videoUrls": list(urls)},
            )
        except httpx.TimeoutException as exc:
            logger.warning("content-app video merge timed out scene_count=%s", len(urls))
            raise VideoToolExecutionError("视频交付合并超时") from exc
        finally:
            authorization = ""
        return _job_from_response(response, job_id=job_id, output_type=output_type, scene_count=len(urls))


def _borrow_authorization(context: VideoToolContext) -> str:
    if context.credential is None:
        raise VideoToolExecutionError("视频交付缺少临时授权")
    try:
        value = context.credential.borrow_authorization().strip()
    except VideoAgentCredentialUnavailableError as exc:
        raise VideoToolExecutionError("视频交付缺少临时授权") from exc
    if not value.startswith("Bearer ") or not value.removeprefix("Bearer ").strip():
        raise VideoToolExecutionError("视频交付缺少临时授权")
    return value


def _job_from_response(
    response: httpx.Response,
    *,
    job_id: str,
    output_type: DeliveryOutputType,
    scene_count: int,
) -> DeliveryOperationJob:
    if response.status_code == 402:
        if _billing_profile_missing(response):
            logger.warning("content-app video merge billing profile missing")
            raise VideoToolExecutionError("视频交付计费档缺失")
        logger.warning("content-app video merge paused quota")
        return DeliveryOperationJob(
            job_id=job_id,
            output_type=output_type,
            status="start_paused_quota",
        )
    if response.is_error:
        logger.warning(
            "content-app video merge http failed status_code=%s scene_count=%s",
            response.status_code,
            scene_count,
        )
        raise VideoToolExecutionError("视频交付合并失败")
    payload = _json_object(response)
    if payload.get("success") is False:
        logger.warning("content-app video merge business failed scene_count=%s", scene_count)
        raise VideoToolExecutionError("视频交付合并失败")
    url = _merge_result_url(payload)
    if url is None:
        logger.warning("content-app video merge result url missing")
        raise VideoToolExecutionError("视频交付合并失败")
    logger.info("content-app video merge succeeded scene_count=%s", scene_count)
    return _succeeded_job(job_id, output_type, url)


def _succeeded_job(job_id: str, output_type: DeliveryOutputType, url: str) -> DeliveryOperationJob:
    digest = hashlib.sha256(job_id.encode()).hexdigest()[:24]
    return DeliveryOperationJob(
        job_id=job_id,
        output_type=output_type,
        status="succeeded",
        artifact_ref=f"artifact:merge:{digest}",
        delivery_url=url,
    )


def _delivery_job_id(
    *,
    workspace_id: str,
    output_type: str,
    attempt: int,
    artifact_refs: Sequence[str],
) -> str:
    digest = hashlib.sha256(
        f"v1:{workspace_id}:{output_type}:{attempt}:{','.join(artifact_refs)}".encode()
    ).hexdigest()[:40]
    return f"delivery-{digest}"


def _resolve_scene_urls(
    payload: Mapping[str, object],
    scenes: Sequence[Mapping[str, JsonValue]],
) -> list[str]:
    by_ref = _variant_urls_by_artifact(payload)
    urls: list[str] = []
    for item in scenes:
        ref = str(item.get("artifact_ref") or "")
        url = by_ref.get(ref)
        if not url:
            raise VideoToolExecutionError("视频交付无法解析分镜成片")
        urls.append(url)
    return urls


def _variant_urls_by_artifact(payload: Mapping[str, object]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for scene in _records(payload.get("scenes")):
        for variant in _records(scene.get("variants")):
            ref = str(variant.get("artifact_ref") or "")
            url = variant.get("video_url")
            if ref.startswith("artifact:") and isinstance(url, str) and url.startswith("https://"):
                mapping[ref] = url.strip()
        scene_ref = str(scene.get("artifact_ref") or "")
        scene_url = scene.get("video_url")
        if (
            scene_ref.startswith("artifact:")
            and scene_ref not in mapping
            and isinstance(scene_url, str)
            and scene_url.startswith("https://")
        ):
            mapping[scene_ref] = scene_url.strip()
    return mapping


def _merge_billing_headers(
    payload: Mapping[str, object],
    scenes: Sequence[Mapping[str, JsonValue]],
) -> tuple[str, str, int]:
    contract = payload.get("creation_contract")
    contract_map = contract if isinstance(contract, Mapping) else {}
    model = canonicalize_video_model(str(contract_map.get("video_model") or "").strip())
    size = _video_size(str(contract_map.get("video_size") or "").strip() or "1080p")
    if not model:
        raise VideoToolExecutionError("视频交付缺少已冻结的生产合同")
    duration = sum(_scene_duration(payload, str(item.get("scene_id") or "")) for item in scenes)
    return model, size, max(duration, 4)


def _scene_duration(payload: Mapping[str, object], scene_id: str) -> int:
    for scene in _records(payload.get("scenes")):
        if str(scene.get("scene_id") or "") != scene_id:
            continue
        raw = scene.get("duration_sec") or scene.get("duration")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = int(raw)
            if 4 <= value <= 30:
                return value
        return _DEFAULT_SCENE_DURATION_SEC
    return _DEFAULT_SCENE_DURATION_SEC


def _video_size(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"480p", "720p", "1080p", "2k", "4k"}:
        return "2K" if lowered == "2k" else "4K" if lowered == "4k" else lowered
    return value.strip() or "1080p"


def _merge_result_url(payload: Mapping[str, object]) -> str | None:
    data = payload.get("data")
    if isinstance(data, str):
        return _https_url(data)
    return _first_https_url(data) or _first_https_url(payload)


def _first_https_url(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key in ("video_url", "videoUrl", "url", "video", "merged_video_url"):
            found = _https_url(value.get(key))
            if found:
                return found
        for key in ("videos", "video_urls", "videoUrls"):
            items = value.get(key)
            if isinstance(items, list):
                for item in items:
                    found = _https_url(item)
                    if found:
                        return found
    return _https_url(value)


def _https_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme == "https" and parsed.netloc:
        return value.strip()
    return None


def _json_object(response: httpx.Response) -> Mapping[str, object]:
    try:
        value = response.json()
    except ValueError as exc:
        raise VideoToolExecutionError("视频交付合并失败") from exc
    if not isinstance(value, Mapping):
        raise VideoToolExecutionError("视频交付合并失败")
    return value


def _billing_profile_missing(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    text = response.text if isinstance(response.text, str) else ""
    return _contains_billing_marker(payload) or "价格配置不存在" in text or "price config" in text.casefold()


def _contains_billing_marker(value: object, *, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, str):
        return "价格配置不存在" in value or "price config" in value.casefold()
    if isinstance(value, Mapping):
        return any(_contains_billing_marker(item, depth=depth + 1) for item in value.values())
    if isinstance(value, list):
        return any(_contains_billing_marker(item, depth=depth + 1) for item in value[:20])
    return False


def _merge_read_timeout_seconds() -> float:
    """同步 ffmpeg 合并可能长达数分钟；默认 1 小时，避免 30 秒读超时截断。"""

    raw = os.environ.get("BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT", "3600").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT 配置无效") from exc
    if value <= 0 or value > 7200:
        raise ValueError("BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT 配置无效")
    return value


def _records(value: object) -> list[dict[str, JsonValue]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


__all__ = ["ContentAppVideoMergeAdapter"]
