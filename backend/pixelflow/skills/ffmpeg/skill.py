"""FFmpeg render skill — the EDIT-phase headless I/O boundary.

Consumes the Timeline IR and emits a finished mp4 (``EditResult.kind ==
"video"``), unlike the 剪映 skill which emits an editable draft folder. The
blocking work — downloading clips and running the ffmpeg subprocess — is
offloaded to a worker thread so the event loop stays free.

Runtime dep: the ``ffmpeg`` binary on PATH (only needed when this skill
actually runs; absent in offline tests). A missing binary or a render error is
normalized to ``EditResult(ok=False, ...)`` so a failure never crashes the
EDIT phase.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

import httpx

from pixelflow.edit import build_draft_plan, build_ffmpeg_args
from pixelflow.skills.base import EditResult

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SEC = 60.0
_RENDER_TIMEOUT_SEC = 600.0


def _render_root(output_root: str | None) -> str:
    """Resolve where rendered videos are written, creating the root if needed."""
    root = output_root or os.environ.get("PIXELFLOW_RENDER_ROOT") or os.path.join(tempfile.gettempdir(), "pixelflow_renders")
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


def _render(timeline: dict, draft_name: str, output_root: str | None) -> EditResult:
    """Download clips and run ffmpeg (blocking; run off-loop)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return EditResult(ok=False, error="ffmpeg 未安装，无法渲染成片")

    plan = build_draft_plan(timeline)
    if not plan.segments:
        return EditResult(ok=False, error="empty plan: no clips to assemble")

    work_dir = os.path.join(_render_root(output_root), draft_name)
    assets_dir = os.path.join(work_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    inputs = [_download(seg.source_url, assets_dir, i) for i, seg in enumerate(plan.segments)]
    output_path = os.path.join(work_dir, f"{draft_name}.mp4")
    args = build_ffmpeg_args(plan, inputs, output_path, font_file=os.environ.get("PIXELFLOW_CAPTION_FONT") or None)
    args[0] = ffmpeg

    proc = subprocess.run(args, capture_output=True, text=True, timeout=_RENDER_TIMEOUT_SEC)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-500:]
        return EditResult(ok=False, error=f"ffmpeg 渲染失败 (exit {proc.returncode}): {tail}")
    logger.info("[pixelflow] ffmpeg render saved path=%s clips=%d", output_path, len(plan.segments))
    return EditResult(ok=True, output_path=output_path, kind="video")


class FFmpegEditSkill:
    """FFmpeg implementation of ``VideoEditSkill`` — renders a finished mp4."""

    async def render(self, timeline: dict, *, draft_name: str, output_root: str | None = None) -> EditResult:
        try:
            return await asyncio.to_thread(_render, timeline, draft_name, output_root)
        except Exception as exc:  # noqa: BLE001 - boundary: normalize all render errors
            logger.exception("ffmpeg render failed draft_name=%s", draft_name)
            return EditResult(ok=False, error=str(exc))
