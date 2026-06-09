"""PixelFlow phase nodes.

These are skeleton implementations: each node advances the pipeline and records
its phase, leaving the real work (skill calls) as marked TODOs. The control
flow — phase transitions, the Brief human-in-the-loop gate, and the QC retry
loop — is fully wired so the graph runs end-to-end with stub logic.
"""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from pixelflow.creative import brief_generate, validate_and_fix
from pixelflow.edit import build_timeline
from pixelflow.generate import build_seedance_prompt
from pixelflow.intake import demand_integrity_check, normalize_video_params, product_info_extract
from pixelflow.qc import qc_check
from pixelflow.skills import get_video_edit_skill, get_video_skill
from pixelflow.state import Phase, TaskState

logger = logging.getLogger(__name__)

# Bounds the QC -> GENERATE retry loop so a persistently failing task terminates.
MAX_QC_ATTEMPTS = 2
# Bounds the INTAKE follow-up loop so an unanswerable demand can't spin forever.
MAX_INTAKE_ROUNDS = 3


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

    video_params, _notes = normalize_video_params(state.get("video_params"))
    result = demand_integrity_check(product_info, video_params, creative_direction, state.get("reference_videos"))

    if not result.is_complete and rounds < MAX_INTAKE_ROUNDS:
        answers = interrupt({"action": "collect_demand", "questions": result.questions(), "check": result.model_dump()})
        if isinstance(answers, dict):
            product_info.update(answers.get("product_info") or {})
            creative_direction.update(answers.get("creative_direction") or {})
            video_params, _notes = normalize_video_params({**video_params, **(answers.get("video_params") or {})})
            result = demand_integrity_check(product_info, video_params, creative_direction, state.get("reference_videos"))

    next_phase = Phase.CREATIVE if result.is_complete else Phase.INTAKE
    return {
        "phase": next_phase.value,
        "product_info": product_info,
        "video_params": video_params,
        "creative_direction": creative_direction,
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

    try:
        brief = await brief_generate(
            product_info=product_info,
            video_params=video_params,
            creative_direction=direction,
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
    Expected payload: ``{"approved": bool}`` (defaults to approved).
    """
    decision = interrupt({"brief": state.get("brief", {}), "action": "confirm_brief"})
    approved = bool(decision.get("approved", True)) if isinstance(decision, dict) else True
    next_phase = Phase.GENERATE if approved else Phase.CREATIVE
    return {"phase": next_phase.value, "brief_approved": approved}


def _resolve_shot_image(shot: dict, product_info: dict) -> tuple[str | None, str | None]:
    """Resolve a shot's source image from its ``asset_strategy`` (PRD §9.4).

    The Brief schema (§9.4) carries no per-shot image URL — the asset is bound
    here at generation time. The product's main image is the anchor for
    ``use_real_asset``/``mixed``; the strategies that need an unbuilt capability
    (text-to-image for ``generate_asset``, reference parsing for
    ``use_reference_structure``) fall back to the product image so the pipeline
    runs, recording a note so the gap stays visible.

    Returns ``(image_url, note)``; ``image_url`` is None when no source exists.
    """
    main = product_info.get("main_image_url")
    strategy = shot.get("asset_strategy", "use_real_asset")
    if strategy in ("use_real_asset", "mixed"):
        return main, None
    if main:
        return main, f"asset_strategy '{strategy}' 暂未支持，已回退到商品主图"
    return None, None


async def generate_node(state: TaskState) -> TaskState:
    """生成: shot-by-shot generation via the video-generation skill.

    Iterates the Brief's shots and produces one clip per shot through the
    capability interface (vendor-agnostic). Each shot's source image is resolved
    from ``product_info`` by ``asset_strategy`` (see ``_resolve_shot_image``).
    With no shots (Brief empty) this is a no-op, keeping the pipeline runnable
    offline.

    TODO: expand each shot's prompt via PromptEngine; add a text-to-image skill
    for ``generate_asset`` and ``extend_video`` for shot continuity.
    """
    task_id = state.get("task_id")
    brief = state.get("brief") or {}
    product_info = state.get("product_info") or {}
    shots = brief.get("shots", [])
    global_visual = brief.get("global_visual") or {}
    ratio = brief.get("ratio", "9:16")
    logger.info("[pixelflow] generate task_id=%s shots=%d", task_id, len(shots))

    if not shots:
        return {"phase": Phase.EDIT.value, "generated_assets": []}

    skill = get_video_skill()
    assets: list[dict] = []
    for i, shot in enumerate(shots):
        image_url, note = _resolve_shot_image(shot, product_info)
        if not image_url:
            assets.append({"shot_index": i, "ok": False, "error": "无可用图源：商品缺少 main_image_url"})
            continue
        duration = shot.get("duration", 10)
        result = await skill.image_to_video(
            image_url=image_url,
            prompt=build_seedance_prompt(shot, global_visual, duration),
            duration=duration,
            ratio=ratio,
        )
        record = {
            "shot_index": i,
            "ok": result.ok,
            "url": result.url,
            "task_id": result.task_id,
            "error": result.error,
        }
        if note:
            record["note"] = note
        assets.append(record)

    return {"phase": Phase.EDIT.value, "generated_assets": assets}


async def edit_node(state: TaskState) -> TaskState:
    """剪辑: assemble generated shots into a Timeline and a 剪映 draft.

    ``build_timeline`` (纯逻辑) binds each generated clip to its Brief shot and
    carries the shot's editing metadata (duration, transitions, narration/花字),
    skipping shots that produced no usable clip. The edit skill then renders the
    Timeline into an editable 剪映 draft folder (``draft_path``).

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
    if timeline.clips:
        try:
            result = await get_video_edit_skill().render(timeline.model_dump(), draft_name=f"pixelflow_{task_id}")
            if result.ok:
                draft_path = result.output_path or ""
            else:
                notes.append(f"剪映草稿生成失败: {result.error}")
        except Exception as exc:  # noqa: BLE001 - boundary: never crash the EDIT phase
            logger.exception("[pixelflow] edit render failed task_id=%s", task_id)
            notes.append(f"剪映草稿生成异常: {exc}")

    return {
        "phase": Phase.QC.value,
        "timeline": timeline.model_dump(),
        "draft_path": draft_path,
        "final_video_url": "",
        "edit_notes": notes,
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
        "phase": (Phase.DONE if result.passed else Phase.GENERATE).value,
        "qc_passed": result.passed,
        "qc_attempts": attempts,
        "qc_report": result.model_dump(),
    }
