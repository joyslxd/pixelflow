"""真 LLM thinking 流：chunk 解析与模型流式聚合。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pixelflow.video_agent.thinking_stream import _chunk_text, stream_chat_tokens


def test_fold_thinking_history_from_events_rebuilds_transcript() -> None:
    from types import SimpleNamespace

    from pixelflow.agent_runtime.contracts import AgentEventType
    from pixelflow.video_agent.thinking_stream import fold_thinking_history_from_events

    events = [
        SimpleNamespace(
            type=AgentEventType.AGENT_THINKING_STARTED,
            payload={
                "turn_id": "turn-1",
                "title": "正在判断…",
                "subtitle": "AI 编剧思考中…",
                "started_at": "2026-08-11T13:00:00Z",
            },
        ),
        SimpleNamespace(
            type=AgentEventType.AGENT_THINKING_DELTA,
            payload={"turn_id": "turn-1", "delta": "先看脚本", "channel": "reasoning"},
        ),
        SimpleNamespace(
            type=AgentEventType.AGENT_THINKING_DELTA,
            payload={"turn_id": "turn-1", "delta": "是否完整", "channel": "reasoning"},
        ),
        SimpleNamespace(
            type=AgentEventType.AGENT_THINKING_DELTA,
            payload={"turn_id": "turn-1", "delta": "可以导入。", "channel": "answer"},
        ),
        SimpleNamespace(
            type=AgentEventType.AGENT_THINKING_COMPLETED,
            payload={"turn_id": "turn-1"},
        ),
    ]
    history = fold_thinking_history_from_events(events)
    assert len(history) == 1
    assert history[0]["turn_id"] == "turn-1"
    assert history[0]["text"] == "先看脚本是否完整"
    assert history[0]["answer"] == "可以导入。"
    assert history[0]["status"] == "completed"


def test_fold_thinking_history_includes_native_reasoning_and_response() -> None:
    from pixelflow.agent_runtime.contracts import AgentEventType
    from pixelflow.video_agent.thinking_stream import fold_thinking_history_from_events

    events = [
        SimpleNamespace(
            type=AgentEventType.AGENT_REASONING_SUMMARY_DELTA,
            occurred_at="2026-08-13T00:42:00Z",
            payload={"turn_id": "turn-native", "delta": "先导入"},
        ),
        SimpleNamespace(
            type=AgentEventType.AGENT_REASONING_SUMMARY_COMPLETED,
            occurred_at="2026-08-13T00:42:10Z",
            payload={"turn_id": "turn-native", "summary": "先导入再补字段"},
        ),
        SimpleNamespace(
            type=AgentEventType.AGENT_RESPONSE_COMPLETED,
            occurred_at="2026-08-13T00:43:00Z",
            payload={"turn_id": "turn-native", "text": "请确认画幅与 CTA"},
        ),
    ]
    history = fold_thinking_history_from_events(events)
    assert len(history) == 1
    assert history[0]["turn_id"] == "turn-native"
    assert history[0]["text"] == "先导入再补字段"
    assert history[0]["answer"] == "请确认画幅与 CTA"
    assert history[0]["status"] == "completed"


def test_parse_intake_allows_script_plan_confirm_missing() -> None:
    from pixelflow.video_agent.thinking_stream import _parse_intake_answer_and_verdict

    result = _parse_intake_answer_and_verdict(
        "工作区已有脚本，请先在右侧确认方案。\n"
        "<<<INTAKE_CONTEXT>>>\n"
        '{"answer":"工作区已有脚本，请先在右侧确认方案。",'
        '"needs_user_reply":true,"missing":["脚本方案确认"],'
        '"facts":{"intent":"continue_assets"}}\n'
        "<<<END>>>"
    )
    assert result.needs_user_reply is True
    assert result.missing_requirements == ("脚本方案确认",)
    assert result.intent == "continue_assets"
    assert result.entry_path == "continue"


def test_parse_intake_preserves_controlled_state_diagnosis_from_bare_json() -> None:
    from pixelflow.video_agent.thinking_stream import _parse_intake_answer_and_verdict

    result = _parse_intake_answer_and_verdict(
        '{"answer":"脚本已确认，可以检查分镜资产包。",'
        '"intent":"continue_assets",'
        '"target_capability":"inspect_storyboard",'
        '"readiness":"inspect_required",'
        '"current_state":{"script_available":true,"script_confirmed":true,'
        '"storyboard_available":true,"scene_packages_available":true,'
        '"scene_assets_available":false,"scene_videos_available":false,'
        '"final_video_available":false,"invented_state":true},'
        '"missing":[],"facts":{"scene_ids":["scene-2","scene-2","",42]},'
        '"constraints":{"dirty_scene_only":true,'
        '"requires_visual_inspection":true,"unsafe":"ignored"}}'
    )

    assert result.user_message == "脚本已确认，可以检查分镜资产包。"
    assert result.target_capability == "inspect_storyboard"
    assert result.readiness == "inspect_required"
    assert result.current_state == {
        "script_available": True,
        "script_confirmed": True,
        "storyboard_available": True,
        "scene_packages_available": True,
        "scene_assets_available": False,
        "scene_videos_available": False,
        "final_video_available": False,
    }
    assert result.scene_ids == ("scene-2",)
    assert result.constraints == {
        "dirty_scene_only": True,
        "requires_visual_inspection": True,
    }
    digest = result.as_planner_digest()
    assert digest["target_capability"] == "inspect_storyboard"
    assert digest["readiness"] == "inspect_required"
    assert digest["scene_ids"] == ["scene-2"]
    assert "invented_state" not in digest["current_state"]
    assert "unsafe" not in digest["constraints"]


def test_intake_system_prompt_encodes_state_diagnosis_not_tool_selection() -> None:
    from pixelflow.video_agent import thinking_stream as mod

    prompt = mod._INTAKE_SYSTEM_PROMPT
    assert "允许的目标能力" in prompt
    assert "prepare_scene_packages" in prompt
    assert "inspect_storyboard" in prompt
    assert "inspect_scene_results" in prompt
    assert "target_capability" in prompt
    assert "readiness" in prompt
    assert "current_state" in prompt
    assert "scene_ids" in prompt
    assert "constraints" in prompt
    assert "不选择 Tool，不输出 steps" in prompt
    assert "不要从固定阶段顺序反推意图" in prompt
    assert '"type":"progress"' in prompt
    assert '"type":"result"' in prompt
    assert "每条记录独占一行" in prompt
    assert "不得输出内部推理" in prompt


def test_parse_intake_answer_and_verdict_strips_machine_block() -> None:
    from pixelflow.video_agent.thinking_stream import _parse_intake_answer_and_verdict

    result = _parse_intake_answer_and_verdict(
        "脚本已收到，请补充画幅与结尾引导。\n"
        "<<<INTAKE_VERDICT>>>\n"
        '{"entry_path":"polish","missing":["视频画幅","结尾行动引导"],'
        '"duration_sec":180,"needs_user_reply":true}\n'
        "<<<END>>>"
    )
    assert result.user_message == "脚本已收到，请补充画幅与结尾引导。"
    assert result.entry_path == "polish"
    assert result.missing_requirements == ("视频画幅", "结尾行动引导")
    assert result.duration_sec == 180
    assert result.needs_user_reply is True
    assert not hasattr(result, "steps")


def test_parse_intake_context_ignores_tool_like_steps() -> None:
    from pixelflow.video_agent.thinking_stream import _parse_intake_answer_and_verdict

    answer = "字段已齐，先导入成稿再确认。"
    result = _parse_intake_answer_and_verdict(
        f"{answer}\n"
        "<<<INTAKE_PLAN>>>\n"
        "{"
        f'"answer":"{answer}",'
        '"needs_user_reply":false,'
        '"missing":[],'
        '"facts":{"duration_sec":180,"aspect_ratio":"9:16","ending_cta":"keep","intent":"polish"},'
        f'"public_goal":"{answer}",'
        '"steps":[{"tool_name":"import_script","title":"导入脚本","arguments":{}}]'
        "}\n"
        "<<<END>>>"
    )
    assert result.user_message == answer
    assert result.entry_path == "polish"
    assert result.intent == "polish"
    assert result.aspect_ratio == "9:16"
    assert result.ending_cta == "keep"
    assert result.duration_sec == 180
    assert result.needs_user_reply is False
    assert not hasattr(result, "steps")
    digest = result.as_planner_digest()
    assert digest["public_goal"] == answer
    assert "steps" not in digest

def test_chunk_text_reads_reasoning_and_content() -> None:
    chunk = SimpleNamespace(
        content="正文",
        additional_kwargs={"reasoning_content": "思考"},
    )
    reasoning, content = _chunk_text(chunk)
    assert reasoning == "思考"
    assert content == "正文"


def test_create_streaming_chat_model_forces_thinking_extra_body() -> None:
    """入场思考必须把 thinking.enabled 传给模型工厂，而不是 stream_chat_tokens。"""

    from pixelflow.video_agent.thinking_stream import _create_streaming_chat_model

    captured: dict = {}

    def factory(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return SimpleNamespace(astream=None, streaming=False)

    model = _create_streaming_chat_model(factory, thinking_enabled=True)
    assert captured.get("name") == "deepseek-v4-pro"
    assert captured.get("thinking_enabled") is True
    assert captured.get("streaming") is True
    assert captured.get("extra_body") == {"thinking": {"type": "enabled"}}
    assert captured.get("reasoning_effort") == "low"
    # LangChain 实例 streaming=False 会让 astream 退化成非流式；创建后必须强制 True。
    assert getattr(model, "streaming", None) is True


def test_chunk_text_reads_thinking_blocks() -> None:
    chunk = SimpleNamespace(
        content=[{"type": "thinking", "thinking": "嗯"}, {"type": "text", "text": "答"}],
        additional_kwargs={},
    )
    reasoning, content = _chunk_text(chunk)
    assert reasoning == "嗯"
    assert content == "答"


def test_thinking_event_id_fits_runtime_limit() -> None:
    from pixelflow.video_agent.thinking_stream import _thinking_event_id

    event_id = _thinking_event_id(
        "turn_" + ("a" * 80),
        "started",
    )
    assert len(event_id) <= 64
    assert event_id.startswith("thk_")


@pytest.mark.asyncio
async def test_stream_chat_tokens_passes_stream_true() -> None:
    """astream 必须显式 stream=True，避免上游收到 stream=false。"""

    seen_kwargs: dict = {}

    class _StreamingModel:
        async def astream(self, messages, **kwargs):  # noqa: ANN001, ANN003
            seen_kwargs.update(kwargs)
            del messages
            yield SimpleNamespace(content="ok", additional_kwargs={})

    reasoning, content = await stream_chat_tokens(
        model=_StreamingModel(),
        messages=[("human", "hi")],
        timeout_sec=5,
    )
    assert seen_kwargs.get("stream") is True
    assert content == "ok"
    assert reasoning == ""


@pytest.mark.asyncio
async def test_stream_chat_tokens_accumulates() -> None:
    seen: list[str] = []

    async def on_content(delta: str) -> None:
        seen.append(delta)

    reasoning, content = await stream_chat_tokens(
        model=_FakeModel(),
        messages=[("human", "hi")],
        on_content=on_content,
        timeout_sec=5,
    )
    assert reasoning == "想"
    assert content == "Hello"
    assert seen == ["Hel", "lo"]


@pytest.mark.asyncio
async def test_stream_chat_tokens_uses_astream_not_invoke() -> None:
    """确保走 astream 增量回调，而不是一次性 invoke。"""

    class _StreamingModel:
        def __init__(self) -> None:
            self.invoke_calls = 0
            self.astream_calls = 0
            self.stream_kw: bool | None = None

        def invoke(self, messages):  # noqa: ANN001, ARG002
            self.invoke_calls += 1
            return SimpleNamespace(content="整段", additional_kwargs={})

        async def astream(self, messages, **kwargs):  # noqa: ANN001, ANN003
            self.astream_calls += 1
            self.stream_kw = kwargs.get("stream")
            yield SimpleNamespace(content="一", additional_kwargs={})
            yield SimpleNamespace(content="二", additional_kwargs={})

    model = _StreamingModel()
    seen: list[str] = []

    async def on_content(delta: str) -> None:
        seen.append(delta)

    reasoning, content = await stream_chat_tokens(
        model=model,
        messages=[("human", "hi")],
        on_content=on_content,
        timeout_sec=5,
    )
    assert model.astream_calls == 1
    assert model.invoke_calls == 0
    assert model.stream_kw is True
    assert content == "一二"
    assert seen == ["一", "二"]
    assert reasoning == ""


class _FakeModel:
    async def astream(self, messages, **kwargs):  # noqa: ANN001, ANN003
        del messages, kwargs
        yield SimpleNamespace(content="Hel", additional_kwargs={})
        yield SimpleNamespace(content="lo", additional_kwargs={"reasoning_content": "想"})


def test_parse_duration_sec_payload() -> None:
    from pixelflow.video_agent.thinking_stream import _parse_duration_sec_payload

    assert _parse_duration_sec_payload('{"duration_sec": 180}') == 180
    assert _parse_duration_sec_payload('废话\n{"duration_sec": 180}\n') == 180
    assert _parse_duration_sec_payload('{"duration_sec": null}') is None
    assert _parse_duration_sec_payload("not-json") is None


def test_truncate_for_thinking_keeps_round_instruction() -> None:
    from pixelflow.video_agent.thinking_stream import (
        THINKING_LLM_INPUT_MAX_CHARS,
        _truncate_for_thinking,
    )

    head = "分镜正文" * 800
    text = f"{head}\n\n【本轮指令】180s 9:16 结尾不变"
    truncated = _truncate_for_thinking(text, max_chars=THINKING_LLM_INPUT_MAX_CHARS)
    assert "【本轮指令】180s 9:16 结尾不变" in truncated
    assert len(truncated) <= THINKING_LLM_INPUT_MAX_CHARS + 40
