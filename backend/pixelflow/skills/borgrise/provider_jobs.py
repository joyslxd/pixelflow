"""content-app异步任务到M06 ExistingJobService的安全Client。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import JsonValue

from pixelflow.agent_runtime.jobs import ExistingJobService

logger = logging.getLogger(__name__)

ProviderRequestBuilder = Callable[
    [Mapping[str, JsonValue]],
    Mapping[str, JsonValue],
]
ProviderResultProjector = Callable[
    [Mapping[str, object]],
    Mapping[str, JsonValue],
]
ProviderStartHeadersBuilder = Callable[
    [Mapping[str, JsonValue]],
    Mapping[str, str],
]
ProviderStatusHeadersProvider = Callable[[], Mapping[str, str]]
ProviderStatusAuthMode = Literal["internal_headers", "service_authorization"]
ProviderStartEndpoint = str | Callable[[Mapping[str, JsonValue]], str]

_POLLING_STATUSES = frozenset(
    {"pending", "processing", "running", "queued", "polling", "in_progress"}
)
_SUCCEEDED_STATUSES = frozenset({"completed", "succeeded", "success", "done"})
_FAILED_STATUSES = frozenset({"failed", "error", "cancelled"})
_SAFE_SHOT_FIELDS = frozenset(
    {
        "description",
        "visual_description",
        "duration",
        "duration_sec",
        "shot_type",
        "camera_movement",
        "narration",
        "narration_text",
        "onscreen_text",
        "scene_type",
        "start_time",
        "end_time",
    }
)


class ContentAppTaskContractError(RuntimeError):
    """content-app响应不满足可恢复任务合同。"""


class ContentAppTaskNotFoundError(ContentAppTaskContractError):
    """content-app以业务失败表示provider job不存在或已过期。"""

    status_code = 404


class ContentAppTaskJobService(ExistingJobService):
    """显式分离一次性start鉴权和可重启status鉴权的任务Client。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        start_endpoint: ProviderStartEndpoint,
        request_builder: ProviderRequestBuilder,
        result_projector: ProviderResultProjector,
        status_headers_provider: ProviderStatusHeadersProvider,
        status_auth_mode: ProviderStatusAuthMode = "internal_headers",
        start_headers_builder: ProviderStartHeadersBuilder | None = None,
    ) -> None:
        if not isinstance(client, httpx.AsyncClient):
            raise TypeError("client必须是httpx.AsyncClient")
        self._base_url = _normalized_base_url(base_url)
        if not isinstance(start_endpoint, str) and not callable(start_endpoint):
            raise TypeError("start_endpoint必须是路径或可调用解析器")
        self._start_endpoint = start_endpoint
        self._status_url_template = _endpoint_url(
            self._base_url,
            "/task/{provider_job_id}/status",
        )
        if not callable(request_builder) or not callable(result_projector):
            raise TypeError("request_builder和result_projector必须可调用")
        if not callable(status_headers_provider):
            raise TypeError("status_headers_provider必须可调用")
        if start_headers_builder is not None and not callable(start_headers_builder):
            raise TypeError("start_headers_builder必须可调用")
        if status_auth_mode not in {"internal_headers", "service_authorization"}:
            raise ValueError("status_auth_mode不受支持")
        self._client = client
        self._request_builder = request_builder
        self._result_projector = result_projector
        self._status_headers_provider = status_headers_provider
        self._status_auth_mode = status_auth_mode
        self._start_headers_builder = start_headers_builder or (lambda request: {})

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        """一次性透传用户Authorization，响应只保留provider job ID和安全结果。"""

        normalized_authorization = _required_text("authorization", authorization)
        normalized_idempotency_key = _required_text(
            "idempotency_key",
            idempotency_key,
        )
        provider_request = _json_mapping(
            self._request_builder(dict(request)),
            field="provider_request",
        )
        start_endpoint = (
            self._start_endpoint(dict(request))
            if callable(self._start_endpoint)
            else self._start_endpoint
        )
        start_url = _endpoint_url(self._base_url, start_endpoint)
        custom_headers = _safe_custom_headers(
            self._start_headers_builder(dict(request)),
            allow_authorization=False,
        )
        response = await self._client.post(
            start_url,
            json=provider_request,
            headers={
                **custom_headers,
                "Authorization": normalized_authorization,
                "Idempotency-Key": normalized_idempotency_key,
            },
        )
        response.raise_for_status()
        payload = _response_mapping(response)
        return self._normalize_response(payload, expected_job_id=None)

    async def status(self, provider_job_id: str) -> object:
        """只凭provider job ID和独立服务凭据查询，禁止复用用户请求token。"""

        normalized_job_id = _provider_job_id(provider_job_id)
        status_headers = _safe_custom_headers(
            self._status_headers_provider(),
            allow_authorization=self._status_auth_mode == "service_authorization",
        )
        if not status_headers:
            raise ContentAppTaskContractError("content-app状态查询通道未配置")
        if (
            self._status_auth_mode == "service_authorization"
            and not any(key.lower() == "authorization" for key in status_headers)
        ):
            raise ContentAppTaskContractError("服务Authorization状态通道缺少凭据")
        response = await self._client.get(
            self._status_url_template.format(provider_job_id=normalized_job_id),
            headers=status_headers,
        )
        response.raise_for_status()
        payload = _response_mapping(response)
        if payload.get("success") is False and payload.get("data") is None:
            raise ContentAppTaskNotFoundError("content-app任务不存在或已过期")
        return self._normalize_response(
            payload,
            expected_job_id=normalized_job_id,
        )

    def _normalize_response(
        self,
        payload: Mapping[str, object],
        *,
        expected_job_id: str | None,
    ) -> dict[str, JsonValue]:
        job_id = _extract_job_id(payload) or expected_job_id
        if job_id is None:
            raise ContentAppTaskContractError("content-app响应缺少任务标识")
        normalized_job_id = _provider_job_id(job_id)
        if expected_job_id is not None and normalized_job_id != expected_job_id:
            raise ContentAppTaskContractError("content-app任务标识不一致")
        status = _extract_status(payload)
        result: Mapping[str, JsonValue] | None = None
        if status in _SUCCEEDED_STATUSES:
            result = _json_mapping(
                self._result_projector(payload),
                field="provider_result",
            )
        return {
            "ok": status not in _FAILED_STATUSES,
            "job_id": normalized_job_id,
            "status": status,
            "result": None if result is None else dict(result),
        }

    def __repr__(self) -> str:
        return (
            "ContentAppTaskJobService("
            f"base_url={self._base_url!r}, status_channel='configured')"
        )


class ContentAppMergeJobService(ExistingJobService):
    """把content-app同步合并接口适配为M06的start直接终态。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        request_timeout_seconds: float = 3600.0,
    ) -> None:
        if not isinstance(client, httpx.AsyncClient):
            raise TypeError("client必须是httpx.AsyncClient")
        if (
            isinstance(request_timeout_seconds, bool)
            or not isinstance(request_timeout_seconds, (int, float))
            or request_timeout_seconds <= 0
        ):
            raise ValueError("视频合并超时必须是正数")
        self._client = client
        self._merge_url = _endpoint_url(
            _normalized_base_url(base_url),
            "/video/merge",
        )
        self._request_timeout_seconds = float(request_timeout_seconds)

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        """同步合并并返回带稳定内部任务标识的安全终态。"""

        normalized_authorization = _required_text("authorization", authorization)
        normalized_key = _required_text("idempotency_key", idempotency_key)
        video_urls = _https_urls(request.get("video_urls"))
        if not video_urls:
            raise ContentAppTaskContractError("视频合并至少需要一个视频URL")
        provider_job_id = _synchronous_provider_job_id("merge", normalized_key)
        if len(video_urls) == 1:
            return {
                "ok": True,
                "job_id": provider_job_id,
                "status": "succeeded",
                "result": {"video_url": video_urls[0], "raw": {}},
            }
        model = _required_text("model", request.get("model"))
        size = _required_text("size", request.get("size"))
        duration = request.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ContentAppTaskContractError("视频合并时长无效")
        try:
            response = await self._client.post(
                self._merge_url,
                json={"videoUrls": video_urls},
                headers={
                    "Authorization": normalized_authorization,
                    "Idempotency-Key": normalized_key,
                    "modelType": model,
                    "billType": "3",
                    "duration": str(duration),
                    "apiModelParamObj": '{"size":"' + size + '"}',
                },
                timeout=self._request_timeout_seconds,
            )
        except httpx.TimeoutException:
            # 读超时：本进程等待上限到点。成片可能已在 content-app 侧生成。
            logger.warning(
                "content-app video merge read timeout seconds=%s",
                self._request_timeout_seconds,
            )
            return {
                "ok": False,
                "job_id": provider_job_id,
                "status": "timeout",
                "result": None,
            }
        except httpx.TransportError as exc:
            # 网关 proxy_read_timeout（常见 300s）会在长合并中途掐断连接，
            # 而 content-app 异步任务仍可能继续 ffmpeg 并上传 TOS。
            logger.warning(
                "content-app video merge transport failed error_type=%s",
                type(exc).__name__,
            )
            return {
                "ok": False,
                "job_id": provider_job_id,
                "status": "timeout",
                "result": None,
            }
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "content-app video merge http failed status_code=%s",
                exc.response.status_code if exc.response is not None else None,
            )
            return {
                "ok": False,
                "job_id": provider_job_id,
                "status": "failed",
                "result": None,
            }
        payload = _response_mapping(response)
        if payload.get("success") is False:
            logger.warning(
                "content-app video merge business failed status_code=%s",
                response.status_code,
            )
            return {
                "ok": False,
                "job_id": provider_job_id,
                "status": "failed",
                "result": None,
            }
        video_url = _find_video_url(payload)
        if video_url is None:
            raise ContentAppTaskContractError("视频合并完成但缺少安全成片URL")
        return {
            "ok": True,
            "job_id": provider_job_id,
            "status": "succeeded",
            "result": {"video_url": video_url, "raw": {}},
        }

    async def status(self, provider_job_id: str) -> object:
        """同步合并已在start事务终结，不存在可重复调用的status副作用。"""

        _provider_job_id(provider_job_id)
        raise ContentAppTaskContractError("同步视频合并任务不得进入轮询")

    def __repr__(self) -> str:
        return "ContentAppMergeJobService(mode='synchronous_terminal')"


def make_reference_analysis_job_service(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    status_headers_provider: ProviderStatusHeadersProvider,
    status_auth_mode: ProviderStatusAuthMode = "internal_headers",
) -> ContentAppTaskJobService:
    """构造参考视频拆解的可恢复start/status Client。"""

    return ContentAppTaskJobService(
        client=client,
        base_url=base_url,
        start_endpoint="/creative/decompose_video_to_storyboard",
        request_builder=lambda request: {
            "video_url": _required_text("video_url", request.get("video_url")),
        },
        start_headers_builder=lambda request: {
            "ModelType": "gemini-3-flash-preview",
            "billType": "1",
            "duration": "1",
            "apiModelParamObj": '{"size":"all"}',
        },
        result_projector=_project_reference_storyboard,
        status_headers_provider=status_headers_provider,
        status_auth_mode=status_auth_mode,
    )


def make_scene_video_job_service(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    status_headers_provider: ProviderStatusHeadersProvider,
    status_auth_mode: ProviderStatusAuthMode = "internal_headers",
) -> ContentAppTaskJobService:
    """构造单镜头视频生成的可恢复start/status Client。"""

    return ContentAppTaskJobService(
        client=client,
        base_url=base_url,
        start_endpoint=_scene_video_endpoint,
        request_builder=_scene_video_request,
        start_headers_builder=_scene_video_headers,
        result_projector=_project_scene_video_result,
        status_headers_provider=status_headers_provider,
        status_auth_mode=status_auth_mode,
    )


def make_quality_review_job_service(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    status_headers_provider: ProviderStatusHeadersProvider,
    status_auth_mode: ProviderStatusAuthMode = "internal_headers",
) -> ContentAppTaskJobService:
    """构造QAAgent QC质检的可恢复start/status Client。"""

    return ContentAppTaskJobService(
        client=client,
        base_url=base_url,
        start_endpoint="/creative/video_quality_review",
        request_builder=_quality_review_request,
        start_headers_builder=lambda request: {
            "ModelType": "gemini-3-flash-preview",
            "billType": "1",
            "duration": "1",
            "apiModelParamObj": '{"size":"all"}',
        },
        result_projector=_project_quality_review_result,
        status_headers_provider=status_headers_provider,
        status_auth_mode=status_auth_mode,
    )


def make_merge_video_job_service(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    request_timeout_seconds: float = 3600.0,
) -> ContentAppMergeJobService:
    """构造同步视频合并的M06直接终态Client。"""

    return ContentAppMergeJobService(
        client=client,
        base_url=base_url,
        request_timeout_seconds=request_timeout_seconds,
    )


def _scene_video_endpoint(request: Mapping[str, JsonValue]) -> str:
    mode = str(request.get("generation_mode") or "text_to_video").strip()
    endpoints = {
        "text_to_video": "/video/text-to-video",
        "image_to_video": "/video/image-to-video",
        "two_image_to_video": "/video/two-image-to-video",
        "reference_mode_video": "/video/reference-mode-video",
        "edit_video": "/video/edit-video",
        "extend_video": "/video/extend-video",
    }
    try:
        return endpoints[mode]
    except KeyError:
        raise ContentAppTaskContractError("镜头生成模式不受支持") from None


def _scene_video_request(
    request: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    mode = str(request.get("generation_mode") or "text_to_video").strip()
    prompt = _required_text("prompt", request.get("prompt"))
    model = _required_text("model", request.get("model"))
    ratio = _required_text("ratio", request.get("ratio"))
    size = _required_text("size", request.get("size"))
    sound = _required_text("sound", request.get("sound", "on"))
    duration = request.get("duration") or request.get("duration_sec")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ContentAppTaskContractError("镜头生成时长无效")
    common: dict[str, JsonValue] = {
        "prompt": prompt,
        "model": model,
        "ratio": ratio,
        "size": size,
        "duration": duration,
        "videoCount": 1,
        "sound": sound,
    }
    image_urls = _https_urls(request.get("image_urls"))
    video_urls = _https_urls(request.get("video_urls"))
    audio_urls = _https_urls(request.get("audio_urls"))
    if mode == "text_to_video":
        return common
    if mode == "image_to_video":
        if len(image_urls) != 1:
            raise ContentAppTaskContractError("首帧图生视频必须包含一张图片")
        return {**common, "image_url": image_urls[0]}
    if mode == "two_image_to_video":
        if len(image_urls) != 2:
            raise ContentAppTaskContractError("首尾帧视频必须包含两张图片")
        return {
            **common,
            "first_frame_image_url": image_urls[0],
            "last_frame_image_url": image_urls[1],
        }
    if mode == "reference_mode_video":
        if not image_urls and not video_urls and not audio_urls:
            raise ContentAppTaskContractError("全能参考视频缺少参考素材")
        return {
            **common,
            "imageUrls": image_urls,
            "videoUrls": video_urls,
            "audioUrls": audio_urls,
        }
    if mode in {"edit_video", "extend_video"}:
        if len(video_urls) != 1:
            raise ContentAppTaskContractError("编辑或延伸视频必须包含一个参考视频")
        return {
            **common,
            "refVideo": video_urls[0],
            "refImage": image_urls[0] if image_urls else None,
        }
    raise ContentAppTaskContractError("镜头生成模式不受支持")


def _scene_video_headers(
    request: Mapping[str, JsonValue],
) -> Mapping[str, str]:
    duration = request.get("duration") or request.get("duration_sec")
    return {
        "modelType": _required_text("model", request.get("model")),
        "billType": "3",
        "duration": str(duration),
        "apiModelParamObj": (
            '{"size":"'
            + _required_text("size", request.get("size"))
            + '"}'
        ),
    }


def _project_scene_video_result(
    payload: Mapping[str, object],
) -> Mapping[str, JsonValue]:
    provider_job_id = _extract_job_id(payload)
    video_url = _find_video_url(payload)
    if provider_job_id is None or video_url is None:
        raise ContentAppTaskContractError("镜头任务完成但缺少视频产物")
    digest = hashlib.sha256(provider_job_id.encode()).hexdigest()[:24]
    return {
        "variant_id": f"provider_variant_{digest}",
        "artifact_ref": f"artifact:provider-video:{digest}",
        "video_url": video_url,
        "completed_at": datetime.now(UTC).isoformat(),
    }


def _quality_review_request(
    request: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    merged_video_url = _https_url(
        request.get("merged_video_url"),
        field="merged_video_url",
    )
    scene_videos = request.get("scene_videos")
    if not isinstance(scene_videos, list) or not scene_videos:
        raise ContentAppTaskContractError("视频质检必须包含分镜视频")
    normalized_scenes: list[dict[str, JsonValue]] = []
    for item in scene_videos:
        if not isinstance(item, Mapping):
            raise ContentAppTaskContractError("视频质检分镜必须是JSON对象")
        scene_id = _required_text("scene_id", item.get("scene_id"))
        scene_index = item.get("scene_index")
        if isinstance(scene_index, bool) or not isinstance(scene_index, int):
            raise ContentAppTaskContractError("视频质检分镜序号无效")
        normalized_scenes.append(
            {
                "scene_id": scene_id,
                "scene_index": scene_index,
                "video_url": _https_url(
                    item.get("video_url"),
                    field="scene_video_url",
                ),
            }
        )
    payload: dict[str, JsonValue] = {
        "merged_video_url": merged_video_url,
        "scene_videos": normalized_scenes,
        "scene_packages": _json_value_or_default(
            request.get("scene_packages"),
            default=[],
            field="scene_packages",
        ),
        "brief": _json_value_or_default(
            request.get("brief"),
            default={},
            field="brief",
        ),
        "materials": _json_value_or_default(
            request.get("materials"),
            default=[],
            field="materials",
        ),
        "user_feedback": str(request.get("user_feedback") or "").strip(),
    }
    for key in ("ratio", "size"):
        value = request.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()
    return payload


def _project_quality_review_result(
    payload: Mapping[str, object],
) -> Mapping[str, JsonValue]:
    result = _find_result_mapping(payload)
    issues = result.get("issues", [])
    affected = result.get("affected_scene_ids") or result.get("affectedSceneIds") or []
    if not isinstance(issues, list) or not all(isinstance(item, Mapping) for item in issues):
        raise ContentAppTaskContractError("视频质检结果issues格式无效")
    if not isinstance(affected, list) or not all(isinstance(item, str) for item in affected):
        raise ContentAppTaskContractError("视频质检结果affected_scene_ids格式无效")
    summary = str(
        result.get("summary_markdown")
        or result.get("summaryMarkdown")
        or result.get("quality_report_markdown")
        or result.get("qualityReportMarkdown")
        or ""
    ).strip()
    report = str(
        result.get("quality_report_markdown")
        or result.get("qualityReportMarkdown")
        or summary
    ).strip()
    revision_prompt = str(
        result.get("revision_prompt") or result.get("revisionPrompt") or ""
    ).strip()
    return {
        "passed": result.get("passed") is True,
        "summary_markdown": summary[:20_000],
        "quality_report_markdown": report[:20_000],
        "issues": [dict(item) for item in issues],
        "affected_scene_ids": [item.strip() for item in affected if item.strip()],
        "revision_prompt": revision_prompt[:20_000],
        "raw": {},
    }


def _find_result_mapping(
    value: Mapping[str, object],
    *,
    depth: int = 0,
) -> Mapping[str, object]:
    if depth > 6:
        return {}
    if any(
        key in value
        for key in (
            "passed",
            "issues",
            "summary_markdown",
            "summaryMarkdown",
            "quality_report_markdown",
            "qualityReportMarkdown",
        )
    ):
        return value
    for key in ("result", "data", "output"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            found = _find_result_mapping(nested, depth=depth + 1)
            if found:
                return found
    raise ContentAppTaskContractError("视频质检任务完成但缺少结构化结果")


def _find_video_url(value: object, *, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(value, str):
        parsed = urlparse(value.strip())
        if parsed.scheme == "https" and parsed.netloc and not parsed.query:
            return value.strip()
        return None
    if isinstance(value, Mapping):
        for key in (
            "video_url",
            "videoUrl",
            "url",
            "result",
            "output",
            "data",
            "videos",
        ):
            if key in value:
                found = _find_video_url(value[key], depth=depth + 1)
                if found:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _find_video_url(item, depth=depth + 1)
            if found:
                return found
    return None


def _https_urls(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    urls: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ContentAppTaskContractError("参考素材URL格式无效")
        parsed = urlparse(item.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ContentAppTaskContractError("参考素材必须使用HTTPS URL")
        urls.append(item.strip())
    return urls


def _https_url(value: object, *, field: str) -> str:
    normalized = _required_text(field, value)
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ContentAppTaskContractError(f"{field}必须使用HTTPS URL")
    return normalized


def _json_value_or_default(
    value: object,
    *,
    default: JsonValue,
    field: str,
) -> JsonValue:
    normalized = default if value is None else value
    _validate_json(normalized, field=field)
    return normalized  # type: ignore[return-value] - 校验后仅包含标准JSON值


def _project_reference_storyboard(
    payload: Mapping[str, object],
) -> Mapping[str, JsonValue]:
    shots = _find_shots(payload)
    if not shots:
        raise ContentAppTaskContractError("参考视频任务完成但缺少分镜")
    storyboard: list[dict[str, JsonValue]] = []
    for shot in shots:
        safe: dict[str, JsonValue] = {}
        for key in _SAFE_SHOT_FIELDS:
            value = shot.get(key)
            if value is None or isinstance(value, (str, int, float, bool)):
                safe[key] = value
        description = str(
            safe.get("description")
            or safe.get("visual_description")
            or "参考镜头"
        ).strip()
        safe["description"] = description[:2_000]
        storyboard.append(safe)
    return {"storyboard": storyboard}


def _find_shots(value: object, *, depth: int = 0) -> list[Mapping[str, object]]:
    if depth > 6 or not isinstance(value, Mapping):
        return []
    for key in ("shots", "storyboard", "segments"):
        candidate = value.get(key)
        if isinstance(candidate, list) and candidate and all(
            isinstance(item, Mapping) for item in candidate
        ):
            return list(candidate)
    for key in ("data", "result", "output"):
        nested = _find_shots(value.get(key), depth=depth + 1)
        if nested:
            return nested
    return []


def _extract_job_id(payload: Mapping[str, object]) -> str | None:
    for container in _response_containers(payload):
        for key in ("taskId", "task_id", "job_id", "provider_job_id"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_status(payload: Mapping[str, object]) -> str:
    for container in _response_containers(payload):
        value = container.get("status")
        if isinstance(value, str) and value.strip():
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in _POLLING_STATUSES | _SUCCEEDED_STATUSES | _FAILED_STATUSES:
                return normalized
            raise ContentAppTaskContractError("content-app返回未知任务状态")
    if _find_shots(payload):
        return "completed"
    raise ContentAppTaskContractError("content-app响应缺少任务状态")


def _response_containers(
    payload: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    containers: list[Mapping[str, object]] = [payload]
    current: object = payload
    for _ in range(4):
        if not isinstance(current, Mapping):
            break
        child = current.get("data")
        if not isinstance(child, Mapping):
            break
        containers.append(child)
        current = child
    return tuple(containers)


def _response_mapping(response: httpx.Response) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ContentAppTaskContractError("content-app响应不是合法JSON") from exc
    if not isinstance(payload, Mapping):
        raise ContentAppTaskContractError("content-app响应必须是JSON对象")
    return payload


def _normalized_base_url(value: str) -> str:
    normalized = _required_text("base_url", value).rstrip("/") + "/"
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url必须是HTTP(S)地址")
    return normalized


def _endpoint_url(base_url: str, endpoint: str) -> str:
    normalized_endpoint = _required_text("endpoint", endpoint)
    if not normalized_endpoint.startswith("/"):
        raise ValueError("endpoint必须以/开头")
    return urljoin(base_url, normalized_endpoint.lstrip("/"))


def _provider_job_id(value: object) -> str:
    normalized = _required_text("provider_job_id", value)
    if len(normalized) > 128 or not all(
        character.isalnum() or character in "._:-" for character in normalized
    ):
        raise ContentAppTaskContractError("provider job ID格式无效")
    return normalized


def _synchronous_provider_job_id(stage: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"pixelflow:sync-provider:v1:{stage}:{idempotency_key}".encode()
    ).hexdigest()[:32]
    return f"sync-{stage}-{digest}"


def _required_text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}不能为空")
    return value.strip()


def _json_mapping(
    value: Mapping[str, Any],
    *,
    field: str,
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ContentAppTaskContractError(f"{field}必须是JSON对象")
    normalized = dict(value)
    _validate_json(normalized, field=field)
    return normalized


def _validate_json(value: object, *, field: str) -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, field=field)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContentAppTaskContractError(f"{field}包含非字符串键")
            _validate_json(item, field=field)
        return
    raise ContentAppTaskContractError(f"{field}包含非JSON值")


def _safe_custom_headers(
    headers: Mapping[str, str],
    *,
    allow_authorization: bool,
) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        raise ContentAppTaskContractError("请求头必须是映射")
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        name = _required_text("header_name", key)
        content = _required_text("header_value", value)
        if name.lower() == "authorization" and not allow_authorization:
            raise ContentAppTaskContractError("状态通道禁止使用持久化用户Authorization")
        if "\n" in name or "\r" in name or "\n" in content or "\r" in content:
            raise ContentAppTaskContractError("请求头包含非法换行")
        normalized[name] = content
    return normalized


__all__ = [
    "ContentAppTaskContractError",
    "ContentAppMergeJobService",
    "ContentAppTaskJobService",
    "ContentAppTaskNotFoundError",
    "ProviderRequestBuilder",
    "ProviderResultProjector",
    "ProviderStartHeadersBuilder",
    "ProviderStartEndpoint",
    "ProviderStatusHeadersProvider",
    "ProviderStatusAuthMode",
    "make_quality_review_job_service",
    "make_merge_video_job_service",
    "make_reference_analysis_job_service",
    "make_scene_video_job_service",
]
