"""定义领域 Service 可依赖的稳定聊天模型 DTO。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """拒绝未知字段，避免模型 Provider 参数越过稳定领域边界。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatMessage(_StrictModel):
    """表示发送给模型的一条已清洗消息。"""

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=64_000)


class ChatModelRequest(_StrictModel):
    """表示一次 OpenAI-compatible 文本生成请求，不包含用户授权或业务副作用。"""

    model: str = Field(min_length=1, max_length=160)
    messages: tuple[ChatMessage, ...] = Field(min_length=1, max_length=64)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=131_072)
    response_format: Literal["text", "json_object"] = "text"


class ChatCompletion(_StrictModel):
    """Provider 响应的安全最小投影，禁止保留原始响应体或凭据。"""

    model: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=128_000)
    finish_reason: str | None = Field(default=None, max_length=80)


__all__ = ["ChatCompletion", "ChatMessage", "ChatModelRequest"]
