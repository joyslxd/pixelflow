"""PixelFlow task state and phase definitions.

The PixelFlow agent is a staged pipeline (not a free-form ReAct loop). A task
moves through five phases that mirror the PRD: 采集 → 策划 → 生成 → 剪辑 → 质检.
``TaskState`` is the single LangGraph state object threaded through every node;
each node returns a partial update that LangGraph merges.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class Phase(StrEnum):
    """Pipeline phases. Order matters: it defines the happy-path progression."""

    INTAKE = "intake"  # 采集: gather product info, check demand integrity
    CREATIVE = "creative"  # 策划: generate Brief + validate constraints
    BRIEF_REVIEW = "brief_review"  # human-in-the-loop confirmation of the Brief
    GENERATE = "generate"  # 生成: shot-by-shot video generation via Borgrise
    EDIT = "edit"  # 剪辑: assemble shots into the final video
    QC = "qc"  # 质检: quality check, may loop back to GENERATE
    DONE = "done"  # terminal


class TaskState(TypedDict, total=False):
    """State for a single PixelFlow video-generation task.

    All fields are optional (``total=False``) so nodes can return partial
    updates. ``messages`` uses the LangGraph reducer so chat turns accumulate;
    every other field is last-write-wins.
    """

    messages: Annotated[list, add_messages]

    task_id: str
    phase: Phase

    # 采集 — structured product/demand info collected from the user.
    product_info: dict[str, Any]
    demand_complete: bool

    # 策划 — the Brief (authoritative schema: PRD §9.4) and its validation result.
    brief: dict[str, Any]
    brief_valid: bool
    brief_approved: bool
    brief_issues: list[dict[str, Any]]  # validator findings (PRD §9.5): fixed/warn

    # 生成 — per-shot generated asset URLs (Borgrise task results).
    generated_assets: list[dict[str, Any]]

    # 剪辑 — assembled timeline / final video.
    timeline: dict[str, Any]
    final_video_url: str

    # 质检 — QC verdict and retry bookkeeping (bounds the GENERATE retry loop).
    qc_passed: bool
    qc_report: dict[str, Any]
    qc_attempts: int

    # Surfaced errors for the gateway/UI.
    error: str
