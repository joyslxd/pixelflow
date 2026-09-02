"""为原生 Agent 上下文构造可公开的 Workspace 摘要（不含密钥与原文长文）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from pixelflow.video.contracts import AgentPlan, PlanStepStatus, VideoWorkspace
from pixelflow.video.services.production_fields import (
    workspace_has_ending_cta,
    workspace_resolved_aspect_ratio,
)
from pixelflow.video.workspace.payload import migrate_workspace_payload


def _asset_has_image_url(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False
    for key in ("image_url", "url", "generation_reference_url"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip().startswith(("http://", "https://", "asset://")):
            return True
    for key in ("images", "three_view_images"):
        images = item.get(key)
        if isinstance(images, list):
            for image in images:
                if isinstance(image, str) and image.strip().startswith(("http://", "https://", "asset://")):
                    return True
                if isinstance(image, Mapping):
                    url = image.get("url") or image.get("image_url")
                    if isinstance(url, str) and url.strip().startswith(("http://", "https://", "asset://")):
                        return True
    return False


def workspace_has_scene_asset_images(payload: Mapping[str, Any] | None) -> bool:
    """全局资产是否已有至少一张参考图 URL。"""

    if not isinstance(payload, Mapping):
        return False
    global_assets = _as_mapping(payload.get("global_assets")) or {}
    for bucket in ("characters", "scenes", "props"):
        for item in _as_list(global_assets.get(bucket)):
            if _asset_has_image_url(item):
                return True
    return False


# 与前端 SCENE_ASSET_PREFERRED_MODELS / Borgrise 生图入口对齐；禁止向用户推荐未注册模型。
REGISTERED_SCENE_ASSET_IMAGE_MODELS: tuple[dict[str, str], ...] = (
    {"id": "gpt-image-2", "label": "image-2"},
    {"id": "seeddream-5.0", "label": "Seedream 5.0"},
)

# 用途：公开成片预览只允许已验证 TOS 域；影响：工作台可直连播放，Sidecar 也只能看到白名单 HTTPS。
_PUBLIC_MEDIA_HOST_SUFFIXES = (".tos-cn-beijing.volces.com", ".vitamazing.top")
_SECRET_KEY_FRAGMENTS = (
    "credential",
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "authorization",
)

# 用途：V2 工作台在首屏展示已确认的创作合同；影响：只传递用户拥有的规划文本，
# 成片仅公开白名单 TOS 播放地址，凭据和 Provider 原文仍不进入 Snapshot。
_V2_CREATIVE_FIELDS = {
    "brand", "product", "audience", "platform", "aspect_ratio", "target_duration_sec",
    "audio", "cta", "creative_direction", "concept", "tone", "visual_style",
    "delivery", "reference_strategy",
}
_V2_NARRATIVE_FIELDS = {
    "concept", "outline", "character_arc", "era", "narration", "dialogue", "sound",
    "brand_closure", "script", "status", "version",
}
_V2_PROMPT_PREVIEW_CHARS = 8_000
_V2_PACKAGE_LIMIT = 120


def _bounded_text(value: object, *, maximum: int) -> str | None:
    """返回用户可见的有界文本，空值和非文本不进入公开投影。"""

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:maximum] if text else None


def _safe_v2_creative_brief(payload: Mapping[str, Any]) -> dict[str, str | int]:
    """投影当前创意方向与生产约束，不透传可变的选项内部结构。"""

    source = _as_mapping(payload.get("creative_brief")) or {}
    result: dict[str, str | int] = {}
    for key in _V2_CREATIVE_FIELDS:
        value = source.get(key)
        if key == "target_duration_sec":
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result[key] = value
            continue
        text = _bounded_text(value, maximum=2_000)
        if text is not None:
            result[key] = text
    return result


def _safe_creation_contract(payload: Mapping[str, Any]) -> dict[str, str] | None:
    """投影已冻结的生成路由；合同只含用户可见参数，不含授权或 Provider 原始配置。"""

    source = _as_mapping(payload.get("creation_contract"))
    if source is None:
        return None
    result = {
        key: value
        for key in ("video_model", "video_ratio", "video_size", "video_sound")
        if (value := _bounded_text(source.get(key), maximum=128)) is not None
    }
    return result or None


def _safe_v2_narrative_plan(payload: Mapping[str, Any]) -> dict[str, str]:
    """投影脚本大纲与声音骨架；脚本长度与 Tool 合同保持一致。"""

    source = _as_mapping(payload.get("narrative_plan")) or {}
    result: dict[str, str] = {}
    for key in _V2_NARRATIVE_FIELDS:
        text = _bounded_text(source.get(key), maximum=8_000 if key == "script" else 2_000)
        if text is not None:
            result[key] = text
    return result


_BUSY_JOB_STATUSES = {"queued", "starting", "polling"}
_FAILED_ASSET_STATES = {"failed", "timeout", "expired", "indeterminate"}


def _public_asset_generation(item: Mapping[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    """把内部 Job 字段收成工作台可展示的状态，不透传 URL 或 Provider 原文。"""

    state = _bounded_text(item.get("state"), maximum=32) or "planned"
    job_id = _bounded_text(item.get("generation_job_id"), maximum=128)
    job_status = _bounded_text(
        item.get("generation_job_status") or item.get("generation_status"),
        maximum=32,
    )
    failure_code = _bounded_text(item.get("failure_reason_code"), maximum=128)
    if state == "ready":
        return "ready", job_id, "succeeded" if job_id else None, None
    if state in _FAILED_ASSET_STATES:
        return "failed", job_id, "failed" if job_id else None, failure_code
    if job_id and (state in {"planned", "generating"} or job_status in _BUSY_JOB_STATUSES):
        return "generating", job_id, job_status if job_status in _BUSY_JOB_STATUSES else "queued", None
    return state, job_id, job_status, failure_code


def _safe_v2_asset_registry(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """投影稳定资产身份和状态，禁止 URL、Provider 响应及未知扩展字段。"""

    result: list[dict[str, Any]] = []
    for item in _as_list(payload.get("asset_registry"))[:120]:
        if not isinstance(item, Mapping):
            continue
        asset_id = _bounded_text(item.get("asset_id"), maximum=128)
        kind = _bounded_text(item.get("kind"), maximum=64)
        role = _bounded_text(item.get("role"), maximum=256)
        if asset_id is None or kind is None or role is None:
            continue
        state, job_id, job_status, failure_code = _public_asset_generation(item)
        entry: dict[str, Any] = {
            "asset_id": asset_id,
            "slot": _bounded_text(item.get("slot"), maximum=64),
            "kind": kind,
            "role": role,
            "origin": _bounded_text(item.get("origin"), maximum=32) or "planned_generation",
            "generation_prompt": _bounded_text(item.get("generation_prompt"), maximum=8_000),
            "state": state,
            "generation_job_id": job_id,
            "generation_job_status": job_status,
            "failure_reason_code": failure_code,
            "reference_asset_ids": [
                reference[:128]
                for reference in _as_list(item.get("reference_asset_ids"))[:32]
                if isinstance(reference, str) and reference.strip()
            ],
            "usable_for_video": item.get("usable_for_video") is True,
        }
        result.append({key: value for key, value in entry.items() if value is not None})
    return result


def public_workspace_media_url(url: object) -> str | None:
    """只公开白名单 HTTPS TOS 地址，拒绝用户信息和任意外链。"""

    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url.strip())
    host = parsed.hostname.lower() if parsed.hostname else ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or not host
        or not any(host.endswith(suffix) for suffix in _PUBLIC_MEDIA_HOST_SUFFIXES)
    ):
        return None
    return url.strip()


def _merged_video_digest(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """投影合并成片的可播放地址；非白名单主机只保留已完成事实、不给 URL。"""

    merged = _as_mapping(payload.get("merged_video")) or {}
    candidates: list[object] = [
        merged.get("merged_video_url"),
        merged.get("video_url"),
    ]
    for item in _as_list(payload.get("outputs")):
        if isinstance(item, Mapping) and str(item.get("output_type") or "") == "mp4":
            candidates.append(item.get("video_url"))
    for item in _as_list(payload.get("deliveries")):
        if (
            isinstance(item, Mapping)
            and str(item.get("output_type") or "") == "mp4"
            and str(item.get("status") or "") == "succeeded"
        ):
            candidates.append(item.get("video_url"))
    for candidate in candidates:
        url = public_workspace_media_url(candidate)
        if url:
            return {"ok": True, "preview_url": url}
    if merged.get("ok") is True:
        return {"ok": True}
    return None


def workspace_scene_preview_url(payload: Mapping[str, Any], scene_id: str) -> str | None:
    """从权威 Workspace 取出可公开的成片地址，优先已审核版本。"""

    if not str(scene_id).strip():
        return None
    scene = _workspace_scene_record(payload, scene_id)
    if scene is None:
        return None
    for candidate in _scene_preview_url_candidates(scene):
        public = public_workspace_media_url(candidate)
        if public:
            return public
    return None


def _workspace_scene_record(payload: Mapping[str, Any], scene_id: str) -> Mapping[str, Any] | None:
    """按 scene_id 或 segment_id 定位分镜。"""

    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        scenes = payload.get("scene_packages")
    if not isinstance(scenes, list):
        return None
    target = str(scene_id).strip()
    for item in scenes:
        if not isinstance(item, Mapping):
            continue
        identifiers = {str(item.get("scene_id") or "").strip(), str(item.get("segment_id") or "").strip()}
        if target in identifiers:
            return item
    return None


def _scene_preview_url_candidates(scene: Mapping[str, Any]) -> list[str]:
    """只收集 HTTPS 成片地址，顺序：镜头主 URL、已选 variant、成功任务。"""

    urls: list[str] = []
    primary = _https_media_url(scene.get("video_url"))
    if primary:
        urls.append(primary)
    approved = str(scene.get("approved_variant_id") or "").strip()
    variants = scene.get("variants")
    if isinstance(variants, list):
        selected: list[str] = []
        rest: list[str] = []
        for item in variants:
            if not isinstance(item, Mapping):
                continue
            url = _https_media_url(item.get("video_url"))
            if not url:
                continue
            variant_id = str(item.get("variant_id") or "").strip()
            if item.get("selected") is True or (approved and variant_id == approved):
                selected.append(url)
            else:
                rest.append(url)
        urls.extend(selected)
        urls.extend(rest)
    jobs = scene.get("generation_jobs")
    if isinstance(jobs, list):
        for item in jobs:
            if not isinstance(item, Mapping) or str(item.get("status") or "") != "succeeded":
                continue
            url = _https_media_url(item.get("video_url"))
            if url:
                urls.append(url)
    return urls


def _https_media_url(value: object) -> str | None:
    """只接受 https 媒体地址，拒绝相对路径和内部 artifact。"""

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text.lower().startswith("https://") else None


def _scene_video_state_index(payload: Mapping[str, Any], video_status: Mapping[str, Any]) -> dict[str, str]:
    """用 scene_id / segment_id 对齐成片状态，供 Prompt Package 工作台回显。"""

    by_scene_id = {
        str(item.get("scene_id") or "").strip(): str(item.get("state") or "").strip()
        for item in _as_list(video_status.get("scene_video_states"))
        if isinstance(item, Mapping) and str(item.get("scene_id") or "").strip()
    }
    index = dict(by_scene_id)
    for scene in _as_list(payload.get("scenes") or payload.get("scene_packages")):
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        segment_id = str(scene.get("segment_id") or "").strip()
        state = by_scene_id.get(scene_id)
        if state and segment_id:
            index[segment_id] = state
    return index


def _scene_preview_url_index(payload: Mapping[str, Any]) -> dict[str, str]:
    """按 scene_id / segment_id 给出可直连的成片地址。"""

    index: dict[str, str] = {}
    for scene in _as_list(payload.get("scenes") or payload.get("scene_packages")):
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        url = workspace_scene_preview_url({"scenes": [scene]}, scene_id or str(scene.get("segment_id") or "").strip())
        if not url:
            continue
        for key in (scene_id, str(scene.get("segment_id") or "").strip()):
            if key:
                index[key] = url
    return index


def _prompt_package_public_state(
    item: Mapping[str, Any],
    video_states: Mapping[str, str],
    preview_urls: Mapping[str, str],
) -> tuple[str, str | None]:
    """Package 规划态不能单独代表成片；白名单 TOS 地址才进入工作台播放器。"""

    segment_id = _bounded_text(item.get("segment_id") or item.get("scene_id"), maximum=128) or ""
    declared = _bounded_text(item.get("state"), maximum=32) or "planned"
    video_state = video_states.get(segment_id)
    if video_state == "ready":
        return "ready", preview_urls.get(segment_id)
    if video_state == "polling":
        return "generating", None
    if video_state == "failed":
        return "failed", None
    return declared, None


def _safe_v2_prompt_packages(
    payload: Mapping[str, Any],
    video_states: Mapping[str, str] | None = None,
    preview_urls: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """投影每段可审阅 Prompt；正文有界以避免 Snapshot 被长片内容无限放大。"""

    states = video_states or {}
    urls = preview_urls or {}
    result: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(payload.get("prompt_packages"))[:_V2_PACKAGE_LIMIT], start=1):
        if not isinstance(item, Mapping):
            continue
        entry = _public_prompt_package_entry(item, index, states, urls)
        if entry is not None:
            result.append(entry)
    return result


def _public_prompt_package_entry(
    item: Mapping[str, Any],
    index: int,
    video_states: Mapping[str, str],
    preview_urls: Mapping[str, str],
) -> dict[str, Any] | None:
    """单段公开字段：成片只带白名单 TOS 播放地址。"""

    segment_id = _bounded_text(item.get("segment_id") or item.get("scene_id"), maximum=128)
    prompt = _bounded_text(item.get("prompt"), maximum=_V2_PROMPT_PREVIEW_CHARS)
    duration = item.get("duration_sec")
    if segment_id is None or prompt is None or not isinstance(duration, int) or isinstance(duration, bool):
        return None
    state, preview_url = _prompt_package_public_state(item, video_states, preview_urls)
    sequence = item.get("sequence")
    entry: dict[str, Any] = {
        "segment_id": segment_id,
        "sequence": sequence if isinstance(sequence, int) and sequence > 0 else index,
        "duration_sec": duration,
        "generation_mode": _bounded_text(item.get("generation_mode"), maximum=64) or "independent",
        "prompt_summary": prompt,
        "prompt_char_count": len(str(item.get("prompt") or "").strip()),
        "prompt_truncated": len(str(item.get("prompt") or "").strip()) > _V2_PROMPT_PREVIEW_CHARS,
        "reference_asset_ids": [
            reference[:128]
            for reference in _as_list(item.get("reference_asset_ids"))[:32]
            if isinstance(reference, str) and reference.strip()
        ],
        "continuity_from": _bounded_text(item.get("continuity_from"), maximum=128),
        "transition_out": _bounded_text(item.get("transition_out"), maximum=2_000),
        "era": _bounded_text(item.get("era"), maximum=512),
        "camera": _bounded_text(item.get("camera") or item.get("camera_movement"), maximum=2_000),
        "sound": _bounded_text(item.get("sound"), maximum=2_000),
        "hard_constraints": [
            constraint[:2_000]
            for constraint in _as_list(item.get("hard_constraints"))[:64]
            if isinstance(constraint, str) and constraint.strip()
        ],
        "state": state,
        "has_preview": True if preview_url else None,
        "preview_url": preview_url,
    }
    return {key: value for key, value in entry.items() if value is not None}


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _safe_len(value: Any) -> int:
    if isinstance(value, str):
        return len(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return 0


def _count_named_items(value: Any) -> int:
    items = _as_list(value)
    count = 0
    for item in items:
        if isinstance(item, Mapping) and str(item.get("name") or "").strip():
            count += 1
        elif isinstance(item, str) and item.strip():
            count += 1
    return count


def _asset_summaries(value: object, *, limit: int = 12) -> list[dict[str, str]]:
    """提取全局素材的安全展示字段，不向浏览器发送 URL、提示词或 Provider 原始结果。"""

    result: list[dict[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, Mapping):
            continue
        asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
        name = str(item.get("name") or item.get("title") or "").strip()
        if not asset_id and not name:
            continue
        result.append({"asset_id": asset_id[:64], "name": name[:120] or "未命名素材"})
        if len(result) >= limit:
            break
    return result


def _reference_image_summaries(value: object, *, limit: int = 9) -> list[dict[str, str]]:
    """仅投影用户确认的参考图名称与资产身份，TOS URL 不进入浏览器 Snapshot。"""

    result: list[dict[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, Mapping):
            continue
        reference_id = str(item.get("reference_id") or "").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not reference_id or not asset_id or not name:
            continue
        result.append({"reference_id": reference_id[:64], "asset_id": asset_id[:64], "name": name[:120]})
        if len(result) >= limit:
            break
    return result


def _material_summaries(value: object, *, limit: int = 9) -> list[dict[str, str]]:
    """仅公开用户可见材料标签，TOS URL 与 asset 库内部字段不进入 Snapshot。"""

    result: list[dict[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, Mapping):
            continue
        material_id = str(item.get("material_id") or "").strip()
        name = str(item.get("name") or "").strip()
        label = str(item.get("reference_label") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not material_id or not name or not label or kind not in {"image", "video", "audio", "file"}:
            continue
        result.append({"material_id": material_id[:64], "name": name[:120], "reference_label": label[:80], "kind": kind})
        if len(result) >= limit:
            break
    return result


def _scene_summaries(scenes: list[Any], states: object) -> list[dict[str, Any]]:
    """将场景状态压缩为看板所需字段，避免把镜头提示词和媒体 URL 放入 Snapshot。"""

    states_by_id = {
        str(item.get("scene_id") or ""): item
        for item in _as_list(states)
        if isinstance(item, Mapping) and str(item.get("scene_id") or "")
    }
    result: list[dict[str, Any]] = []
    for scene in scenes[:24]:
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue
        state = states_by_id.get(scene_id, {})
        index = scene.get("scene_index")
        result.append(
            {
                "scene_id": scene_id[:64],
                "scene_index": index if isinstance(index, int) and not isinstance(index, bool) else None,
                "title": str(scene.get("title") or scene.get("name") or "未命名分镜")[:160],
                "state": str(state.get("state") or "idle")[:32],
            }
        )
    return result


def summarize_scene_asset_status(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """参考图完整度摘要：区分任意图已生成与全部生成完成。"""

    if not isinstance(payload, Mapping):
        payload = {}
    global_assets = _as_mapping(payload.get("global_assets")) or {}
    asset_types = {
        "characters": "character",
        "scenes": "scene_image",
        "props": "prop_image",
    }
    targets: list[tuple[dict[str, str], bool]] = []
    for bucket, asset_type in asset_types.items():
        for item in _as_list(global_assets.get(bucket)):
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            target = {"asset_id": asset_id, "asset_type": asset_type}
            targets.append((target, _asset_has_image_url(item)))

    missing_targets = [target for target, ready in targets if not ready]
    missing_keys = {
        (target["asset_type"], target["asset_id"])
        for target in missing_targets
        if target["asset_id"]
    }
    failed_keys: set[tuple[str, str]] = set()
    for failure in _as_list(payload.get("scene_asset_failures")):
        if not isinstance(failure, Mapping):
            continue
        key = (
            str(failure.get("asset_type") or "").strip(),
            str(failure.get("asset_id") or "").strip(),
        )
        if key in missing_keys:
            failed_keys.add(key)

    required_count = len(targets)
    ready_count = sum(1 for _target, ready in targets if ready)
    missing_count = required_count - ready_count
    scene_asset_job = _as_mapping(payload.get("scene_asset_job")) or {}
    job_status = str(scene_asset_job.get("status") or "").strip().casefold()
    is_running = job_status in {
        "created",
        "pending",
        "polling",
        "queued",
        "running",
        "start_paused_quota",
    }
    if required_count == 0:
        status = "empty"
    elif missing_count == 0:
        status = "ready"
    elif is_running:
        status = "running"
    elif ready_count > 0:
        status = "partial"
    elif failed_keys:
        status = "failed"
    else:
        status = "empty"

    return {
        "scene_asset_status": status,
        "scene_asset_required_count": required_count,
        "scene_asset_ready_count": ready_count,
        "scene_asset_missing_count": missing_count,
        "scene_asset_failed_count": len(failed_keys),
        "scene_assets_ready": status == "ready",
        "scene_asset_missing_targets": [
            target for target in missing_targets if target["asset_id"]
        ][:64],
    }


def _scene_has_video_url(scene: Mapping[str, Any]) -> bool:
    if str(scene.get("video_url") or "").strip().lower().startswith(("http://", "https://")):
        return True
    for variant in _as_list(scene.get("variants")):
        if not isinstance(variant, Mapping):
            continue
        if str(variant.get("video_url") or "").strip().lower().startswith(("http://", "https://")):
            return True
    return False


def _job_status_bucket(status: str) -> str:
    normalized = status.strip().casefold()
    if normalized in {"succeeded", "success", "completed"}:
        return "succeeded"
    if normalized in {"failed", "timeout", "expired", "error", "indeterminate"}:
        return "failed"
    if normalized in {
        "polling",
        "queued",
        "starting",
        "created",
        "running",
        "generating",
        "start_paused_quota",
    }:
        return "polling"
    return "other"


def _latest_generation_job(scene: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """只认该镜最后一条 GenerationJob，历史失败不得盖住当前重试。"""

    jobs = [item for item in _as_list(scene.get("generation_jobs")) if isinstance(item, Mapping)]
    return jobs[-1] if jobs else None


def _scene_public_video_state(scene: Mapping[str, Any]) -> str:
    """当前镜头公开态：进行中优先于旧失败和旧成片。"""

    latest = _latest_generation_job(scene)
    latest_bucket = _job_status_bucket(str(latest.get("status") or "")) if latest else "other"
    regenerating = str(scene.get("edit_status") or "").strip() == "重新生成中"
    if latest_bucket == "polling" or (regenerating and latest_bucket not in {"succeeded", "failed"}):
        return "polling"
    if _scene_has_video_url(scene) or latest_bucket == "succeeded":
        return "ready"
    if latest_bucket == "failed":
        return "failed"
    return "idle"


def _public_generation_jobs(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """每镜只公开最新视频任务身份和状态，不含 URL。"""

    result: list[dict[str, Any]] = []
    for scene in _as_list(payload.get("scenes") or payload.get("scene_packages")):
        if not isinstance(scene, Mapping) or len(result) >= 24:
            continue
        scene_id = _bounded_text(scene.get("scene_id"), maximum=128)
        latest = _latest_generation_job(scene)
        job_id = _bounded_text(
            (latest or {}).get("job_id") or (latest or {}).get("generation_job_id"),
            maximum=128,
        )
        status = _bounded_text((latest or {}).get("status"), maximum=32)
        if scene_id is None or job_id is None or status is None:
            continue
        result.append(
            {
                "generation_job_id": job_id,
                "item_id": scene_id,
                "kind": "video",
                "status": status,
            }
        )
    return result


def summarize_scene_video_status(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """分镜视频进度公开摘要：供 digest / inspect 共用，不含 URL 原文。"""

    if not isinstance(payload, Mapping):
        return {
            "scene_count": 0,
            "scene_videos_ready_count": 0,
            "scene_videos_polling_count": 0,
            "scene_videos_failed_count": 0,
            "scene_videos_idle_count": 0,
        }
    scenes = _as_list(payload.get("scenes") or payload.get("scene_packages"))
    ready = 0
    polling = 0
    failed = 0
    idle = 0
    per_scene: list[dict[str, Any]] = []
    for scene in scenes:
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue
        state = _scene_public_video_state(scene)
        if state == "ready":
            ready += 1
        elif state == "polling":
            polling += 1
        elif state == "failed":
            failed += 1
        else:
            idle += 1
        if len(per_scene) < 24:
            entry: dict[str, Any] = {
                "scene_id": scene_id,
                "state": state,
            }
            index = scene.get("scene_index")
            if isinstance(index, int) and not isinstance(index, bool):
                entry["scene_index"] = index
            per_scene.append(entry)

    progress = _as_mapping(payload.get("scene_video_progress")) or {}
    completed = progress.get("completed")
    total = progress.get("total")
    result: dict[str, Any] = {
        "scene_count": len(scenes),
        "scene_videos_ready_count": ready,
        "scene_videos_polling_count": polling,
        "scene_videos_failed_count": failed,
        "scene_videos_idle_count": idle,
        "scene_video_states": per_scene or None,
    }
    if isinstance(completed, int) and not isinstance(completed, bool) and completed >= 0:
        result["scene_video_progress_completed"] = completed
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        result["scene_video_progress_total"] = total
    return {key: value for key, value in result.items() if value is not None}


def build_workspace_digest(workspace: VideoWorkspace) -> dict[str, Any]:
    """从 VideoWorkspace 抽取规划用公开摘要。"""

    payload = workspace.payload if isinstance(workspace.payload, dict) else {}
    v2_payload = migrate_workspace_payload(payload)
    script = _as_mapping(payload.get("script")) or {}
    script_content = str(script.get("content") or "").strip()
    pipeline = _as_mapping(payload.get("script_pipeline")) or {}
    pipeline_stages = sorted(
        str(key)
        for key, item in pipeline.items()
        if isinstance(item, Mapping) and str(item.get("content") or item.get("stage") or "").strip()
    )
    global_assets = _as_mapping(payload.get("global_assets")) or {}
    scenes = _as_list(payload.get("scenes") or payload.get("scene_packages"))
    dirty = payload.get("dirty_scene_ids")
    dirty_ids = [str(item) for item in dirty] if isinstance(dirty, list) else []
    qc = _as_mapping(payload.get("qc")) or {}
    quota_interrupt = _as_mapping(payload.get("quota_interrupt")) or {}
    product = _as_mapping(payload.get("product_info")) or {}
    safe_product = {
        key: value
        for key, value in product.items()
        if not any(fragment in str(key).lower() for fragment in _SECRET_KEY_FRAGMENTS)
        and key in {"name", "category", "brand"}
    }
    resolved_ratio = workspace_resolved_aspect_ratio(payload)
    asset_status = summarize_scene_asset_status(payload)
    video_status = summarize_scene_video_status(payload)
    scene_summaries = _scene_summaries(scenes, video_status.get("scene_video_states"))
    package_video_states = _scene_video_state_index(payload, video_status)
    package_preview_urls = _scene_preview_url_index(payload)
    return {
        key: value
        for key, value in {
            "workspace_id": workspace.workspace_id,
            "revision": workspace.revision,
            "workspace_schema_version": v2_payload.get("workspace_schema_version"),
            "creative_brief": _safe_v2_creative_brief(v2_payload) or None,
            "creation_contract": _safe_creation_contract(v2_payload),
            "narrative_plan": _safe_v2_narrative_plan(v2_payload) or None,
            "asset_registry": _safe_v2_asset_registry(v2_payload) or None,
            "prompt_packages": _safe_v2_prompt_packages(
                v2_payload, package_video_states, package_preview_urls
            ) or None,
            "has_script": bool(script_content) or bool(pipeline_stages),
            "script_status": str(script.get("status") or "") or None,
            "script_source": str(script.get("source") or "") or None,
            "script_version": script.get("version"),
            "script_chars": _safe_len(script_content),
            "script_preview": script_content[:1_200] or None,
            # 前端脚本编辑走同一 Workspace Command；长度与 Tool 输入合同一致。
            "script_editor_content": script_content[:8_000] or None,
            "script_pipeline_stages": pipeline_stages,
            "script_entry_path": str(payload.get("script_entry_path") or "") or None,
            "script_plan_confirmed": bool(payload.get("script_plan_confirmed")),
            "script_plan_confirmed_version": payload.get(
                "script_plan_confirmed_version"
            ),
            "awaiting_production_fields": bool(payload.get("awaiting_production_fields")),
            "awaiting_production_constraints": bool(payload.get("awaiting_production_fields")),
            "has_aspect_ratio": resolved_ratio is not None,
            "video_ratio": resolved_ratio,
            "has_ending_cta": workspace_has_ending_cta(payload),
            "script_missing_requirements": [
                str(item).strip()
                for item in (_as_list(script.get("missing_requirements")))
                if str(item).strip()
            ][:8]
            or None,
            "character_count": _count_named_items(global_assets.get("characters")),
            "scene_asset_count": _count_named_items(global_assets.get("scenes")),
            "prop_count": _count_named_items(global_assets.get("props")),
            "character_summaries": _asset_summaries(global_assets.get("characters")) or None,
            "scene_asset_summaries": _asset_summaries(global_assets.get("scenes")) or None,
            "prop_summaries": _asset_summaries(global_assets.get("props")) or None,
            "reference_image_count": _safe_len(payload.get("reference_images")),
            "reference_image_summaries": _reference_image_summaries(payload.get("reference_images")) or None,
            "material_count": _safe_len(payload.get("materials")),
            "material_summaries": _material_summaries(payload.get("materials")) or None,
            "scene_count": len(scenes),
            "dirty_scene_ids": dirty_ids[:32],
            "dirty_scene_count": len(dirty_ids),
            "qc_status": str(qc.get("status") or qc.get("verdict") or "") or None,
            "qc_issue_count": _safe_len(qc.get("issues") or qc.get("findings")),
            "pending_confirmations": bool(payload.get("pending_confirmations")),
            "quota_interrupt_id": str(quota_interrupt.get("quota_interrupt_id") or "") or None,
            "quota_interrupt_state": str(quota_interrupt.get("state") or "") or None,
            "quota_interrupt_reason_code": str(quota_interrupt.get("reason_code") or "") or None,
            "failed_scene_asset_count": _safe_len(payload.get("scene_asset_failures")),
            "has_scene_packages": bool(scenes),
            "has_scene_asset_images": workspace_has_scene_asset_images(payload),
            **asset_status,
            "registered_scene_asset_image_models": [
                dict(item) for item in REGISTERED_SCENE_ASSET_IMAGE_MODELS
            ],
            "product_info": safe_product or None,
            "latest_input_chars": _safe_len(payload.get("latest_input")),
            "has_materials": bool(_as_list(payload.get("materials"))),
            **video_status,
            "scene_summaries": scene_summaries or None,
            "generation_jobs": _public_generation_jobs(payload) or None,
            "merged_video": _merged_video_digest(payload),
        }.items()
        if value is not None
    }


def build_plan_digest(plan: AgentPlan | None) -> dict[str, Any] | None:
    """将最新计划投影为只读看板数据，不暴露 Tool 参数、原始 Provider 结果或内部事件。"""

    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "status": plan.status.value,
        "goal": (plan.public_goal or "")[:800] or None,
        "steps": [
            {
                "step_id": step.step_id,
                "sequence": step.sequence,
                "title": step.title,
                "status": step.status.value,
                "summary": (step.public_summary or "")[:400] or None,
                "confirmation_required": step.confirmation_required,
            }
            for step in plan.steps[:20]
        ],
    }


def blocking_confirmation_from_plan(plan: AgentPlan | None) -> dict[str, Any] | None:
    """若最新计划卡在确认闸门，返回公开摘要。"""

    if plan is None or not plan.steps:
        return None
    for step in plan.steps:
        if step.status is PlanStepStatus.AWAITING_CONFIRMATION:
            return {
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "tool_name": step.tool_name,
                "title": step.title,
            }
    if plan.status.value == "awaiting_confirmation":
        waiting = next(
            (step for step in plan.steps if step.confirmation_required),
            plan.steps[0],
        )
        return {
            "plan_id": plan.plan_id,
            "step_id": waiting.step_id,
            "tool_name": waiting.tool_name,
            "title": waiting.title,
        }
    return None
