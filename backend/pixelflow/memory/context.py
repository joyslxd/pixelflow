"""Helpers for injecting semantic memory into PixelFlow flow context."""

from __future__ import annotations

import json
from typing import Any

from pixelflow.memory.service import SemanticMemoryItem

MAX_MEMORY_SNIPPETS = 5
MAX_MEMORY_CHARS = 900


def memory_context_payload(items: list[SemanticMemoryItem]) -> dict[str, Any]:
    snippets = [item.to_dict() for item in items[:MAX_MEMORY_SNIPPETS]]
    return {
        "enabled": bool(snippets),
        "provider": "powermem",
        "items": snippets,
        "summary": semantic_memory_text(items),
    }


def semantic_memory_text(items: list[SemanticMemoryItem] | dict[str, Any] | None) -> str:
    if not items:
        return ""
    if isinstance(items, dict):
        raw_items = items.get("items")
        if not isinstance(raw_items, list):
            summary = items.get("summary")
            return str(summary or "").strip()
        parts = [str(item.get("content") or "").strip() for item in raw_items if isinstance(item, dict)]
    else:
        parts = [item.content.strip() for item in items if item.content.strip()]
    text = "；".join(part for part in parts if part)
    return text[:MAX_MEMORY_CHARS]


def with_semantic_memory(
    intake_context: dict[str, Any] | None,
    items: list[SemanticMemoryItem],
    *,
    product_creative_profile: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    context = dict(intake_context or {})
    profile = dict(product_creative_profile or context.get("product_creative_profile") or {})
    payload = memory_context_payload(items)
    if payload["items"]:
        context["semantic_memory"] = payload
        profile["semantic_memory"] = payload
        context["product_creative_profile"] = profile
    return context, profile


def build_memory_query(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        parts.extend(_collect_text(value))
    return "\n".join(part for part in parts if part).strip()[:4000]


def _collect_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        collected: list[str] = []
        for key, item in value.items():
            if str(key).lower() in {"authorization", "token", "api_key", "apikey", "password", "secret"}:
                continue
            collected.extend(_collect_text(item))
        return collected
    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_collect_text(item))
        return collected
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except TypeError:
        encoded = str(value)
    return [encoded] if encoded.strip() else []
