"""PixelFlow P0 业务任务 API。

这里把底层 LangGraph run/thread API 包装成电商短视频业务语义：创建任务、查询任务
状态/结果、Brief 人工确认/修改、资产查询和 PixelFlow 进度事件流。

按 Java/Spring 视角看，本文件相当于 PixelFlow 的 Controller：负责 HTTP 入参/出参、
状态码和鉴权用户透传；真正阶段编排在 ``pixelflow.nodes``，持久化在
``pixelflow.tasks`` Store。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.gateway.content_app_auth import ContentAppAuthError, verify_authorization_header_remote
from app.gateway.deps import get_checkpointer, get_current_user, get_run_context, get_run_manager, get_stream_bridge
from app.gateway.pixelflow_memory import concise_result_summary, power_mem_service, record_power_mem_background, search_power_mem
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import build_run_config, format_sse, inject_authenticated_user_context, merge_run_context_overrides, normalize_input, normalize_stream_modes
from deerflow.runtime import END_SENTINEL, ConflictError, DisconnectMode, UnsupportedStrategyError, run_agent, serialize_channel_values
from pixelflow import make_pixelflow_graph
from pixelflow.memory import memory_context_payload
from pixelflow.preferences import UserPreferenceStore, extract_structured_preferences
from pixelflow.tasks import PixelFlowAssetRecord, PixelFlowTaskRecord, PixelFlowTaskStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent/flows", tags=["pixelflow-flows"])

ExplainableEvent = tuple[str, dict[str, Any]]


_PHASE_COPY: dict[str, dict[str, str]] = {
    "intake": {
        "title": "采集商品与素材",
        "summary": "我正在整理商品信息、视频参数和参考素材，确认后续策划需要的输入是否完整。",
    },
    "creative": {
        "title": "策划短视频 Brief",
        "summary": "我正在把商品卖点、平台比例和目标时长转成可执行的分镜 Brief。",
    },
    "brief_review": {
        "title": "等待 Brief 人工确认",
        "summary": "我已生成初版策划，正在等待人工确认后再进入视频生成，避免错误方向继续放大。",
    },
    "generate": {
        "title": "生成分镜片段",
        "summary": "我正在按已确认的分镜逐段准备视频生成请求，并检查每个片段是否能被后续剪辑使用。",
    },
    "segment_review": {
        "title": "等待片段人工确认",
        "summary": "分镜片段已返回，我正在等待人工确认是否进入剪辑合成。",
    },
    "edit": {
        "title": "剪辑与合成成片",
        "summary": "我正在把生成片段、旁白和时间线组合成剪辑计划，并调用剪辑能力输出草稿或成片。",
    },
    "edit_review": {
        "title": "等待剪辑人工确认",
        "summary": "剪辑结果已返回，我正在等待人工确认是否进入质检。",
    },
    "qc": {
        "title": "质检最终产物",
        "summary": "我正在检查成片是否满足基础可用性、结构完整性和交付要求。",
    },
    "qc_review": {
        "title": "等待质检人工确认",
        "summary": "质检报告已生成，我正在等待人工确认是否完成本次流程。",
    },
    "done": {
        "title": "流程完成",
        "summary": "本次 Agent 流程已完成，最终产物和中间资产已同步到资产列表。",
    },
}

_VENDOR_COPY: dict[str, dict[str, str]] = {
    "generate": {
        "vendor": "borgrise",
        "title": "调用视频生成能力",
        "summary": "我正在把分镜内容转换成视频生成请求，并交给视频生成服务处理。",
    },
    "edit": {
        "vendor": "jianying/ffmpeg",
        "title": "调用剪辑渲染能力",
        "summary": "我正在把片段和时间线交给剪辑/渲染能力，生成可预览的草稿或最终视频。",
    },
}


class VideoParamsRequest(BaseModel):
    platform: str = "douyin"
    duration_sec: int = Field(default=30, ge=1)
    ratio: str = "9:16"
    size: str = "1080x1920"
    business_goal: str = ""


class TaskCreateRequest(BaseModel):
    task_type: Literal["ecom_video"] = "ecom_video"
    product_url: str | None = None
    product_info: dict[str, Any] = Field(default_factory=dict)
    video_params: VideoParamsRequest = Field(default_factory=VideoParamsRequest)
    reference_videos: list[str] = Field(default_factory=list)
    creative_direction: dict[str, Any] = Field(default_factory=dict)
    user_message: str = ""
    auto_start: bool = True


class BriefConfirmRequest(BaseModel):
    approved: bool = True


class StageConfirmRequest(BaseModel):
    approved: bool = True


class SessionContextRequest(BaseModel):
    task_id: str
    context: dict[str, Any] = Field(default_factory=dict)


class SessionContextResponse(BaseModel):
    task_id: str
    user_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = ""


class BriefReviseRequest(BaseModel):
    brief_patch: dict[str, Any] = Field(default_factory=dict)
    feedback: str = ""


class TaskResponse(BaseModel):
    task_id: str
    user_id: str | None = None
    task_type: str
    status: str
    phase: str
    thread_id: str
    run_id: str | None = None
    product_info: dict[str, Any] = Field(default_factory=dict)
    video_params: dict[str, Any] = Field(default_factory=dict)
    reference_videos: list[dict[str, Any]] = Field(default_factory=list)
    creative_direction: dict[str, Any] = Field(default_factory=dict)
    brief: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


class AssetResponse(BaseModel):
    asset_id: str
    task_id: str
    user_id: str | None = None
    asset_type: str
    status: str
    phase: str = ""
    shot_id: str | None = None
    url: str = ""
    local_path: str = ""
    vendor: str = ""
    vendor_task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


def _task_store(request: Request) -> PixelFlowTaskStore:
    store = getattr(request.app.state, "pixelflow_task_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="PixelFlow task store not available")
    return store


def _preference_store(request: Request) -> UserPreferenceStore:
    store = getattr(request.app.state, "pixelflow_preference_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="PixelFlow preference store not available")
    return store


def _response(record: PixelFlowTaskRecord) -> TaskResponse:
    return TaskResponse(**record.to_dict())


def _asset_response(record: PixelFlowAssetRecord) -> AssetResponse:
    return AssetResponse(**record.to_dict())


def _phase_title(phase: str) -> str:
    """返回阶段展示名。

    watcher 会频繁从 LangGraph state 里拿到 ``phase`` 字符串。这里统一兜底，
    避免某个未来阶段没有配置文案时前端收到空标题。
    """
    return _PHASE_COPY.get(phase, {}).get("title") or f"{phase} 阶段"


def _phase_summary(phase: str, state: dict[str, Any]) -> str:
    """生成安全的阶段说明。

    这不是大模型原始思维链，而是后端根据当前业务状态组织出来的“可解释摘要”。
    类比 Java 里不要把内部 debug 堆栈直接返回给前端，而是返回面向用户的状态说明。
    """
    base = _PHASE_COPY.get(phase, {}).get("summary") or "我正在推进当前 Agent 流程。"
    if phase == "creative":
        product_name = (state.get("product_info") or {}).get("product_name")
        if product_name:
            return f"{base} 当前重点围绕「{product_name}」提炼卖点和镜头结构。"
    if phase in {"brief_review", "generate"}:
        brief = state.get("brief") or {}
        shots = brief.get("shots") if isinstance(brief, dict) else []
        if isinstance(shots, list) and shots:
            return f"{base} 当前 Brief 包含 {len(shots)} 个分镜。"
    if phase == "edit":
        assets = state.get("generated_assets") or []
        if isinstance(assets, list) and assets:
            return f"{base} 当前可用于剪辑的生成片段有 {len(assets)} 个。"
    return base


def _safe_step_payload(phase: str, state: dict[str, Any], status: str, run_id: str | None) -> dict[str, Any]:
    """构造 ``step_started`` / ``step_finished`` 的公共 payload。

    payload 只放 phase、标题、摘要、状态和 run_id，不放 prompt、原始消息、隐藏推理文本、
    token 或供应商密钥，保证 SSE 面板展示的是可解释业务日志。
    """
    payload: dict[str, Any] = {
        "phase": phase,
        "title": _phase_title(phase),
        "summary": _phase_summary(phase, state),
        "status": status,
    }
    if run_id:
        payload["run_id"] = run_id
    return payload


def _vendor_started_event(phase: str, run_id: str | None) -> ExplainableEvent | None:
    """在进入外部能力阶段时生成 ``vendor_call_started`` 事件。"""
    copy = _VENDOR_COPY.get(phase)
    if not copy:
        return None
    payload: dict[str, Any] = {
        "phase": phase,
        "vendor": copy["vendor"],
        "title": copy["title"],
        "summary": copy["summary"],
        "status": "running",
    }
    if run_id:
        payload["run_id"] = run_id
    return "vendor_call_started", payload


def _generated_asset_stats(state: dict[str, Any]) -> tuple[int, int]:
    """统计生成片段成功/失败数量，用于供应商调用结束摘要。"""
    assets = state.get("generated_assets") or []
    if not isinstance(assets, list):
        return 0, 0
    success = sum(1 for asset in assets if isinstance(asset, dict) and asset.get("ok"))
    failed = sum(1 for asset in assets if isinstance(asset, dict) and asset.get("ok") is False)
    return success, failed


def _vendor_finished_event(phase: str, state: dict[str, Any], run_id: str | None) -> ExplainableEvent | None:
    """根据 state 判断某个外部能力是否已经返回结果。

    这一步只做“是否已有结果”的归纳，不直接调用供应商。真正的第三方 Client/Skill 仍在
    ``pixelflow.skills`` 层。
    """
    if phase == "generate":
        success, failed = _generated_asset_stats(state)
        if success == 0 and failed == 0:
            return None
        payload: dict[str, Any] = {
            "phase": "generate",
            "vendor": _VENDOR_COPY["generate"]["vendor"],
            "title": "视频生成能力已返回",
            "summary": f"视频生成服务已返回 {success} 个成功片段、{failed} 个失败片段。",
            "status": "success" if failed == 0 else "partial",
        }
        if run_id:
            payload["run_id"] = run_id
        return "vendor_call_finished", payload
    if phase == "edit" and (state.get("draft_path") or state.get("final_video_url") or state.get("timeline")):
        payload = {
            "phase": "edit",
            "vendor": _VENDOR_COPY["edit"]["vendor"],
            "title": "剪辑渲染能力已返回",
            "summary": "剪辑/渲染能力已返回结果，正在同步草稿或最终成片资产。",
            "status": "success",
        }
        if run_id:
            payload["run_id"] = run_id
        return "vendor_call_finished", payload
    return None


def _build_phase_transition_events(previous_phase: str | None, phase: str, state: dict[str, Any], run_id: str | None = None) -> list[ExplainableEvent]:
    """把 phase 变化转换成前端时间线事件。

    这是可解释日志的核心转换函数：它不会暴露 LangGraph 原始 stream 或模型推理链，
    只把“阶段开始/结束、是否进入供应商能力、供应商是否已有结果”整理成稳定事件。
    测试直接覆盖这个函数，避免未来改 watcher 时破坏 SSE 合同。
    """
    events: list[ExplainableEvent] = []
    if previous_phase:
        events.append(("step_finished", _safe_step_payload(previous_phase, state, "success", run_id)))
    events.append(("step_started", _safe_step_payload(phase, state, "running", run_id)))
    vendor_started = _vendor_started_event(phase, run_id)
    if vendor_started:
        events.append(vendor_started)
    vendor_finished = _vendor_finished_event(phase, state, run_id)
    if vendor_finished:
        events.append(vendor_finished)
    return events


def _build_state_snapshot_events(state: dict[str, Any], run_id: str | None = None) -> list[ExplainableEvent]:
    """从同一份 state 中补充非 phase-change 类事件。

    有些结果不是伴随 phase 变化出现的，例如 ``generated_assets`` 可能在
    ``segment_review`` 状态才进入 checkpoint。这里补一层快照检测，保证供应商完成
    事件不会因为阶段切换顺序不同而漏发。
    """
    events: list[ExplainableEvent] = []
    generated_done = _vendor_finished_event("generate", state, run_id)
    if generated_done:
        events.append(generated_done)
    edit_done = _vendor_finished_event("edit", state, run_id)
    if edit_done:
        events.append(edit_done)
    qc_report = state.get("qc_report")
    if isinstance(qc_report, dict) and qc_report:
        score = qc_report.get("score")
        score_text = f"，评分 {round(float(score) * 100)} / 100" if isinstance(score, int | float) else ""
        payload: dict[str, Any] = {
            "phase": "qc",
            "title": "质检判断摘要",
            "summary": f"我已根据质检结果归纳成片可用性{score_text}，请在画布查看具体检查项。",
            "status": "success" if qc_report.get("passed") else "needs_review",
        }
        if run_id:
            payload["run_id"] = run_id
        events.append(("llm_summary", payload))
    return events


def _build_brief_summary_events(brief: dict[str, Any], run_id: str | None = None) -> list[ExplainableEvent]:
    """生成 Brief 就绪后的安全 LLM 摘要事件。

    这里的 ``llm_summary`` 只总结“模型输出了什么业务结论”，不返回 prompt、hidden
    reasoning、工具参数细节或供应商密钥。
    """
    shots = brief.get("shots") if isinstance(brief, dict) else []
    shot_count = len(shots) if isinstance(shots, list) else 0
    platform = brief.get("platform") if isinstance(brief, dict) else ""
    ratio = brief.get("ratio") if isinstance(brief, dict) else ""
    payload: dict[str, Any] = {
        "phase": "brief_review",
        "title": "策划判断摘要",
        "summary": f"我已把商品卖点整理成 {shot_count} 个分镜，并按 {platform or '目标平台'} / {ratio or '目标比例'} 准备生成参数。",
        "status": "needs_review",
    }
    if run_id:
        payload["run_id"] = run_id
    return [("llm_summary", payload)]


def _asset_ready_summary(asset: PixelFlowAssetRecord) -> str:
    """按资产类型生成前端可读摘要。"""
    if asset.asset_type == "final_video":
        return "最终成片已准备好，可以在前端预览或下载。"
    if asset.asset_type == "generated_video":
        return "分镜片段已准备好，可以在画布中预览并决定是否进入剪辑。"
    if asset.asset_type == "jianying_draft":
        return "剪辑草稿已准备好，可以继续人工确认或后续渲染。"
    return "资产已准备好，可以在任务资产列表中查看。"


def _build_asset_ready_events(assets: list[PixelFlowAssetRecord], run_id: str | None = None) -> list[ExplainableEvent]:
    """把资产表记录转换为 ``asset_ready`` SSE 事件。

    注意这里故意不透出 ``local_path`` 和完整 ``metadata``：本地路径可能包含部署目录，
    metadata 可能包含供应商返回的冗余字段。前端只需要安全的资产 id、类型、URL 和摘要。
    """
    events: list[ExplainableEvent] = []
    for asset in assets:
        if asset.status != "ready":
            continue
        payload: dict[str, Any] = {
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type,
            "phase": asset.phase,
            "status": asset.status,
            "url": asset.url,
            "vendor": asset.vendor,
            "summary": _asset_ready_summary(asset),
        }
        if run_id:
            payload["run_id"] = run_id
        events.append(("asset_ready", payload))
    return events


def _explainable_event_key(event_name: str, payload: dict[str, Any]) -> str:
    """生成 watcher 内存级去重 key。

    ``StreamBridge`` 可能重放 values；同一 run 内同一阶段/资产的可解释事件只应写一次。
    这里不用数据库唯一索引，是为了不改变现有事件表结构。
    """
    return ":".join(
        str(part)
        for part in (
            event_name,
            payload.get("phase"),
            payload.get("asset_id"),
            payload.get("vendor"),
            payload.get("title"),
        )
        if part
    )


async def _append_explainable_events(
    store: PixelFlowTaskStore,
    task_id: str,
    user_id: str | None,
    events: list[ExplainableEvent],
    emitted_keys: set[str],
) -> None:
    """批量写入可解释事件，并在单个 watcher 生命周期内去重。"""
    for event_name, payload in events:
        key = _explainable_event_key(event_name, payload)
        if key in emitted_keys:
            continue
        emitted_keys.add(key)
        await store.append_event(task_id, event_name, payload, user_id=user_id)


def _apply_preference_defaults(
    video_params: dict[str, Any],
    creative_direction: dict[str, Any],
    preference_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把用户偏好默认值合并到本次任务参数中。

    合并规则是“用户本次显式入参优先，历史偏好只补空值”。这样不会因为用户过去的
    默认平台/比例覆盖本次前端弹窗里刚选择的值。风格偏好会作为创意方向的默认项，
    但本次 creative_direction 仍然可以覆盖它。
    """
    defaults = preference_snapshot.get("defaults") or {}
    style = preference_snapshot.get("style_preferences") or {}
    out_video = dict(video_params)
    if defaults.get("platform") and not out_video.get("platform"):
        out_video["platform"] = defaults["platform"]
    if defaults.get("ratio") and not out_video.get("ratio"):
        out_video["ratio"] = defaults["ratio"]
    if defaults.get("duration_sec") and not out_video.get("duration_sec"):
        out_video["duration_sec"] = defaults["duration_sec"]
    out_creative = {**style, **creative_direction}
    negative_rules = preference_snapshot.get("negative_rules") or []
    if negative_rules:
        out_creative.setdefault("negative_rules", negative_rules)
    return out_video, out_creative


def _initial_state(task_id: str, user_id: str | None, body: TaskCreateRequest, preference_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 HTTP 创建任务 DTO 转成 LangGraph ``TaskState`` 初始上下文。

    这是 Controller 和工作流之间的适配器。前端/HTTP 使用 ``duration_sec``，而
    INTAKE 旧逻辑读取 ``video_duration_sec``，因此这里做一次字段名转换。参考视频
    URL 也会转成带 ``status=pending`` 的对象，供 INTAKE 阶段拆解。
    """
    product_info = dict(body.product_info)
    if body.product_url:
        product_info.setdefault("product_url", body.product_url)
    video_params, creative_direction = _apply_preference_defaults(
        body.video_params.model_dump(),
        dict(body.creative_direction),
        preference_snapshot or {},
    )
    # INTAKE 既有代码读取 video_duration_sec，这里保留兼容转换。
    video_params["video_duration_sec"] = video_params.pop("duration_sec")
    refs = [{"url": url, "status": "pending"} for url in body.reference_videos]
    return {
        "task_id": task_id,
        "user_id": user_id or "",
        "phase": "intake",
        "product_info": product_info,
        "video_params": video_params,
        "reference_videos": refs,
        "creative_direction": creative_direction,
        "user_preferences": preference_snapshot or {},
        "messages": [{"role": "human", "content": body.user_message or "请为这个商品生成带货短视频"}],
    }


async def _start_pixelflow_run(body: RunCreateRequest, thread_id: str, request: Request):
    """启动或恢复一个 PixelFlow 专用 LangGraph run。

    这和通用 ``services.start_run`` 的核心区别是指定
    ``agent_factory=make_pixelflow_graph``，保证运行的是 PixelFlow 状态机。创建
    新任务时传普通 graph input；Brief 确认时传 ``Command(resume=...)`` 恢复
    interrupt。
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    run_ctx = get_run_context(request)
    disconnect = DisconnectMode.cancel if body.on_disconnect == "cancel" else DisconnectMode.continue_

    try:
        record = await run_mgr.create_or_reject(
            thread_id,
            "pixelflow",
            on_disconnect=disconnect,
            metadata=body.metadata or {},
            kwargs={"input": body.input, "config": body.config},
            multitask_strategy=body.multitask_strategy,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedStrategyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    try:
        existing = await run_ctx.thread_store.get(thread_id)
        if existing is None:
            await run_ctx.thread_store.create(thread_id, assistant_id="pixelflow", metadata=body.metadata)
        else:
            await run_ctx.thread_store.update_status(thread_id, "running")
    except Exception:
        logger.warning("Failed to upsert PixelFlow thread_meta for %s", thread_id, exc_info=True)

    graph_input = Command(**body.command) if body.command else normalize_input(body.input)
    config = build_run_config(thread_id, body.config, body.metadata, assistant_id=None)
    merge_run_context_overrides(config, body.context)
    inject_authenticated_user_context(config, request)
    stream_modes = normalize_stream_modes(body.stream_mode)

    task = asyncio.create_task(
        run_agent(
            bridge,
            run_mgr,
            record,
            ctx=run_ctx,
            agent_factory=make_pixelflow_graph,
            graph_input=graph_input,
            config=config,
            stream_modes=stream_modes,
            stream_subgraphs=body.stream_subgraphs,
            interrupt_before=body.interrupt_before,
            interrupt_after=body.interrupt_after,
        )
    )
    record.task = task
    return record


async def _sync_task_from_checkpoint(task_id: str, user_id: str | None, request: Request) -> list[PixelFlowAssetRecord]:
    """从 LangGraph checkpoint 同步业务任务视图和资产表。

    LangGraph checkpoint 是真实运行时状态；业务 API 需要的是任务主表、结果字段和
    资产列表。本函数就是 ``TaskState -> PixelFlowTaskRecord/AssetRecord`` 的同步桥。
    查询任务详情、run 结束 watcher 都会调用它。返回值是本次同步后可用于前端展示的
    资产记录，watcher 会据此补发 ``asset_ready`` 时间线事件。
    """
    store = _task_store(request)
    task = await store.get(task_id, user_id=user_id)
    if task is None:
        return []
    checkpointer = get_checkpointer(request)
    try:
        checkpoint_tuple = await checkpointer.aget_tuple({"configurable": {"thread_id": task.thread_id}})
        if checkpoint_tuple is None:
            return []
        checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
        state = serialize_channel_values(checkpoint.get("channel_values", {}))
    except Exception:
        logger.debug("Unable to sync PixelFlow task %s from checkpoint", task_id, exc_info=True)
        return []
    phase = str(state.get("phase") or task.phase)
    result = {
        "generated_assets": state.get("generated_assets") or [],
        "timeline": state.get("timeline") or {},
        "draft_path": state.get("draft_path") or "",
        "final_video_url": state.get("final_video_url") or "",
        "qc_report": state.get("qc_report") or {},
    }
    error = state.get("error")
    brief = state.get("brief") or task.brief
    status = "done" if phase == "done" else task.status
    if error and (not brief or phase == "generate"):
        status = "error"
    elif phase in {"brief_review", "segment_review", "edit_review", "qc_review"}:
        status = "pending"
    await store.update(task_id, user_id=user_id, phase=phase, status=status, brief=brief, result=result, error=error)
    synced_assets: list[PixelFlowAssetRecord] = []
    for asset in result["generated_assets"]:
        shot_index = asset.get("shot_index") or asset.get("segment_index")
        synced_assets.append(
            await store.upsert_asset(
                PixelFlowAssetRecord(
                    asset_id=f"{task_id}:generated:{shot_index}",
                    task_id=task_id,
                    user_id=user_id,
                    asset_type="generated_video",
                    status="ready" if asset.get("ok") else "error",
                    phase="generate",
                    shot_id=str(asset.get("shot_id") or shot_index or ""),
                    url=asset.get("url") or "",
                    vendor="borgrise",
                    vendor_task_id=asset.get("task_id"),
                    metadata=asset,
                    error=asset.get("error"),
                )
            )
        )
    if result["draft_path"]:
        synced_assets.append(
            await store.upsert_asset(
                PixelFlowAssetRecord(
                    asset_id=f"{task_id}:draft:jianying",
                    task_id=task_id,
                    user_id=user_id,
                    asset_type="jianying_draft",
                    status="ready",
                    phase="edit",
                    local_path=result["draft_path"],
                    vendor="jianying",
                    metadata={"draft_path": result["draft_path"]},
                )
            )
        )
    if result["final_video_url"]:
        synced_assets.append(
            await store.upsert_asset(
                PixelFlowAssetRecord(
                    asset_id=f"{task_id}:final:video",
                    task_id=task_id,
                    user_id=user_id,
                    asset_type="final_video",
                    status="ready",
                    phase="done",
                    url=result["final_video_url"],
                    metadata={"final_video_url": result["final_video_url"]},
                )
            )
        )
    return synced_assets


async def _watch_run_to_task(
    task_id: str,
    user_id: str | None,
    run_id: str,
    request: Request,
    *,
    suppress_replay_phases: set[str] | None = None,
) -> None:
    """把 LangGraph stream 事件转换成 PixelFlow 业务事件。

    前端订阅的是任务事件表，而不是直接订阅 LangGraph 原始 stream。watcher 会消费
    ``StreamBridge``，在阶段变化时写 ``phase_change`` 和可解释步骤事件，Brief
    准备好时写 ``brief_ready`` + ``llm_summary``，run 结束后先同步 checkpoint，
    再写 ``asset_ready``、``task_done`` 或 ``run_finished``。
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    store = _task_store(request)
    last_phase = None
    brief_emitted = False
    emitted_explainable_keys: set[str] = set()
    suppressed_phases = suppress_replay_phases or set()
    try:
        async for entry in bridge.subscribe(run_id):
            if entry is END_SENTINEL:
                synced_assets = await _sync_task_from_checkpoint(task_id, user_id, request)
                task = await store.get(task_id, user_id=user_id)
                if task and last_phase:
                    await _append_explainable_events(
                        store,
                        task_id,
                        user_id,
                        # run 结束时收口“本 watcher 实际看到的最后阶段”。如果 checkpoint
                        # 已经被同步成 done，但 stream 没发过 done started，就不要凭空补一个
                        # “done finished”，避免前端时间线看起来前后不配对。
                        [("step_finished", _safe_step_payload(str(last_phase), {"phase": last_phase}, "success", run_id))],
                        emitted_explainable_keys,
                    )
                await _append_explainable_events(
                    store,
                    task_id,
                    user_id,
                    _build_asset_ready_events(synced_assets, run_id=run_id),
                    emitted_explainable_keys,
                )
                run = await run_mgr.get(run_id, user_id=user_id)
                if (run and run.status.value == "error") or (task and task.status == "error"):
                    error = run.error if run and run.error else (task.error if task else "PixelFlow run failed")
                    await store.update(task_id, user_id=user_id, status="error", error=error)
                    await store.append_event(task_id, "task_failed", {"run_id": run_id, "error": error}, user_id=user_id)
                    record_power_mem_background(
                        power_mem_service(request),
                        user_id=user_id,
                        content=f"旧 LangGraph 任务流失败；phase={task.phase if task else ''}；error={str(error)[:300]}",
                        category="experience",
                        source_agent="legacy_task_flow",
                        metadata={"source": "task_run_failed", "task_id": task_id, "run_id": run_id, "asset_count": len(synced_assets)},
                        memory_type="experience",
                        run_id=run_id,
                        infer=False,
                    )
                    return
                event = "task_done" if task and task.status == "done" else "run_finished"
                payload = {"run_id": run_id, "status": task.status, "phase": task.phase} if task else {"run_id": run_id}
                await store.append_event(task_id, event, payload, user_id=user_id)
                record_power_mem_background(
                    power_mem_service(request),
                    user_id=user_id,
                    content=concise_result_summary(
                        "旧 LangGraph 任务流结束",
                        {"stage": payload.get("phase"), "message": event, "ok": event == "task_done"},
                    ),
                    category="experience",
                    source_agent="legacy_task_flow",
                    metadata={"source": "task_run_finished", "task_id": task_id, "run_id": run_id, "event": event, "asset_count": len(synced_assets)},
                    memory_type="experience",
                    run_id=run_id,
                    infer=False,
                )
                return
            data = getattr(entry, "data", None)
            if isinstance(data, dict):
                phase = data.get("phase")
                if phase in suppressed_phases:
                    suppressed_phases.remove(str(phase))
                    continue
                if phase and phase != last_phase:
                    previous_phase = str(last_phase) if last_phase else None
                    last_phase = phase
                    await _append_explainable_events(
                        store,
                        task_id,
                        user_id,
                        _build_phase_transition_events(previous_phase, str(phase), data, run_id=run_id),
                        emitted_explainable_keys,
                    )
                    await store.update(task_id, user_id=user_id, phase=str(phase), status="running")
                    await store.append_event(task_id, "phase_change", {"phase": phase}, user_id=user_id)
                if data.get("brief") and phase == "brief_review" and "brief_review" not in suppressed_phases and not brief_emitted:
                    brief_emitted = True
                    await store.update(task_id, user_id=user_id, brief=data["brief"])
                    await store.append_event(task_id, "brief_ready", {"brief": data["brief"]}, user_id=user_id)
                    await _append_explainable_events(
                        store,
                        task_id,
                        user_id,
                        _build_brief_summary_events(data["brief"], run_id=run_id),
                        emitted_explainable_keys,
                    )
                await _append_explainable_events(
                    store,
                    task_id,
                    user_id,
                    _build_state_snapshot_events(data, run_id=run_id),
                    emitted_explainable_keys,
                )
    except Exception as exc:
        logger.warning("PixelFlow task watcher failed task_id=%s run_id=%s", task_id, run_id, exc_info=True)
        await store.update(task_id, user_id=user_id, status="error", error=str(exc))
        await store.append_event(task_id, "task_failed", {"error": str(exc)}, user_id=user_id)


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(body: TaskCreateRequest, request: Request) -> TaskResponse:
    """创建 PixelFlow 业务任务，并按需立即启动工作流。

    调用链：创建业务 task -> 构造初始 TaskState -> 启动 PixelFlow graph run ->
    后台启动 watcher，把 run 进度持续同步回业务事件表。
    """
    store = _task_store(request)
    user_id = await get_current_user(request)
    task_id = str(uuid.uuid4())
    thread_id = f"pixelflow-{task_id}"
    preference_snapshot = {}
    if user_id:
        preference_snapshot = (await _preference_store(request).get(user_id)).to_dict()
    memory_user_id, memories = await search_power_mem(
        request,
        source_agent="legacy_task_flow",
        query_values=[body.product_url, body.product_info, body.video_params.model_dump(), body.creative_direction, body.user_message],
        categories=["preference", "brand", "skill", "experience"],
    )
    if memories:
        preference_snapshot["semantic_memory"] = memory_context_payload(memories)
    initial = _initial_state(task_id, user_id, body, preference_snapshot)
    record = PixelFlowTaskRecord(
        task_id=task_id,
        user_id=user_id,
        task_type=body.task_type,
        status="created",
        phase="intake",
        thread_id=thread_id,
        product_info=initial["product_info"],
        video_params=initial["video_params"],
        reference_videos=initial["reference_videos"],
        creative_direction=initial["creative_direction"],
    )
    record = await store.create(record)
    await store.append_event(task_id, "task_created", {"task_id": task_id, "phase": "intake"}, user_id=user_id)
    record_power_mem_background(
        power_mem_service(request),
        user_id=memory_user_id,
        content=concise_result_summary("旧任务流创建 PixelFlow 任务", {"stage": "task_created", "message": body.user_message or body.task_type, "ok": True}),
        category="experience",
        source_agent="legacy_task_flow",
        metadata={"source": "task_create", "task_id": task_id, "task_type": body.task_type},
        memory_type="experience",
        run_id=task_id,
        infer=False,
    )

    if body.auto_start:
        run_body = RunCreateRequest(
            assistant_id="pixelflow",
            input=initial,
            metadata={"pixelflow_task_id": task_id, "task_type": body.task_type},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode=["values"],
            on_disconnect="continue",
            multitask_strategy="reject",
        )
        run = await _start_pixelflow_run(run_body, thread_id, request)
        record = await store.update(task_id, user_id=user_id, run_id=run.run_id, status="running") or record
        await store.append_event(task_id, "run_started", {"run_id": run.run_id, "thread_id": thread_id}, user_id=user_id)
        asyncio.create_task(_watch_run_to_task(task_id, user_id, run.run_id, request))

    return _response(record)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> list[TaskResponse]:
    user_id = await get_current_user(request)
    rows = await _task_store(request).list(user_id=user_id, limit=limit)
    return [_response(r) for r in rows]


@router.get("/session/context", response_model=SessionContextResponse | None)
async def get_session_context(request: Request, task_id: str | None = Query(default=None)) -> SessionContextResponse | None:
    user_id = await get_current_user(request)
    row = await _task_store(request).get_session_context(task_id, user_id=user_id)
    return SessionContextResponse(**row) if row else None


@router.put("/session/context", response_model=SessionContextResponse)
async def save_session_context(body: SessionContextRequest, request: Request) -> SessionContextResponse:
    user_id = await get_current_user(request)
    store = _task_store(request)
    if await store.get(body.task_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {body.task_id} not found")
    row = await store.upsert_session_context(body.task_id, body.context, user_id=user_id)
    return SessionContextResponse(**row)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, request: Request) -> TaskResponse:
    user_id = await get_current_user(request)
    await _sync_task_from_checkpoint(task_id, user_id, request)
    record = await _task_store(request).get(task_id, user_id=user_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    return _response(record)


@router.get("/{task_id}/result")
async def get_task_result(task_id: str, request: Request) -> dict[str, Any]:
    task = await get_task(task_id, request)
    return {"task_id": task.task_id, "status": task.status, "phase": task.phase, "result": task.result, "error": task.error}


@router.get("/{task_id}/assets", response_model=list[AssetResponse])
async def list_task_assets(task_id: str, request: Request) -> list[AssetResponse]:
    user_id = await get_current_user(request)
    store = _task_store(request)
    if await store.get(task_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    rows = await store.list_assets(task_id, user_id=user_id)
    return [_asset_response(r) for r in rows]


@router.get("/{task_id}/assets/{asset_id}/content")
async def get_task_asset_content(task_id: str, asset_id: str, request: Request):
    user_id = await get_current_user(request)
    store = _task_store(request)
    if await store.get(task_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    rows = await store.list_assets(task_id, user_id=user_id)
    asset = next((row for row in rows if row.asset_id == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow asset {asset_id} not found")
    if asset.url.startswith(("http://", "https://")):
        return RedirectResponse(asset.url)
    path = asset.local_path or asset.url
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Asset file not found")
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))


@router.post("/{task_id}/brief/confirm", response_model=TaskResponse)
async def confirm_brief(task_id: str, body: BriefConfirmRequest, request: Request) -> TaskResponse:
    """确认或驳回 Brief，并恢复 LangGraph 的人工审批 interrupt。

    ``approved=True`` 会让图进入 GENERATE；``approved=False`` 会回到 CREATIVE 重新策划。
    """
    store = _task_store(request)
    user_id = await get_current_user(request)
    task = await store.get(task_id, user_id=user_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    run_body = RunCreateRequest(
        assistant_id="pixelflow",
        command={"resume": {"approved": body.approved}},
        metadata={"pixelflow_task_id": task_id, "action": "brief_confirm"},
        config={"configurable": {"thread_id": task.thread_id}},
        stream_mode=["values"],
        on_disconnect="continue",
        multitask_strategy="reject",
    )
    run = await _start_pixelflow_run(run_body, task.thread_id, request)
    updated = await store.update(task_id, user_id=user_id, run_id=run.run_id, status="running", phase="generate" if body.approved else "creative")
    await store.append_event(task_id, "brief_confirmed" if body.approved else "brief_rejected", {"run_id": run.run_id}, user_id=user_id)
    suppress_phases = {"brief_review"} if body.approved else set()
    asyncio.create_task(_watch_run_to_task(task_id, user_id, run.run_id, request, suppress_replay_phases=suppress_phases))
    return _response(updated or task)


@router.post("/{task_id}/brief/revise", response_model=TaskResponse)
async def revise_brief(task_id: str, body: BriefReviseRequest, request: Request) -> TaskResponse:
    """修改当前业务 Brief，并抽取用户偏好。

    当前实现只更新业务任务表中的 brief、记录反馈并更新偏好，不直接恢复 LangGraph run。
    如果产品希望“修改后立刻继续生成”，需要另开逻辑变更，不应只靠注释表达。
    """
    store = _task_store(request)
    user_id = await get_current_user(request)
    task = await store.get(task_id, user_id=user_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    brief = {**task.brief, **body.brief_patch}
    updated = await store.update(task_id, user_id=user_id, brief=brief, phase="brief_review", status="pending")
    await store.append_event(task_id, "brief_revised", {"brief": brief, "feedback": body.feedback}, user_id=user_id)
    if user_id and body.feedback.strip():
        pref_patch = extract_structured_preferences(body.feedback, brief_patch=body.brief_patch)
        await _preference_store(request).update(user_id, pref_patch)
        await _preference_store(request).append_feedback(user_id, body.feedback, task_id=task_id, metadata={"source": "brief_revise"})
        await store.append_event(task_id, "preferences_updated", {"patch": pref_patch}, user_id=user_id)
        record_power_mem_background(
            power_mem_service(request),
            user_id=user_id,
            content=body.feedback,
            category="preference",
            source_agent="legacy_brief_review_agent",
            metadata={"source": "brief_revise", "task_id": task_id, "preference_patch": pref_patch},
            memory_type="preference",
            run_id=task_id,
        )
    return _response(updated or task)


@router.post("/{task_id}/stages/{stage}/confirm", response_model=TaskResponse)
async def confirm_stage(task_id: str, stage: Literal["segments", "edit", "qc"], body: StageConfirmRequest, request: Request) -> TaskResponse:
    store = _task_store(request)
    user_id = await get_current_user(request)
    task = await store.get(task_id, user_id=user_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    stage_phase = {"segments": "segment_review", "edit": "edit_review", "qc": "qc_review"}[stage]
    if task.phase != stage_phase:
        raise HTTPException(status_code=409, detail=f"Task is in phase {task.phase}, not {stage_phase}")
    run_body = RunCreateRequest(
        assistant_id="pixelflow",
        command={"resume": {"approved": body.approved}},
        metadata={"pixelflow_task_id": task_id, "action": f"{stage}_confirm"},
        config={"configurable": {"thread_id": task.thread_id}},
        stream_mode=["values"],
        on_disconnect="continue",
        multitask_strategy="reject",
    )
    run = await _start_pixelflow_run(run_body, task.thread_id, request)
    next_phase = {"segments": "edit", "edit": "qc", "qc": "done"}[stage] if body.approved else {"segments": "generate", "edit": "edit", "qc": "generate"}[stage]
    updated = await store.update(task_id, user_id=user_id, run_id=run.run_id, status="running", phase=next_phase)
    event_name = f"{stage}_confirmed" if body.approved else f"{stage}_rejected"
    await store.append_event(task_id, event_name, {"run_id": run.run_id}, user_id=user_id)
    suppress_phases = {"segments": "segment_review", "edit": "edit_review", "qc": "qc_review"}
    asyncio.create_task(_watch_run_to_task(task_id, user_id, run.run_id, request, suppress_replay_phases={suppress_phases[stage]}))
    return _response(updated or task)


@router.get("/{task_id}/events")
async def stream_task_events(task_id: str, request: Request, after_id: int | None = Query(default=None)) -> StreamingResponse:
    """业务侧 SSE 事件流。

    这里每秒轮询 ``pixelflow_task_events`` 表，把新增事件转为 SSE 返回给前端。
    ``after_id`` 用于断点续订；这不是直接透传 LangGraph stream。
    """
    store = _task_store(request)
    user_id = await get_current_user(request)
    if await store.get(task_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    authorization = request.headers.get("Authorization", "")

    async def gen():
        cursor = after_id
        while True:
            try:
                # SSE 是长连接：中间件只能在建连时校验一次。这里每轮都让
                # content-app 再确认 token，保证用户被禁用后不会继续收到任务进度。
                await verify_authorization_header_remote(authorization)
            except ContentAppAuthError as exc:
                yield format_sse("auth_revoked", {"code": exc.code, "message": exc.message})
                return
            rows = await store.list_events(task_id, user_id=user_id, after_id=cursor, limit=100)
            for row in rows:
                cursor = row["id"]
                yield format_sse(row["event"], row["data"], event_id=str(row["id"]))
            if await request.is_disconnected():
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_id}/events/history")
async def list_task_events(task_id: str, request: Request, after_id: int | None = Query(default=None), limit: int = Query(default=200, ge=1, le=500)) -> dict[str, Any]:
    store = _task_store(request)
    user_id = await get_current_user(request)
    if await store.get(task_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail=f"PixelFlow task {task_id} not found")
    rows = await store.list_events(task_id, user_id=user_id, after_id=after_id, limit=limit)
    return {"data": rows}
