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
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.gateway.deps import get_checkpointer, get_current_user, get_run_context, get_run_manager, get_stream_bridge
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import build_run_config, format_sse, inject_authenticated_user_context, merge_run_context_overrides, normalize_input, normalize_stream_modes
from deerflow.runtime import END_SENTINEL, ConflictError, DisconnectMode, UnsupportedStrategyError, run_agent, serialize_channel_values
from pixelflow import make_pixelflow_graph
from pixelflow.preferences import UserPreferenceStore, extract_structured_preferences
from pixelflow.tasks import PixelFlowAssetRecord, PixelFlowTaskRecord, PixelFlowTaskStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["pixelflow-tasks"])


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


async def _sync_task_from_checkpoint(task_id: str, user_id: str | None, request: Request) -> None:
    """从 LangGraph checkpoint 同步业务任务视图和资产表。

    LangGraph checkpoint 是真实运行时状态；业务 API 需要的是任务主表、结果字段和
    资产列表。本函数就是 ``TaskState -> PixelFlowTaskRecord/AssetRecord`` 的同步桥。
    查询任务详情、run 结束 watcher 都会调用它。
    """
    store = _task_store(request)
    task = await store.get(task_id, user_id=user_id)
    if task is None:
        return
    checkpointer = get_checkpointer(request)
    try:
        checkpoint_tuple = await checkpointer.aget_tuple({"configurable": {"thread_id": task.thread_id}})
        if checkpoint_tuple is None:
            return
        checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
        state = serialize_channel_values(checkpoint.get("channel_values", {}))
    except Exception:
        logger.debug("Unable to sync PixelFlow task %s from checkpoint", task_id, exc_info=True)
        return
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
    elif phase == "brief_review":
        status = "pending"
    await store.update(task_id, user_id=user_id, phase=phase, status=status, brief=brief, result=result, error=error)
    for asset in result["generated_assets"]:
        # 注意：generate_node 当前写的是 segment_index。这里保留既有 shot_index 读取
        # 逻辑，意味着多段资产可能落到 generated:None；这是已知风险，后续应单独修复，
        # 本次只补注释不改变业务行为。
        shot_index = asset.get("shot_index")
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
    if result["draft_path"]:
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
    if result["final_video_url"]:
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


async def _watch_run_to_task(
    task_id: str,
    user_id: str | None,
    run_id: str,
    request: Request,
    *,
    suppress_pending_replay: bool = False,
) -> None:
    """把 LangGraph stream 事件转换成 PixelFlow 业务事件。

    前端订阅的是任务事件表，而不是直接订阅 LangGraph 原始 stream。watcher 会消费
    ``StreamBridge``，在阶段变化时写 ``phase_change``，Brief 准备好时写
    ``brief_ready``，run 结束后先同步 checkpoint，再写 ``task_done`` 或
    ``run_finished``。
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    store = _task_store(request)
    last_phase = None
    try:
        async for entry in bridge.subscribe(run_id):
            if entry is END_SENTINEL:
                await _sync_task_from_checkpoint(task_id, user_id, request)
                task = await store.get(task_id, user_id=user_id)
                run = await run_mgr.get(run_id, user_id=user_id)
                if (run and run.status.value == "error") or (task and task.status == "error"):
                    error = run.error if run and run.error else (task.error if task else "PixelFlow run failed")
                    await store.update(task_id, user_id=user_id, status="error", error=error)
                    await store.append_event(task_id, "task_failed", {"run_id": run_id, "error": error}, user_id=user_id)
                    return
                event = "task_done" if task and task.status == "done" else "run_finished"
                payload = {"run_id": run_id, "status": task.status, "phase": task.phase} if task else {"run_id": run_id}
                await store.append_event(task_id, event, payload, user_id=user_id)
                return
            data = getattr(entry, "data", None)
            if isinstance(data, dict):
                phase = data.get("phase")
                if suppress_pending_replay and phase == "brief_review":
                    continue
                if phase and phase != last_phase:
                    last_phase = phase
                    await store.update(task_id, user_id=user_id, phase=str(phase), status="running")
                    await store.append_event(task_id, "phase_change", {"phase": phase}, user_id=user_id)
                if data.get("brief") and phase == "brief_review" and not suppress_pending_replay:
                    await store.update(task_id, user_id=user_id, brief=data["brief"])
                    await store.append_event(task_id, "brief_ready", {"brief": data["brief"]}, user_id=user_id)
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
    asyncio.create_task(_watch_run_to_task(task_id, user_id, run.run_id, request, suppress_pending_replay=body.approved))
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

    async def gen():
        cursor = after_id
        while True:
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
