"""验证 M1 自有 ChatModelPort 的 OpenAI-compatible 协议与脱敏边界。"""

from __future__ import annotations

import json

import httpx
import pytest

from pixelflow.chat_model import (
    ChatMessage,
    ChatModelRequest,
    OpenAICompatibleChatModelClient,
    OpenAICompatibleModelSettings,
)
from pixelflow.chat_model.openai_compatible import ChatModelProviderError


@pytest.mark.asyncio
async def test_openai_compatible_client_maps_stable_completion() -> None:
    """Client 必须发送最小请求并只返回安全的首个文本结果。"""

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  生成完成  "}, "finish_reason": "stop"}]},
        )

    client = OpenAICompatibleChatModelClient(
        OpenAICompatibleModelSettings("http://127.0.0.1:9000/v1", "test-secret"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await client.complete(
        ChatModelRequest(
            model="test-model",
            messages=(ChatMessage(role="user", content="生成文案"),),
            response_format="json_object",
        ),
    )

    assert result.content == "生成完成"
    assert seen["authorization"] == "Bearer test-secret"
    assert seen["payload"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "生成文案"}],
        "response_format": {"type": "json_object"},
    }


@pytest.mark.asyncio
async def test_openai_compatible_client_rejects_provider_body() -> None:
    """Provider 失败不得把原始响应正文传播给领域 Service。"""

    client = OpenAICompatibleChatModelClient(
        OpenAICompatibleModelSettings("http://127.0.0.1:9000/v1", "test-secret"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500, text="provider-secret"))),
    )

    with pytest.raises(ChatModelProviderError, match="请求被拒绝或不可用"):
        await client.complete(
            ChatModelRequest(model="test-model", messages=(ChatMessage(role="user", content="测试"),)),
        )
