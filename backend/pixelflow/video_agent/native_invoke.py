"""原生 Video Agent 的单次 Turn 调用封装。"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from pixelflow.video_agent.agent import VIDEO_AGENT_NAME, create_video_agent
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.credentials import TransientVideoAgentCredential
from pixelflow.video_agent.events.publisher import NativeAgentEventPublisher
from pixelflow.video_agent.production_fields import workspace_resolved_aspect_ratio
from pixelflow.video_agent.tool_runtime_context import bind_tool_runtime_context
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolExecutionError,
    VideoToolRegistry,
    VideoToolValidationError,
)

logger = logging.getLogger(__name__)

# 流式 delta 事件节流：过密会打爆 Outbox/SSE。
_RESPONSE_DELTA_MIN_CHARS = 8
_REASONING_DELTA_MIN_CHARS = 12
_SAFE_MODEL_REASONING_PROGRESS = "正在核对工作区状态和执行前置条件…"
_SAFE_MODEL_REASONING_COMPLETED = "已完成工作区状态与执行条件检查"

# 模型偶发把伪 Tool Call 写进 content；这些标记起必须从公开回答切断。
_TOOL_MARKUP_MARKERS: tuple[str, ...] = (
    "<tool_call",
    "</tool_call",
    "<tool_calls",
    "</tool_calls",
    "<|tool_call",
    "<|tool_calls",
    "```tool_call",
    '{"tool_name"',
    '{\n"tool_name"',
)


@dataclass(frozen=True, slots=True)
class NativeVideoAgentInvokeRequest:
    """一次原生 Agent invocation 的输入边界。"""

    user_id: str
    conversation_id: str
    turn_id: str
    plan_id: str
    content: str
    workspace: VideoWorkspace
    credential: TransientVideoAgentCredential | None = None


@dataclass(frozen=True, slots=True)
class NativeVideoAgentInvokeResult:
    """一次原生 Agent invocation 的安全摘要。"""

    final_text: str
    tool_names: tuple[str, ...]
    message_count: int


class NativeVideoAgentInvoker:
    """把 Turn 内容交给 create_video_agent 图；经 astream_events 真流式推送公开事件。"""

    def __init__(
        self,
        *,
        model: Any,
        registry: VideoToolRegistry,
        executor: object,
        video_repository: object,
        runtime_repository: object,
        skill_catalog: object,
        checkpointer: Any | None = None,
        app_config: Any | None = None,
        memory_config: Any | None = None,
        agent: CompiledStateGraph | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._video_repository = video_repository
        self._runtime_repository = runtime_repository
        self._checkpointer = checkpointer
        self._agent = agent or create_video_agent(
            model=model,
            registry=registry,
            executor=executor,
            video_repository=video_repository,
            runtime_repository=runtime_repository,
            skill_catalog=skill_catalog,
            checkpointer=checkpointer,
            app_config=app_config,
            memory_config=memory_config,
        )

    @property
    def agent_name(self) -> str:
        return VIDEO_AGENT_NAME

    @property
    def checkpointer(self) -> Any | None:
        return self._checkpointer

    async def invoke(
        self,
        request: NativeVideoAgentInvokeRequest,
    ) -> NativeVideoAgentInvokeResult:
        if not isinstance(request, NativeVideoAgentInvokeRequest):
            raise TypeError("request 必须是 NativeVideoAgentInvokeRequest")
        content = request.content.strip()
        if not content:
            raise ValueError("原生 Agent 输入内容不能为空")

        runtime_context: dict[str, object] = {
            "user_id": request.user_id,
            "workspace_id": request.workspace.workspace_id,
            "workspace": request.workspace,
            "turn_id": request.turn_id,
            "conversation_id": request.conversation_id,
            "revision": request.workspace.revision,
        }
        # VideoToolContext 要求 plan_id/step_id 成对；观察 Plan 尚无业务 Step 时不注入。
        if request.plan_id.strip():
            runtime_context["plan_id"] = request.plan_id
            runtime_context["step_id"] = f"{request.plan_id}-native"
        if request.credential is not None:
            runtime_context["credential"] = request.credential
        approved = request.workspace.payload.get("native_approved_confirmation")
        if isinstance(approved, dict) and approved:
            runtime_context["approved_confirmation"] = dict(approved)

        # 每 Turn 独立 thread，避免跨轮 checkpointer 脏状态导致秒级空转「已完成本轮处理」。
        # 同 Turn 内多步 Tool 仍共享该 thread；跨 Turn 真相以 Workspace digest 为准。
        thread_id = (
            f"{request.conversation_id}:{request.workspace.workspace_id}:{request.turn_id}"
        )
        # LoopDetectionMiddleware 读 runtime.context.thread_id；若不注入会落到全局
        # 「default」桶，跨 Turn 累计相同 compose 调用后秒级 FORCED STOP。
        runtime_context["thread_id"] = thread_id
        runtime_context["run_id"] = request.turn_id
        config = {"configurable": {"thread_id": thread_id}}
        model_content = _model_facing_user_content(content)
        payload = {
            "messages": [HumanMessage(content=model_content)],
            "workspace_id": request.workspace.workspace_id,
            "conversation_id": request.conversation_id,
            "turn_id": request.turn_id,
            "plan_id": request.plan_id,
        }

        # 成稿粘贴 / 补生产字段 / 确认脚本：确定性边界先落库或启动 Tool，再交原生 Agent 续步。
        request, bootstrap_tools, payload, bootstrap_reply = (
            await self._bootstrap_production_fields_if_needed(
                request=request,
                content=content,
                runtime_context=runtime_context,
                payload=payload,
            )
        )
        if not bootstrap_tools:
            request, bootstrap_tools, payload, bootstrap_reply = (
                await self._bootstrap_complete_script_import(
                    request=request,
                    content=content,
                    runtime_context=runtime_context,
                    payload=payload,
                )
            )
        # 「重新拆解脚本」不提前 bootstrap：交给 ReAct 选 Tool；空 Turn 再走 failsafe。
        # 确认脚本走独立命令 API，不再用「确认脚本」话术 bootstrap prepare。
        if not bootstrap_tools:
            request, bootstrap_tools, payload, bootstrap_reply = (
                await self._bootstrap_no_ref_image_continue_if_needed(
                    request=request,
                    content=content,
                    runtime_context=runtime_context,
                    payload=payload,
                )
            )
        if not bootstrap_tools:
            request, bootstrap_tools, payload, bootstrap_reply = (
                await self._bootstrap_generate_scene_assets_if_needed(
                    request=request,
                    content=content,
                    runtime_context=runtime_context,
                    payload=payload,
                )
            )
        if not bootstrap_tools:
            request, bootstrap_tools, payload, bootstrap_reply = (
                await self._bootstrap_replace_scene_asset_if_needed(
                    request=request,
                    content=content,
                    runtime_context=runtime_context,
                    payload=payload,
                )
            )
        if not bootstrap_tools:
            request, bootstrap_tools, payload, bootstrap_reply = (
                await self._bootstrap_patch_scene_if_needed(
                    request=request,
                    content=content,
                    runtime_context=runtime_context,
                    payload=payload,
                )
            )
        if not bootstrap_tools:
            request, bootstrap_tools, payload, bootstrap_reply = (
                await self._bootstrap_generate_scenes_if_needed(
                    request=request,
                    content=content,
                    runtime_context=runtime_context,
                    payload=payload,
                )
            )

        target_scene = _scene_patch_target_context(content, request.workspace)
        if target_scene is not None:
            runtime_context["target_scene"] = target_scene

        # 补字段 / 成稿导入 / 重新拆解 / 生图 / 改镜 / 生成视频：
        # 已落定公开回复则禁止再进模型（避免上游 500 把已成功 Tool 冲成空转）。
        # 合并成片走 ReAct + compose_or_export_video，不做确定性 bootstrap。
        if (
            bootstrap_tools
            in {
                ("apply_production_fields",),
                ("import_script",),
                ("generate_scene_assets",),
                ("replace_scene_asset",),
                ("patch_scene",),
                ("generate_scenes",),
            }
            and isinstance(bootstrap_reply, str)
            and bootstrap_reply.strip()
        ):
            await self._emit_response_completed(request, bootstrap_reply.strip())
            return NativeVideoAgentInvokeResult(
                final_text=bootstrap_reply.strip(),
                tool_names=bootstrap_tools,
                message_count=1,
            )
        # 生图 / 改镜 / 生成视频 bootstrap 失败时 tool_names 为空，但仍有确定性回复，同样短接。
        if (
            not bootstrap_tools
            and isinstance(bootstrap_reply, str)
            and bootstrap_reply.strip()
            and any(
                token in bootstrap_reply
                for token in (
                    "generate_scene_assets",
                    "分镜",
                    "参考图尚未就绪",
                    "场景包",
                    "视频启动失败",
                )
            )
        ):
            await self._emit_response_completed(request, bootstrap_reply.strip())
            return NativeVideoAgentInvokeResult(
                final_text=bootstrap_reply.strip(),
                tool_names=(),
                message_count=1,
            )

        with bind_tool_runtime_context(runtime_context):
            # 入模前再收敛一次：bootstrap 可能改写了 messages，避免把整篇成稿塞回模型。
            payload = _payload_with_model_facing_user_message(payload, content)
            followup = _followup_instruction(content)
            opened_reasoning = False
            if _looks_like_reprepare_scene_packages(followup):
                await self._emit_bootstrap_reasoning_open(
                    self._make_publisher(request),
                    text="正在处理「重新生成视频分镜包」…",
                )
                opened_reasoning = True
            elif _looks_like_restructure_script(followup):
                # 意图唯一且 markdown 可由服务端注入：禁止再进模型空等 Thought。
                # 直接 failsafe 重拆，思考流接 progress/token，避免只停在开场句。
                await self._emit_bootstrap_reasoning_open(
                    self._make_publisher(request),
                    text="正在处理「重新拆解脚本」…",
                )
                self._install_tool_stream_reporters(
                    request=request,
                    runtime_context=runtime_context,
                    start_chunk=0,
                )
                return await self._failsafe_import_script_restructure(
                    request=request,
                    runtime_context=runtime_context,
                    announce=(
                        "正在用当前脚本重新拆解结构"
                        "（角色/场景/道具与剧本正文）…"
                    ),
                    announce_chunk_index=1,
                )
            # Gateway / failsafe 长工具：阶段文案与拆解 token 推入思考流（共享 chunk 序号）。
            # 已发 open(chunk=0) 时 start=0，后续 delta 自增；未开场时 start=-1，首条 delta 为 0。
            self._install_tool_stream_reporters(
                request=request,
                runtime_context=runtime_context,
                start_chunk=0 if opened_reasoning else -1,
            )
            reasoning_start = 0
            seq = runtime_context.get("reasoning_chunk_seq")
            if isinstance(seq, list) and seq:
                reasoning_start = max(0, int(seq[0]))
            try:
                result = await self._invoke_streaming(
                    request=request,
                    payload=payload,
                    config=config,
                    runtime_context=runtime_context,
                    fallback_response=bootstrap_reply,
                    prefer_bootstrap_tools=bootstrap_tools,
                    reasoning_chunk_start=reasoning_start,
                )
            except (TypeError, NotImplementedError) as exc:
                logger.warning("astream_events 不可用，回退 ainvoke: %s", type(exc).__name__)
                result = await self._invoke_blocking(
                    payload=payload,
                    config=config,
                    runtime_context=runtime_context,
                )
                final_text = result.final_text
                if bootstrap_reply and _should_prefer_bootstrap_reply(
                    bootstrap_tools,
                    final_text,
                ):
                    final_text = bootstrap_reply
                elif (
                    bootstrap_reply
                    and (not final_text.strip() or final_text.strip() == "已完成本轮处理")
                ):
                    final_text = bootstrap_reply
                await self._emit_response_completed(request, final_text, revision="blocking")
                result = NativeVideoAgentInvokeResult(
                    final_text=final_text,
                    tool_names=result.tool_names,
                    message_count=result.message_count,
                )
            except Exception as exc:  # noqa: BLE001
                # 仍进入 reprepare 恢复：不能让未捕获异常跳过补救并留下空转文案。
                logger.exception(
                    "原生 Agent astream 失败 conversation=%s turn=%s",
                    request.conversation_id,
                    request.turn_id,
                )
                result = NativeVideoAgentInvokeResult(
                    final_text=_public_model_failure_message(exc),
                    tool_names=(),
                    message_count=0,
                )
            if not bootstrap_tools:
                recovered = await self._recover_reprepare_empty_turn(
                    request=request,
                    content=content,
                    result=result,
                    payload=payload,
                    config=config,
                    runtime_context=runtime_context,
                )
                if recovered is not result:
                    return recovered
                return await self._recover_restructure_empty_turn(
                    request=request,
                    content=content,
                    result=result,
                    payload=payload,
                    config=config,
                    runtime_context=runtime_context,
                )
            merged = tuple(
                dict.fromkeys((*bootstrap_tools, *result.tool_names))
            )
            return NativeVideoAgentInvokeResult(
                final_text=result.final_text,
                tool_names=merged,
                message_count=result.message_count,
            )

    async def _recover_reprepare_empty_turn(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        result: NativeVideoAgentInvokeResult,
        payload: dict[str, Any],
        config: dict[str, Any],
        runtime_context: dict[str, object],
    ) -> NativeVideoAgentInvokeResult:
        """重新生成分镜包：LLM 未真正调用 prepare 时立刻 failsafe，禁止口头成功。"""

        instruction = _followup_instruction(content)
        if not _looks_like_reprepare_scene_packages(instruction):
            return result
        text = (result.final_text or "").strip()
        has_prepare = "prepare_scene_packages" in result.tool_names
        if has_prepare and text and text != "已完成本轮处理":
            return result
        if has_prepare:
            repaired = (
                "已启动重新生成视频分镜包。"
                "请打开「视频场景包」卡片查看最新分镜。"
            )
            await self._emit_response_completed(
                request, repaired, revision="reprepare-tool-ok"
            )
            return NativeVideoAgentInvokeResult(
                final_text=repaired,
                tool_names=result.tool_names,
                message_count=max(1, result.message_count),
            )

        # 已给过 LLM 一次机会仍无原生 Tool Call（含长篇思考后只口头答应）：
        # 不再二次 astream（易拖到数分钟且仍空转），直接确定性补救。
        _ = (payload, config, text)
        return await self._failsafe_prepare_scene_packages(
            request=request,
            runtime_context=runtime_context,
        )

    async def _recover_restructure_empty_turn(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        result: NativeVideoAgentInvokeResult,
        payload: dict[str, Any],
        config: dict[str, Any],
        runtime_context: dict[str, object],
    ) -> NativeVideoAgentInvokeResult:
        """重新拆解脚本：LLM 未真正调用 import_script 时立刻 failsafe。"""

        instruction = _followup_instruction(content)
        if not _looks_like_restructure_script(instruction):
            return result
        text = (result.final_text or "").strip()
        has_import = "import_script" in result.tool_names
        if has_import and text and text != "已完成本轮处理":
            return result
        if has_import:
            repaired = (
                "已按当前脚本重新拆解结构。"
                "请打开脚本预览核对后再确认方案。"
            )
            await self._emit_response_completed(
                request, repaired, revision="restructure-tool-ok"
            )
            return NativeVideoAgentInvokeResult(
                final_text=repaired,
                tool_names=result.tool_names,
                message_count=max(1, result.message_count),
            )

        _ = (payload, config, text)
        return await self._failsafe_import_script_restructure(
            request=request,
            runtime_context=runtime_context,
        )

    async def _failsafe_import_script_restructure(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        runtime_context: dict[str, object],
        announce: str | None = None,
        announce_chunk_index: int = 80,
    ) -> NativeVideoAgentInvokeResult:
        """确定性重拆：直执 import_script(force_reextract=true)。

        可由空 Turn recover 调用，也可在识别到「重新拆解」后直接调用（跳过入模空等）。
        """

        if self._registry.resolve("import_script") is None:
            fallback = (
                "本轮未能重新拆解脚本（导入工具未装配）。"
                "请稍后重试或刷新页面。"
            )
            await self._emit_response_completed(
                request, fallback, revision="restructure-failsafe-missing"
            )
            return NativeVideoAgentInvokeResult(
                final_text=fallback,
                tool_names=(),
                message_count=1,
            )

        markdown = _workspace_script_markdown_for_restructure(request.workspace)
        if not markdown:
            fallback = (
                "当前 Workspace 还没有可拆解的脚本。"
                "请先粘贴完整脚本后再试。"
            )
            await self._emit_response_completed(
                request, fallback, revision="restructure-failsafe-empty"
            )
            return NativeVideoAgentInvokeResult(
                final_text=fallback,
                tool_names=(),
                message_count=1,
            )

        publisher = self._make_publisher(request)
        tool_call_id = f"failsafe-restructure-{uuid4().hex[:12]}"
        started = time.monotonic()
        plan_id = str(runtime_context.get("plan_id") or request.plan_id or "").strip() or (
            f"plan-restructure-{request.turn_id}"
        )
        step_id = (
            str(runtime_context.get("step_id") or "").strip()
            or f"{plan_id}-restructure-failsafe"
        )
        runtime_context["plan_id"] = plan_id
        runtime_context["step_id"] = step_id

        announce_text = (
            announce
            if isinstance(announce, str) and announce.strip()
            else "模型未能发出工具调用，正在按你的明确要求重新拆解脚本…"
        )
        await self._emit_bootstrap_reasoning_open(
            publisher,
            text=announce_text,
            chunk_index=max(0, int(announce_chunk_index)),
        )
        # 覆盖/续写思考流回调：拆解 LLM 可长达数分钟，必须推 progress + token。
        self._install_tool_stream_reporters(
            request=request,
            runtime_context=runtime_context,
            start_chunk=max(0, int(announce_chunk_index)),
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="import_script",
                    tool_call_id=tool_call_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    title="重新拆解脚本",
                )
            )
        context = VideoToolContext(
            user_id=request.user_id,
            workspace=request.workspace,
            plan_id=plan_id,
            step_id=step_id,
            credential=request.credential,
            report_progress=runtime_context.get("report_progress"),
            report_thinking=runtime_context.get("report_thinking"),
        )
        execute = getattr(self._executor, "execute_tool_call", None)
        try:
            if callable(execute):
                tool_result = await execute(
                    context=context,
                    tool_name="import_script",
                    arguments={"markdown": markdown, "force_reextract": True},
                )
            else:
                tool_result = await self._registry.execute(
                    context,
                    "import_script",
                    {"markdown": markdown, "force_reextract": True},
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failsafe 重新拆解 import_script 失败 conversation=%s",
                request.conversation_id,
            )
            detail = "重新拆解脚本失败，请稍后重试"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="import_script",
                        tool_call_id=tool_call_id,
                        public_summary=detail,
                    )
                )
            await self._emit_response_completed(
                request, detail, revision="restructure-failsafe-error"
            )
            return NativeVideoAgentInvokeResult(
                final_text=detail,
                tool_names=(),
                message_count=1,
            )

        summary = str(
            getattr(tool_result, "public_summary", "") or "已重新拆解脚本"
        ).strip()
        artifact_refs = tuple(getattr(tool_result, "artifact_refs", ()) or ())
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="import_script",
                    tool_call_id=tool_call_id,
                    public_summary=summary[:500],
                    artifact_refs=artifact_refs,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )
        reply = (
            f"{summary}。"
            "请打开脚本预览核对拆解结果，确认后再进入分镜包。"
        )
        await self._emit_response_completed(
            request, reply, revision="restructure-failsafe-ok"
        )
        return NativeVideoAgentInvokeResult(
            final_text=reply,
            tool_names=("import_script",),
            message_count=1,
        )

    async def _failsafe_prepare_scene_packages(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        runtime_context: dict[str, object],
    ) -> NativeVideoAgentInvokeResult:
        """LLM 空转后的确定性补救：直执 prepare_scene_packages 并发布可读回复。"""

        if self._registry.resolve("prepare_scene_packages") is None:
            fallback = (
                "本轮未能启动重新生成视频分镜包（场景包工具未装配）。"
                "请稍后重试或刷新页面。"
            )
            await self._emit_response_completed(
                request, fallback, revision="reprepare-failsafe-missing"
            )
            return NativeVideoAgentInvokeResult(
                final_text=fallback,
                tool_names=(),
                message_count=1,
            )

        publisher = self._make_publisher(request)
        tool_call_id = f"failsafe-reprepare-{uuid4().hex[:12]}"
        started = time.monotonic()
        plan_id = str(runtime_context.get("plan_id") or request.plan_id or "").strip() or (
            f"plan-reprepare-{request.turn_id}"
        )
        step_id = (
            str(runtime_context.get("step_id") or "").strip()
            or f"{plan_id}-reprepare-failsafe"
        )
        runtime_context["plan_id"] = plan_id
        runtime_context["step_id"] = step_id
        payload_map = (
            request.workspace.payload
            if isinstance(request.workspace.payload, Mapping)
            else {}
        )
        from pixelflow.video_agent.tools.scene_packages import _resolve_prepare_attempt

        attempt = _resolve_prepare_attempt(payload_map, requested=1)

        await self._emit_bootstrap_reasoning_open(
            publisher,
            text="模型未能发出工具调用，正在按你的明确要求直接重新生成视频分镜包…",
            chunk_index=80,
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="prepare_scene_packages",
                    tool_call_id=tool_call_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    title="重新生成视频分镜包",
                )
            )
        context = VideoToolContext(
            user_id=request.user_id,
            workspace=request.workspace,
            plan_id=plan_id,
            step_id=step_id,
            credential=request.credential,
        )
        execute = getattr(self._executor, "execute_tool_call", None)
        try:
            if callable(execute):
                tool_result = await execute(
                    context=context,
                    tool_name="prepare_scene_packages",
                    arguments={"attempt": attempt},
                )
            else:
                tool_result = await self._registry.execute(
                    context,
                    "prepare_scene_packages",
                    {"attempt": attempt},
                )
        except (VideoToolValidationError, VideoToolExecutionError) as exc:
            detail = str(exc).strip()[:280] or "视频分镜包未能重新生成"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="prepare_scene_packages",
                        tool_call_id=tool_call_id,
                        public_summary=detail,
                    )
                )
            await self._emit_response_completed(
                request, detail, revision="reprepare-failsafe-fail"
            )
            return NativeVideoAgentInvokeResult(
                final_text=detail,
                tool_names=(),
                message_count=1,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failsafe prepare_scene_packages 失败 conversation=%s",
                request.conversation_id,
            )
            detail = "视频分镜包重新生成失败，请稍后重试"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="prepare_scene_packages",
                        tool_call_id=tool_call_id,
                        public_summary=detail,
                    )
                )
            await self._emit_response_completed(
                request, detail, revision="reprepare-failsafe-error"
            )
            return NativeVideoAgentInvokeResult(
                final_text=detail,
                tool_names=(),
                message_count=1,
            )

        summary = str(
            getattr(tool_result, "public_summary", "") or "已启动重新生成视频分镜包"
        ).strip()
        artifact_refs = tuple(getattr(tool_result, "artifact_refs", ()) or ())
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="prepare_scene_packages",
                    tool_call_id=tool_call_id,
                    public_summary=summary[:500],
                    artifact_refs=artifact_refs,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )
        reply = (
            f"{summary}。"
            "请打开「视频场景包」卡片查看最新分镜；确认结构后可继续生成参考图。"
        )
        await self._emit_response_completed(
            request, reply, revision="reprepare-failsafe-ok"
        )
        return NativeVideoAgentInvokeResult(
            final_text=reply,
            tool_names=("prepare_scene_packages",),
            message_count=1,
        )

    async def _bootstrap_production_fields_if_needed(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        runtime_context: dict[str, object],
        payload: dict[str, Any],
    ) -> tuple[NativeVideoAgentInvokeRequest, tuple[str, ...], dict[str, Any], str | None]:
        """fallback：短补画幅/CTA 确定性写入 script，写完短接；禁止空转「已完成本轮处理」。"""

        from pixelflow.video_agent.production_fields import (
            analyze_production_fields_with_llm,
            apply_production_fields_to_script,
            looks_like_production_field_reply,
            production_fields_form_patch,
        )

        if not _workspace_has_script(request.workspace):
            return request, (), payload, None
        instruction = _followup_instruction(content)
        if not looks_like_production_field_reply(
            instruction,
            workspace_payload=request.workspace.payload,
        ):
            return request, (), payload, None

        tool_call_id = f"bootstrap-fields-{uuid4().hex[:12]}"
        publisher = self._make_publisher(request)
        started = time.monotonic()
        await self._emit_bootstrap_reasoning_open(
            publisher,
            text="正在根据你的补充写入画幅与结尾行动引导…",
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="apply_production_fields",
                    tool_call_id=tool_call_id,
                    plan_id=request.plan_id or None,
                    step_id=(
                        f"{request.plan_id}-bootstrap-fields"
                        if request.plan_id.strip()
                        else None
                    ),
                    title="补全生产字段",
                )
            )

        try:
            analysis = await analyze_production_fields_with_llm(text=content)
            raw_script = request.workspace.payload.get("script")
            script = dict(raw_script) if isinstance(raw_script, dict) else {}
            next_script = apply_production_fields_to_script(
                script,
                analysis,
                workspace_payload=request.workspace.payload,
            )
            patch: dict[str, object] = {
                "script": next_script,
                "script_plan_confirmed": False,
                "script_plan_confirmed_version": None,
            }
            form_patch = production_fields_form_patch(analysis)
            if form_patch:
                raw_form = request.workspace.payload.get("form_values")
                next_form = dict(raw_form) if isinstance(raw_form, dict) else {}
                next_form.update(form_patch)
                patch["form_values"] = next_form
            missing = [
                str(item)
                for item in (next_script.get("missing_requirements") or [])
                if str(item).strip()
            ]
            if not missing:
                patch["awaiting_production_fields"] = False

            apply = getattr(self._video_repository, "apply_workspace_patch", None)
            if not callable(apply):
                raise RuntimeError("video_repository 缺少 apply_workspace_patch")
            from datetime import UTC, datetime

            refreshed = await apply(
                request.user_id,
                request.workspace.workspace_id,
                patch,
                expected_revision=request.workspace.revision,
                now=datetime.now(UTC),
            )
            if refreshed is None:
                get_workspace = getattr(self._video_repository, "get_workspace", None)
                if callable(get_workspace):
                    refreshed = await get_workspace(
                        request.user_id,
                        request.workspace.workspace_id,
                    )
            if refreshed is None:
                raise RuntimeError("补字段后无法读取 workspace")
        except Exception:  # noqa: BLE001
            logger.exception(
                "bootstrap production fields 失败 conversation=%s",
                request.conversation_id,
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="apply_production_fields",
                        tool_call_id=tool_call_id,
                        public_summary="生产字段写入失败，请稍后重试",
                    )
                )
            return request, (), payload, None

        ratio = next_script.get("aspect_ratio") or next_script.get("video_ratio")
        cta = next_script.get("ending_cta")
        parts: list[str] = []
        if isinstance(ratio, str) and ratio.strip():
            parts.append(f"画幅 {ratio.strip()}")
        if isinstance(cta, str) and cta.strip():
            cta_label = {
                "none": "不需要结尾引导",
                "keep": "结尾引导沿用",
                "present": "已有结尾引导",
            }.get(cta.strip(), cta.strip())
            parts.append(cta_label)
        if missing:
            summary = (
                f"已记录部分生产字段（{'、'.join(parts) if parts else '更新中'}）；"
                f"仍缺少：{'、'.join(missing)}"
            )
            reply = (
                f"{summary}。请继续补充缺失项后，我再推进视觉化生产。"
            )
        else:
            summary = f"已补全生产字段：{'、'.join(parts) if parts else '画幅与结尾引导'}"
            reply = (
                f"{summary}。脚本已就绪，请点击对话中的「在右侧查看脚本」预览并在底部确认方案，"
                f"确认后即可继续生成资产包与成片。"
            )

        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="apply_production_fields",
                    tool_call_id=tool_call_id,
                    public_summary=summary[:500],
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

        updated = replace(request, workspace=refreshed)
        runtime_context["workspace"] = refreshed
        runtime_context["revision"] = refreshed.revision
        runtime_context["workspace_id"] = refreshed.workspace_id
        note = (
            f"【系统】{summary}。请用简洁中文向用户确认，并说明下一步；"
            f"不要重复调用 import_script，也不要在正文假装调用工具。"
        )
        next_payload = dict(payload)
        next_payload["messages"] = [HumanMessage(content=f"{note}\n\n{content}")]
        next_payload["workspace_id"] = refreshed.workspace_id
        return updated, ("apply_production_fields",), next_payload, reply

    async def _bootstrap_complete_script_import(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        runtime_context: dict[str, object],
        payload: dict[str, Any],
    ) -> tuple[NativeVideoAgentInvokeRequest, tuple[str, ...], dict[str, Any], str | None]:
        """完整成稿粘贴时先确定性执行 import_script，避免模型只口头宣称。"""

        # 惰性导入，避免与 entrypoint 顶层互相引用。
        from pixelflow.video_agent.entrypoint import looks_like_complete_shooting_script

        if not looks_like_complete_shooting_script(content):
            return request, (), payload, None
        if _workspace_has_script(request.workspace):
            return request, (), payload, None
        if self._registry.resolve("import_script") is None:
            return request, (), payload, None

        markdown = _script_markdown_for_import(content)
        if not markdown:
            return request, (), payload, None

        tool_call_id = f"bootstrap-import-{uuid4().hex[:12]}"
        publisher = self._make_publisher(request)
        started = time.monotonic()
        # 先发思考流，再发活动：保证 UI 顺序为 Thought → Activity → 结论。
        await self._emit_bootstrap_reasoning_open(
            publisher,
            text="检测到完整拍摄脚本，正在导入工作区并拆解角色、场景与分镜…",
        )
        self._install_tool_stream_reporters(
            request=request,
            runtime_context=runtime_context,
            start_chunk=0,
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="import_script",
                    tool_call_id=tool_call_id,
                    plan_id=request.plan_id or None,
                    step_id=(
                        f"{request.plan_id}-bootstrap-import"
                        if request.plan_id.strip()
                        else None
                    ),
                    title="导入完整脚本",
                )
            )

        context = VideoToolContext(
            user_id=request.user_id,
            workspace=request.workspace,
            plan_id=str(runtime_context.get("plan_id") or "") or None,
            step_id=str(runtime_context.get("step_id") or "") or None,
            credential=request.credential,
            report_progress=runtime_context.get("report_progress"),
            report_thinking=runtime_context.get("report_thinking"),
        )
        execute = getattr(self._executor, "execute_tool_call", None)
        try:
            if callable(execute):
                result = await execute(
                    context=context,
                    tool_name="import_script",
                    arguments={"markdown": markdown},
                )
            else:
                result = await self._registry.execute(
                    context,
                    "import_script",
                    {"markdown": markdown},
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "bootstrap import_script 失败 conversation=%s workspace=%s",
                request.conversation_id,
                request.workspace.workspace_id,
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="import_script",
                        tool_call_id=tool_call_id,
                        public_summary="脚本导入失败，请稍后重试或缩短正文后再试",
                    )
                )
            return request, (), payload, None

        summary = str(getattr(result, "public_summary", "") or "已导入脚本").strip()
        artifact_refs = tuple(getattr(result, "artifact_refs", ()) or ())
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="import_script",
                    tool_call_id=tool_call_id,
                    public_summary=summary[:500],
                    artifact_refs=artifact_refs,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

        refreshed = request.workspace
        get_workspace = getattr(self._video_repository, "get_workspace", None)
        if callable(get_workspace):
            loaded = await get_workspace(
                request.user_id,
                request.workspace.workspace_id,
            )
            if loaded is not None:
                refreshed = loaded

        if not _workspace_has_script(refreshed):
            # Executor 未写库（例如缺 patch）时仍视为失败，避免误导 Agent。
            logger.warning(
                "bootstrap import_script 未在 workspace 落库 conversation=%s",
                request.conversation_id,
            )
            return request, (), payload, None

        updated = replace(request, workspace=refreshed)
        runtime_context["workspace"] = refreshed
        runtime_context["revision"] = refreshed.revision
        runtime_context["workspace_id"] = refreshed.workspace_id
        script_payload = refreshed.payload.get("script")
        missing: list[str] = []
        if isinstance(script_payload, Mapping):
            missing = [
                str(item)
                for item in (script_payload.get("missing_requirements") or [])
                if str(item).strip()
            ]
        if missing:
            reply = (
                f"{summary}。"
                f"请补充：{'、'.join(missing)}；"
                f"补齐后即可在右侧预览脚本并确认方案。"
            )
        else:
            reply = (
                f"{summary}。"
                f"请点击对话中的「在右侧查看脚本」预览并确认方案，"
                f"确认后即可继续生成资产包与成片。"
            )
        # 短接后不再进模型；保留 payload 注记仅供回退路径使用。
        note = (
            f"【系统】完整拍摄脚本已由服务端调用 import_script 写入 VideoWorkspace"
            f"（{summary}）。请基于工作区检查缺失生产字段并选择下一步；"
            f"不要重复导入同一正文，也不要在回复正文中假装调用工具。"
        )
        next_payload = dict(payload)
        next_payload["messages"] = [HumanMessage(content=f"{note}\n\n{content}")]
        next_payload["workspace_id"] = refreshed.workspace_id
        return updated, ("import_script",), next_payload, reply

    async def _bootstrap_no_ref_image_continue_if_needed(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        runtime_context: dict[str, object],
        payload: dict[str, Any],
    ) -> tuple[NativeVideoAgentInvokeRequest, tuple[str, ...], dict[str, Any], str | None]:
        """场景包已有、尚无参考图时，用户说「没有参考图」→ 引导选模型，禁止空转结束。

        若已有包明显少于脚本时间线镜数（历史误拆成 2 镜），先重跑 prepare。
        """

        from pixelflow.video_agent.production_fields import looks_like_scene_asset_continue
        from pixelflow.video_agent.workspace.digest import summarize_scene_asset_status

        instruction = _followup_instruction(content)
        if not looks_like_scene_asset_continue(instruction):
            return request, (), payload, None
        if not _workspace_has_scene_packages(request.workspace):
            return request, (), payload, None
        payload_map = (
            request.workspace.payload
            if isinstance(request.workspace.payload, Mapping)
            else {}
        )
        if summarize_scene_asset_status(payload_map)["scene_assets_ready"] is True:
            return request, (), payload, None

        refreshed = request.workspace
        bootstrap_tools: tuple[str, ...] = ()
        if _scene_packages_need_script_refresh(request.workspace):
            if self._registry.resolve("prepare_scene_packages") is None:
                return request, (), payload, None
            publisher = self._make_publisher(request)
            tool_call_id = f"bootstrap-reprepare-{uuid4().hex[:12]}"
            started = time.monotonic()
            await self._emit_bootstrap_reasoning_open(
                publisher,
                text="发现场景包镜数与脚本时间线不一致，正在按脚本重拆分镜…",
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_started(
                        tool_name="prepare_scene_packages",
                        tool_call_id=tool_call_id,
                        plan_id=request.plan_id or None,
                        step_id=(
                            f"{request.plan_id}-bootstrap-reprepare"
                            if request.plan_id.strip()
                            else None
                        ),
                        title="按脚本重拆分镜",
                    )
                )
            context = VideoToolContext(
                user_id=request.user_id,
                workspace=request.workspace,
                plan_id=str(runtime_context.get("plan_id") or "") or None,
                step_id=str(runtime_context.get("step_id") or "") or None,
                credential=request.credential,
            )
            execute = getattr(self._executor, "execute_tool_call", None)
            try:
                if callable(execute):
                    result = await execute(
                        context=context,
                        tool_name="prepare_scene_packages",
                        arguments={},
                    )
                else:
                    result = await self._registry.execute(
                        context,
                        "prepare_scene_packages",
                        {},
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "bootstrap 重拆 prepare_scene_packages 失败 conversation=%s",
                    request.conversation_id,
                )
                if publisher is not None:
                    await self._safe_publish(
                        publisher.tool_failed(
                            tool_name="prepare_scene_packages",
                            tool_call_id=tool_call_id,
                            public_summary="按脚本重拆分镜失败，请稍后重试",
                        )
                    )
                return request, (), payload, None
            summary = str(
                getattr(result, "public_summary", "") or "已按脚本重拆分镜"
            ).strip()
            artifact_refs = tuple(getattr(result, "artifact_refs", ()) or ())
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_completed(
                        tool_name="prepare_scene_packages",
                        tool_call_id=tool_call_id,
                        public_summary=summary[:500],
                        artifact_refs=artifact_refs,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                )
            get_workspace = getattr(self._video_repository, "get_workspace", None)
            if callable(get_workspace):
                loaded = await get_workspace(
                    request.user_id,
                    request.workspace.workspace_id,
                )
                if loaded is not None:
                    refreshed = loaded
            runtime_context["workspace"] = refreshed
            runtime_context["revision"] = refreshed.revision
            runtime_context["workspace_id"] = refreshed.workspace_id
            bootstrap_tools = ("prepare_scene_packages",)

        package_count = 0
        raw_packages = refreshed.payload.get("scene_packages") or refreshed.payload.get(
            "scenes"
        )
        if isinstance(raw_packages, list):
            package_count = sum(1 for item in raw_packages if isinstance(item, Mapping))
        note = (
            "【系统】场景包结构已就绪，用户确认没有参考图。"
            "请引导用户在对话中选择生图模型（image-2 / Seedream 5.0）；"
            "选定模型后再调用 generate_scene_assets。"
            "不要回复「已完成本轮处理」，不要跳过参考图直接 generate_scenes。"
        )
        if bootstrap_tools:
            note = (
                "【系统】已按脚本时间线重拆场景包，用户确认没有参考图。"
                "请引导用户选择生图模型后再调用 generate_scene_assets。"
            )
        next_payload = dict(payload)
        next_payload["messages"] = [HumanMessage(content=f"{note}\n\n{content}")]
        next_payload["workspace_id"] = refreshed.workspace_id
        if package_count > 0:
            reply = (
                f"好的，没有参考图也可以。当前视频场景包共 {package_count} 个分镜。"
                "请在下方卡片选择生图模型（image-2 或 Seedream 5.0），"
                "确认后开始生成角色、场景与道具参考图。"
            )
        else:
            reply = (
                "好的，没有参考图也可以。"
                "请在下方卡片选择生图模型（image-2 或 Seedream 5.0），确认后开始生成参考图。"
            )
        updated = replace(request, workspace=refreshed)
        return updated, bootstrap_tools, next_payload, reply

    async def _bootstrap_generate_scene_assets_if_needed(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        runtime_context: dict[str, object],
        payload: dict[str, Any],
    ) -> tuple[NativeVideoAgentInvokeRequest, tuple[str, ...], dict[str, Any], str | None]:
        """FE 模型选择卡确认后：确定性启动 generate_scene_assets，禁止空转「已完成本轮处理」。

        模型卡本身即计费确认闸门；此处走 Registry/Executor 直执，不经 Gateway 二次确认。
        """

        instruction = _followup_instruction(content)
        parsed = _parse_scene_asset_model_confirm(instruction)
        workspace_payload = (
            request.workspace.payload
            if isinstance(request.workspace.payload, Mapping)
            else {}
        )
        from pixelflow.video_agent.workspace.digest import summarize_scene_asset_status

        asset_status = summarize_scene_asset_status(workspace_payload)
        target_assets = list(asset_status["scene_asset_missing_targets"])
        if parsed is None:
            compact = re.sub(r"\s+", "", instruction)
            is_retry = (
                asset_status["scene_asset_status"] in {"partial", "failed"}
                and (
                    compact in {"继续", "继续生成", "继续生图", "重试"}
                    or bool(re.search(r"(?:继续|重试).{0,12}(?:参考图|生图)", compact))
                )
            )
            contract = workspace_payload.get("creation_contract")
            contract_map = contract if isinstance(contract, Mapping) else {}
            prior_job = workspace_payload.get("scene_asset_job")
            job_map = prior_job if isinstance(prior_job, Mapping) else {}
            stored_model = str(
                contract_map.get("image_model") or job_map.get("image_model") or ""
            ).strip()
            if not is_retry or stored_model not in {"gpt-image-2", "seeddream-5.0"}:
                return request, (), payload, None
            parsed = (
                stored_model,
                str(contract_map.get("scene_image_ratio") or "9:16"),
                str(contract_map.get("scene_image_size") or "2K"),
                str(contract_map.get("reference_brief") or ""),
            )
        if not _workspace_has_scene_packages(request.workspace):
            return request, (), payload, None
        if self._registry.resolve("generate_scene_assets") is None:
            return request, (), payload, None

        image_model, image_ratio, image_size, reference_brief = parsed
        tool_call_id = f"bootstrap-assets-{uuid4().hex[:12]}"
        publisher = self._make_publisher(request)
        started = time.monotonic()
        # Operation 需要成对 plan/step；缺省时用 Turn 派生身份，避免「缺少计划身份」秒失败。
        plan_id = str(runtime_context.get("plan_id") or request.plan_id or "").strip() or (
            f"plan-bootstrap-assets-{request.turn_id}"
        )
        step_id = str(runtime_context.get("step_id") or "").strip() or f"{plan_id}-bootstrap-assets"
        runtime_context["plan_id"] = plan_id
        runtime_context["step_id"] = step_id

        # 旧包可能镜数对了但 global_assets 被空蓝图需求清空；生图前先按脚本重拆结构。
        workspace = request.workspace
        if _workspace_global_assets_empty(workspace) or _workspace_global_assets_look_like_field_labels(
            workspace
        ):
            if self._registry.resolve("prepare_scene_packages") is not None:
                await self._emit_bootstrap_reasoning_open(
                    publisher,
                    text="场景包资产名异常或为空，正在按脚本预览重拆角色/场景/道具…",
                )
                prep_context = VideoToolContext(
                    user_id=request.user_id,
                    workspace=workspace,
                    plan_id=plan_id,
                    step_id=f"{plan_id}-bootstrap-reprepare-assets",
                    credential=request.credential,
                )
                execute_prep = getattr(self._executor, "execute_tool_call", None)
                try:
                    if callable(execute_prep):
                        await execute_prep(
                            context=prep_context,
                            tool_name="prepare_scene_packages",
                            arguments={},
                        )
                    else:
                        await self._registry.execute(
                            prep_context,
                            "prepare_scene_packages",
                            {},
                        )
                    get_workspace = getattr(self._video_repository, "get_workspace", None)
                    if callable(get_workspace):
                        loaded = await get_workspace(
                            request.user_id,
                            workspace.workspace_id,
                        )
                        if loaded is not None:
                            workspace = loaded
                            runtime_context["workspace"] = workspace
                            runtime_context["revision"] = workspace.revision
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "bootstrap 生图前重拆 prepare 失败 conversation=%s",
                        request.conversation_id,
                    )

        workspace_ratio = workspace_resolved_aspect_ratio(
            workspace.payload if isinstance(workspace.payload, Mapping) else {}
        )
        if workspace_ratio is not None:
            image_ratio = workspace_ratio

        await self._emit_bootstrap_reasoning_open(
            publisher,
            text=f"已确认生图模型 {image_model}，正在调用 generate_scene_assets 生成角色/场景/道具参考图…",
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="generate_scene_assets",
                    tool_call_id=tool_call_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    title="生成场景参考图",
                )
            )

        # 把模型写入 creation_contract，供后续 Operation / 卡片投影使用。
        apply = getattr(self._video_repository, "apply_workspace_patch", None)
        if callable(apply):
            from datetime import UTC, datetime

            payload_map = (
                workspace.payload if isinstance(workspace.payload, Mapping) else {}
            )
            raw_contract = payload_map.get("creation_contract")
            next_contract = dict(raw_contract) if isinstance(raw_contract, Mapping) else {}
            next_contract.update(
                {
                    "image_model": image_model,
                    "scene_image_ratio": image_ratio,
                    "scene_image_size": image_size,
                }
            )
            try:
                workspace = await apply(
                    request.user_id,
                    workspace.workspace_id,
                    {"creation_contract": next_contract},
                    expected_revision=workspace.revision,
                    now=datetime.now(UTC),
                )
                runtime_context["workspace"] = workspace
                runtime_context["revision"] = workspace.revision
            except Exception:  # noqa: BLE001
                logger.warning(
                    "bootstrap 写入 creation_contract 失败 conversation=%s",
                    request.conversation_id,
                    exc_info=True,
                )

        context = VideoToolContext(
            user_id=request.user_id,
            workspace=workspace,
            plan_id=plan_id,
            step_id=step_id,
            credential=request.credential,
        )
        arguments: dict[str, object] = {
            "image_model": image_model,
            "image_ratio": image_ratio,
            "image_size": image_size,
            "reference_brief": reference_brief,
            "target_assets": target_assets,
        }
        execute = getattr(self._executor, "execute_tool_call", None)
        try:
            if callable(execute):
                result = await execute(
                    context=context,
                    tool_name="generate_scene_assets",
                    arguments=arguments,
                )
            else:
                result = await self._registry.execute(
                    context,
                    "generate_scene_assets",
                    arguments,
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "bootstrap generate_scene_assets 失败 conversation=%s workspace=%s",
                request.conversation_id,
                workspace.workspace_id,
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="generate_scene_assets",
                        tool_call_id=tool_call_id,
                        public_summary=(
                            "generate_scene_assets 未能启动，请稍后重试或重新选择生图模型"
                        ),
                    )
                )
            return request, (), payload, (
                "参考图工具 generate_scene_assets 启动失败，请稍后重试或重新选择生图模型。"
            )

        summary = str(
            getattr(result, "public_summary", "") or "参考图生成任务已启动"
        ).strip()
        artifact_refs = tuple(getattr(result, "artifact_refs", ()) or ())
        if _generate_scene_assets_result_failed(result):
            fail_summary = (
                f"generate_scene_assets 执行失败：{summary}"
                if summary and "generate_scene_assets" not in summary
                else (summary or "generate_scene_assets 执行失败，请稍后重试")
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="generate_scene_assets",
                        tool_call_id=tool_call_id,
                        public_summary=fail_summary[:500],
                    )
                )
            return request, (), payload, (
                f"{fail_summary}。可打开「视频场景包」检查角色/场景/道具是否正确，"
                "再重新选择生图模型。"
            )

        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="generate_scene_assets",
                    tool_call_id=tool_call_id,
                    public_summary=summary[:500],
                    artifact_refs=artifact_refs,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

        refreshed = workspace
        get_workspace = getattr(self._video_repository, "get_workspace", None)
        if callable(get_workspace):
            loaded = await get_workspace(request.user_id, workspace.workspace_id)
            if loaded is not None:
                refreshed = loaded
        runtime_context["workspace"] = refreshed
        runtime_context["revision"] = refreshed.revision
        runtime_context["workspace_id"] = refreshed.workspace_id

        note = (
            "【系统】用户已确认生图模型；服务端已调用 generate_scene_assets。"
            "请用简洁中文告诉用户：参考图正在生成，可打开「视频场景包」卡片查看进度；"
            "不要回复「已完成本轮处理」，不要再次要求选择模型。"
        )
        next_payload = dict(payload)
        next_payload["messages"] = [HumanMessage(content=f"{note}\n\n{content}")]
        next_payload["workspace_id"] = refreshed.workspace_id
        refreshed_asset_status = summarize_scene_asset_status(
            refreshed.payload if isinstance(refreshed.payload, Mapping) else {}
        )
        if refreshed_asset_status["scene_asset_status"] in {"partial", "failed"}:
            reply = (
                "本轮参考图已完成 "
                f"{refreshed_asset_status['scene_asset_ready_count']}/"
                f"{refreshed_asset_status['scene_asset_required_count']}，"
                f"剩余 {refreshed_asset_status['scene_asset_missing_count']} 项待生成。"
                "已保留成功结果，回复「继续生成」可只重试未完成资产。"
            )
        else:
            reply = (
                f"已选择 {image_model}（{image_ratio} / {image_size}），"
                "已启动 generate_scene_assets 生成参考图。"
                "生成过程中可打开「视频场景包」卡片查看角色、场景与道具；完成后会自动更新参考图。"
            )
        return replace(request, workspace=refreshed), ("generate_scene_assets",), next_payload, reply

    async def _bootstrap_replace_scene_asset_if_needed(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        runtime_context: dict[str, object],
        payload: dict[str, Any],
    ) -> tuple[NativeVideoAgentInvokeRequest, tuple[str, ...], dict[str, Any], str | None]:
        """把工作台素材选择确定性写入 Workspace，避免模型重建数字人参数。"""

        arguments = _parse_structured_scene_asset_replacement(content)
        if arguments is None or self._registry.resolve("replace_scene_asset") is None:
            return request, (), payload, None
        tool_call_id = f"bootstrap-replace-asset-{uuid4().hex[:12]}"
        publisher = self._make_publisher(request)
        started = time.monotonic()
        plan_id = str(runtime_context.get("plan_id") or request.plan_id or "").strip() or (
            f"plan-bootstrap-replace-asset-{request.turn_id}"
        )
        step_id = str(runtime_context.get("step_id") or "").strip() or (
            f"{plan_id}-bootstrap-replace-asset"
        )
        runtime_context["plan_id"] = plan_id
        runtime_context["step_id"] = step_id
        asset_id = str(arguments.get("asset_id") or "").strip()

        await self._emit_bootstrap_reasoning_open(
            publisher,
            text="正在将所选角色素材写入视频场景包…",
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="replace_scene_asset",
                    tool_call_id=tool_call_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    title="替换场景包素材",
                )
            )
        context = VideoToolContext(
            user_id=request.user_id,
            workspace=request.workspace,
            plan_id=plan_id,
            step_id=step_id,
            credential=request.credential,
        )
        execute = getattr(self._executor, "execute_tool_call", None)
        try:
            if callable(execute):
                result = await execute(
                    context=context,
                    tool_name="replace_scene_asset",
                    arguments=arguments,
                )
            else:
                result = await self._registry.execute(
                    context,
                    "replace_scene_asset",
                    arguments,
                )
        except VideoToolValidationError as exc:
            detail = str(exc).strip()[:280] or "场景包素材替换参数无效"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="replace_scene_asset",
                        tool_call_id=tool_call_id,
                        public_summary=detail,
                    )
                )
            return request, (), payload, detail
        except Exception:  # noqa: BLE001
            logger.exception(
                "bootstrap replace_scene_asset 失败 conversation=%s asset=%s",
                request.conversation_id,
                asset_id,
            )
            detail = "场景包素材替换未能写入，请重试"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="replace_scene_asset",
                        tool_call_id=tool_call_id,
                        public_summary=detail,
                    )
                )
            return request, (), payload, detail

        summary = str(getattr(result, "public_summary", "") or "").strip()
        patch_map = getattr(result, "workspace_patch", None)
        if not isinstance(patch_map, Mapping) or not patch_map:
            detail = summary or "场景包素材替换参数无效"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="replace_scene_asset",
                        tool_call_id=tool_call_id,
                        public_summary=detail[:500],
                    )
                )
            return request, (), payload, detail[:280]
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="replace_scene_asset",
                    tool_call_id=tool_call_id,
                    public_summary=(summary or "场景包素材已替换")[:500],
                    artifact_refs=tuple(getattr(result, "artifact_refs", ()) or ()),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

        refreshed = request.workspace
        get_workspace = getattr(self._video_repository, "get_workspace", None)
        if callable(get_workspace):
            loaded = await get_workspace(request.user_id, request.workspace.workspace_id)
            if loaded is not None:
                refreshed = loaded
        runtime_context["workspace"] = refreshed
        runtime_context["revision"] = refreshed.revision
        reply = "已替换所选角色素材，引用该角色的镜头已标记为待重新生成。"
        return replace(request, workspace=refreshed), ("replace_scene_asset",), payload, reply

    async def _bootstrap_patch_scene_if_needed(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        runtime_context: dict[str, object],
        payload: dict[str, Any],
    ) -> tuple[NativeVideoAgentInvokeRequest, tuple[str, ...], dict[str, Any], str | None]:
        """FE 分镜面板结构化「修改分镜 scene-X。…」：确定性 patch_scene，禁止空转。"""

        parsed = _parse_structured_scene_patch(content)
        if parsed is None:
            return request, (), payload, None
        if not _workspace_has_scene_packages(request.workspace):
            return request, (), payload, None
        if self._registry.resolve("patch_scene") is None:
            return request, (), payload, None

        scene_id, patch = parsed
        tool_call_id = f"bootstrap-patch-{uuid4().hex[:12]}"
        publisher = self._make_publisher(request)
        started = time.monotonic()
        plan_id = str(runtime_context.get("plan_id") or request.plan_id or "").strip() or (
            f"plan-bootstrap-patch-{request.turn_id}"
        )
        step_id = str(runtime_context.get("step_id") or "").strip() or f"{plan_id}-bootstrap-patch"
        runtime_context["plan_id"] = plan_id
        runtime_context["step_id"] = step_id

        await self._emit_bootstrap_reasoning_open(
            publisher,
            text=f"正在将分镜 {scene_id} 的修改写入工作区…",
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="patch_scene",
                    tool_call_id=tool_call_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    title="修改分镜",
                )
            )

        context = VideoToolContext(
            user_id=request.user_id,
            workspace=request.workspace,
            plan_id=plan_id,
            step_id=step_id,
            credential=request.credential,
        )
        arguments: dict[str, object] = {"scene_id": scene_id, "patch": patch}
        execute = getattr(self._executor, "execute_tool_call", None)
        try:
            if callable(execute):
                result = await execute(
                    context=context,
                    tool_name="patch_scene",
                    arguments=arguments,
                )
            else:
                result = await self._registry.execute(context, "patch_scene", arguments)
        except VideoToolValidationError as exc:
            detail = str(exc).strip()[:280] or f"分镜 {scene_id} 修改参数无效"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="patch_scene",
                        tool_call_id=tool_call_id,
                        public_summary=detail,
                    )
                )
            return request, (), payload, detail
        except Exception:  # noqa: BLE001
            logger.exception(
                "bootstrap patch_scene 失败 conversation=%s scene=%s",
                request.conversation_id,
                scene_id,
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="patch_scene",
                        tool_call_id=tool_call_id,
                        public_summary=f"分镜 {scene_id} 修改未能写入，请重试",
                    )
                )
            return request, (), payload, (
                f"分镜 {scene_id} 修改写入失败，请稍后在分镜面板重试保存。"
            )

        summary = str(getattr(result, "public_summary", "") or "").strip()
        patch_map = getattr(result, "workspace_patch", None)
        # Registry 对校验失败会吞成空 patch 的 VideoToolResult，不得假成功。
        if not isinstance(patch_map, Mapping) or not patch_map:
            detail = summary or f"分镜 {scene_id} 修改参数无效"
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="patch_scene",
                        tool_call_id=tool_call_id,
                        public_summary=detail[:500],
                    )
                )
            return request, (), payload, detail[:280]

        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="patch_scene",
                    tool_call_id=tool_call_id,
                    public_summary=(summary or f"镜头 {scene_id} 已更新")[:500],
                    artifact_refs=tuple(getattr(result, "artifact_refs", ()) or ()),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

        refreshed = request.workspace
        get_workspace = getattr(self._video_repository, "get_workspace", None)
        if callable(get_workspace):
            loaded = await get_workspace(request.user_id, request.workspace.workspace_id)
            if loaded is not None:
                refreshed = loaded
        runtime_context["workspace"] = refreshed
        runtime_context["revision"] = refreshed.revision

        reply = (
            f"已更新分镜 {scene_id}，并标记为待重新生成。"
            "可继续改其他镜头，或点击「确认并生成视频」只重生成已修改镜头。"
        )
        return replace(request, workspace=refreshed), ("patch_scene",), payload, reply

    async def _bootstrap_generate_scenes_if_needed(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        content: str,
        runtime_context: dict[str, object],
        payload: dict[str, Any],
    ) -> tuple[NativeVideoAgentInvokeRequest, tuple[str, ...], dict[str, Any], str | None]:
        """「确认并生成分镜视频 / 生成视频吧」：确定性 generate_scenes，禁止空转。

        FE 确认按钮即计费确认闸门；此处直执 Registry/Executor，不经 Gateway 二次确认。
        """

        mode = _parse_generate_scenes_intent(content)
        if mode is None:
            return request, (), payload, None
        if not _workspace_has_scene_packages(request.workspace):
            return request, (), payload, (
                "当前还没有视频场景包。请先确认脚本并生成场景包，再生成分镜视频。"
            )
        if not _workspace_has_scene_asset_images(request.workspace):
            return request, (), payload, (
                "参考图尚未就绪。请先在对话中选择生图模型生成角色/场景/道具参考图，"
                "完成后再确认生成视频。"
            )
        if self._registry.resolve("generate_scenes") is None:
            return request, (), payload, None

        scene_ids = _workspace_scene_ids(request.workspace)
        dirty_ids = _workspace_dirty_scene_ids(request.workspace)
        selected_ids: list[str]
        if mode == "dirty":
            selected_ids = dirty_ids or scene_ids
        elif mode == "retry":
            selected_ids = _parse_scene_ids_from_paren(content) or dirty_ids or scene_ids
        else:
            # 「确认并生成分镜视频（scene-x）」只生成括号内分镜；无括号则全量。
            selected_ids = _parse_scene_ids_from_paren(content) or scene_ids
        if not selected_ids:
            return request, (), payload, (
                "没有可生成的分镜。请先在「视频场景包」中确认结构，或修改后再试。"
            )

        tool_call_id = f"bootstrap-scenes-{uuid4().hex[:12]}"
        publisher = self._make_publisher(request)
        started = time.monotonic()
        plan_id = str(runtime_context.get("plan_id") or request.plan_id or "").strip() or (
            f"plan-bootstrap-scenes-{request.turn_id}"
        )
        step_id = str(runtime_context.get("step_id") or "").strip() or f"{plan_id}-bootstrap-scenes"
        runtime_context["plan_id"] = plan_id
        runtime_context["step_id"] = step_id

        await self._emit_bootstrap_reasoning_open(
            publisher,
            text=f"正在启动 generate_scenes，生成 {len(selected_ids)} 个分镜视频…",
        )
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_started(
                    tool_name="generate_scenes",
                    tool_call_id=tool_call_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    title="生成分镜视频",
                )
            )

        context = VideoToolContext(
            user_id=request.user_id,
            workspace=request.workspace,
            plan_id=plan_id,
            step_id=step_id,
            credential=request.credential,
        )
        arguments: dict[str, object] = {
            "scene_ids": tuple(selected_ids),
            "variant_count": 1,
            "attempt": 1,
        }
        execute = getattr(self._executor, "execute_tool_call", None)
        try:
            if callable(execute):
                result = await execute(
                    context=context,
                    tool_name="generate_scenes",
                    arguments=arguments,
                )
            else:
                result = await self._registry.execute(context, "generate_scenes", arguments)
        except (VideoToolValidationError, VideoToolExecutionError) as exc:
            detail = str(exc).strip()[:280] or "分镜视频未能启动，请稍后重试"
            logger.warning(
                "bootstrap generate_scenes 业务失败 conversation=%s detail=%s",
                request.conversation_id,
                type(exc).__name__,
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="generate_scenes",
                        tool_call_id=tool_call_id,
                        public_summary=detail,
                    )
                )
            return request, (), payload, detail
        except Exception:  # noqa: BLE001
            logger.exception(
                "bootstrap generate_scenes 失败 conversation=%s",
                request.conversation_id,
            )
            if publisher is not None:
                await self._safe_publish(
                    publisher.tool_failed(
                        tool_name="generate_scenes",
                        tool_call_id=tool_call_id,
                        public_summary="分镜视频未能启动，请稍后重试",
                    )
                )
            return request, (), payload, "分镜视频启动失败，请稍后重试或检查场景包与参考图是否完整。"

        summary = str(
            getattr(result, "public_summary", "") or f"已启动 {len(selected_ids)} 个分镜视频生成"
        ).strip()
        if publisher is not None:
            await self._safe_publish(
                publisher.tool_completed(
                    tool_name="generate_scenes",
                    tool_call_id=tool_call_id,
                    public_summary=summary[:500],
                    artifact_refs=tuple(getattr(result, "artifact_refs", ()) or ()),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

        refreshed = request.workspace
        get_workspace = getattr(self._video_repository, "get_workspace", None)
        if callable(get_workspace):
            loaded = await get_workspace(request.user_id, request.workspace.workspace_id)
            if loaded is not None:
                refreshed = loaded
        runtime_context["workspace"] = refreshed
        runtime_context["revision"] = refreshed.revision

        reply = (
            f"已启动 {len(selected_ids)} 个分镜视频生成。"
            "底栏会切换到「执行规划 · 分镜视频」显示进度；"
            "完成后打开「视频场景包」→「查看分镜」即可预览成片（生成中也会回填已完成片段）。"
        )
        return replace(request, workspace=refreshed), ("generate_scenes",), payload, reply

    def _install_tool_stream_reporters(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        runtime_context: dict[str, object],
        start_chunk: int = 0,
    ) -> None:
        """把 Tool 的 progress / thinking 接到原生 reasoning_summary 流。

        Gateway 构造 VideoToolContext 时从 runtime_context 读取这两个回调；
        未安装时 import_script 拆解只会静默跑完，UI 停在「思考中」开场句。
        """

        publisher = self._make_publisher(request)
        seq = runtime_context.get("reasoning_chunk_seq")
        # start_chunk=-1：尚无开场，首条 delta 用 chunk 0；>=0：已占用该 index，下一条自增。
        initial = int(start_chunk)
        if not isinstance(seq, list) or not seq:
            seq = [initial]
            runtime_context["reasoning_chunk_seq"] = seq
        else:
            seq[0] = max(int(seq[0]), initial)

        thinking_pending = {"text": ""}

        async def _publish_reasoning(delta: str) -> None:
            text = (delta or "").strip()
            if publisher is None or not text:
                return
            seq[0] = int(seq[0]) + 1
            await self._safe_publish(
                publisher.reasoning_summary_delta(
                    delta=text[:800],
                    chunk_index=int(seq[0]),
                )
            )

        async def _report_progress(message: str, *, phase: str) -> None:
            label = (message or "").strip()
            phase_key = (phase or "").strip()
            if not label:
                return
            prefix = f"[{phase_key}] " if phase_key else ""
            await _publish_reasoning(f"{prefix}{label}")

        async def _report_thinking(piece: str) -> None:
            # 拆解 token 很密：缓冲后再推，避免刷爆 SSE / event_id。
            chunk = piece or ""
            if not chunk:
                return
            thinking_pending["text"] += chunk
            pending = thinking_pending["text"]
            if len(pending) < max(24, _REASONING_DELTA_MIN_CHARS):
                return
            thinking_pending["text"] = ""
            await _publish_reasoning(pending)

        runtime_context["report_progress"] = _report_progress
        runtime_context["report_thinking"] = _report_thinking

    async def _emit_bootstrap_reasoning_open(
        self,
        publisher: NativeAgentEventPublisher | None,
        *,
        text: str,
        chunk_index: int = 0,
    ) -> None:
        """Bootstrap 工具前先打开思考流，避免活动卡抢在 Thought 之前。

        默认占用 reasoning chunk_index=0；同 Turn 再次开场须传入更大 index，
        否则确定性 event_id 冲突，思考流发布失败。
        """

        summary = (text or "").strip()
        if publisher is None or not summary:
            return
        await self._safe_publish(
            publisher.reasoning_summary_delta(
                delta=summary,
                chunk_index=max(0, int(chunk_index)),
            )
        )

    async def _invoke_streaming(
        self,
        *,
        request: NativeVideoAgentInvokeRequest,
        payload: dict[str, Any],
        config: dict[str, Any],
        runtime_context: dict[str, object],
        fallback_response: str | None = None,
        prefer_bootstrap_tools: tuple[str, ...] = (),
        response_revision: str = "final",
        reasoning_chunk_start: int = 0,
    ) -> NativeVideoAgentInvokeResult:
        publisher = self._make_publisher(request)
        started = time.monotonic()
        safe_reasoning_started = False
        # 若同 Turn 已发过 bootstrap open(chunk=0)，从此处继续，避免 event_id 冲突。
        reasoning_chunk_i = max(0, int(reasoning_chunk_start))
        response_chunk_i = 0
        # 公开回答只接受「无 Tool Call / 无伪 markup」的模型轮次；多轮时以后一轮为准。
        public_response = ""
        gen_content = ""
        gen_pending = ""
        gen_streamed_len = 0
        gen_blocked = False
        final_state: Mapping[str, Any] | None = None
        stream_error: BaseException | None = None

        def _reset_generation() -> None:
            nonlocal gen_content, gen_pending, gen_streamed_len, gen_blocked
            gen_content = ""
            gen_pending = ""
            gen_streamed_len = 0
            gen_blocked = False

        async def _publish_response_pending(*, force: bool = False) -> None:
            nonlocal response_chunk_i, gen_pending
            if not gen_pending or publisher is None:
                return
            if not force and len(gen_pending) < _RESPONSE_DELTA_MIN_CHARS:
                return
            response_chunk_i += 1
            await self._safe_publish(
                publisher.response_delta(
                    delta=gen_pending,
                    chunk_index=response_chunk_i,
                )
            )
            gen_pending = ""

        async def _consume(stream) -> None:
            nonlocal safe_reasoning_started, reasoning_chunk_i
            nonlocal public_response, gen_content, gen_pending, gen_streamed_len
            nonlocal gen_blocked, final_state
            async for event in stream:
                kind = event.get("event") if isinstance(event, dict) else None
                data = event.get("data") if isinstance(event, dict) else None
                if kind == "on_chat_model_start":
                    _reset_generation()
                    continue
                if kind == "on_chat_model_stream" and isinstance(data, dict):
                    chunk = data.get("chunk")
                    if _chunk_has_tool_calls(chunk):
                        gen_blocked = True
                        gen_pending = ""
                    content_delta, reasoning_delta = _chunk_deltas(chunk)
                    if content_delta and not gen_blocked:
                        gen_content += content_delta
                        cut = tool_markup_cut_index(gen_content)
                        if cut is not None:
                            gen_blocked = True
                            publishable = gen_content[:cut]
                        else:
                            publishable = gen_content
                        fresh = publishable[gen_streamed_len:]
                        if fresh:
                            gen_pending += fresh
                            gen_streamed_len = len(publishable)
                            await _publish_response_pending()
                    if reasoning_delta:
                        # reasoning_content 是模型私有推理，不可原样暴露给前端。
                        # 首次收到时只发布服务端定义的安全进度。
                        if not safe_reasoning_started:
                            safe_reasoning_started = True
                            shared = runtime_context.get("reasoning_chunk_seq")
                            if isinstance(shared, list) and shared:
                                shared[0] = int(shared[0]) + 1
                                reasoning_chunk_i = int(shared[0])
                            else:
                                reasoning_chunk_i += 1
                            if publisher is not None:
                                await self._safe_publish(
                                    publisher.reasoning_summary_delta(
                                        delta=_SAFE_MODEL_REASONING_PROGRESS,
                                        chunk_index=reasoning_chunk_i,
                                    )
                                )
                    continue
                if kind == "on_chat_model_end" and isinstance(data, dict):
                    output = data.get("output")
                    message = _message_from_model_end(output)
                    has_tools = bool(
                        message is not None
                        and (getattr(message, "tool_calls", None) or [])
                    )
                    if has_tools:
                        gen_blocked = True
                        gen_pending = ""
                    elif not gen_blocked:
                        cleaned = strip_tool_markup(gen_content)
                        if cleaned:
                            public_response = cleaned
                            await _publish_response_pending(force=True)
                    # 无论本轮是否含 Tool，都清零，避免挡住后续最终回答流。
                    _reset_generation()
                    continue
                if kind == "on_chain_end" and isinstance(data, dict):
                    output = data.get("output")
                    if isinstance(output, Mapping) and isinstance(
                        output.get("messages"), list
                    ):
                        final_state = output

        completed_text = "已完成本轮处理"
        result = NativeVideoAgentInvokeResult(
            final_text=completed_text,
            tool_names=(),
            message_count=0,
        )
        try:
            try:
                await _consume(
                    self._agent.astream_events(
                        payload,
                        config=config,
                        version="v2",
                        context=runtime_context,
                    )
                )
            except TypeError:
                await _consume(
                    self._agent.astream_events(
                        payload,
                        config=config,
                        version="v2",
                    )
                )

            await _publish_response_pending(force=True)

            result = (
                _summarize_invoke_result(final_state)
                if final_state is not None
                else NativeVideoAgentInvokeResult(
                    final_text=public_response or completed_text,
                    tool_names=(),
                    message_count=0,
                )
            )
            completed_text = choose_public_response_text(
                summarized=result.final_text,
                streamed_public=public_response,
                fallback=fallback_response,
            )
            if fallback_response and _should_prefer_bootstrap_reply(
                prefer_bootstrap_tools,
                completed_text,
            ):
                completed_text = fallback_response
            result = NativeVideoAgentInvokeResult(
                final_text=completed_text,
                tool_names=result.tool_names,
                message_count=result.message_count,
            )
            if safe_reasoning_started and publisher is not None:
                duration_ms = int((time.monotonic() - started) * 1000)
                await self._safe_publish(
                    publisher.reasoning_summary_completed(
                        summary=_SAFE_MODEL_REASONING_COMPLETED,
                        duration_ms=duration_ms,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            stream_error = exc
            logger.exception(
                "原生 Agent astream 失败 conversation=%s turn=%s",
                request.conversation_id,
                request.turn_id,
            )
            # 工具可能已成功（如 MP4 成片）；优先salvage，避免成功被笼统「处理中断」盖掉。
            completed_text, salvaged_tools = _salvage_public_text_after_stream_error(
                final_state=final_state if isinstance(final_state, Mapping) else None,
                public_response=public_response,
                fallback_response=fallback_response,
                stream_error=exc,
            )
            result = NativeVideoAgentInvokeResult(
                final_text=completed_text,
                tool_names=salvaged_tools or result.tool_names,
                message_count=result.message_count,
            )
        finally:
            # 即使中途异常/截断，也用清洁文本盖住可能已泄漏的半截 tool JSON。
            if publisher is not None:
                safe_text = choose_public_response_text(
                    summarized=completed_text,
                    streamed_public=public_response or strip_tool_markup(gen_content),
                    fallback=fallback_response,
                )
                if fallback_response and _should_prefer_bootstrap_reply(
                    prefer_bootstrap_tools,
                    safe_text,
                ):
                    safe_text = fallback_response
                elif (
                    stream_error is not None
                    and (not safe_text.strip() or safe_text.strip() == "已完成本轮处理")
                ):
                    safe_text, _ = _salvage_public_text_after_stream_error(
                        final_state=(
                            final_state if isinstance(final_state, Mapping) else None
                        ),
                        public_response=public_response or strip_tool_markup(gen_content),
                        fallback_response=fallback_response,
                        stream_error=stream_error,
                    )
                await self._safe_publish(
                    publisher.response_completed(
                        text=safe_text[:8_000],
                        revision=response_revision,
                    )
                )
        if stream_error is not None:
            # 交给外层 recover；不再二次抛出以免跳过 reprepare 补救。
            return NativeVideoAgentInvokeResult(
                final_text=completed_text,
                tool_names=result.tool_names,
                message_count=result.message_count,
            )
        return result

    async def _invoke_blocking(
        self,
        *,
        payload: dict[str, Any],
        config: dict[str, Any],
        runtime_context: dict[str, object],
    ) -> NativeVideoAgentInvokeResult:
        try:
            raw = await self._agent.ainvoke(
                payload,
                config=config,
                context=runtime_context,
            )
        except TypeError:
            raw = await self._agent.ainvoke(payload, config=config)
        return _summarize_invoke_result(raw)

    def _make_publisher(
        self,
        request: NativeVideoAgentInvokeRequest,
    ) -> NativeAgentEventPublisher | None:
        repository = getattr(self, "_runtime_repository", None)
        if repository is None or not hasattr(repository, "create_event"):
            return None
        try:
            return NativeAgentEventPublisher(
                repository=repository,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("NativeAgentEventPublisher 创建失败")
            return None

    async def _safe_publish(self, awaitable) -> None:
        if awaitable is None:
            return
        try:
            await awaitable
        except Exception:  # noqa: BLE001
            logger.exception("原生 Agent 流式事件发布失败")

    async def _emit_response_completed(
        self,
        request: NativeVideoAgentInvokeRequest,
        text: str,
        *,
        revision: str = "final",
    ) -> None:
        publisher = self._make_publisher(request)
        if publisher is None:
            return
        await self._safe_publish(
            publisher.response_completed(text=text, revision=revision)
        )


def tool_markup_cut_index(text: str) -> int | None:
    """若正文混入伪 Tool Call 标记，返回应切断的下标。"""

    if not text:
        return None
    lowered = text.lower()
    hits: list[int] = []
    for marker in _TOOL_MARKUP_MARKERS:
        index = lowered.find(marker.lower())
        if index >= 0:
            hits.append(index)
    return min(hits) if hits else None


def strip_tool_markup(text: str) -> str:
    """去掉伪 Tool Call 及其后内容，只保留用户可读前缀。"""

    raw = text or ""
    cut = tool_markup_cut_index(raw)
    cleaned = (raw[:cut] if cut is not None else raw).strip()
    return cleaned


def choose_public_response_text(
    *,
    summarized: str,
    streamed_public: str,
    fallback: str | None = None,
) -> str:
    """优先无 Tool 的最终 AIMessage；否则用已过滤的流式公开正文。"""

    primary = strip_tool_markup(summarized)
    if primary and primary != "已完成本轮处理":
        return primary[:8_000]
    secondary = strip_tool_markup(streamed_public)
    if secondary:
        return secondary[:8_000]
    tertiary = (fallback or "").strip()
    if tertiary:
        return tertiary[:8_000]
    return "已完成本轮处理"


def _should_prefer_bootstrap_reply(
    bootstrap_tools: tuple[str, ...],
    final_text: str,
) -> bool:
    """Bootstrap 已落定业务事实时，模型若答非所问则改用确定性公开回复。"""

    text = (final_text or "").strip()
    if not text or text == "已完成本轮处理":
        return True
    if "prepare_scene_packages" in bootstrap_tools:
        return not any(
            token in text
            for token in ("场景包", "资产包", "分镜", "参考图", "打开卡片")
        )
    if "generate_scene_assets" in bootstrap_tools:
        return not any(
            token in text
            for token in ("参考图", "生图", "生成中", "场景包", "模型")
        )
    if "replace_scene_asset" in bootstrap_tools:
        return not any(token in text for token in ("素材", "角色", "已替换", "待重新生成"))
    if "patch_scene" in bootstrap_tools:
        return not any(token in text for token in ("分镜", "已更新", "待重新生成", "镜头"))
    if "generate_scenes" in bootstrap_tools:
        return not any(token in text for token in ("分镜视频", "生成", "场景包", "进度"))
    if "apply_production_fields" in bootstrap_tools:
        return not any(token in text for token in ("画幅", "结尾", "脚本", "确认"))
    if "import_script" in bootstrap_tools:
        return not any(token in text for token in ("导入", "脚本", "画幅", "字段"))
    return False


def _workspace_has_script(workspace: VideoWorkspace) -> bool:
    script = workspace.payload.get("script")
    if isinstance(script, dict):
        content = script.get("content")
        if isinstance(content, str) and content.strip():
            return True
    return False


def _workspace_has_scene_packages(workspace: VideoWorkspace) -> bool:
    """已有结构化分镜包时，确认脚本不再重复 bootstrap prepare。"""

    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    for key in ("scene_packages", "scenes"):
        value = payload.get(key)
        if isinstance(value, list) and any(isinstance(item, Mapping) for item in value):
            return True
    return False


def _scene_packages_need_script_refresh(workspace: VideoWorkspace) -> bool:
    """已有包与当前脚本不一致时需要重拆（镜数明显偏少，或脚本指纹变化）。"""

    from pixelflow.creative.script_shots import (
        compute_scene_packages_source_digest,
        extract_script_shot_entries,
        resolve_shot_source_markdown,
    )

    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    packages = payload.get("scene_packages") or payload.get("scenes")
    package_count = 0
    if isinstance(packages, list):
        package_count = sum(1 for item in packages if isinstance(item, Mapping))
    if package_count <= 0:
        return False
    stored = payload.get("scene_packages_source_digest")
    current = compute_scene_packages_source_digest(payload)
    if isinstance(stored, str) and stored.strip() and stored.strip() != current:
        return True
    markdown = resolve_shot_source_markdown(payload)
    entries = extract_script_shot_entries(markdown)
    if len(entries) < 4:
        return False
    # 脚本 ≥4 镜且现有包不到一半（常见：默认 30s→2 镜 vs 14 镜成稿）
    return package_count * 2 <= len(entries)


def _workspace_global_assets_empty(workspace: VideoWorkspace) -> bool:
    """角色/场景/道具是否都没有可生图的命名资产。"""

    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    global_assets = payload.get("global_assets")
    if not isinstance(global_assets, Mapping):
        return True
    for collection in ("characters", "scenes", "props"):
        items = global_assets.get(collection)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping) and str(item.get("name") or "").strip():
                return False
    return True


_FIELD_LABEL_ASSET_NAMES = frozenset(
    {
        "视觉特征",
        "动作习惯",
        "人物弧光",
        "关键关系",
        "视觉形象",
        "身份",
        "性格",
        "金句",
        "核心标签",
        "定位",
        "时段",
        "光线",
        "光影",
        "色调",
        "视觉要点",
        "功能",
        "时空背景",
        "陈设细节",
        "光线氛围",
        "可拍要点",
        "分镜提示词",
        "镜头列表",
        "分镜大纲",
        "外观材质",
        "品牌露出",
        "使用动作",
    }
)


def _workspace_global_assets_look_like_field_labels(workspace: VideoWorkspace) -> bool:
    """已有资产名多数是设定字段标签时，生图前应重拆（否则质量校验秒失败）。"""

    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    global_assets = payload.get("global_assets")
    if not isinstance(global_assets, Mapping):
        return False
    names: list[str] = []
    for collection in ("characters", "scenes", "props"):
        items = global_assets.get(collection)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    if not names:
        return False
    label_hits = sum(1 for name in names if name in _FIELD_LABEL_ASSET_NAMES)
    return label_hits >= max(2, (len(names) + 1) // 2)


def _generate_scene_assets_result_failed(result: object) -> bool:
    """Registry 常把执行异常吞成 VideoToolResult；bootstrap 需识别失败，禁止假成功。"""

    summary = str(getattr(result, "public_summary", "") or "").strip()
    pending = tuple(getattr(result, "pending_operation_job_ids", ()) or ())
    patch = getattr(result, "workspace_patch", None)
    patch_map = patch if isinstance(patch, Mapping) else {}
    job = patch_map.get("scene_asset_job")
    job_status = ""
    if isinstance(job, Mapping):
        job_status = str(job.get("status") or "").strip().casefold()
    if pending:
        return False
    if job_status in {"polling", "succeeded", "partial", "start_paused_quota"}:
        return False
    if "失败" in summary or "未能" in summary or "无效" in summary:
        return True
    if not patch_map:
        return True
    return job_status in {"failed", "error", "expired"}


_SCENE_ASSET_MODEL_CONFIRM_RE = re.compile(
    r"确认生图模型\s*(?P<model>[\w.\-]+)"
    r"(?:.*?比例\s*(?P<ratio>\d{1,2}\s*:\s*\d{1,2}))?"
    r"(?:.*?清晰度\s*(?P<size>[\w.\-]+))?"
    r"(?:.*?用途[：:]\s*(?P<brief>[^\n]+))?",
    re.IGNORECASE | re.DOTALL,
)


def _parse_scene_asset_model_confirm(
    content: str,
) -> tuple[str, str, str, str] | None:
    """解析 FE 模型确认 Turn：返回 (model, ratio, size, brief)。"""

    text = (content or "").strip()
    if not text or "确认生图模型" not in text:
        return None
    match = _SCENE_ASSET_MODEL_CONFIRM_RE.search(text)
    if match is None:
        return None
    model = (match.group("model") or "").strip()
    if not model:
        return None
    ratio = re.sub(r"\s+", "", (match.group("ratio") or "9:16").strip()) or "9:16"
    size = (match.group("size") or ("4K" if "gpt-image" in model else "2K")).strip() or "2K"
    brief = (match.group("brief") or "").strip()[:4_000]
    return model, ratio, size, brief


_SCENE_PATCH_HEAD_RE = re.compile(
    r"^修改分镜\s+(?P<scene_id>[A-Za-z0-9._:-]+)\s*[。.\s]*(?P<body>.*)$",
    re.DOTALL,
)
_SCENE_PATCH_FIELD_SPECS: tuple[tuple[str, str, int], ...] = (
    ("故事线", "storyline", 4_000),
    ("提示词", "prompt", 10_000),
    # 仅匹配 FE 发出的「旁白：」；不要匹配镜头正文里的「旁白（对白）：」，否则会截断镜头描述。
    ("旁白", "narration", 4_000),
    ("转场", "transition", 512),
    ("时长毫秒", "duration_ms", 0),
    ("镜头描述", "shot_description", 10_000),
    ("参考素材", "reference_asset_ids", 0),
)

_SCENE_ASSET_REPLACEMENT_RE = re.compile(
    r"<<<REPLACE_SCENE_ASSET>>>\s*(?P<payload>\{.*?\})\s*<<<END>>>",
    re.DOTALL,
)


def _parse_structured_scene_asset_replacement(content: str) -> dict[str, object] | None:
    match = _SCENE_ASSET_REPLACEMENT_RE.search(content or "")
    if match is None:
        return None
    try:
        value = json.loads(match.group("payload"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def _parse_structured_scene_patch(content: str) -> tuple[str, dict[str, object]] | None:
    """解析 FE 分镜面板结构化 Turn：修改分镜 scene-1。镜头描述：…"""

    text = _followup_instruction(content).strip()
    match = _SCENE_PATCH_HEAD_RE.match(text)
    if match is None:
        return None
    scene_id = (match.group("scene_id") or "").strip()
    body = (match.group("body") or "").strip()
    if not scene_id or not body:
        return None

    markers: list[tuple[int, int, str, int]] = []
    for label, key, limit in _SCENE_PATCH_FIELD_SPECS:
        # 行首或「。；;」后的 FE 字段；「旁白」须用 (?!（对白）) 排除镜头正文「旁白（对白）：」。
        if label == "旁白":
            pattern = re.compile(r"(?:^|\n|(?<=[。；;]))\s*旁白(?!（对白）)\s*[：:]")
        else:
            pattern = re.compile(rf"(?:^|\n|(?<=[。；;]))\s*{re.escape(label)}\s*[：:]")
        found = pattern.search(body)
        if found is None and (
            body.startswith(f"{label}：") or body.startswith(f"{label}:")
        ):
            if label == "旁白" and (
                body.startswith("旁白（对白）：") or body.startswith("旁白（对白）:")
            ):
                found = None
            else:
                found = re.match(rf"{re.escape(label)}\s*[：:]", body)
        if found is None:
            continue
        markers.append((found.start(), found.end(), key, limit))
    if not markers:
        # 没有显式字段标签的是自然语言指令，由原生 Agent 结合当前镜头理解；
        # 不能把「场地还是在……」之类指令直接覆盖成完整镜头正文。
        return None

    markers.sort(key=lambda item: item[0])
    patch: dict[str, object] = {}
    for index, (_start, value_start, key, limit) in enumerate(markers):
        value_end = markers[index + 1][0] if index + 1 < len(markers) else len(body)
        value = body[value_start:value_end].strip().strip("。").strip()
        if not value:
            continue
        if key == "duration_ms":
            digits = re.sub(r"\D", "", value)
            if digits:
                patch[key] = int(digits)
            continue
        if key == "reference_asset_ids":
            ids = [
                part.strip()
                for part in re.split(r"[、,，\s]+", value)
                if part.strip()
            ][:12]
            if ids:
                patch[key] = ids
            continue
        patch[key] = value[:limit] if limit > 0 else value
    if not patch:
        return None
    return scene_id, patch


_SCENE_CONTEXT_FIELDS: tuple[str, ...] = (
    "scene_id",
    "title",
    "storyline",
    "shot_description",
    "prompt",
    "narration",
    "transition",
    "duration_ms",
    "reference_asset_ids",
    "edit_status",
)


def _scene_patch_target_context(
    content: str,
    workspace: VideoWorkspace,
) -> dict[str, object] | None:
    """为自然语言局部改镜提供当前镜头快照，不暴露整个脚本或敏感字段。"""

    text = _followup_instruction(content).strip()
    match = _SCENE_PATCH_HEAD_RE.match(text)
    if match is None or _parse_structured_scene_patch(content) is not None:
        return None
    scene_id = (match.group("scene_id") or "").strip()
    body = (match.group("body") or "").strip(" ，,。\n\t")
    if not scene_id or not body:
        return None

    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    raw_packages = payload.get("scene_packages") or payload.get("scenes") or []
    if isinstance(raw_packages, Mapping):
        raw_packages = raw_packages.get("scene_packages") or raw_packages.get("scenes") or []
    if not isinstance(raw_packages, list):
        return None
    target = next(
        (
            item
            for item in raw_packages
            if isinstance(item, Mapping)
            and str(item.get("scene_id") or item.get("id") or "").strip() == scene_id
        ),
        None,
    )
    if target is None:
        return None

    context: dict[str, object] = {"scene_id": scene_id}
    for field in _SCENE_CONTEXT_FIELDS:
        if field == "scene_id" or field not in target:
            continue
        value = target[field]
        if field == "shot_description" and isinstance(value, Mapping):
            context[field] = {
                "text": str(value.get("text") or "")[:10_000],
                "mentions": list(value.get("mentions") or [])[:24],
            }
        elif field == "reference_asset_ids" and isinstance(value, (list, tuple)):
            context[field] = [str(item) for item in value[:12]]
        elif isinstance(value, str):
            context[field] = value[:10_000]
        elif isinstance(value, (int, float, bool)) or value is None:
            context[field] = value
    return context


def _parse_generate_scenes_intent(content: str) -> str | None:
    """解析生成分镜视频意图：all / dirty / retry。"""

    text = _followup_instruction(content).strip()
    if not text:
        return None
    # 「合并/合成」交给 ReAct compose_or_export_video，勿误入 generate_scenes bootstrap。
    if _looks_like_merge_videos_intent(content):
        return None
    if "重新生成已修改的分镜视频" in text:
        return "dirty"
    if "继续生成失败的分镜视频" in text:
        return "retry"
    if "确认并生成分镜视频" in text or "确认并生成视频" in text:
        return "all"
    compact = re.sub(r"\s+", "", text)
    if compact in {"生成视频", "生成视频吧", "生成分镜视频", "生成分镜视频吧", "开始生成视频"}:
        return "all"
    if len(text) <= 40 and re.match(
        r"^(请)?(帮我)?生成(?:全部|所有)?(?:的)?(?:分镜)?视频",
        text,
    ):
        return "all"
    return None


def _looks_like_merge_videos_intent(content: str) -> bool:
    """识别「合并视频 / 合成成片」：禁止拼回脚本、禁止误入 generate_scenes。"""

    text = _followup_instruction(content).strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    if any(
        token in compact
        for token in (
            "合并视频",
            "合并成片",
            "合成视频",
            "合成成片",
            "合并分镜视频",
            "合成分镜视频",
            "导出成片",
            "导出mp4",
            "导出MP4",
        )
    ):
        return True
    if compact in {
        "合并",
        "合并吧",
        "合成",
        "合成吧",
        "开始合并",
        "开始合成",
        "帮我合并",
        "请合并",
    }:
        return True
    if len(text) <= 40 and re.match(
        r"^(请)?(帮我)?(把)?(分镜)?(视频)?(合并|合成)(成片|成视频|视频|一下|吧)?$",
        compact,
    ):
        return True
    return False


def _public_model_failure_message(exc: BaseException) -> str:
    """把上游模型/网关异常收成用户可读中文，避免空转「已完成本轮处理」。"""

    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    if (
        "500" in text
        or "InternalServerError" in name
        or "系统内部错误" in text
        or "internal server error" in lowered
    ):
        return (
            "模型服务暂时不可用（上游返回错误），请稍后重试。"
            "若要合并成片，可再说一次「合并视频吧」。"
        )
    if "429" in text or "rate" in lowered or "额度" in text or "quota" in lowered:
        return "模型服务繁忙或额度不足，请稍后重试。"
    if (
        "APIConnectionError" in name
        or "ConnectError" in name
        or "connection error" in lowered
        or "connecterror" in lowered
        or "nodename nor servname" in lowered
    ):
        return (
            "模型服务连接失败，请稍后重试。"
            "若工具卡已显示成功（例如成片已生成），请先查看右侧结果，无需重复计费操作。"
        )
    return "本轮处理中断，请稍后重试。"


def _salvage_public_text_after_stream_error(
    *,
    final_state: Mapping[str, Any] | None,
    public_response: str,
    fallback_response: str | None,
    stream_error: BaseException,
) -> tuple[str, tuple[str, ...]]:
    """流式中途模型挂掉时：尽量保留已执行 Tool 的公开结论，避免成功被「处理中断」盖掉。"""

    tool_names: tuple[str, ...] = ()
    summarized = ""
    tool_summaries: list[str] = []
    if final_state is not None:
        salvaged = _summarize_invoke_result(final_state)
        tool_names = salvaged.tool_names
        summarized = salvaged.final_text
        tool_summaries = _tool_public_summaries_from_messages(
            final_state.get("messages") if isinstance(final_state, Mapping) else None
        )
    completed = choose_public_response_text(
        summarized=summarized,
        streamed_public=public_response,
        fallback=fallback_response,
    )
    if completed.strip() and completed.strip() != "已完成本轮处理":
        if tool_names:
            return (
                f"{completed.strip()}\n（收尾说明连接失败，业务结果以工具卡与右侧工作台为准）",
                tool_names,
            )
        return completed, tool_names
    if tool_summaries:
        return (
            f"{tool_summaries[-1]}\n（收尾说明连接失败，业务结果以工具卡与右侧工作台为准）",
            tool_names,
        )
    if tool_names:
        return (
            "相关工具已执行；模型收尾说明失败，请查看工具卡与右侧工作台结果。",
            tool_names,
        )
    return _public_model_failure_message(stream_error), tool_names


def _tool_public_summaries_from_messages(messages: object) -> list[str]:
    """从 ToolMessage JSON 载荷提取 public_summary（仅用于异常收口，不写回模型上下文）。"""

    if not isinstance(messages, list):
        return []
    summaries: list[str] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        raw = message.content
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        summary = str(payload.get("public_summary") or "").strip()
        if summary:
            summaries.append(summary[:500])
    return summaries


def _parse_scene_ids_from_paren(content: str) -> list[str]:
    match = re.search(r"[（(]([^）)]+)[）)]", content or "")
    if match is None:
        return []
    raw = match.group(1)
    return [part.strip() for part in re.split(r"[、,，\s]+", raw) if part.strip()]


def _workspace_scene_ids(workspace: VideoWorkspace) -> list[str]:
    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    for key in ("scene_packages", "scenes"):
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        ids = [
            str(item.get("scene_id") or "").strip()
            for item in value
            if isinstance(item, Mapping) and str(item.get("scene_id") or "").strip()
        ]
        if ids:
            return ids
    return []


def _workspace_dirty_scene_ids(workspace: VideoWorkspace) -> list[str]:
    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    dirty = payload.get("dirty_scene_ids")
    if not isinstance(dirty, list):
        return []
    return [str(item).strip() for item in dirty if str(item).strip()]


def _workspace_has_scene_asset_images(workspace: VideoWorkspace) -> bool:
    from pixelflow.video_agent.workspace.digest import summarize_scene_asset_status

    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    return summarize_scene_asset_status(payload)["scene_assets_ready"] is True


def _public_prepare_scene_summary(raw: str, package_count: int = 0) -> str:
    """把领域内部「请前端展示」口吻收成用户可读摘要。"""

    text = (raw or "").strip()
    if (
        not text
        or "请前端" in text
        or "LLM 已生成" in text
        or "规则已生成" in text
    ):
        if package_count > 0:
            return f"已生成 {package_count} 个分镜资产包，请打开卡片查看"
        return "视频场景包已生成，请打开卡片查看"
    return text[:500]


def _script_markdown_for_import(content: str) -> str:
    """从 Turn 正文取出成稿；去掉合并进 latest_input 的【本轮指令】尾注。"""

    text = content.strip()
    marker = "\n\n【本轮指令】"
    if marker in text:
        head, _, _ = text.partition(marker)
        return head.strip() or text
    return text


def _followup_instruction(content: str) -> str:
    """取出短跟进指令；有【本轮指令】时只看指令段。"""

    text = content.strip()
    marker = "\n\n【本轮指令】"
    if marker in text:
        return text.partition(marker)[2].strip() or text
    if "【本轮指令】" in text:
        return text.partition("【本轮指令】")[2].strip() or text
    return text


def _looks_like_reprepare_scene_packages(content: str) -> bool:
    """识别重新生成分镜/场景/资产包意图（不含重新生成分镜视频）。"""

    text = re.sub(r"\s+", "", (content or "").strip())
    if not text or len(text) > 80:
        return False
    if "分镜视频" in text or "场景视频" in text:
        return False
    markers = (
        "重新生成视频分镜包",
        "重新生成分镜包",
        "重新生成视频场景包",
        "重新生成场景包",
        "重新生成资产包",
        "重新生成视频资产包",
        "重拆分镜包",
        "重拆场景包",
    )
    return any(marker in text for marker in markers)


def _looks_like_restructure_script(content: str) -> bool:
    """识别「重新拆解脚本」意图（不含重新生成分镜包/场景包）。"""

    text = re.sub(r"\s+", "", (content or "").strip())
    if not text or len(text) > 80:
        return False
    if _looks_like_reprepare_scene_packages(text):
        return False
    if any(
        token in text
        for token in ("分镜包", "场景包", "资产包", "分镜视频", "场景视频")
    ):
        return False
    if "脚本" not in text:
        return False
    if "拆解" not in text and "重拆" not in text:
        return False
    return (
        "重新拆解" in text
        or "再拆解" in text
        or "拆解下" in text
        or "重拆脚本" in text
        or "脚本重拆" in text
        or "请拆解" in text
    )


def _workspace_script_markdown_for_restructure(workspace: VideoWorkspace) -> str:
    """重新拆解优先用 script.content；否则回退到镜头源稿。"""

    script = workspace.payload.get("script")
    if isinstance(script, Mapping):
        content = script.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    from pixelflow.creative.script_shots import resolve_shot_source_markdown

    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    return (resolve_shot_source_markdown(payload) or "").strip()


def _model_facing_user_content(content: str) -> str:
    """入模正文：合并稿只送本轮指令；重新生成意图附加强制 Tool Call 提示。"""

    instruction = _followup_instruction(content)
    body = instruction.strip() or content.strip()
    if _looks_like_reprepare_scene_packages(body):
        return (
            "【系统】用户明确要求重新生成视频分镜包。"
            "当前脚本与场景包已在 VideoWorkspace；你必须立刻发出原生 Tool Call："
            "prepare_scene_packages。"
            "禁止只口头追问确认；禁止空回复。\n\n"
            f"{body}"
        )
    if _looks_like_restructure_script(body):
        return (
            "【系统】用户明确要求重新拆解脚本。"
            "Workspace 已有脚本全文；你必须立刻发出原生 Tool Call："
            "import_script（force_reextract=true；markdown 可省略，服务端自动读取 Workspace）。"
            "禁止先 inspect 只为「找全文」；禁止口头答应；禁止空回复。"
            "若误先 inspect，拿到结果后必须在同一轮继续 import_script(force_reextract=true)。\n\n"
            f"{body}"
        )
    # 「成稿 + 【本轮指令】」合并稿：脚本已在 Workspace，勿反复塞进 checkpointer。
    if instruction != content.strip() and instruction.strip():
        return body
    return content.strip()


def _payload_with_model_facing_user_message(
    payload: Mapping[str, Any],
    content: str,
) -> dict[str, Any]:
    """保留 bootstrap 系统注，但把用户正文收敛为入模短指令。"""

    next_payload = dict(payload)
    facing = _model_facing_user_content(content)
    messages = next_payload.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        prior = str(getattr(first, "content", "") or "")
        if prior.startswith("【系统】") and "\n\n" in prior:
            note = prior.split("\n\n", 1)[0].strip()
            # bootstrap 注 + 收敛后的用户指令
            if note and not facing.startswith(note):
                facing = f"{note}\n\n{_followup_instruction(content) or facing}"
        next_payload["messages"] = [HumanMessage(content=facing), *messages[1:]]
    else:
        next_payload["messages"] = [HumanMessage(content=facing)]
    return next_payload


def _chunk_has_tool_calls(chunk: Any) -> bool:
    if chunk is None:
        return False
    if getattr(chunk, "tool_call_chunks", None):
        return True
    if getattr(chunk, "tool_calls", None):
        return True
    additional = getattr(chunk, "additional_kwargs", None)
    if isinstance(additional, Mapping):
        if additional.get("tool_calls") or additional.get("function_call"):
            return True
    return False


def _message_from_model_end(output: Any) -> BaseMessage | None:
    if isinstance(output, BaseMessage):
        return output
    if isinstance(output, Mapping):
        generations = output.get("generations")
        if isinstance(generations, list) and generations:
            first = generations[0]
            if isinstance(first, list) and first:
                first = first[0]
            message = getattr(first, "message", None)
            if isinstance(message, BaseMessage):
                return message
            if isinstance(first, Mapping):
                nested = first.get("message")
                if isinstance(nested, BaseMessage):
                    return nested
        message = output.get("message")
        if isinstance(message, BaseMessage):
            return message
    message = getattr(output, "message", None)
    if isinstance(message, BaseMessage):
        return message
    return None


def _chunk_deltas(chunk: Any) -> tuple[str, str]:
    """从模型流式 chunk 提取公开正文与安全思考摘要 delta。"""

    content = ""
    reasoning = ""
    if chunk is None:
        return content, reasoning
    raw_content = getattr(chunk, "content", None)
    if isinstance(raw_content, str):
        content = raw_content
    elif isinstance(raw_content, list):
        parts: list[str] = []
        for block in raw_content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                # 跳过 thinking / tool 块，避免泄漏到公开回答。
                block_type = str(block.get("type") or "").lower()
                if block_type in {"thinking", "reasoning", "tool_use", "tool_call"}:
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        content = "".join(parts)
    additional = getattr(chunk, "additional_kwargs", None)
    if isinstance(additional, Mapping):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = additional.get(key)
            if isinstance(value, str) and value:
                reasoning = value
                break
    # 部分供应商把思考块放在 content blocks 的 type=thinking
    if not reasoning and isinstance(raw_content, list):
        parts = []
        for block in raw_content:
            if isinstance(block, Mapping) and block.get("type") in {
                "thinking",
                "reasoning",
            }:
                text = block.get("thinking") or block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        reasoning = "".join(parts)
    if isinstance(chunk, AIMessageChunk) and not content and not reasoning:
        # 兜底：某些 chunk 只有 content 为空的工具片段
        pass
    return content, reasoning


def _summarize_invoke_result(raw: Mapping[str, Any] | Any) -> NativeVideoAgentInvokeResult:
    messages: list[BaseMessage] = []
    if isinstance(raw, Mapping):
        maybe_messages = raw.get("messages")
        if isinstance(maybe_messages, list):
            messages = [item for item in maybe_messages if isinstance(item, BaseMessage)]

    tool_names: list[str] = []
    final_text = ""
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            name = ""
            if isinstance(call, Mapping):
                name = str(call.get("name") or "").strip()
            else:
                name = str(getattr(call, "name", "") or "").strip()
            if name:
                tool_names.append(name)
        if isinstance(message, AIMessage) and not tool_calls:
            text = message.content if isinstance(message.content, str) else str(message.content)
            cleaned = strip_tool_markup(text)
            if cleaned:
                final_text = cleaned

    if not final_text:
        final_text = "已完成本轮处理"
    return NativeVideoAgentInvokeResult(
        final_text=final_text[:8_000],
        tool_names=tuple(dict.fromkeys(tool_names)),
        message_count=len(messages),
    )
