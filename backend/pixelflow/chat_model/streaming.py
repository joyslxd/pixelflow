"""框架无关的流式聊天文本消费辅助函数。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)


def extract_stream_chunk_text(chunk: Any) -> tuple[str, str]:
    """从兼容 Provider 的增量对象提取推理与公开文本。"""

    reasoning = ""
    content = ""
    kwargs = getattr(chunk, "additional_kwargs", None)
    if isinstance(kwargs, Mapping):
        raw = kwargs.get("reasoning_content")
        if isinstance(raw, str) and raw:
            reasoning = raw
    raw_content = getattr(chunk, "content", None)
    if isinstance(raw_content, str):
        content = raw_content
    elif isinstance(raw_content, list):
        parts: list[str] = []
        for part in raw_content:
            if isinstance(part, Mapping):
                kind = str(part.get("type") or "")
                if kind in {"thinking", "reasoning"}:
                    text = part.get("thinking") or part.get("text") or ""
                    if isinstance(text, str) and text:
                        reasoning += text
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            else:
                parts.append(str(part))
        content = "".join(parts)
    return reasoning, content


async def stream_chat_tokens(
    *,
    model: Any,
    messages: Sequence[Any],
    on_reasoning: Callable[[str], Awaitable[None]] | None = None,
    on_content: Callable[[str], Awaitable[None]] | None = None,
    timeout_sec: float,
) -> tuple[str, str]:
    """消费模型流式输出并返回完整推理和公开文本。"""

    reasoning_parts: list[str] = []
    content_parts: list[str] = []

    async def consume() -> None:
        astream = getattr(model, "astream", None)
        if astream is None:
            logger.warning("模型缺少 astream，流式输出退化为一次性响应")
            message = await asyncio.to_thread(model.invoke, list(messages))
            reasoning, content = extract_stream_chunk_text(message)
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning is not None:
                    await on_reasoning(reasoning)
            if content:
                content_parts.append(content)
                if on_content is not None:
                    await on_content(content)
            return
        try:
            stream_iter = astream(list(messages), stream=True)
        except TypeError:
            stream_iter = astream(list(messages))
        async for chunk in stream_iter:
            reasoning, content = extract_stream_chunk_text(chunk)
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning is not None:
                    await on_reasoning(reasoning)
            if content:
                content_parts.append(content)
                if on_content is not None:
                    await on_content(content)

    await asyncio.wait_for(consume(), timeout=timeout_sec)
    return "".join(reasoning_parts).strip(), "".join(content_parts).strip()


__all__ = ["extract_stream_chunk_text", "stream_chat_tokens"]
