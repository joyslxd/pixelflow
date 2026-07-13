"""Gateway helpers for PixelFlow semantic memory."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import Request

from app.gateway.deps import get_current_user
from pixelflow.memory import PowerMemService, SemanticMemoryItem, build_memory_query

logger = logging.getLogger(__name__)

_SKILL_MEMORY_SOURCE_PREFIXES = ("image_", "video_", "ppt_")


def power_mem_service(request: Request) -> PowerMemService | None:
    service = getattr(request.app.state, "pixelflow_power_mem_service", None)
    return service if isinstance(service, PowerMemService) or service is not None else None


async def current_user_id(request: Request) -> str | None:
    try:
        return await get_current_user(request)
    except Exception as exc:
        logger.debug(
            "Unable to resolve current user for PowerMem exception_type=%s",
            type(exc).__name__,
        )
        return None


async def search_power_mem(
    request: Request,
    *,
    source_agent: str,
    query_values: list[Any],
    categories: list[str] | None = None,
    limit: int | None = None,
) -> tuple[str | None, list[SemanticMemoryItem]]:
    user_id = await current_user_id(request)
    service = power_mem_service(request)
    if service is None:
        return user_id, []
    query = build_memory_query(*query_values)
    if not query:
        return user_id, []
    items = await service.search(
        user_id=user_id,
        query=query,
        categories=categories or ["preference", "brand", "skill", "experience"],
        source_agent=None,
        limit=limit,
    )
    return user_id, items


async def record_power_mem(
    service: Any,
    *,
    user_id: str | None,
    content: str,
    category: str,
    source_agent: str,
    metadata: dict[str, Any] | None = None,
    memory_type: str | None = None,
    run_id: str | None = None,
    infer: bool | None = None,
) -> bool:
    if service is None:
        return False
    if not hasattr(service, "record"):
        return False
    return await service.record(
        user_id=user_id,
        content=content,
        category=category,
        source_agent=source_agent,
        metadata=metadata or {},
        memory_type=memory_type,
        run_id=run_id,
        infer=_resolve_power_mem_infer(category, infer),
    )


def record_power_mem_background(
    service: Any,
    *,
    user_id: str | None,
    content: str,
    category: str,
    source_agent: str,
    metadata: dict[str, Any] | None = None,
    memory_type: str | None = None,
    run_id: str | None = None,
    infer: bool | None = None,
) -> None:
    if service is None or not content.strip():
        return

    async def _run() -> None:
        try:
            await record_power_mem(
                service,
                user_id=user_id,
                content=content,
                category=category,
                source_agent=source_agent,
                metadata=metadata,
                memory_type=memory_type,
                run_id=run_id,
                infer=infer,
            )
            if _should_record_skill_memory(category, metadata):
                skill_metadata = {"linked_category": category, **(metadata or {})}
                await record_power_mem(
                    service,
                    user_id=user_id,
                    content=f"可复用 Skill 经验：{content}",
                    category="skill",
                    source_agent=source_agent,
                    metadata=skill_metadata,
                    memory_type="skill",
                    run_id=run_id,
                    infer=False,
                )
        except Exception as exc:
            logger.warning(
                "PowerMem background record failed exception_type=%s",
                type(exc).__name__,
            )

    coroutine = _run()
    try:
        create_background_task = getattr(service, "create_background_task", None)
        if callable(create_background_task):
            create_background_task(coroutine)
        else:
            # 测试 fake service 与旧的轻量实现没有生命周期调度器，保留兼容路径。
            asyncio.create_task(coroutine)
    except Exception as exc:
        coroutine.close()
        logger.warning(
            "PowerMem background record scheduling failed exception_type=%s",
            type(exc).__name__,
        )


def concise_result_summary(prefix: str, payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    parts = [prefix]
    for key in ("intent", "method", "mode", "stage", "endpoint", "message"):
        value = payload.get(key)
        if value:
            parts.append(f"{key}={value}")
    if "ok" in payload:
        parts.append(f"ok={bool(payload.get('ok'))}")
    if "quota_insufficient" in payload:
        parts.append(f"quota_insufficient={bool(payload.get('quota_insufficient'))}")
    return "；".join(parts)[:1500]


def _should_record_skill_memory(category: str, metadata: dict[str, Any] | None) -> bool:
    if category != "experience" or not metadata:
        return False
    source = str(metadata.get("source") or "")
    if source.startswith(_SKILL_MEMORY_SOURCE_PREFIXES):
        return True
    return any(key in metadata for key in ("endpoint", "method", "mode"))


def _resolve_power_mem_infer(category: str, infer: bool | None) -> bool:
    if infer is not None:
        return infer
    return category == "preference"
