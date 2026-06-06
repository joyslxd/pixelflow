"""PixelFlow phase nodes.

These are skeleton implementations: each node advances the pipeline and records
its phase, leaving the real work (skill calls) as marked TODOs. The control
flow — phase transitions, the Brief human-in-the-loop gate, and the QC retry
loop — is fully wired so the graph runs end-to-end with stub logic.
"""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from deerflow.pixelflow.skills import get_video_skill
from deerflow.pixelflow.state import Phase, TaskState

logger = logging.getLogger(__name__)

# Bounds the QC -> GENERATE retry loop so a persistently failing task terminates.
MAX_QC_ATTEMPTS = 2


async def intake_node(state: TaskState) -> TaskState:
    """采集: collect product info and check demand integrity.

    TODO: call product_info_extract + demand_integrity_check skills; when the
    demand is incomplete, ask follow-up questions instead of advancing.
    """
    logger.info("[pixelflow] intake task_id=%s", state.get("task_id"))
    return {"phase": Phase.CREATIVE.value, "demand_complete": True}


async def creative_node(state: TaskState) -> TaskState:
    """策划: produce the Brief and validate its hard constraints.

    TODO: call brief_generate + brief_constraint_validator + creative
    direction analysis. Brief schema is authoritative per PRD §9.4.
    """
    logger.info("[pixelflow] creative task_id=%s", state.get("task_id"))
    return {"phase": Phase.BRIEF_REVIEW.value, "brief": {}, "brief_valid": True}


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
