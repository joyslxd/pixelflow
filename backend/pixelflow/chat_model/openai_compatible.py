"""提供 OpenAI-compatible HTTP Provider Client，不依赖 LangChain。"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .contracts import ChatCompletion, ChatModelRequest


class ChatModelProviderError(RuntimeError):
    """表示模型网络、鉴权或协议失败，不回显下游正文。"""


@dataclass(frozen=True, slots=True)
class OpenAICompatibleModelSettings:
    """冻结单个 Provider 的端点与模型配置；API key 只在内存中短暂保存。"""

    base_url: str
    api_key: str
    timeout_seconds: float = 60

    def __post_init__(self) -> None:
        """拒绝不安全端点、空凭据与不可控超时。"""

        normalized = self.base_url.rstrip("/")
        if not normalized.startswith("https://") and not normalized.startswith("http://127.0.0.1:"):
            raise ValueError("模型 Provider 地址必须使用 HTTPS，测试仅允许 loopback HTTP")
        if not self.api_key.strip():
            raise ValueError("模型 Provider 凭据不能为空")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise ValueError("模型 Provider 超时必须在 0 到 300 秒之间")


class OpenAICompatibleChatModelClient:
    """类似 Feign Client：仅调用 `/chat/completions` 并映射稳定 DTO。"""

    def __init__(
        self,
        settings: OpenAICompatibleModelSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """注入固定 Provider 配置；外部传入 Client 时不接管其关闭生命周期。"""

        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.timeout_seconds)

    async def complete(self, request: ChatModelRequest) -> ChatCompletion:
        """调用兼容接口，并严格提取首个文本 choice。"""

        payload: dict[str, object] = {
            "model": request.model,
            "messages": [message.model_dump() for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        try:
            response = await self._client.post(
                f"{self._settings.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                json=payload,
            )
        except httpx.HTTPError as error:
            raise ChatModelProviderError("模型 Provider 网络请求失败") from error
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ChatModelProviderError("模型 Provider 请求被拒绝或不可用")
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("模型回复为空")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ValueError("结束原因格式无效")
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise ChatModelProviderError("模型 Provider 响应协议无效") from error
        return ChatCompletion(
            model=request.model,
            content=content.strip(),
            finish_reason=finish_reason,
        )

    async def aclose(self) -> None:
        """关闭自建 HTTP 连接池，避免 Gateway 关闭后泄漏连接。"""

        if self._owns_client:
            await self._client.aclose()


__all__ = ["ChatModelProviderError", "OpenAICompatibleChatModelClient", "OpenAICompatibleModelSettings"]
