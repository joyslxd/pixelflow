"""PixelFlow P0 business task API.

This wraps the lower-level LangGraph run API in e-commerce-video terminology:
task creation, task status/result lookup, Brief confirmation/revision, and a
PixelFlow progress event stream.
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


def _apply_preference_defaults(
    video_params: dict[str, Any],
    creative_direction: dict[str, Any],
    preference_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
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
    product_info = dict(body.product_info)
    if body.product_url:
        product_info.setdefault("product_url", body.product_url)
    video_params, creative_direction = _apply_preference_defaults(
        body.video_params.model_dump(),
        dict(body.creative_direction),
        preference_snapshot or {},
    )
    # Existing intake code expects video_duration_sec.
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
    elif phase in {"brief_review", "segment_review", "edit_review", "qc_review"}:
        status = "pending"
    await store.update(task_id, user_id=user_id, phase=phase, status=status, brief=brief, result=result, error=error)
    for asset in result["generated_assets"]:
        shot_index = asset.get("shot_index") or asset.get("segment_index")
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
    suppress_replay_phases: set[str] | None = None,
) -> None:
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    store = _task_store(request)
    last_phase = None
    brief_emitted = False
    suppressed_phases = suppress_replay_phases or set()
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
                if phase in suppressed_phases:
                    suppressed_phases.remove(str(phase))
                    continue
                if phase and phase != last_phase:
                    last_phase = phase
                    await store.update(task_id, user_id=user_id, phase=str(phase), status="running")
                    await store.append_event(task_id, "phase_change", {"phase": phase}, user_id=user_id)
                if data.get("brief") and phase == "brief_review" and "brief_review" not in suppressed_phases and not brief_emitted:
                    brief_emitted = True
                    await store.update(task_id, user_id=user_id, brief=data["brief"])
                    await store.append_event(task_id, "brief_ready", {"brief": data["brief"]}, user_id=user_id)
    except Exception as exc:
        logger.warning("PixelFlow task watcher failed task_id=%s run_id=%s", task_id, run_id, exc_info=True)
        await store.update(task_id, user_id=user_id, status="error", error=str(exc))
        await store.append_event(task_id, "task_failed", {"error": str(exc)}, user_id=user_id)


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(body: TaskCreateRequest, request: Request) -> TaskResponse:
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
