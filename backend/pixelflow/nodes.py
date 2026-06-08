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
from pixelflow.intake import demand_integrity_check, normalize_video_params, product_info_extract
from pixelflow.skills import get_video_skill
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


async def generate_node(state: TaskState) -> TaskState:
    """生成: shot-by-shot generation via the video-generation skill.

    Iterates the Brief's shots and produces one clip per shot through the
    capability interface (vendor-agnostic). Shots come from the CREATIVE phase
    (Brief schema: PRD §9.4); until that is implemented the Brief has no shots
    and this node is a no-op, keeping the pipeline runnable offline.

    TODO: expand each shot's prompt via PromptEngine and pick image_to_video vs
    extend_video per the shot's asset_strategy.
    """
    task_id = state.get("task_id")
    shots = (state.get("brief") or {}).get("shots", [])
    logger.info("[pixelflow] generate task_id=%s shots=%d", task_id, len(shots))

    if not shots:
        return {"phase": Phase.EDIT.value, "generated_assets": []}

    skill = get_video_skill()
    assets: list[dict] = []
    for i, shot in enumerate(shots):
        image_url = shot.get("image_url")
        if not image_url:
            assets.append({"shot_index": i, "ok": False, "error": "shot has no image_url"})
            continue
        result = await skill.image_to_video(
            image_url=image_url,
            prompt=shot.get("generation_prompt"),
            duration=shot.get("duration", 10),
            ratio=shot.get("ratio", "9:16"),
        )
        assets.append(
            {
                "shot_index": i,
                "ok": result.ok,
                "url": result.url,
                "task_id": result.task_id,
                "error": result.error,
            }
        )

    return {"phase": Phase.EDIT.value, "generated_assets": assets}


async def edit_node(state: TaskState) -> TaskState:
    """剪辑: assemble generated shots into the final video.

    TODO: build the Timeline IR and render via FFmpeg / 剪映 draft (multi-shot
    editing) — distinct from extend-video, which only extends a single shot.
    """
    logger.info("[pixelflow] edit task_id=%s", state.get("task_id"))
    return {"phase": Phase.QC.value, "timeline": {}, "final_video_url": ""}


async def qc_node(state: TaskState) -> TaskState:
    """质检: quality check; route back to GENERATE on failure (bounded)."""
    logger.info("[pixelflow] qc task_id=%s", state.get("task_id"))
    attempts = state.get("qc_attempts", 0) + 1
    # TODO: real QC scoring; stub passes so the happy path terminates.
    passed = True
    return {
        "phase": (Phase.DONE if passed else Phase.GENERATE).value,
        "qc_passed": passed,
        "qc_attempts": attempts,
        "qc_report": {},
    }
