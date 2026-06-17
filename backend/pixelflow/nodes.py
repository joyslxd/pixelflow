"""PixelFlow phase nodes.

These are skeleton implementations: each node advances the pipeline and records
its phase, leaving the real work (skill calls) as marked TODOs. The control
flow — phase transitions, the Brief human-in-the-loop gate, and the QC retry
loop — is fully wired so the graph runs end-to-end with stub logic.
"""

from __future__ import annotations

import asyncio
import logging
import math

from langgraph.types import interrupt

from pixelflow.creative import brief_generate, validate_and_fix
from pixelflow.edit import build_timeline
from pixelflow.generate import build_segment_prompt, plan_segments
from pixelflow.intake import demand_integrity_check, normalize_video_params, product_info_extract, summarize_storyboards
from pixelflow.qc import qc_check
from pixelflow.skills import get_video_decompose_skill, get_video_edit_skill, get_video_skill
from pixelflow.state import Phase, TaskState

logger = logging.getLogger(__name__)

# Bounds the QC -> GENERATE retry loop so a persistently failing task terminates.
MAX_QC_ATTEMPTS = 2
# Bounds the INTAKE follow-up loop so an unanswerable demand can't spin forever.
MAX_INTAKE_ROUNDS = 3
# seedance-2.0 单次最长 10s(v2 skill 校验上限)；短分镜按下限生成，>10s 由
# 多段并行 + concat 承接(plan_segments),EDIT 再裁回精确时长。
SEEDANCE_MIN_DURATION = 4
SEEDANCE_MAX_DURATION = 10


async def _parse_reference_videos(reference_videos: list | None, task_id: str | None) -> list[dict]:
    """Decompose pending reference videos into storyboards via the decompose skill.

    Each entry is copied; ones already ``done``/``failed`` (or without a URL)
    pass through untouched, so loop re-entries and resumes never re-parse.
    Failure-safe: a vendor error marks the entry ``failed`` (the integrity check
    treats that as a non-blocking warn) instead of crashing INTAKE.
    """
    refs = [dict(r or {}) for r in reference_videos or []]
    pending = [r for r in refs if r.get("url") and r.get("status") not in ("done", "failed")]
    if not pending:
        return refs
    skill = get_video_decompose_skill()
    for ref in pending:
        result = await skill.decompose_video_to_storyboard(ref["url"])
        if result.ok:
            ref["status"] = "done"
            ref["storyboard"] = result.shots
        else:
            ref["status"] = "failed"
            ref["error"] = result.error
            logger.warning("[pixelflow] reference decompose failed task_id=%s url=%s error=%s", task_id, ref["url"], result.error)
    return refs


async def intake_node(state: TaskState) -> TaskState:
    """采集: extract product info, normalize params, gate on demand integrity.

    One human-in-the-loop round per invocation: if the demand is incomplete it
    ``interrupt``s to ask for the missing fields (≤3), merges the answers, and
    re-checks. While still incomplete the graph loops back here (bounded by
    ``MAX_INTAKE_ROUNDS``); when complete it advances to CREATIVE.

    Failure-safe: a product-page fetch/extract failure is logged and the run
    continues — the integrity gate then asks the user for the missing fields.
    """
    task_id = state.get("task_id")
    product_info = dict(state.get("product_info") or {})
    creative_direction = dict(state.get("creative_direction") or {})
    rounds = state.get("intake_rounds", 0)
    logger.info("[pixelflow] intake task_id=%s round=%d", task_id, rounds)

    # Extract from a product URL only once (guard on already-having a name so a
    # loop re-entry / resume doesn't re-fetch).
    if product_info.get("product_url") and not product_info.get("product_name"):
        try:
            extracted = await product_info_extract(product_info["product_url"], product_info.get("user_note", ""))
            # User-supplied values win over scraped ones.
            product_info = {**extracted.model_dump(), **product_info}
        except Exception:  # noqa: BLE001 - boundary: never crash on a bad link
            logger.exception("[pixelflow] product_info_extract failed task_id=%s", task_id)

    reference_videos = await _parse_reference_videos(state.get("reference_videos"), task_id)

    video_params, _notes = normalize_video_params(state.get("video_params"))
    result = demand_integrity_check(product_info, video_params, creative_direction, reference_videos)

    if not result.is_complete and rounds < MAX_INTAKE_ROUNDS:
        answers = interrupt({"action": "collect_demand", "questions": result.questions(), "check": result.model_dump()})
        if isinstance(answers, dict):
            product_info.update(answers.get("product_info") or {})
            creative_direction.update(answers.get("creative_direction") or {})
            video_params, _notes = normalize_video_params({**video_params, **(answers.get("video_params") or {})})
            result = demand_integrity_check(product_info, video_params, creative_direction, reference_videos)

    next_phase = Phase.CREATIVE if result.is_complete else Phase.INTAKE
    return {
        "phase": next_phase.value,
        "product_info": product_info,
        "video_params": video_params,
        "creative_direction": creative_direction,
        "reference_videos": reference_videos,
        "intake_check": result.model_dump(),
        "demand_complete": result.is_complete,
        "intake_rounds": rounds + 1,
    }


async def creative_node(state: TaskState) -> TaskState:
    """策划: produce the Brief and validate its hard constraints.

    brief_generate (纯 Claude) drafts the shot plan, then
    brief_constraint_validator (纯逻辑, PRD §9.5) auto-fixes hard-constraint
    violations. ``brief_valid`` is False when any unresolved ``warn`` issue
    remains, so the BRIEF_REVIEW gate can flag it for the user.

    Failure-safe: if the LLM/config is unavailable (e.g. offline), this logs
    and emits an empty Brief with ``brief_valid=False`` rather than crashing —
    the human gate then catches it.
    """
    task_id = state.get("task_id")
    logger.info("[pixelflow] creative task_id=%s", task_id)

    product_info = state.get("product_info") or {}
    vp = state.get("video_params") or {}
    video_params = {
        "platform": vp.get("platform", "douyin"),
        "duration_sec": vp.get("video_duration_sec", 30),
        "ratio": vp.get("ratio", "9:16"),
        "size": vp.get("size", "1080x1920"),
    }
    cd = state.get("creative_direction") or {}
    direction = cd if isinstance(cd, str) else "；".join(f"{k}: {v}" for k, v in cd.items() if v)

    # 参考视频分析 (纯逻辑摘要) drives the creative mode: 无参考→original,
    # 单参考→reference (结构复刻), 多参考→attribution (归因融合).
    reference_analysis = summarize_storyboards(state.get("reference_videos"))
    video_count = (reference_analysis or {}).get("video_count", 0)
    creative_mode = "original" if video_count == 0 else ("reference" if video_count == 1 else "attribution")

    try:
        brief = await brief_generate(
            product_info=product_info,
            video_params=video_params,
            creative_direction=direction,
            reference_analysis=reference_analysis,
            creative_mode=creative_mode,
        )
    except Exception as exc:  # noqa: BLE001 - boundary: never crash the CREATIVE phase
        logger.exception("[pixelflow] brief_generate failed task_id=%s", task_id)
        return {"phase": Phase.BRIEF_REVIEW.value, "brief": {}, "brief_valid": False, "error": str(exc)}

    fixed, issues = validate_and_fix(brief, product_info)
    brief_valid = not any(i["level"] == "warn" for i in issues)
    logger.info("[pixelflow] creative task_id=%s shots=%d issues=%d valid=%s", task_id, len(fixed.shots), len(issues), brief_valid)
    return {
        "phase": Phase.BRIEF_REVIEW.value,
        "brief": fixed.model_dump(),
        "brief_valid": brief_valid,
        "brief_issues": issues,
    }


async def brief_review_node(state: TaskState) -> TaskState:
    """Human-in-the-loop: pause for the user to approve or revise the Brief.

    ``interrupt`` suspends the run; the resume payload decides the next phase.
    Expected payload: ``{"approved": bool}``. Missing/invalid payload is
    treated as not approved so the pipeline never advances without an explicit
    user confirmation.
    """
    decision = interrupt({"brief": state.get("brief", {}), "action": "confirm_brief"})
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    next_phase = Phase.GENERATE if approved else Phase.CREATIVE
    return {"phase": next_phase.value, "brief_approved": approved}


async def segment_review_node(state: TaskState) -> TaskState:
    """Pause after video segments are generated so the user can approve them."""
    decision = interrupt({"action": "confirm_segments", "generated_assets": state.get("generated_assets", [])})
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    next_phase = Phase.EDIT if approved else Phase.GENERATE
    return {"phase": next_phase.value, "segments_approved": approved}


async def edit_review_node(state: TaskState) -> TaskState:
    """Pause after editing/draft generation so the user can approve the edit."""
    decision = interrupt(
        {
            "action": "confirm_edit",
            "timeline": state.get("timeline", {}),
            "draft_path": state.get("draft_path", ""),
            "final_video_url": state.get("final_video_url", ""),
        }
    )
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    next_phase = Phase.QC if approved else Phase.EDIT
    return {"phase": next_phase.value, "edit_approved": approved}


async def qc_review_node(state: TaskState) -> TaskState:
    """Pause after QC so the user decides whether to accept or regenerate."""
    decision = interrupt({"action": "confirm_qc", "qc_report": state.get("qc_report", {})})
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    if approved:
        next_phase = Phase.DONE
    elif state.get("qc_attempts", 0) >= MAX_QC_ATTEMPTS:
        next_phase = Phase.DONE
    else:
        next_phase = Phase.GENERATE
    return {"phase": next_phase.value, "qc_approved": approved}


async def _generate_segment(skill, segment: dict, *, image_url: str, global_visual: dict, ratio: str) -> dict:
    """Generate one segment clip (a group of shots, ≤15s) in a single call.

    seedance takes integer seconds in [4, 15]; ceil then clamp the segment's
    duration so the clip is valid and long enough for EDIT to trim back to the
    exact length. Returns a normalized asset record keyed by ``segment_index``.
    """
    gen_duration = max(SEEDANCE_MIN_DURATION, min(SEEDANCE_MAX_DURATION, math.ceil(segment["duration"])))
    result = await skill.image_to_video(
        image_url=image_url,
        prompt=build_segment_prompt(segment["shots"], global_visual),
        duration=gen_duration,
        ratio=ratio,
    )
    return {
        "segment_index": segment["index"],
        "shot_indices": segment["shot_indices"],
        "duration": segment["duration"],
        "ok": result.ok,
        "url": result.url,
        "task_id": result.task_id,
        "error": result.error,
    }


async def generate_node(state: TaskState) -> TaskState:
    """生成: segment-based generation via the video-generation skill.

    seedance-2.0 produces a coherent clip up to 15s per call, so the Brief's
    shots are grouped into the fewest ≤15s segments (``plan_segments``) and each
    segment is generated once from a fused multi-scene prompt — a ≤15s video is a
    single call, longer videos are several segments generated **in parallel** and
    concatenated in EDIT. The product main image anchors every segment.

    With no shots (Brief empty) this is a no-op, keeping the pipeline runnable
    offline. Failure-safe: a missing image fails all segments with a note rather
    than crashing; per-segment vendor errors are normalized by the skill.
    """
    task_id = state.get("task_id")
    brief = state.get("brief") or {}
    product_info = state.get("product_info") or {}
    shots = brief.get("shots", [])
    global_visual = brief.get("global_visual") or {}
    ratio = brief.get("ratio", "9:16")

    if not shots:
        return {"phase": Phase.GENERATE.value, "generated_assets": [], "generation_ready": False, "error": "Brief 中没有可生成的分镜"}

    segments = plan_segments(shots, SEEDANCE_MAX_DURATION)
    logger.info("[pixelflow] generate task_id=%s shots=%d segments=%d", task_id, len(shots), len(segments))

    image_url = product_info.get("main_image_url")
    if not image_url:
        assets = [{"segment_index": s["index"], "shot_indices": s["shot_indices"], "duration": s["duration"], "ok": False, "error": "无可用图源：商品缺少 main_image_url"} for s in segments]
        return {"phase": Phase.GENERATE.value, "generated_assets": assets, "generation_ready": False, "error": "无可用图源：商品缺少 main_image_url"}

    skill = get_video_skill()
    assets = await asyncio.gather(*(_generate_segment(skill, s, image_url=image_url, global_visual=global_visual, ratio=ratio) for s in segments))
    ready = any(asset.get("ok") and asset.get("url") for asset in assets)
    if not ready:
        errors = [str(asset.get("error")) for asset in assets if asset.get("error")]
        error = "视频生成失败，未返回可用片段"
        if errors:
            error = f"{error}: {'; '.join(errors[:3])}"
        return {"phase": Phase.GENERATE.value, "generated_assets": list(assets), "generation_ready": False, "error": error}
    return {"phase": Phase.SEGMENT_REVIEW.value, "generated_assets": list(assets), "generation_ready": True, "segments_approved": False, "error": ""}


async def edit_node(state: TaskState) -> TaskState:
    """剪辑: assemble generated shots into a Timeline and a 剪映 draft.

    ``build_timeline`` (纯逻辑) binds each generated clip to its Brief shot and
    carries the shot's editing metadata (duration, transitions, narration/花字),
    skipping shots that produced no usable clip. The edit skill then renders the
    Timeline — into an editable 剪映 draft folder (``draft_path``) or, with the
    FFmpeg skill, a finished mp4 (``final_video_url``), routed by ``result.kind``.

    Failure-safe: with no clips the skill is skipped; a missing render dep or a
    render error is logged into ``edit_notes`` rather than crashing — so the
    pipeline still advances to QC (and runs offline).
    """
    task_id = state.get("task_id")
    brief = state.get("brief") or {}
    assets = state.get("generated_assets") or []
    timeline, notes = build_timeline(brief, assets)
    logger.info("[pixelflow] edit task_id=%s clips=%d skipped=%d", task_id, len(timeline.clips), len(notes))

    draft_path = ""
    final_video_url = ""
    if timeline.clips:
        try:
            result = await get_video_edit_skill().render(timeline.model_dump(), draft_name=f"pixelflow_{task_id}")
            if result.ok:
                if result.kind == "video":
                    final_video_url = result.output_path or ""
                else:
                    draft_path = result.output_path or ""
            else:
                notes.append(f"剪辑渲染失败: {result.error}")
        except Exception as exc:  # noqa: BLE001 - boundary: never crash the EDIT phase
            logger.exception("[pixelflow] edit render failed task_id=%s", task_id)
            notes.append(f"剪辑渲染异常: {exc}")

    return {
        "phase": Phase.EDIT_REVIEW.value,
        "timeline": timeline.model_dump(),
        "draft_path": draft_path,
        "final_video_url": final_video_url,
        "edit_notes": notes,
        "edit_approved": False,
    }


async def qc_node(state: TaskState) -> TaskState:
    """质检: verdict over the produced output; route back to GENERATE on failure.

    ``qc_check`` (纯逻辑) inspects generation coverage (blocking) and assembled
    duration (warn). A blocking ``fail`` re-runs GENERATE, bounded by
    ``MAX_QC_ATTEMPTS`` so a persistently failing task terminates.
    """
    task_id = state.get("task_id")
    attempts = state.get("qc_attempts", 0) + 1
    result = qc_check(state.get("brief") or {}, state.get("generated_assets") or [], state.get("timeline") or {})
    logger.info("[pixelflow] qc task_id=%s attempt=%d passed=%s score=%.2f", task_id, attempts, result.passed, result.score)
    return {
        "phase": Phase.QC_REVIEW.value,
        "qc_passed": result.passed,
        "qc_approved": False,
        "qc_attempts": attempts,
        "qc_report": result.model_dump(),
    }
