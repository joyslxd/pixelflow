"""原生 Video Agent Turn invoke 合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import BaseModel, ConfigDict, PrivateAttr

from deerflow.config.memory_config import MemoryConfig
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.contracts import (
    AgentPlanStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint, video_agent_plan_id
from pixelflow.video_agent.events.publisher import NativeAgentEventPublisher
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.native_invoke import (
    NativeVideoAgentInvoker,
    NativeVideoAgentInvokeRequest,
    _looks_like_merge_videos_intent,
    _parse_generate_scenes_intent,
    _parse_scene_asset_model_confirm,
    _parse_structured_scene_asset_replacement,
    _parse_structured_scene_patch,
    _public_model_failure_message,
    _scene_patch_target_context,
    choose_public_response_text,
    strip_tool_markup,
)
from pixelflow.video_agent.runner import VideoAgentRunner, VideoAgentRunScope
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolRegistry,
    VideoToolSpec,
)
from pixelflow.video_agent.tools.scene_packages import PrepareScenePackagesInput
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository

T0 = datetime(2026, 8, 12, tzinfo=UTC)


def test_strip_tool_markup_cuts_fake_tool_call_blob() -> None:
    text = (
        "好的，我先清理之前的计划状态，然后把这个完整的脚本录入系统。\n\n"
        "<tool_call>\n"
        '{"tool_name": "update_script_content", "arguments": {"script_content": "0—10秒", "ty'
    )
    cleaned = strip_tool_markup(text)
    assert "录入系统" in cleaned
    assert "tool_call" not in cleaned
    assert "update_script_content" not in cleaned
    assert choose_public_response_text(summarized=text, streamed_public="") == cleaned


def test_parse_scene_asset_model_confirm_from_fe_turn() -> None:
    parsed = _parse_scene_asset_model_confirm(
        "确认生图模型 seeddream-5.0，比例 9:16，清晰度 2K，开始生成参考图"
    )
    assert parsed == ("seeddream-5.0", "9:16", "2K", "")
    assert _parse_scene_asset_model_confirm("没有参考图") is None


def test_parse_structured_scene_patch_from_storyboard_turn() -> None:
    parsed = _parse_structured_scene_patch(
        "修改分镜 scene-1。镜头描述：安然盯着手机。旁白：安然：“如果失败呢？”"
    )
    assert parsed is not None
    scene_id, patch = parsed
    assert scene_id == "scene-1"
    assert patch["shot_description"].startswith("安然盯着手机")
    assert "如果失败" in str(patch["narration"])
    assert _parse_structured_scene_patch("随便聊聊分镜") is None

    # 镜头正文里的「旁白（对白）：」不得截断镜头描述；参考素材单独成字段。
    dialogue = _parse_structured_scene_patch(
        "修改分镜 scene-2\n"
        "镜头描述：0-10秒: 画面：安然盯着手机。\n旁白（对白）：安然：“如果失败呢？”\n"
        "参考素材：character-1、scene-f5229a158a"
    )
    assert dialogue is not None
    assert dialogue[0] == "scene-2"
    shot = str(dialogue[1]["shot_description"])
    assert "安然盯着手机" in shot
    assert "旁白（对白）" in shot
    assert "如果失败" in shot
    assert "narration" not in dialogue[1]
    assert dialogue[1]["reference_asset_ids"] == ["character-1", "scene-f5229a158a"]

    # FE 显式「旁白：」行仍可拆出 narration。
    with_fe_narration = _parse_structured_scene_patch(
        "修改分镜 scene-3\n镜头描述：只改画面\n旁白：安然：继续"
    )
    assert with_fe_narration is not None
    assert with_fe_narration[1]["shot_description"] == "只改画面"
    assert "继续" in str(with_fe_narration[1]["narration"])

    # 自然语言局部修改必须交给原生 Agent 理解，不能把指令本身覆盖成镜头正文。
    assert _parse_structured_scene_patch(
        "修改分镜 scene-2，场地还是在临时剪辑室"
    ) is None


def test_parse_structured_scene_asset_replacement_from_storyboard_turn() -> None:
    parsed = _parse_structured_scene_asset_replacement(
        "替换场景包角色「安然」\n"
        "<<<REPLACE_SCENE_ASSET>>>\n"
        '{"asset_group":"characters","asset_id":"character-anran","replacement":'
        '{"source":"digital_human","display_image_url":"https://cdn.example.invalid/a.png",'
        '"generation_reference_url":"asset://digital-7","third_asset_id":"digital-7",'
        '"asset_type":"xnszr"}}\n'
        "<<<END>>>"
    )
    assert parsed is not None
    assert parsed["asset_id"] == "character-anran"
    assert parsed["replacement"]["third_asset_id"] == "digital-7"
    assert _parse_structured_scene_asset_replacement("替换一下角色") is None


def test_scene_patch_target_context_exposes_only_requested_scene() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-scenes",
        conversation_id="conversation-scenes",
        revision=4,
        payload={
            "scene_packages": [
                {
                    "scene_id": "scene-1",
                    "shot_description": {"text": "会议室开场", "mentions": []},
                },
                {
                    "scene_id": "scene-2",
                    "title": "手机特写",
                    "storyline": "安然检查素材",
                    "shot_description": {
                        "text": "安然攥着只剩九段轨道的手机",
                        "mentions": [{"asset_id": "character-1"}],
                    },
                    "prompt": "安然攥着手机",
                    "narration": "如果失败呢？",
                    "duration_ms": 5_000,
                    "reference_asset_ids": ["character-1"],
                    "authorization": "Bearer must-not-leak",
                },
            ],
        },
        created_at=T0,
        updated_at=T0,
    )

    context = _scene_patch_target_context(
        "修改分镜 scene-2，场地还是在临时剪辑室",
        workspace,
    )

    assert context is not None
    assert context["scene_id"] == "scene-2"
    assert context["shot_description"]["text"] == "安然攥着只剩九段轨道的手机"
    assert context["reference_asset_ids"] == ["character-1"]
    assert "authorization" not in context
    assert "会议室开场" not in str(context)


def test_parse_generate_scenes_intent_phrases() -> None:
    assert _parse_generate_scenes_intent("确认并生成分镜视频") == "all"
    assert _parse_generate_scenes_intent("确认并生成分镜视频（scene-2）") == "all"
    assert _parse_generate_scenes_intent("生成视频吧") == "all"
    assert _parse_generate_scenes_intent("重新生成已修改的分镜视频（scene-1）") == "dirty"
    assert _parse_generate_scenes_intent("资产包是可以的") is None
    # 合并意图不得误入 generate_scenes bootstrap
    assert _parse_generate_scenes_intent("合并视频吧") is None


def test_looks_like_merge_videos_intent_phrases() -> None:
    assert _looks_like_merge_videos_intent("合并视频吧") is True
    assert _looks_like_merge_videos_intent("帮我合成成片") is True
    assert _looks_like_merge_videos_intent("生成视频吧") is False


def test_public_model_failure_message_for_upstream_500() -> None:
    class Fake500(Exception):
        pass

    msg = _public_model_failure_message(
        Fake500("Error code: 500 - {'message': '系统内部错误', 'code': 1000}")
    )
    assert "模型服务暂时不可用" in msg


def test_public_model_failure_message_for_connection_error() -> None:
    class APIConnectionError(Exception):
        pass

    msg = _public_model_failure_message(APIConnectionError("Connection error."))
    assert "模型服务连接失败" in msg
    assert "成片已生成" in msg or "工具卡" in msg


def test_salvage_public_text_prefers_tool_success_over_interrupt() -> None:
    from langchain_core.messages import AIMessage, ToolMessage

    from pixelflow.video_agent.native_invoke import _salvage_public_text_after_stream_error

    class APIConnectionError(Exception):
        pass

    # 典型路径：Tool 已成功，下一轮模型收尾连接失败，尚无最终 AIMessage。
    text, tools = _salvage_public_text_after_stream_error(
        final_state={
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "compose_or_export_video",
                            "args": {"output_type": "mp4"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content='{"public_summary":"MP4成片已生成","artifact_refs":["artifact:x"]}',
                    tool_call_id="call-1",
                    name="compose_or_export_video",
                ),
            ]
        },
        public_response="",
        fallback_response=None,
        stream_error=APIConnectionError("Connection error."),
    )
    assert "MP4成片已生成" in text
    assert "本轮处理中断" not in text
    assert "compose_or_export_video" in tools


def test_merge_videos_intent_does_not_reattach_script() -> None:
    from pixelflow.video_agent.entrypoint import (
        _merge_turn_with_workspace_context,
        merge_video_turn_content_with_history,
    )

    prior = "0—10秒｜所有退路同时消失 【剧情/动作】 最终提案倒计时40分钟。" * 8
    assert merge_video_turn_content_with_history("合并视频吧", [prior]) == "合并视频吧"
    workspace = VideoWorkspace(
        workspace_id="workspace-merge-1",
        conversation_id="conversation-merge-1",
        payload={
            "script": {"content": prior, "status": "ready"},
            "latest_input": prior,
        },
        created_at=T0,
        updated_at=T0,
    )
    assert _merge_turn_with_workspace_context("合并视频吧", workspace) == "合并视频吧"


def test_parse_scene_ids_from_paren_for_single_scene_generate() -> None:
    from pixelflow.video_agent.native_invoke import _parse_scene_ids_from_paren

    assert _parse_scene_ids_from_paren("确认并生成分镜视频（scene-2）") == ["scene-2"]
    assert _parse_scene_ids_from_paren("确认并生成分镜视频") == []
    assert _parse_scene_ids_from_paren("继续生成失败的分镜视频（scene-2、scene-7）") == [
        "scene-2",
        "scene-7",
    ]


def test_model_facing_user_content_strips_merged_script_for_reprepare() -> None:
    from pixelflow.video_agent.native_invoke import (
        _looks_like_reprepare_scene_packages,
        _looks_like_restructure_script,
        _model_facing_user_content,
    )

    merged = (
        "第10集｜最后一镜\n0—10秒｜开场\n" * 20
        + "\n\n【本轮指令】重新生成视频分镜包"
    )
    facing = _model_facing_user_content(merged)
    assert "重新生成视频分镜包" in facing
    assert "prepare_scene_packages" in facing
    assert "第10集" not in facing
    assert _looks_like_reprepare_scene_packages("重新生成视频分镜包") is True
    assert _looks_like_reprepare_scene_packages("重新生成已修改的分镜视频") is False

    assert _looks_like_restructure_script("重新拆解下脚本") is True
    assert _looks_like_restructure_script("再拆解脚本") is True
    assert _looks_like_restructure_script("重新生成视频分镜包") is False
    assert _looks_like_restructure_script("重拆分镜包") is False
    restructure_facing = _model_facing_user_content("重新拆解下脚本")
    assert "import_script" in restructure_facing
    assert "force_reextract" in restructure_facing
    assert "重新拆解" in restructure_facing
    assert "可省略" in restructure_facing or "省略" in restructure_facing


def test_merge_history_does_not_attach_script_for_reprepare() -> None:
    from pixelflow.video_agent.entrypoint import merge_video_turn_content_with_history

    prior = "第10集｜最后一镜，换我来拍\n0—10秒｜所有退路同时消失\n【剧情/动作】安然盯着手机。" * 3
    merged = merge_video_turn_content_with_history(
        "重新生成视频分镜包",
        [prior],
    )
    assert merged == "重新生成视频分镜包"
    assert "【本轮指令】" not in merged


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InspectStubTool:
    spec = VideoToolSpec(
        name="inspect_video_workspace",
        description="读取项目资料",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: VideoToolContext, arguments):
        self.calls += 1
        assert context.user_id == "user-1"
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="项目资料已读取",
            artifact_refs=("artifact:workspace-1",),
        )


class ScriptedToolModel(BaseChatModel):
    """先发 Tool Call，再给最终回答；支持真流式 chunk。"""

    _calls: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _next_message(self) -> AIMessage:
        self._calls += 1
        if self._calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "inspect_video_workspace",
                        "args": {},
                        "id": "call-inspect-1",
                    }
                ],
            )
        return AIMessage(content="已读取当前视频工作区")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next_message()
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next_message()
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_calls=message.tool_calls,
                )
            )
            return
        text = message.content if isinstance(message.content, str) else str(message.content)
        for index in range(0, len(text), 4):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=text[index : index + 4])
            )

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk


@pytest.mark.asyncio
async def test_native_invoker_runs_tool_then_final_answer() -> None:
    tool = InspectStubTool()
    registry = VideoToolRegistry([tool])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={"latest_input": "看看现在项目状态"},
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=ScriptedToolModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            plan_id="plan-1",
            content="看看现在项目状态",
            workspace=workspace,
        )
    )

    assert tool.calls == 1
    assert "inspect_video_workspace" in result.tool_names
    assert "已读取" in result.final_text

    events = await event_repository.list_events("user-1", "conversation-1")
    types = [event.type.value for event in events]
    assert "agent.response.delta" in types
    assert "agent.response.completed" in types
    delta_text = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value == "agent.response.delta"
    )
    assert "已读取" in delta_text


class FakeXmlToolModel(BaseChatModel):
    """把伪 XML tool_call 写进 content，模拟用户看到的卡死泄漏。"""

    _calls: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "fake-xml-tool-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        if self._calls == 1:
            text = (
                "好的，我先把脚本录入系统。\n"
                "<tool_call>\n"
                '{"tool_name": "update_script_content", "arguments": {"ty'
            )
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="脚本已通过 import_script 录入"))]
        )

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        text = result.generations[0].message.content
        assert isinstance(text, str)
        for index in range(0, len(text), 6):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=text[index : index + 6])
            )

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk


@pytest.mark.asyncio
async def test_native_invoker_strips_fake_tool_call_from_public_response() -> None:
    tool = InspectStubTool()
    registry = VideoToolRegistry([tool])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-leak-1",
            conversation_id="conversation-leak-1",
            payload={"latest_input": "录入脚本"},
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=FakeXmlToolModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-leak-1",
            turn_id="turn-leak-1",
            plan_id="plan-leak-1",
            content="请录入完整脚本",
            workspace=workspace,
        )
    )

    assert "tool_call" not in result.final_text
    assert "update_script_content" not in result.final_text
    assert "ty" not in result.final_text or "录入" in result.final_text

    events = await event_repository.list_events("user-1", "conversation-leak-1")
    delta_text = "".join(
        str(event.payload.get("delta") or "")
        for event in events
        if event.type.value == "agent.response.delta"
    )
    completed = next(
        event for event in events if event.type.value == "agent.response.completed"
    )
    assert "<tool_call" not in delta_text
    assert "update_script_content" not in delta_text
    assert "<tool_call" not in str(completed.payload.get("text") or "")


COMPLETE_SCRIPT_FOR_BOOTSTRAP = """
# 防晒霜带货分镜成稿

## 镜头1 0:00-0:15
景别：近景。运镜：缓推。画面：模特涂抹防晒霜。旁白：夏日紫外线很强。

## 镜头2 0:15-0:45
景别：中景。运镜：跟拍。画面：户外步行不脱妆。旁白：清爽不油腻。

## 镜头3 0:45-1:15
景别：特写。运镜：固定。画面：质地与包装特写。旁白：轻薄好推开。

## 镜头4 1:15-1:45
景别：全景。运镜：拉镜。画面：海边场景收束。旁白：立即下单抢购。行动引导：点击购买。
""".strip()


class FinalAnswerOnlyModel(BaseChatModel):
    """bootstrap 后只输出最终回答，不再发 Tool Call。"""

    model_config = ConfigDict(extra="forbid")
    _bound: object = PrivateAttr(default=None)

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ARG002
        self._bound = tools
        return self

    @property
    def _llm_type(self) -> str:
        return "final-answer-only"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content="脚本已写入工作区，请确认画幅后继续。"))
            ]
        )

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        text = "脚本已写入工作区，请确认画幅后继续。"
        for index in range(0, len(text), 6):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=text[index : index + 6])
            )

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk


@pytest.mark.asyncio
async def test_native_invoker_bootstraps_import_script_into_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成稿粘贴时即使模型不发 Tool Call，也必须把 script 写入 Workspace。"""

    from pixelflow.video_agent.entrypoint import looks_like_complete_shooting_script
    from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis
    from pixelflow.video_agent.tools.script import ImportScriptTool

    assert looks_like_complete_shooting_script(COMPLETE_SCRIPT_FOR_BOOTSTRAP)

    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        return ProductionFieldsAnalysis(
            duration_sec=105,
            missing=("视频画幅",),
            has_aspect_ratio=False,
            has_ending_cta=True,
        )

    async def _fake_structure(**_kwargs):  # noqa: ANN003
        return {
            "characters": {
                "stage": "characters",
                "title": "角色/场景/道具设定 /characters",
                "content": "## 角色设定\n模特",
                "artifact_ref": "artifact:video-script-characters-boot",
                "change_summary": "拆解",
            },
            "outline": {
                "stage": "outline",
                "title": "分镜大纲 /outline",
                "content": "## 分镜提示词\n1. 0:00",
                "artifact_ref": "artifact:video-script-outline-boot",
                "change_summary": "拆解",
            },
        }

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script_skill_pipeline.extract_imported_script_structure",
        _fake_structure,
    )

    registry = VideoToolRegistry([ImportScriptTool(), InspectStubTool()])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-boot-1",
            conversation_id="conversation-boot-1",
            payload={"latest_input": COMPLETE_SCRIPT_FOR_BOOTSTRAP},
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=FinalAnswerOnlyModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-boot-1",
            turn_id="turn-boot-1",
            plan_id="plan-boot-1",
            content=COMPLETE_SCRIPT_FOR_BOOTSTRAP,
            workspace=workspace,
        )
    )

    assert "import_script" in result.tool_names
    assert "已完成本轮处理" not in result.final_text
    assert "已导入脚本" in result.final_text or "缺少" in result.final_text
    stored = await repository.get_workspace("user-1", "workspace-boot-1")
    assert stored is not None
    script = stored.payload.get("script")
    assert isinstance(script, dict)
    assert "防晒霜" in str(script.get("content") or "")
    assert script.get("source") == "user_import"

    events = await event_repository.list_events("user-1", "conversation-boot-1")
    types = [event.type.value for event in events]
    assert "agent.tool.started" in types
    assert "agent.tool.completed" in types
    assert "agent.response.completed" in types
    tool_names = [
        str(event.payload.get("tool_name") or "")
        for event in events
        if event.type.value in {"agent.tool.started", "agent.tool.completed"}
    ]
    assert "import_script" in tool_names
    assert types.index("agent.reasoning_summary.delta") < types.index("agent.tool.started")
    # 成稿导入短接：不得再进模型产生第二轮冲突推理事件。
    assert types.count("agent.response.completed") == 1


@pytest.mark.asyncio
async def test_bootstrap_reasoning_chunk_zero_does_not_collide_with_stream(
) -> None:
    """bootstrap open 占用 chunk 0；流式首包从 1 起，同 turn 不得撞 event_id。"""

    repository = MemoryAgentRuntimeRepository()
    publisher = NativeAgentEventPublisher(
        repository=repository,
        user_id="user-1",
        conversation_id="conversation-chunk-1",
        turn_id="turn-chunk-1",
    )
    await publisher.reasoning_summary_delta(delta="检测到完整拍摄脚本…", chunk_index=0)
    await publisher.reasoning_summary_delta(delta="模型续写思考", chunk_index=1)
    events = await repository.list_events("user-1", "conversation-chunk-1")
    assert len(events) == 2
    assert events[0].payload.get("delta") == "检测到完整拍摄脚本…"
    assert events[1].payload.get("delta") == "模型续写思考"


@pytest.mark.asyncio
async def test_native_invoker_bootstraps_production_fields_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补画幅/CTA 时必须写入 workspace，不能空回「已完成本轮处理」。"""

    from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        assert "9：16" in text or "9:16" in text
        return ProductionFieldsAnalysis(
            duration_sec=180,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
            aspect_ratio="9:16",
            ending_cta="none",
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.production_fields.analyze_production_fields_with_llm",
        _fake_analysis,
    )

    registry = VideoToolRegistry([InspectStubTool()])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-fields-1",
            conversation_id="conversation-fields-1",
            payload={
                "awaiting_production_fields": True,
                "script": {
                    "content": "成稿正文",
                    "source": "user_import",
                    "version": 1,
                    "missing_requirements": ["视频画幅", "结尾行动引导"],
                },
                "latest_input": "1. 9：16 2.不用引导",
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=FinalAnswerOnlyModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-fields-1",
            turn_id="turn-fields-1",
            plan_id="plan-fields-1",
            content="1. 9：16 2.不用引导",
            workspace=workspace,
        )
    )

    assert "apply_production_fields" in result.tool_names
    assert "已完成本轮处理" not in result.final_text
    assert "9:16" in result.final_text or "画幅" in result.final_text

    stored = await repository.get_workspace("user-1", "workspace-fields-1")
    assert stored is not None
    script = stored.payload.get("script")
    assert isinstance(script, dict)
    assert script.get("aspect_ratio") == "9:16"
    assert script.get("ending_cta") == "none"
    assert script.get("missing_requirements") == []


class PrepareScenePackagesStubTool:
    spec = VideoToolSpec(
        name="prepare_scene_packages",
        description="从已确认脚本生成结构化视频资产包",
        input_model=PrepareScenePackagesInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("scene_packages", "script_plan_confirmed", "scene_package_job"),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: VideoToolContext, arguments):  # noqa: ANN001, ARG002
        self.calls += 1
        assert context.user_id == "user-1"
        attempt = 1
        if isinstance(arguments, dict) and isinstance(arguments.get("attempt"), int):
            attempt = arguments["attempt"]
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="场景包准备任务已启动",
            workspace_patch={
                "script_plan_confirmed": True,
                "scene_packages": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "title": "开场",
                    }
                ],
                "scene_package_job": {
                    "job_id": "job-prepare-1",
                    "status": "polling",
                    "attempt": attempt,
                },
            },
            pending_operation_job_ids=("job-prepare-1",),
        )


@pytest.mark.asyncio
async def test_native_invoker_no_longer_bootstraps_prepare_on_confirm_phrase() -> None:
    """自然语言「确认脚本」不再靠 marker bootstrap prepare；按钮走独立命令 API。"""

    prepare = PrepareScenePackagesStubTool()
    registry = VideoToolRegistry([InspectStubTool(), prepare])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-confirm-1",
            conversation_id="conversation-confirm-1",
            payload={
                "script_plan_confirmed": True,
                "script": {
                    "content": COMPLETE_SCRIPT_FOR_BOOTSTRAP,
                    "source": "user_import",
                    "version": 1,
                    "aspect_ratio": "9:16",
                    "ending_cta": "present",
                    "missing_requirements": [],
                },
                "latest_input": "确认脚本",
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=FinalAnswerOnlyModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-confirm-1",
            turn_id="turn-confirm-1",
            plan_id="plan-confirm-1",
            content="确认脚本",
            workspace=workspace,
        )
    )

    assert prepare.calls == 0
    assert "prepare_scene_packages" not in result.tool_names


@pytest.mark.asyncio
async def test_native_invoker_confirm_phrase_does_not_hijack_missing_cta_fields() -> None:
    """缺 CTA 时「确认脚本」不得被补字段 bootstrap 截胡，也不得启动 prepare。"""

    prepare = PrepareScenePackagesStubTool()
    registry = VideoToolRegistry([InspectStubTool(), prepare])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-confirm-gap-1",
            conversation_id="conversation-confirm-gap-1",
            payload={
                "awaiting_production_fields": True,
                "script": {
                    "content": COMPLETE_SCRIPT_FOR_BOOTSTRAP,
                    "source": "user_import",
                    "version": 1,
                    "aspect_ratio": "9:16",
                    "missing_requirements": ["结尾行动引导"],
                },
                "latest_input": "确认脚本",
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=FinalAnswerOnlyModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-confirm-gap-1",
            turn_id="turn-confirm-gap-1",
            plan_id="plan-confirm-gap-1",
            content="确认脚本",
            workspace=workspace,
        )
    )

    assert prepare.calls == 0
    assert "apply_production_fields" not in result.tool_names


@pytest.mark.asyncio
async def test_native_invoker_bootstraps_third_cta_choice_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「2. 第三个」即使 LLM 失败，也应落库 ending_cta=none 并停止追问。"""

    class _BoomModel:
        def invoke(self, messages):  # noqa: ANN001, ARG002
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "deerflow.models.create_chat_model",
        lambda **_kwargs: _BoomModel(),
    )

    registry = VideoToolRegistry([InspectStubTool()])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-fields-third-1",
            conversation_id="conversation-fields-third-1",
            payload={
                "awaiting_production_fields": True,
                "script": {
                    "content": "成稿正文",
                    "source": "user_import",
                    "version": 1,
                    "missing_requirements": ["视频画幅", "结尾行动引导"],
                },
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=FinalAnswerOnlyModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-fields-third-1",
            turn_id="turn-fields-third-1",
            plan_id="plan-fields-third-1",
            content="1. 9：16 2. 第三个",
            workspace=workspace,
        )
    )

    assert "apply_production_fields" in result.tool_names
    assert "仍缺少" not in result.final_text
    stored = await repository.get_workspace("user-1", "workspace-fields-third-1")
    assert stored is not None
    script = stored.payload.get("script")
    assert isinstance(script, dict)
    assert script.get("aspect_ratio") == "9:16"
    assert script.get("ending_cta") == "none"
    assert script.get("missing_requirements") == []


@pytest.mark.asyncio
async def test_native_invoker_exposes_checkpointer_when_provided() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    tool = InspectStubTool()
    registry = VideoToolRegistry([tool])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    saver = InMemorySaver()
    invoker = NativeVideoAgentInvoker(
        model=ScriptedToolModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
        checkpointer=saver,
    )
    assert invoker.checkpointer is saver


@pytest.mark.asyncio
async def test_entrypoint_native_path_skips_planner_and_runner_invokes_agent() -> None:
    tool = InspectStubTool()
    registry = VideoToolRegistry([tool])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=ScriptedToolModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=event_repository,
        video_repository=repository,
        native_invoker=invoker,
        clock=lambda: T0,
    )
    runner = VideoAgentRunner(
        repository=repository,
        native_invoker=invoker,
    )

    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-native-1",
        turn_id="turn-native-1",
        content="看看现在项目状态",
        artifact_refs=(),
    )

    assert submission.plan is not None
    assert submission.plan.status is AgentPlanStatus.RUNNING
    assert submission.plan.steps == ()
    assert submission.workspace.payload.get("native_agent") is True

    scope = VideoAgentRunScope(
        user_id="user-1",
        conversation_id="conversation-native-1",
        turn_id="turn-native-1",
        plan_id=submission.plan.plan_id,
    )
    await runner.notify_turn(scope, None)

    assert tool.calls == 1
    completed = await repository.get_plan("user-1", submission.plan.plan_id)
    assert completed is not None
    assert completed.status is AgentPlanStatus.COMPLETED
    assert completed.plan_id == video_agent_plan_id(
        "conversation-native-1",
        "turn-native-1",
    )


class GenerateSceneAssetsStubTool:
    spec = VideoToolSpec(
        name="generate_scene_assets",
        description="生成场景参考图",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("scene_asset_job",),
    )

    def __init__(self) -> None:
        self.calls = 0
        self.last_arguments: dict[str, object] | None = None

    async def execute(self, context: VideoToolContext, arguments):  # noqa: ANN001, ARG002
        self.calls += 1
        self.last_arguments = dict(arguments)
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="参考图生成任务已启动",
            workspace_patch={
                "scene_asset_job": {
                    "job_id": "job-assets-1",
                    "status": "polling",
                },
            },
            pending_operation_job_ids=("job-assets-1",),
        )


class BoomIfCalledModel(BaseChatModel):
    """短接路径下绝不可进入模型。"""

    @property
    def _llm_type(self) -> str:
        return "boom-if-called"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        raise AssertionError("generate_scene_assets bootstrap 短接后不得进入模型")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        raise AssertionError("generate_scene_assets bootstrap 短接后不得进入模型")


@pytest.mark.asyncio
async def test_native_invoker_bootstraps_generate_scene_assets_and_short_circuits() -> None:
    """模型确认 Turn 须直执 generate_scene_assets 并短接，禁止再进模型。"""

    assets = GenerateSceneAssetsStubTool()
    # Registry 校验用真实 input_model；stub 需接受模型确认参数。
    from pixelflow.video_agent.tools.scene_packages import GenerateSceneAssetsInput

    class _AssetsTool(GenerateSceneAssetsStubTool):
        spec = VideoToolSpec(
            name="generate_scene_assets",
            description="生成场景参考图",
            input_model=GenerateSceneAssetsInput,
            cost_level=VideoToolCostLevel.BILLABLE,
            confirmation_required=True,
            idempotency_mode=VideoToolIdempotencyMode.OPERATION,
            recovery_mode=VideoToolRecoveryMode.OPERATION,
            workspace_mutations=("scene_asset_job",),
        )

    assets = _AssetsTool()
    registry = VideoToolRegistry([InspectStubTool(), assets])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-assets-boot-1",
            conversation_id="conversation-assets-boot-1",
            payload={
                "script_plan_confirmed": True,
                "script": {
                    "content": "完整脚本",
                    "aspect_ratio": "16:9",
                },
                "scene_packages": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "title": "开场",
                        "global_assets": {
                            "characters": [{"asset_id": "character-1", "name": "模特"}],
                            "scenes": [],
                            "props": [],
                        },
                    }
                ],
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=BoomIfCalledModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-assets-boot-1",
            turn_id="turn-assets-boot-1",
            plan_id="plan-assets-boot-1",
            content="确认生图模型 seedream-5.0，比例 9:16，清晰度 2K，开始生成参考图",
            workspace=workspace,
        )
    )

    assert assets.calls == 1
    assert assets.last_arguments is not None
    assert assets.last_arguments["image_ratio"] == "16:9"
    assert "generate_scene_assets" in result.tool_names
    assert "已启动" in result.final_text or "参考图" in result.final_text
    events = await event_repository.list_events("user-1", "conversation-assets-boot-1")
    types = [event.type.value for event in events]
    assert "agent.tool.started" in types
    assert "agent.tool.completed" in types


@pytest.mark.asyncio
async def test_native_invoker_continues_only_missing_scene_assets_before_video() -> None:
    from pixelflow.video_agent.tools.scene_packages import GenerateSceneAssetsInput

    class _AssetsTool(GenerateSceneAssetsStubTool):
        spec = VideoToolSpec(
            name="generate_scene_assets",
            description="生成场景参考图",
            input_model=GenerateSceneAssetsInput,
            cost_level=VideoToolCostLevel.BILLABLE,
            confirmation_required=True,
            idempotency_mode=VideoToolIdempotencyMode.OPERATION,
            recovery_mode=VideoToolRecoveryMode.OPERATION,
            workspace_mutations=("scene_asset_job",),
        )

    assets = _AssetsTool()
    registry = VideoToolRegistry([InspectStubTool(), assets])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-assets-partial-1",
            conversation_id="conversation-assets-partial-1",
            payload={
                "script_plan_confirmed": True,
                "creation_contract": {
                    "image_model": "seeddream-5.0",
                    "scene_image_ratio": "9:16",
                    "scene_image_size": "2K",
                },
                "scene_packages": [{"scene_id": "scene-1", "scene_index": 1}],
                "global_assets": {
                    "characters": [
                        {
                            "asset_id": "character-host",
                            "three_view_images": ["https://cdn.example/host.png"],
                        }
                    ],
                    "scenes": [{"asset_id": "scene-room", "images": []}],
                    "props": [],
                },
                "scene_asset_failures": [
                    {
                        "asset_id": "scene-room",
                        "asset_type": "scene_image",
                        "retry_pending": True,
                    }
                ],
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=BoomIfCalledModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-assets-partial-1",
            turn_id="turn-assets-partial-1",
            plan_id="plan-assets-partial-1",
            content="继续生成",
            workspace=workspace,
        )
    )

    assert assets.calls == 1
    assert result.tool_names == ("generate_scene_assets",)
    assert assets.last_arguments is not None
    assert assets.last_arguments["image_model"] == "seeddream-5.0"
    assert assets.last_arguments["target_assets"] == [
        {"asset_id": "scene-room", "asset_type": "scene_image"}
    ]


@pytest.mark.asyncio
async def test_native_invoker_reports_partial_scene_asset_completion() -> None:
    from pixelflow.video_agent.tools.scene_packages import GenerateSceneAssetsInput

    class _PartialAssetsTool:
        spec = VideoToolSpec(
            name="generate_scene_assets",
            description="生成场景参考图",
            input_model=GenerateSceneAssetsInput,
            cost_level=VideoToolCostLevel.BILLABLE,
            confirmation_required=True,
            idempotency_mode=VideoToolIdempotencyMode.OPERATION,
            recovery_mode=VideoToolRecoveryMode.OPERATION,
            workspace_mutations=(
                "global_assets",
                "scene_asset_failures",
                "scene_asset_job",
            ),
        )

        async def execute(self, context: VideoToolContext, arguments):  # noqa: ANN001, ARG002
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="部分参考图已生成，剩余素材可继续重试",
                workspace_patch={
                    "global_assets": {
                        "characters": [
                            {
                                "asset_id": "character-host",
                                "three_view_images": ["https://cdn.example/host.png"],
                            }
                        ],
                        "scenes": [{"asset_id": "scene-room", "images": []}],
                        "props": [],
                    },
                    "scene_asset_failures": [
                        {
                            "asset_id": "scene-room",
                            "asset_type": "scene_image",
                            "retry_pending": True,
                        }
                    ],
                    "scene_asset_job": {"job_id": "job-partial", "status": "partial"},
                },
            )

    registry = VideoToolRegistry([InspectStubTool(), _PartialAssetsTool()])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-assets-partial-reply-1",
            conversation_id="conversation-assets-partial-reply-1",
            payload={
                "script_plan_confirmed": True,
                "scene_packages": [{"scene_id": "scene-1", "scene_index": 1}],
                "global_assets": {
                    "characters": [
                        {"asset_id": "character-host", "name": "主播", "three_view_images": []}
                    ],
                    "scenes": [{"asset_id": "scene-room", "name": "室内", "images": []}],
                    "props": [],
                },
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(repository=repository, registry=registry, clock=lambda: T0)
    invoker = NativeVideoAgentInvoker(
        model=BoomIfCalledModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-assets-partial-reply-1",
            turn_id="turn-assets-partial-reply-1",
            plan_id="plan-assets-partial-reply-1",
            content="确认生图模型 seeddream-5.0，比例 9:16，清晰度 2K，开始生成参考图",
            workspace=workspace,
        )
    )

    assert "1/2" in result.final_text
    assert "继续生成" in result.final_text


@pytest.mark.asyncio
async def test_native_invoker_bootstraps_single_scene_generate_and_short_circuits() -> None:
    """「确认并生成分镜视频（scene-1）」只生成该镜，禁止进模型改走合并。"""

    from pixelflow.video_agent.tools.scene import GenerateScenesInput

    class _GenerateScenesTool:
        spec = VideoToolSpec(
            name="generate_scenes",
            description="生成分镜视频",
            input_model=GenerateScenesInput,
            cost_level=VideoToolCostLevel.BILLABLE,
            confirmation_required=True,
            idempotency_mode=VideoToolIdempotencyMode.OPERATION,
            recovery_mode=VideoToolRecoveryMode.OPERATION,
            workspace_mutations=(
                "scenes",
                "scene_packages",
                "dirty_scene_ids",
                "assets",
                "quota_interrupt",
                "scene_video_progress",
            ),
        )

        def __init__(self) -> None:
            self.calls = 0
            self.last_args: dict | None = None

        async def execute(self, context: VideoToolContext, arguments):
            self.calls += 1
            self.last_args = dict(arguments)
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="已启动 1 个分镜视频生成",
                workspace_patch={
                    "scene_video_progress": {
                        "completed": 0,
                        "total": 1,
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "ok": None,
                    },
                    "quota_interrupt": None,
                },
                pending_operation_job_ids=("job-scene-1",),
                requires_confirmation=True,
            )

    scenes_tool = _GenerateScenesTool()
    registry = VideoToolRegistry([InspectStubTool(), scenes_tool])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-scenes-single-1",
            conversation_id="conversation-scenes-single-1",
            payload={
                "latest_input": "确认并生成分镜视频（scene-1）",
                "awaiting_production_fields": True,
                "scene_packages": [
                    {"scene_id": "scene-1", "scene_index": 1, "title": "开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "title": "中段"},
                ],
                "scenes": [
                    {"scene_id": "scene-1", "scene_index": 1, "title": "开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "title": "中段"},
                ],
                "dirty_scene_ids": ["scene-1"],
                "global_assets": {
                    "characters": [
                        {
                            "asset_id": "character-1",
                            "name": "安然",
                            "image_url": "https://cdn.example.invalid/a.png",
                        }
                    ],
                    "scenes": [],
                    "props": [],
                },
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=BoomIfCalledModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-scenes-single-1",
            turn_id="turn-scenes-single-1",
            plan_id="plan-scenes-single-1",
            content="确认并生成分镜视频（scene-1）",
            workspace=workspace,
        )
    )

    assert scenes_tool.calls == 1
    assert scenes_tool.last_args is not None
    assert list(scenes_tool.last_args.get("scene_ids") or ()) == ["scene-1"]
    assert result.tool_names == ("generate_scenes",)
    assert "已启动 1 个分镜视频生成" in result.final_text
    assert "合并" not in result.final_text
    assert "已完成本轮处理" not in result.final_text


@pytest.mark.asyncio
async def test_native_invoker_bootstraps_patch_scene_and_short_circuits() -> None:
    """分镜面板结构化修改须直执 patch_scene，禁止空转「已完成本轮处理」。"""

    from pixelflow.video_agent.tools.scene import PatchSceneInput

    class _PatchTool:
        spec = VideoToolSpec(
            name="patch_scene",
            description="修改镜头",
            input_model=PatchSceneInput,
            cost_level=VideoToolCostLevel.NONE,
            confirmation_required=False,
            idempotency_mode=VideoToolIdempotencyMode.REQUEST,
            recovery_mode=VideoToolRecoveryMode.REPLAY,
            workspace_mutations=("scenes", "scene_packages", "dirty_scene_ids", "qc"),
        )

        def __init__(self) -> None:
            self.calls = 0
            self.last_args: dict | None = None

        async def execute(self, context: VideoToolContext, arguments):
            self.calls += 1
            self.last_args = dict(arguments)
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="镜头 scene-1 已更新并标记为待重新生成",
                workspace_patch={
                    "dirty_scene_ids": ["scene-1"],
                    "scenes": [
                        {
                            "scene_id": "scene-1",
                            "prompt": "安然盯着手机",
                            "edit_status": "待重新生成",
                        }
                    ],
                    "scene_packages": [
                        {
                            "scene_id": "scene-1",
                            "prompt": "安然盯着手机",
                            "edit_status": "待重新生成",
                        }
                    ],
                },
            )

    patch_tool = _PatchTool()
    registry = VideoToolRegistry([InspectStubTool(), patch_tool])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-patch-boot-1",
            conversation_id="conversation-patch-boot-1",
            payload={
                "scene_packages": [
                    {"scene_id": "scene-1", "scene_index": 1, "title": "开场"},
                ],
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=registry,
        clock=lambda: T0,
    )
    invoker = NativeVideoAgentInvoker(
        model=BoomIfCalledModel(),
        registry=registry,
        executor=executor,
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-patch-boot-1",
            turn_id="turn-patch-boot-1",
            plan_id="plan-patch-boot-1",
            content="修改分镜 scene-1。镜头描述：安然盯着手机。旁白：安然：“如果失败呢？”",
            workspace=workspace,
        )
    )

    assert patch_tool.calls == 1
    assert patch_tool.last_args is not None
    assert patch_tool.last_args["scene_id"] == "scene-1"
    assert "shot_description" in patch_tool.last_args["patch"]
    assert "patch_scene" in result.tool_names
    assert "已更新分镜" in result.final_text
    assert "已完成本轮处理" not in result.final_text


@pytest.mark.asyncio
async def test_native_invoker_generate_scenes_bootstrap_requires_asset_images() -> None:
    """无参考图时「生成视频吧」须明确提示，不得空转「已完成本轮处理」。"""

    registry = VideoToolRegistry([InspectStubTool()])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-scenes-boot-1",
            conversation_id="conversation-scenes-boot-1",
            payload={
                "scene_packages": [
                    {"scene_id": "scene-1", "scene_index": 1, "title": "开场"},
                ],
                "global_assets": {"characters": [{"asset_id": "c1", "name": "安然"}], "scenes": [], "props": []},
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    invoker = NativeVideoAgentInvoker(
        model=BoomIfCalledModel(),
        registry=registry,
        executor=VideoAgentExecutor(repository=repository, registry=registry, clock=lambda: T0),
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-scenes-boot-1",
            turn_id="turn-scenes-boot-1",
            plan_id="plan-scenes-boot-1",
            content="生成视频吧",
            workspace=workspace,
        )
    )

    assert result.tool_names == ()
    assert "参考图" in result.final_text
    assert "已完成本轮处理" not in result.final_text


class EmptyReplyModel(BaseChatModel):
    """故意空转：不发 Tool、不吐正文，触发 reprepare failsafe。"""

    @property
    def _llm_type(self) -> str:
        return "empty-reply-model"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        yield ChatGenerationChunk(message=AIMessageChunk(content=""))

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk


class VerbalReprepareModel(BaseChatModel):
    """只口头答应重拆、不发 Tool Call —— 必须仍走 failsafe。"""

    @property
    def _llm_type(self) -> str:
        return "verbal-reprepare-model"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content="好的，立刻为你重新生成视频分镜包。")
                )
            ]
        )

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        text = "好的，立刻为你重新生成视频分镜包。"
        for index in range(0, len(text), 8):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=text[index : index + 8])
            )

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ARG002
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk


class PrepareScenePackagesStubTool:
    """failsafe 直执路径的 prepare stub。"""

    spec = VideoToolSpec(
        name="prepare_scene_packages",
        description="生成视频分镜包",
        input_model=PrepareScenePackagesInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("scene_packages", "scene_package_job"),
    )

    def __init__(self) -> None:
        self.calls = 0
        self.attempts: list[int] = []

    async def execute(self, context: VideoToolContext, arguments):  # noqa: ANN001
        self.calls += 1
        self.attempts.append(int(arguments.get("attempt") or 1))
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="已启动重新生成视频分镜包",
            artifact_refs=("artifact:scene-packages",),
        )


@pytest.mark.asyncio
async def test_native_invoker_reprepare_empty_turn_failsafe_prepare() -> None:
    """「重新生成视频分镜包」模型空转时，必须 failsafe 直执 prepare，不得停在已完成本轮处理。"""

    prepare = PrepareScenePackagesStubTool()
    registry = VideoToolRegistry([InspectStubTool(), prepare])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-reprepare-1",
            conversation_id="conversation-reprepare-1",
            payload={
                "latest_input": "重新生成视频分镜包",
                "script_plan_confirmed": True,
                "script": {
                    "status": "ready",
                    "content": "# 脚本\n镜头1",
                    "aspect_ratio": "9:16",
                    "ending_cta": "none",
                    "missing_requirements": [],
                },
                "scene_packages": [
                    {"scene_id": "scene-1", "scene_index": 1, "title": "旧开场"},
                ],
                "scene_package_job": {"attempt": 1, "status": "succeeded"},
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    invoker = NativeVideoAgentInvoker(
        model=EmptyReplyModel(),
        registry=registry,
        executor=VideoAgentExecutor(
            repository=repository, registry=registry, clock=lambda: T0
        ),
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-reprepare-1",
            turn_id="turn-reprepare-1",
            plan_id="plan-reprepare-1",
            content="重新生成视频分镜包",
            workspace=workspace,
        )
    )

    assert prepare.calls >= 1
    assert "prepare_scene_packages" in result.tool_names
    assert "重新生成" in result.final_text or "分镜包" in result.final_text
    assert result.final_text.strip() != "已完成本轮处理"

    events = await event_repository.list_events("user-1", "conversation-reprepare-1")
    completed_texts = [
        str((getattr(event, "payload", None) or {}).get("text") or "")
        for event in events
        if str(getattr(getattr(event, "type", None), "value", getattr(event, "type", "")) or "")
        .endswith("response.completed")
    ]
    # 至少有一帧非空转终态（revision 区分，避免 final 冲突吞掉 failsafe 文案）。
    assert any(text and text != "已完成本轮处理" for text in completed_texts)


@pytest.mark.asyncio
async def test_native_invoker_reprepare_verbal_reply_still_failsafe_prepare() -> None:
    """模型只口头答应、未发 Tool Call 时，不得当成成功结束。"""

    prepare = PrepareScenePackagesStubTool()
    registry = VideoToolRegistry([InspectStubTool(), prepare])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-reprepare-verbal-1",
            conversation_id="conversation-reprepare-verbal-1",
            payload={
                "latest_input": "重新生成视频分镜包",
                "script_plan_confirmed": True,
                "script": {
                    "status": "ready",
                    "content": "# 脚本\n镜头1",
                    "aspect_ratio": "9:16",
                    "ending_cta": "none",
                    "missing_requirements": [],
                },
                "scene_packages": [
                    {"scene_id": "scene-1", "scene_index": 1, "title": "旧开场"},
                ],
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    invoker = NativeVideoAgentInvoker(
        model=VerbalReprepareModel(),
        registry=registry,
        executor=VideoAgentExecutor(
            repository=repository, registry=registry, clock=lambda: T0
        ),
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-reprepare-verbal-1",
            turn_id="turn-reprepare-verbal-1",
            plan_id="plan-reprepare-verbal-1",
            content="重新生成视频分镜包",
            workspace=workspace,
        )
    )

    assert prepare.calls >= 1
    assert "prepare_scene_packages" in result.tool_names
    assert "好的，立刻" not in result.final_text or "已启动" in result.final_text
    assert result.final_text.strip() != "已完成本轮处理"


@pytest.mark.asyncio
async def test_native_invoker_restructure_empty_turn_failsafe_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「重新拆解脚本」不走提前 bootstrap；模型空转时 failsafe 直执 import_script。"""

    from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis
    from pixelflow.video_agent.tools.script import ImportScriptTool

    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        return ProductionFieldsAnalysis(
            duration_sec=60,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    async def _fake_structure(**_kwargs):  # noqa: ANN003
        return {
            "characters": {
                "stage": "characters",
                "title": "角色/场景/道具设定 /characters",
                "content": "## 角色设定\n安然",
                "artifact_ref": "artifact:video-script-characters-restructure",
                "change_summary": "重新拆解",
            },
            "outline": {
                "stage": "outline",
                "title": "分镜大纲 /outline",
                "content": "## 分镜提示词\n1. 0:00",
                "artifact_ref": "artifact:video-script-outline-restructure",
                "change_summary": "重新拆解",
            },
        }

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script_skill_pipeline.extract_imported_script_structure",
        _fake_structure,
    )

    registry = VideoToolRegistry([ImportScriptTool(), InspectStubTool()])
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    script_body = "# 第10集\n0—10秒｜开场\n安然盯着手机。"
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-restructure-1",
            conversation_id="conversation-restructure-1",
            payload={
                "latest_input": "重新拆解下脚本",
                "script": {
                    "status": "ready",
                    "content": script_body,
                    "aspect_ratio": "9:16",
                    "ending_cta": "none",
                    "missing_requirements": [],
                    "source": "user_import",
                },
            },
            created_at=T0,
            updated_at=T0,
        ),
    )
    invoker = NativeVideoAgentInvoker(
        model=EmptyReplyModel(),
        registry=registry,
        executor=VideoAgentExecutor(
            repository=repository, registry=registry, clock=lambda: T0
        ),
        video_repository=repository,
        runtime_repository=event_repository,
        skill_catalog=SimpleNamespace(),
        memory_config=MemoryConfig(enabled=False),
    )

    result = await invoker.invoke(
        NativeVideoAgentInvokeRequest(
            user_id="user-1",
            conversation_id="conversation-restructure-1",
            turn_id="turn-restructure-1",
            plan_id="plan-restructure-1",
            content="重新拆解下脚本",
            workspace=workspace,
        )
    )

    assert "import_script" in result.tool_names
    assert result.final_text.strip() != "已完成本轮处理"
    assert "拆解" in result.final_text or "导入" in result.final_text
    # 直接 failsafe：不得假死在仅有开场句；应有 tool 事件与终态回复。
    events = await event_repository.list_events("user-1", "conversation-restructure-1")
    types = [event.type.value for event in events]
    assert "agent.tool.started" in types
    assert "agent.tool.completed" in types
    assert "agent.response.completed" in types
    reasoning = [
        str((getattr(event, "payload", None) or {}).get("delta")
            or (getattr(event, "payload", None) or {}).get("text")
            or "")
        for event in events
        if "reasoning_summary" in str(
            getattr(getattr(event, "type", None), "value", getattr(event, "type", "")) or ""
        )
    ]
    assert any("重新拆解" in item or "拆解" in item for item in reasoning)

    stored = await repository.get_workspace("user-1", "workspace-restructure-1")
    assert stored is not None
    pipeline = stored.payload.get("script_pipeline")
    assert isinstance(pipeline, dict)
    characters = pipeline.get("characters")
    assert isinstance(characters, dict)
    assert "安然" in str(characters.get("content") or "")
