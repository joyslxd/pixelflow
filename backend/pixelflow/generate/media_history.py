"""会话媒体结果历史落库：Job 完成后幂等追加对话结果卡并更新 context。

不依赖前端轮询成功。``client_message_id = media-result:{kind}:{job_id}`` 保证重复 complete 不插第二条。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from pixelflow.tasks.store import PixelFlowConversationMessageRecord, PixelFlowTaskStore

logger = logging.getLogger(__name__)

MediaResultKind = Literal[
    "scene_assets",
    "scene_videos",
    "merge_video",
    "image_generate",
    "image_asset_edit",
    "image_asset_fusion",
]


def media_result_client_message_id(kind: MediaResultKind | str, job_id: str) -> str:
    return f"media-result:{kind}:{job_id}"


def resolve_conversation_id(*, body_conversation_id: str | None, header_conversation_id: str | None) -> str | None:
    for candidate in (body_conversation_id, header_conversation_id):
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def _stable_message_id(conversation_id: str, client_message_id: str) -> str:
    idempotency_key = f"pixelflow-conversation-message:{conversation_id}:{client_message_id}"
    return uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex


def _has_any_image_url(global_assets: dict[str, Any] | None, scene_packages: list[dict[str, Any]] | None) -> bool:
    assets = global_assets or {}
    for key in ("characters", "scenes", "props"):
        items = assets.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in ("images", "three_view_images"):
                urls = item.get(field)
                if isinstance(urls, list) and any(str(url or "").strip() for url in urls):
                    return True
    for scene in scene_packages or []:
        if not isinstance(scene, dict):
            continue
        urls = scene.get("image_urls")
        if isinstance(urls, list) and any(str(url or "").strip() for url in urls):
            return True
    return False


def _has_scene_video_urls(scene_videos: list[dict[str, Any]] | None) -> bool:
    for item in scene_videos or []:
        if isinstance(item, dict) and str(item.get("video_url") or "").strip():
            return True
    return False


async def persist_media_result_message(
    store: PixelFlowTaskStore | None,
    *,
    conversation_id: str | None,
    user_id: str | None,
    job_id: str,
    kind: MediaResultKind | str,
    content: str,
    artifact: dict[str, Any],
    last_phase: str,
    context_patch: dict[str, Any] | None = None,
) -> bool:
    """幂等写入结果消息并合并 context。无 conversation/store 时跳过并打 warning。"""
    cid = str(conversation_id or "").strip()
    jid = str(job_id or "").strip()
    if store is None or not cid or not jid:
        logger.warning(
            "skip media result persist kind=%s job_id=%s conversation_id=%s store=%s",
            kind,
            jid or "-",
            cid or "-",
            "yes" if store is not None else "no",
        )
        return False

    conversation = await store.get_conversation(cid, user_id=user_id)
    if conversation is None and user_id is not None:
        # owner 过滤未命中时再试一次不带 user（后台任务偶发身份不一致）
        conversation = await store.get_conversation(cid, user_id=None)
    if conversation is None:
        logger.warning("skip media result persist: conversation not found conversation_id=%s", cid)
        return False

    client_message_id = media_result_client_message_id(kind, jid)
    payload = {
        "artifact": artifact,
        "client_message_id": client_message_id,
        "media_result_kind": kind,
        "media_result_job_id": jid,
    }
    await upsert_conversation_message(
        store,
        conversation_id=cid,
        user_id=user_id or conversation.user_id,
        client_message_id=client_message_id,
        content=content,
        payload=payload,
    )

    next_context = dict(conversation.context or {})
    if context_patch:
        next_context.update(context_patch)
    await store.update_conversation(
        cid,
        user_id=user_id or conversation.user_id,
        last_phase=last_phase,
        context=next_context,
    )
    return True


async def upsert_conversation_message(
    store: PixelFlowTaskStore,
    *,
    conversation_id: str,
    user_id: str | None,
    client_message_id: str,
    content: str,
    payload: dict[str, Any],
) -> PixelFlowConversationMessageRecord:
    """按 client_message_id 更新已有消息；不存在则 append。

    注意：``append_conversation_message`` 对同 message_id 是「返回旧记录、不覆盖」。
    进度卡 / 结果卡需要反复写同一 client id 时必须走本函数。
    """
    cid = str(conversation_id or "").strip()
    client_id = str(client_message_id or "").strip()
    if not cid or not client_id:
        raise ValueError("conversation_id and client_message_id are required")
    next_payload = dict(payload or {})
    next_payload["client_message_id"] = client_id
    updated = await store.update_conversation_message(
        cid,
        client_id,
        user_id=user_id,
        content=content,
        payload=next_payload,
    )
    if updated is not None:
        return updated
    if user_id is not None:
        updated = await store.update_conversation_message(
            cid,
            client_id,
            user_id=None,
            content=content,
            payload=next_payload,
        )
        if updated is not None:
            return updated
    return await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id=_stable_message_id(cid, client_id),
            conversation_id=cid,
            user_id=user_id,
            role="assistant",
            content=content,
            payload=next_payload,
        )
    )


def scene_asset_progress_client_message_id(job_id: str) -> str:
    """与前端 ``scenePackageJobMessageId({ kind: scene_asset_generation })`` 对齐。"""
    return f"scene-package-job:scene_asset_generation:{job_id}"


def build_scene_assets_progress_artifact(
    *,
    global_assets: dict[str, Any],
    scene_packages: list[dict[str, Any]],
    failed_assets: list[dict[str, Any]] | None = None,
    progress: dict[str, Any] | None = None,
    creation_contract: Any = None,
    target_duration_ms: int | None = None,
) -> dict[str, Any]:
    completed = int((progress or {}).get("completed") or 0)
    total = int((progress or {}).get("total") or 0)
    packages = {
        "ok": True,
        "message": "参考图生成中。",
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": int(target_duration_ms or 30_000),
        "global_assets": global_assets or {},
        "scene_packages": scene_packages or [],
        "creation_contract": creation_contract,
    }
    progress_label = f"{completed}/{total}" if total > 0 else "进行中"
    return {
        "type": "video_scene_packages",
        "title": "视频场景包",
        "description": f"{len(packages['scene_packages'])} 个场景片段，参考图生成中（{progress_label}）。",
        "actionLabel": "查看",
        "videoScenePackages": packages,
        "originalVideoScenePackages": packages,
        "sceneAssetFailures": list(failed_assets or []),
        "sceneAssetsGenerating": True,
        "sceneAssetsAwaitingModel": False,
        "intent": "video",
    }


async def persist_scene_assets_progress(
    store: PixelFlowTaskStore | None,
    *,
    conversation_id: str | None,
    user_id: str | None,
    job_id: str,
    global_assets: dict[str, Any],
    scene_packages: list[dict[str, Any]],
    failed_assets: list[dict[str, Any]] | None = None,
    progress: dict[str, Any] | None = None,
    creation_contract: Any = None,
    target_duration_ms: int | None = None,
) -> bool:
    """参考图逐张完成后增量落库进度卡 + context，刷新后可恢复已生成图片。"""
    cid = str(conversation_id or "").strip()
    jid = str(job_id or "").strip()
    if store is None or not cid or not jid:
        return False
    if not _has_any_image_url(global_assets, scene_packages):
        return False

    conversation = await store.get_conversation(cid, user_id=user_id)
    if conversation is None and user_id is not None:
        conversation = await store.get_conversation(cid, user_id=None)
    if conversation is None:
        logger.warning("skip scene asset progress persist: conversation not found conversation_id=%s", cid)
        return False

    client_message_id = scene_asset_progress_client_message_id(jid)
    completed = int((progress or {}).get("completed") or 0)
    total = int((progress or {}).get("total") or 0)
    content = (
        f"参考图生成中（{completed}/{total}），可先打开卡片预览已完成素材。"
        if total > 0
        else "参考图生成中，可先打开卡片预览已完成素材。"
    )
    artifact = build_scene_assets_progress_artifact(
        global_assets=global_assets,
        scene_packages=scene_packages,
        failed_assets=failed_assets,
        progress=progress,
        creation_contract=creation_contract,
        target_duration_ms=target_duration_ms,
    )
    await upsert_conversation_message(
        store,
        conversation_id=cid,
        user_id=user_id or conversation.user_id,
        client_message_id=client_message_id,
        content=content,
        payload={"artifact": artifact, "client_message_id": client_message_id},
    )
    next_context = dict(conversation.context or {})
    next_context.update(
        {
            "global_assets": global_assets or {},
            "scene_packages": scene_packages or [],
            "scene_asset_failures": list(failed_assets or []),
            "creation_contract": creation_contract,
            "scene_package_stage": "generate_scene_assets",
        }
    )
    await store.update_conversation(
        cid,
        user_id=user_id or conversation.user_id,
        last_phase="scene_asset_generation_running",
        context=next_context,
    )
    return True


def scene_assets_should_persist(result: dict[str, Any]) -> bool:
    if result.get("quota_insufficient") and not _has_any_image_url(
        result.get("global_assets") if isinstance(result.get("global_assets"), dict) else {},
        result.get("scene_packages") if isinstance(result.get("scene_packages"), list) else [],
    ):
        return False
    return _has_any_image_url(
        result.get("global_assets") if isinstance(result.get("global_assets"), dict) else {},
        result.get("scene_packages") if isinstance(result.get("scene_packages"), list) else [],
    )


def build_scene_assets_artifact(result: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    packages = {
        "ok": bool(result.get("ok")),
        "message": str(result.get("message") or ""),
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": int(result.get("target_duration_ms") or 30_000),
        "global_assets": result.get("global_assets") or {},
        "scene_packages": result.get("scene_packages") or [],
        "creation_contract": result.get("creation_contract"),
    }
    return {
        "type": "video_scene_packages",
        "title": "视频场景包",
        "description": f"{len(packages['scene_packages'])} 个场景片段，参考图本批已落库，可查看历史。",
        "actionLabel": "确认",
        "videoScenePackages": packages,
        "originalVideoScenePackages": packages,
        "sceneAssetFailures": list(result.get("failed_assets") or []),
        "sceneAssetsGenerating": False,
        "sceneAssetsAwaitingModel": False,
        "intent": "video",
        "mediaResultKind": "scene_assets",
        "mediaResultJobId": job_id,
    }


def build_scene_videos_artifact(result: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    generated = {
        "ok": bool(result.get("ok")),
        "endpoint": str(result.get("endpoint") or "/api/video/reference-mode-video"),
        "scene_videos": list(result.get("scene_videos") or []),
        "failed_scenes": list(result.get("failed_scenes") or []),
        "message": str(result.get("message") or ""),
        "quota_insufficient": bool(result.get("quota_insufficient")),
    }
    return {
        "type": "video_result",
        "title": "分镜视频",
        "description": "本批分镜视频已生成，可在历史中查看。",
        "actionLabel": "查看",
        "generatedSceneVideos": generated,
        "intent": "video",
        "mediaResultKind": "scene_videos",
        "mediaResultJobId": job_id,
    }


def build_merge_video_artifact(result: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    merged = {
        "ok": bool(result.get("ok")),
        "endpoint": str(result.get("endpoint") or "/api/video/merge"),
        "merged_video_url": result.get("merged_video_url"),
        "task_id": result.get("task_id"),
        "scene_videos": list(result.get("scene_videos") or []),
        "error": result.get("error"),
        "message": str(result.get("message") or ""),
        "quota_insufficient": bool(result.get("quota_insufficient")),
    }
    generated = {
        "ok": True,
        "endpoint": "/api/video/reference-mode-video",
        "scene_videos": [
            {
                "scene_id": str(item.get("scene_id") or ""),
                "scene_index": item.get("scene_index"),
                "video_url": str(item.get("video_url") or ""),
            }
            for item in (result.get("scene_videos") or [])
            if isinstance(item, dict) and str(item.get("video_url") or "").strip()
        ],
        "failed_scenes": [],
        "message": "合并所用分镜视频。",
    }
    return {
        "type": "video_result",
        "title": "合成视频",
        "description": "成片已生成，可在历史中查看。",
        "actionLabel": "查看",
        "mergedVideo": merged,
        "generatedSceneVideos": generated,
        "intent": "video",
        "mediaResultKind": "merge_video",
        "mediaResultJobId": job_id,
    }


def build_image_result_artifact(result: dict[str, Any], *, job_id: str, kind: MediaResultKind | str) -> dict[str, Any]:
    return {
        "type": "image_result",
        "title": "图片生成结果" if kind == "image_generate" else "图片编辑结果",
        "description": "本批图片已落库，可在历史中查看。",
        "actionLabel": "查看",
        "imageResult": result,
        "intent": "image",
        "mediaResultKind": kind,
        "mediaResultJobId": job_id,
    }


def scene_videos_should_persist(result: dict[str, Any]) -> bool:
    videos = result.get("scene_videos")
    items = [item.model_dump() if hasattr(item, "model_dump") else item for item in (videos or [])]
    return _has_scene_video_urls([item for item in items if isinstance(item, dict)])


def merge_video_should_persist(result: dict[str, Any]) -> bool:
    return bool(str(result.get("merged_video_url") or "").strip())


def image_result_should_persist(result: dict[str, Any]) -> bool:
    images = result.get("images")
    if isinstance(images, list) and any(
        isinstance(item, dict) and str(item.get("url") or item.get("image_url") or "").strip() for item in images
    ):
        return True
    fused = result.get("fused_image") or result.get("edited_image")
    if isinstance(fused, dict) and str(fused.get("url") or fused.get("image_url") or "").strip():
        return True
    return bool(str(result.get("image_url") or "").strip())
