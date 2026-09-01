"""从 V2 Workspace Prompt Package 构造 Provider 请求。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolExecutionError


def build_scene_generation_request(
    context: VideoToolContext,
    scene: Mapping[str, JsonValue],
    variant_index: int,
) -> dict[str, JsonValue]:
    """校验 V2 资产引用，并构造单镜 Provider 请求。"""

    scene_id = _text(scene.get("scene_id"))
    shot_description = scene.get("shot_description")
    prompt = _text(shot_description.get("text")) if isinstance(shot_description, Mapping) else ""
    prompt = prompt or _text(scene.get("prompt")) or _text(scene.get("storyline"))
    if not scene_id or not prompt:
        raise VideoToolExecutionError("镜头生成请求缺少镜头或提示词")
    payload = context.workspace.payload
    contract = payload.get("creation_contract")
    contract_map = contract if isinstance(contract, Mapping) else {}
    model = _text(scene.get("model")) or _text(contract_map.get("video_model"))
    ratio = _text(scene.get("ratio")) or _text(contract_map.get("video_ratio"))
    size = _text(scene.get("size")) or _text(contract_map.get("video_size")) or "1080p"
    sound = _text(scene.get("sound")) or _text(contract_map.get("video_sound")) or "on"
    duration = _duration(scene)
    if not model or not ratio:
        raise VideoToolExecutionError("镜头生成缺少视频模型或画幅参数")
    if duration is None:
        raise VideoToolExecutionError("镜头生成请求缺少有效时长（4-30 秒）")
    image_urls = _asset_reference_urls(payload, scene)
    video_urls = _https_urls(scene.get("video_urls"))
    audio_urls = _https_urls(scene.get("audio_urls"))
    generation_mode = _text(scene.get("generation_mode")) or _infer_mode(
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
    )
    return {
        "scene_id": scene_id,
        "variant_index": variant_index,
        "prompt": prompt,
        "duration": duration,
        "duration_sec": duration,
        "model": model,
        "ratio": ratio,
        "size": size,
        "sound": sound if sound in {"on", "off"} else "on",
        "generation_mode": generation_mode,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "audio_urls": audio_urls,
    }


def _asset_reference_urls(
    payload: Mapping[str, JsonValue],
    scene: Mapping[str, JsonValue],
) -> list[str]:
    registry_value = payload.get("asset_registry")
    registry = {
        _text(item.get("asset_id")): item
        for item in registry_value
        if isinstance(item, Mapping) and _text(item.get("asset_id"))
    } if isinstance(registry_value, list) else {}
    references = scene.get("reference_asset_ids")
    reference_ids = [
        _text(value)
        for value in references
        if _text(value)
    ] if isinstance(references, (list, tuple)) else []
    if registry and not reference_ids:
        raise VideoToolExecutionError("分镜尚未声明已登记资产，不能开始生成")
    urls: list[str] = []
    for asset_id in reference_ids:
        asset = registry.get(asset_id)
        if asset is None:
            raise VideoToolExecutionError("分镜引用了未登记资产，不能开始生成")
        if asset.get("state") != "ready" or asset.get("usable_for_video") is not True:
            raise VideoToolExecutionError("分镜引用资产尚未就绪，需先完成素材生成")
        url = _safe_url(asset.get("image_url"))
        if url and url not in urls:
            urls.append(url)
    for item in payload.get("materials", []) if isinstance(payload.get("materials"), list) else []:
        if not isinstance(item, Mapping) or item.get("kind") != "image":
            continue
        url = _safe_url(item.get("url"))
        if url and url not in urls:
            urls.append(url)
    if len(urls) > 9:
        raise VideoToolExecutionError("单分镜最多允许 9 张参考图")
    return urls


def _duration(scene: Mapping[str, JsonValue]) -> int | None:
    raw = scene.get("duration_sec") or scene.get("duration")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = int(raw)
        return value if 4 <= value <= 30 else None
    raw_ms = scene.get("duration_ms")
    if isinstance(raw_ms, (int, float)) and not isinstance(raw_ms, bool) and int(raw_ms) % 1000 == 0:
        value = int(raw_ms) // 1000
        return value if 4 <= value <= 30 else None
    return None


def _infer_mode(*, image_urls: Sequence[str], video_urls: Sequence[str], audio_urls: Sequence[str]) -> str:
    if video_urls:
        return "reference_mode_video"
    if image_urls or audio_urls:
        return "reference_mode_video"
    return "text_to_video"


def _https_urls(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [url for item in value if (url := _safe_url(item))]


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text.startswith("https://") and "?" not in text and "#" not in text else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = ["build_scene_generation_request"]
