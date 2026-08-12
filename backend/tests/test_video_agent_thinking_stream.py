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


@pytest.mark.asyncio
async def test_intake_thinking_always_uses_llm_not_local_script_copy() -> None:
    """成熟长脚本也必须走真 LLM 思考，禁止本地拼「从时间码粗看」类文案。"""

    from datetime import UTC, datetime

    from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
    from pixelflow.video_agent.thinking_stream import (
        ThinkingStreamPublisher,
        stream_intake_thinking,
    )

    script = (
        "0—10秒｜开场\n【剧情/动作】安然盯着手机。\n【新增对白】安然：如果失败呢？\n"
        "10—20秒｜转折\n【剧情/动作】Yann退到镜头外。\n【新增对白】Yann：选择你来做。\n"
        "170—180秒｜收束\n【剧情/动作】字幕结束。\n【新增对白】安然：准备好了。\n"
    ) * 8
    merged = f"{script}\n\n【本轮指令】180s 9:16 结尾不变"

    class _ThinkingModel:
        async def astream(self, messages):  # noqa: ANN001
            joined = str(messages)
            assert "【本轮指令】" in joined
            assert "180s" in joined
            assert "【workspace_digest】" in joined
            assert "【blocking_confirmation】" in joined
            yield SimpleNamespace(
                content=(
                    "这是补字段跟进，总时长按本轮180秒，画幅9:16，结尾沿用脚本。\n"
                    "<<<INTAKE_PLAN>>>\n"
                    '{"answer":"这是补字段跟进，总时长按本轮180秒，画幅9:16，结尾沿用脚本。",'
                    '"needs_user_reply":false,"missing":[],'
                    '"facts":{"duration_sec":180,"aspect_ratio":"9:16","ending_cta":"keep","intent":"polish"},'
                    '"public_goal":"这是补字段跟进，总时长按本轮180秒，画幅9:16，结尾沿用脚本。",'
                    '"steps":[{"tool_name":"import_script","title":"导入脚本","arguments":{}}]}\n'
                    "<<<END>>>"
                ),
                additional_kwargs={"reasoning_content": "先核对本轮补丁与脚本时间码。"},
            )

    def factory(**_kwargs):  # noqa: ANN003
        return _ThinkingModel()

    repo = MemoryAgentRuntimeRepository()
    publisher = ThinkingStreamPublisher(
        repository=repo,
        user_id="u1",
        conversation_id="c1",
        turn_id="t1",
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    result = await stream_intake_thinking(
        publisher=publisher,
        content=merged,
        workspace_digest={"has_script": True, "missing_requirements": []},
        blocking_confirmation=None,
        model_factory=factory,
    )
    events = await repo.list_events("u1", "c1")
    reasoning = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value.endswith("delta")
        and event.payload.get("channel") == "reasoning"
    )
    answer = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value.endswith("delta")
        and event.payload.get("channel") == "answer"
    )
    assert "正在核对工作区状态" in reasoning
    assert "正在检查生成前置条件" in reasoning
    assert "先核对本轮补丁" not in reasoning
    assert "补字段跟进" in answer
    assert "INTAKE_PLAN" not in answer
    assert "INTAKE_VERDICT" not in answer
    assert "补字段跟进" not in reasoning
    assert "从时间码粗看" not in reasoning
    assert "已识别为较完整的分镜" not in reasoning
    assert "已识别时长：10秒" not in reasoning
    assert result.entry_path == "polish"
    assert result.duration_sec == 180
    assert result.aspect_ratio == "9:16"
    assert result.needs_user_reply is False
    assert not hasattr(result, "steps")


@pytest.mark.asyncio
async def test_intake_streams_safe_ndjson_progress_and_parses_terminal_result() -> None:
    from datetime import UTC, datetime

    from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
    from pixelflow.video_agent.thinking_stream import (
        ThinkingStreamPublisher,
        stream_intake_thinking,
    )

    class _NdjsonModel:
        async def astream(self, _messages):  # noqa: ANN001
            yield SimpleNamespace(
                content='{"type":"progress","message":"已识别现有脚本，正在核对生产字段。"}\n'
                '{"type":"progress","message":"正在检查分镜资产包是否完整。"',
                additional_kwargs={"reasoning_content": "系统要求我先复述提示词和规则。"},
            )
            yield SimpleNamespace(
                content='}\n'
                '{"type":"progress","message":"正在检查分镜资产包是否完整。"}\n'
                '{"type":"progress","message":"系统提示词规定必须调用 import_script。"}\n',
                additional_kwargs={},
            )
            yield SimpleNamespace(
                content='{"type":"result","data":{"answer":"可以进入分镜检查。",'
                '"intent":"continue_assets","target_capability":"inspect_storyboard",'
                '"readiness":"inspect_required","current_state":'
                '{"script_available":true,"script_confirmed":true,'
                '"storyboard_available":true},"missing":[],"facts":'
                '{"scene_ids":["scene-2"]},"constraints":'
                '{"requires_visual_inspection":true}}}',
                additional_kwargs={},
            )

    repo = MemoryAgentRuntimeRepository()
    result = await stream_intake_thinking(
        publisher=ThinkingStreamPublisher(
            repository=repo,
            user_id="u1",
            conversation_id="c-ndjson",
            turn_id="t-ndjson",
            clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        ),
        content="检查一下现有分镜",
        model_factory=lambda **_kwargs: _NdjsonModel(),
    )

    events = await repo.list_events("u1", "c-ndjson")
    visible_reasoning = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value.endswith("delta")
        and event.payload.get("channel") == "reasoning"
    )
    assert "已识别现有脚本，正在核对生产字段。" in visible_reasoning
    assert visible_reasoning.count("正在检查分镜资产包是否完整。") == 1
    assert "系统要求我" not in visible_reasoning
    assert "系统提示词" not in visible_reasoning
    assert result.user_message == "可以进入分镜检查。"
    assert result.target_capability == "inspect_storyboard"
    assert result.readiness == "inspect_required"
    assert result.scene_ids == ("scene-2",)
    assert result.constraints == {"requires_visual_inspection": True}


@pytest.mark.asyncio
async def test_intake_thinking_skips_answer_channel_when_needs_user_reply() -> None:
    """缺字段追问只落 Plan 卡，不写 answer 气泡。"""

    from datetime import UTC, datetime

    from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
    from pixelflow.video_agent.thinking_stream import (
        ThinkingStreamPublisher,
        stream_intake_thinking,
    )

    class _AskModel:
        async def astream(self, _messages):  # noqa: ANN001
            yield SimpleNamespace(
                content=(
                    "缺少画幅与结尾行动引导，请补充。\n"
                    "<<<INTAKE_VERDICT>>>\n"
                    '{"entry_path":"polish","missing":["视频画幅","结尾行动引导"],'
                    '"duration_sec":180,"needs_user_reply":true}\n'
                    "<<<END>>>"
                ),
                additional_kwargs={"reasoning_content": "先核缺字段。"},
            )

    def factory(**_kwargs):  # noqa: ANN003
        return _AskModel()

    repo = MemoryAgentRuntimeRepository()
    publisher = ThinkingStreamPublisher(
        repository=repo,
        user_id="u1",
        conversation_id="c-waiting",
        turn_id="t-waiting",
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    result = await stream_intake_thinking(
        publisher=publisher,
        content="# 剧本\n### 镜头 01\n- **时间**：0-10秒\n",
        workspace_digest=None,
        blocking_confirmation=None,
        model_factory=factory,
    )
    events = await repo.list_events("u1", "c-waiting")
    answer = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value.endswith("delta")
        and event.payload.get("channel") == "answer"
    )
    assert result.needs_user_reply is True
    assert answer == ""
    assert "缺少画幅" in result.user_message


@pytest.mark.asyncio
async def test_intake_thinking_content_only_goes_to_answer_channel() -> None:
    """模型未返回 reasoning 时，公开区仍只展示服务端安全进度。"""

    from datetime import UTC, datetime

    from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
    from pixelflow.video_agent.thinking_stream import (
        ThinkingStreamPublisher,
        stream_intake_thinking,
    )

    class _ContentOnlyModel:
        async def astream(self, messages):  # noqa: ANN001, ARG002
            yield SimpleNamespace(
                content="已识别为短补丁，继续规划。",
                additional_kwargs={},
            )

    repo = MemoryAgentRuntimeRepository()
    publisher = ThinkingStreamPublisher(
        repository=repo,
        user_id="u1",
        conversation_id="c1",
        turn_id="t2",
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    await stream_intake_thinking(
        publisher=publisher,
        content="9:16",
        model_factory=lambda **_kwargs: _ContentOnlyModel(),
    )
    events = await repo.list_events("u1", "c1")
    reasoning = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value.endswith("delta")
        and event.payload.get("channel") == "reasoning"
    )
    answer = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value.endswith("delta")
        and event.payload.get("channel") == "answer"
    )
    assert reasoning == "正在核对工作区状态。正在检查生成前置条件。"
    assert answer == "已识别为短补丁，继续规划。"
    assert answer.count("已识别为短补丁") == 1


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
