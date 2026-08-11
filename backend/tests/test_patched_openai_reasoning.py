"""PatchedChatOpenAIReasoning 从流式 delta 提取 reasoning_content。"""

from __future__ import annotations

from deerflow.models.patched_openai_reasoning import PatchedChatOpenAIReasoning
from langchain_core.messages import AIMessageChunk


def test_convert_chunk_keeps_reasoning_content() -> None:
    model = PatchedChatOpenAIReasoning(model="deepseek-v4-pro", api_key="test")
    generation = model._convert_chunk_to_generation_chunk(
        {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "先看时长再看画幅",
                    },
                    "index": 0,
                }
            ],
        },
        AIMessageChunk,
        None,
    )
    assert generation is not None
    assert generation.message.additional_kwargs.get("reasoning_content") == "先看时长再看画幅"
