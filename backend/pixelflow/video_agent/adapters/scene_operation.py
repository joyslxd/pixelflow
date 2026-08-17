"""把VideoAgent定向镜头生成接入M06 External Job Operation。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import JsonValue, ValidationError

from pixelflow.agent_runtime.contracts import ExternalJobStatus
from pixelflow.agent_runtime.jobs import (
    OperationStartCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    build_operation_request,
)
from pixelflow.agent_runtime.jobs.providers import ProviderJobCallError
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolExecutionError,
)
from pixelflow.video_agent.tools.scene import SceneGenerationJob

# Workspace 镜头正文使用 @asset_id；发给视频模型时再换成 @正式名称。
# 只用 ASCII：\w 会吞掉紧随的中文（如 @character-2握住 → 整段匹配失败）。
_AT_ASSET_ID_TOKEN = re.compile(r"@([A-Za-z][A-Za-z0-9_-]*)")

SceneProviderRequestBuilder = Callable[
    [Mapping[str, JsonValue], int],
    Mapping[str, JsonValue],
]
_TERMINAL_FAILURES = frozenset(
    {
        ExternalJobStatus.FAILED,
        ExternalJobStatus.TIMEOUT,
        ExternalJobStatus.EXPIRED,
    }
)


class M06SceneGenerationOperationPort:
    """按镜头和版本幂等启动M06任务，并从完成事件恢复产物。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        adapter: ProviderJobAdapter,
        authorization_provider: Callable[[VideoToolContext], str] | None = None,
        lease_owner: str,
        provider_request_builder: SceneProviderRequestBuilder | None = None,
        clock: Callable[[], datetime] | None = None,
        job_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(adapter, ProviderJobAdapter):
            raise TypeError("adapter 必须是 ProviderJobAdapter")
        if authorization_provider is not None and not callable(
            authorization_provider
        ):
            raise TypeError("authorization_provider 必须可调用")
        normalized_owner = lease_owner.strip()
        if not normalized_owner or len(normalized_owner) > 128:
            raise ValueError("lease_owner 必须是1到128个字符")
        self._repository = repository
        self._adapter = adapter
        self._authorization_provider = (
            authorization_provider or _context_authorization
        )
        self._lease_owner = normalized_owner
        # 未注入自定义 builder 时，从 Workspace creation_contract + 分镜 mentions 组装。
        self._provider_request_builder = provider_request_builder
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory

    async def start_scene_variant(
        self,
        context: VideoToolContext,
        *,
        scene: Mapping[str, JsonValue],
        variant_index: int,
        attempt: int,
    ) -> SceneGenerationJob:
        """启动或回读同一镜头版本，Authorization不进入对象或持久层。"""

        if context.plan_id is None or context.step_id is None:
            raise VideoToolExecutionError("镜头生成Operation缺少计划身份")
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            raise VideoToolExecutionError("镜头生成Operation缺少镜头身份")
        if self._provider_request_builder is not None:
            provider_request = dict(
                self._provider_request_builder(scene, variant_index)
            )
        else:
            provider_request = dict(
                build_scene_provider_request(context, scene, variant_index)
            )
        scene_digest = hashlib.sha256(scene_id.encode()).hexdigest()[:12]
        request = build_operation_request(
            workflow_id=context.plan_id,
            stage=f"generate_scene:{scene_digest}:v{variant_index}",
            stage_version=1,
            attempt=attempt,
            provider_request=provider_request,
        )
        coordinator = OperationStartCoordinator(
            self._repository,
            adapter=self._adapter,
            user_id=context.user_id,
            conversation_id=context.workspace.conversation_id,
            clock=self._clock,
            job_id_factory=self._job_id_factory,
        )
        try:
            operation = await coordinator.start(
                request,
                provider_request=provider_request,
                authorization_provider=lambda: self._authorization_provider(
                    context
                ),
                lease_owner=self._lease_owner,
            )
        except OperationStartQuotaPausedError as exc:
            return SceneGenerationJob(
                job_id=exc.operation.job_id,
                scene_id=scene_id,
                variant_index=variant_index,
                status="start_paused_quota",
            )
        except OperationConflictError as exc:
            raise VideoToolExecutionError("镜头生成Operation启动失败") from exc
        except ProviderJobCallError as exc:
            raise VideoToolExecutionError(
                "镜头生成供应商调用失败，请检查创作合同、分镜提示词与参考图后重试"
            ) from exc

        if operation.status in {ExternalJobStatus.CREATED, ExternalJobStatus.POLLING}:
            return SceneGenerationJob(
                job_id=operation.job_id,
                scene_id=scene_id,
                variant_index=variant_index,
                status="polling",
            )
        if operation.status in _TERMINAL_FAILURES:
            raise VideoToolExecutionError("镜头生成Operation执行失败")
        if operation.status is not ExternalJobStatus.SUCCEEDED:
            raise VideoToolExecutionError("镜头生成Operation状态不受支持")
        return await self._completed_job(
            context,
            operation_job_id=operation.job_id,
            scene_id=scene_id,
            variant_index=variant_index,
        )

    async def _completed_job(
        self,
        context: VideoToolContext,
        *,
        operation_job_id: str,
        scene_id: str,
        variant_index: int,
    ) -> SceneGenerationJob:
        events = await self._repository.list_events(
            context.user_id,
            context.workspace.conversation_id,
        )
        matches = [
            event
            for event in events
            if event.payload.get("job_id") == operation_job_id
        ]
        if len(matches) != 1:
            raise VideoToolExecutionError("镜头生成Operation完成事件不唯一")
        result = matches[0].payload.get("result")
        if not isinstance(result, Mapping):
            raise VideoToolExecutionError("镜头生成Operation缺少安全结果")
        try:
            job = SceneGenerationJob.model_validate(
                {
                    "job_id": operation_job_id,
                    "scene_id": scene_id,
                    "variant_index": variant_index,
                    "status": "succeeded",
                    "variant_id": result.get("variant_id"),
                    "artifact_ref": result.get("artifact_ref"),
                    "video_url": result.get("video_url"),
                    "completed_at": (
                        result.get("completed_at") or matches[0].occurred_at
                    ),
                }
            )
        except ValidationError as exc:
            raise VideoToolExecutionError("镜头生成Operation结果无效") from exc
        return job


def build_scene_provider_request(
    context: VideoToolContext,
    scene: Mapping[str, JsonValue],
    variant_index: int,
) -> Mapping[str, JsonValue]:
    """从 Workspace creation_contract + 分镜组装 content-app 镜头生成请求。

    场景包镜头通常只有 prompt/duration_ms/image mentions，不含 model/ratio/size；
    这些必须从 creation_contract 补齐，否则 Provider 会因缺字段直接失败。
    """

    scene_id = str(scene.get("scene_id") or "").strip()
    prompt = _resolve_scene_prompt(scene)
    if not scene_id or not prompt:
        raise VideoToolExecutionError("镜头生成请求缺少镜头或提示词")

    payload = (
        context.workspace.payload
        if isinstance(context.workspace.payload, Mapping)
        else {}
    )
    contract = payload.get("creation_contract")
    contract_map = contract if isinstance(contract, Mapping) else {}
    global_assets = payload.get("global_assets")
    # 仅改 Provider 提示词：@character-1 → @安然；落库正文仍保留 @asset_id。
    prompt = _rewrite_prompt_asset_ids_to_names(
        prompt,
        global_assets=global_assets,
        scene=scene,
    )

    model = _first_text(scene.get("model"), contract_map.get("video_model"))
    ratio = _first_text(scene.get("ratio"), contract_map.get("video_ratio"))
    size = _first_text(scene.get("size"), contract_map.get("video_size"), "1080p")
    sound = _first_text(scene.get("sound"), contract_map.get("video_sound"), "on")
    if not model or not ratio or not size:
        raise VideoToolExecutionError(
            "镜头生成缺少视频模型参数：请确认创作合同中的 video_model / video_ratio / video_size"
        )

    duration_sec = _resolve_duration_sec(scene)
    if duration_sec is None:
        raise VideoToolExecutionError("镜头生成请求缺少有效时长（4-15 秒）")

    # mentions 常缺 image_url（资产图后未回填）；按 reference_asset_ids / mentions.asset_id
    # 从 global_assets 补齐，否则会误走 text_to_video。
    image_urls = _collect_https_urls(
        scene.get("image_urls"),
        _mention_image_urls(scene.get("shot_description")),
        _global_asset_image_urls_for_scene(
            scene, contract_map=None, global_assets=global_assets
        ),
    )
    video_urls = _collect_https_urls(scene.get("video_urls"))
    audio_urls = _collect_https_urls(scene.get("audio_urls"))
    if len(image_urls) > 9:
        raise VideoToolExecutionError(
            f"单分镜最多允许 9 张参考图，当前为 {len(image_urls)} 张"
        )

    generation_mode = _first_text(scene.get("generation_mode")) or _infer_generation_mode(
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        scene=scene,
    )

    request: dict[str, JsonValue] = {
        "scene_id": scene_id,
        "variant_index": variant_index,
        "prompt": prompt,
        "duration": duration_sec,
        "duration_sec": duration_sec,
        "model": model,
        "ratio": ratio,
        "size": size,
        "sound": sound if sound in {"on", "off"} else "on",
        "generation_mode": generation_mode,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "audio_urls": audio_urls,
    }
    for key in (
        "shot_type",
        "camera_movement",
        "narration",
        "narration_text",
        "onscreen_text",
        "asset_refs",
    ):
        value = scene.get(key)
        if value is not None:
            request[key] = value
    return request


def _default_provider_request(
    scene: Mapping[str, JsonValue],
    variant_index: int,
) -> Mapping[str, JsonValue]:
    """兼容旧注入签名：仅使用镜头自身字段，不含创作合同补齐。"""

    scene_id = str(scene.get("scene_id") or "").strip()
    prompt = _resolve_scene_prompt(scene)
    if not scene_id or not prompt:
        raise VideoToolExecutionError("镜头生成请求缺少镜头或提示词")
    request: dict[str, JsonValue] = {
        "scene_id": scene_id,
        "variant_index": variant_index,
        "prompt": prompt,
    }
    for key in (
        "duration_sec",
        "duration",
        "shot_type",
        "camera_movement",
        "narration",
        "narration_text",
        "onscreen_text",
        "asset_refs",
        "generation_mode",
        "model",
        "ratio",
        "size",
        "sound",
        "image_urls",
        "video_urls",
        "audio_urls",
    ):
        value = scene.get(key)
        if value is not None:
            request[key] = value
    return request


def _resolve_scene_prompt(scene: Mapping[str, Any]) -> str:
    """优先用分镜面板镜头描述正文；prompt 可能是「故事线+镜头描述」拼接脏字段。"""

    shot = scene.get("shot_description")
    if isinstance(shot, Mapping):
        text = str(shot.get("text") or "").strip()
        if text:
            return text
    prompt = str(scene.get("prompt") or "").strip()
    if prompt:
        return prompt
    return str(scene.get("storyline") or "").strip()


def _rewrite_prompt_asset_ids_to_names(
    prompt: str,
    *,
    global_assets: object,
    scene: Mapping[str, Any],
) -> str:
    """把发给视频模型的 @asset_id 换成 @正式名称，便于与参考图身份对齐。

    Workspace / mentions 仍以 asset_id 为稳定主键；参考图 URL 也按 asset_id 解析。
    仅在组装 content-app 请求时改写提示词，避免模型只看到 character-1 这类不透明 ID。
    """

    lookup = _global_asset_name_lookup(global_assets)
    shot = scene.get("shot_description")
    if isinstance(shot, Mapping):
        mentions = shot.get("mentions")
        if isinstance(mentions, list):
            for item in mentions:
                if not isinstance(item, Mapping):
                    continue
                asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
                name = str(item.get("name") or "").strip()
                if not asset_id or not name or "@" in name:
                    continue
                lookup.setdefault(asset_id, name)
    if not lookup:
        return prompt

    def _replace(match: re.Match[str]) -> str:
        asset_id = match.group(1)
        name = lookup.get(asset_id)
        if not name or name == asset_id:
            return match.group(0)
        return f"@{name}"

    return _AT_ASSET_ID_TOKEN.sub(_replace, prompt)


def _global_asset_name_lookup(value: object) -> dict[str, str]:
    """从 global_assets 建立 asset_id → 正式名称映射。"""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key in ("characters", "scenes", "props"):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not asset_id or not name or "@" in name or asset_id in result:
                continue
            result[asset_id] = name
    return result


def _resolve_duration_sec(scene: Mapping[str, Any]) -> int | None:
    raw = scene.get("duration_sec")
    if raw is None:
        raw = scene.get("duration")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
        return value if 4 <= value <= 15 else None
    duration_ms = scene.get("duration_ms")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return None
    if int(duration_ms) % 1000 != 0:
        return None
    value = int(duration_ms) // 1000
    return value if 4 <= value <= 15 else None


def _infer_generation_mode(
    *,
    image_urls: Sequence[str],
    video_urls: Sequence[str],
    audio_urls: Sequence[str],
    scene: Mapping[str, Any],
) -> str:
    text = "\n".join(
        str(item or "")
        for item in (
            scene.get("prompt"),
            scene.get("storyline"),
            scene.get("narration"),
            scene.get("shot_description"),
        )
    ).lower()
    if video_urls and any(token in text for token in ("延伸", "续写", "extend")):
        return "extend_video"
    if video_urls and any(token in text for token in ("编辑", "修改", "调整", "edit")):
        return "edit_video"
    if image_urls or video_urls or audio_urls:
        return "reference_mode_video"
    return "text_to_video"


def _mention_image_urls(shot_description: object) -> list[str]:
    if not isinstance(shot_description, Mapping):
        return []
    mentions = shot_description.get("mentions")
    if not isinstance(mentions, list):
        return []
    urls: list[str] = []
    for item in mentions:
        if isinstance(item, Mapping):
            url = item.get("image_url") or item.get("url")
            if isinstance(url, str):
                urls.append(url)
    return urls


def _global_asset_image_urls_for_scene(
    scene: Mapping[str, Any],
    *,
    contract_map: Mapping[str, Any] | None,
    global_assets: object,
) -> list[str]:
    del contract_map
    lookup = _global_asset_image_lookup(global_assets)
    if not lookup:
        return []
    asset_ids: list[str] = []
    raw_ids = scene.get("reference_asset_ids")
    if isinstance(raw_ids, (list, tuple)):
        for item in raw_ids:
            asset_id = str(item or "").strip()
            if asset_id:
                asset_ids.append(asset_id)
    shot = scene.get("shot_description")
    if isinstance(shot, Mapping):
        mentions = shot.get("mentions")
        if isinstance(mentions, list):
            for item in mentions:
                if not isinstance(item, Mapping):
                    continue
                asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
                if asset_id:
                    asset_ids.append(asset_id)
    urls: list[str] = []
    seen: set[str] = set()
    for asset_id in asset_ids:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        url = lookup.get(asset_id)
        if url:
            urls.append(url)
        if len(urls) >= 9:
            break
    return urls


def _global_asset_image_lookup(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key in ("characters", "scenes", "props"):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            if not asset_id or asset_id in result:
                continue
            url = _asset_record_image_url(item)
            if url:
                result[asset_id] = url
    return result


def _asset_record_image_url(item: Mapping[str, Any]) -> str | None:
    for key in ("image_url", "url", "generation_reference_url"):
        url = _safe_https_url(item.get(key))
        if url:
            return url
    for key in ("images", "three_view_images", "image_urls"):
        values = item.get(key)
        if isinstance(values, str):
            url = _safe_https_url(values)
            if url:
                return url
            continue
        if not isinstance(values, (list, tuple)):
            continue
        for entry in values:
            if isinstance(entry, Mapping):
                url = _safe_https_url(
                    entry.get("url") or entry.get("image_url") or entry.get("src")
                )
            else:
                url = _safe_https_url(entry)
            if url:
                return url
    return None


def _collect_https_urls(*groups: object) -> list[str]:
    result: list[str] = []
    for group in groups:
        values: Sequence[object]
        if group is None:
            continue
        if isinstance(group, (str, bytes)):
            values = [group]
        elif isinstance(group, Sequence):
            values = group
        else:
            continue
        for item in values:
            url = _safe_https_url(item)
            if url and url not in result:
                result.append(url)
    return result


def _safe_https_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None
    return url


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _context_authorization(context: VideoToolContext) -> str:
    """从当前执行上下文借用凭据，不在Operation Adapter中缓存。"""

    if context.credential is None:
        raise VideoToolExecutionError("镜头生成Operation缺少临时授权")
    try:
        return context.credential.borrow_authorization()
    except RuntimeError as exc:
        raise VideoToolExecutionError("镜头生成Operation缺少临时授权") from exc
