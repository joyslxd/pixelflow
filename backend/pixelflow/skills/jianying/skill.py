"""JianYing (剪映) draft-render skill — the EDIT-phase I/O boundary.

Consumes a :class:`~pixelflow.edit.models.DraftPlan` and emits an editable 剪映
draft folder via the third-party ``pyJianYingDraft`` library (referenced, not
vendored). The blocking work — downloading each clip and probing it with the
native MediaInfo lib — is offloaded to a worker thread so the event loop stays
free, mirroring the Borgrise generation skill.

Runtime deps (only needed when this skill actually runs; absent in offline
tests): ``pyJianYingDraft`` + ``pymediainfo`` (+ the MediaInfo native binary).
A missing dep or any vendor error is normalized to ``EditResult(ok=False, ...)``
so a failure never crashes the EDIT phase.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from urllib.parse import urlparse

import httpx

from pixelflow.edit import build_draft_plan
from pixelflow.edit.models import DraftPlan
from pixelflow.skills.base import EditResult

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SEC = 60.0


def _draft_root(output_root: str | None) -> str:
    """Resolve where draft folders are written, creating the root if needed."""
    root = output_root or os.environ.get("PIXELFLOW_DRAFT_ROOT") or os.path.join(tempfile.gettempdir(), "pixelflow_drafts")
    os.makedirs(root, exist_ok=True)
    return root


def _download(url: str, dest_dir: str, index: int) -> str:
    """Fetch a clip to ``dest_dir`` and return the local path."""
    suffix = os.path.splitext(urlparse(url).path)[1] or ".mp4"
    dest = os.path.join(dest_dir, f"clip_{index:03d}{suffix}")
    with httpx.stream("GET", url, timeout=_DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return dest


def _build_draft(plan: DraftPlan, draft_name: str, output_root: str | None) -> EditResult:
    """Build a 剪映 draft from ``plan`` (blocking; run off-loop)."""
    try:
        import pyJianYingDraft as draft
        from pyJianYingDraft import TrackType, trange
    except ImportError as exc:
        return EditResult(ok=False, error=f"pyJianYingDraft 未安装，无法生成剪映草稿: {exc}")

    if not plan.segments:
        return EditResult(ok=False, error="empty plan: no clips to assemble")

    root = _draft_root(output_root)
    folder = draft.DraftFolder(root)
    script = folder.create_draft(draft_name, plan.width, plan.height, fps=plan.fps, allow_replace=True)
    draft_path = os.path.dirname(script.save_path)
    assets_dir = os.path.join(draft_path, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    script.add_track(TrackType.video).add_track(TrackType.text)

    prev_segment = None
    for i, seg in enumerate(plan.segments):
        local = _download(seg.source_url, assets_dir, i)
        video_segment = draft.VideoSegment(local, trange(f"{seg.start}s", f"{seg.duration}s"))
        # Transitions attach to the PREVIOUS clip; map by enum name, best-effort.
        if prev_segment is not None and seg.transition_in:
            transition_type = getattr(draft.TransitionType, seg.transition_in, None)
            if transition_type is not None:
                prev_segment.add_transition(transition_type)
        script.add_segment(video_segment)
        if seg.caption:
            script.add_segment(draft.TextSegment(seg.caption, video_segment.target_timerange))
        prev_segment = video_segment

    script.save()
    logger.info("[pixelflow] jianying draft saved path=%s clips=%d", draft_path, len(plan.segments))
    return EditResult(ok=True, output_path=draft_path)


class JianYingEditSkill:
    """pyJianYingDraft implementation of ``VideoEditSkill``."""

    async def render(self, timeline: dict, *, draft_name: str, output_root: str | None = None) -> EditResult:
        plan = build_draft_plan(timeline)
        try:
            return await asyncio.to_thread(_build_draft, plan, draft_name, output_root)
        except Exception as exc:  # noqa: BLE001 - boundary: normalize all render errors
            logger.exception("jianying render failed draft_name=%s", draft_name)
            return EditResult(ok=False, error=str(exc))
