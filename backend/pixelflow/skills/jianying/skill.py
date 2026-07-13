"""剪映草稿渲染 skill：EDIT 阶段的 I/O 边界。

它消费 ``DraftPlan``，通过第三方 ``pyJianYingDraft`` 库产出可编辑剪映草稿目录。
下载片段、调用本地 MediaInfo 库等都是阻塞 I/O，所以会和 Borgrise skill 一样
放到 worker thread 中执行，避免阻塞 async event loop。

运行依赖只在实际使用该 skill 时需要：``pyJianYingDraft``、``pymediainfo`` 以及
MediaInfo 原生二进制。缺少依赖或供应商库异常都会归一化成
``EditResult(ok=False, ...)``，避免 EDIT 阶段直接崩溃。
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
    """解析剪映草稿输出目录，并在不存在时创建。"""
    root = output_root or os.environ.get("PIXELFLOW_DRAFT_ROOT") or os.path.join(tempfile.gettempdir(), "pixelflow_drafts")
    os.makedirs(root, exist_ok=True)
    return root


def _download(url: str, dest_dir: str, index: int) -> str:
    """下载远程视频片段到 ``dest_dir``，返回本地文件路径。"""
    suffix = os.path.splitext(urlparse(url).path)[1] or ".mp4"
    dest = os.path.join(dest_dir, f"clip_{index:03d}{suffix}")
    with httpx.stream("GET", url, timeout=_DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return dest


def _build_draft(plan: DraftPlan, draft_name: str, output_root: str | None) -> EditResult:
    """根据 ``DraftPlan`` 构建剪映草稿。

    这是阻塞函数，必须由 async 包装层放到线程里执行。
    """
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
        # 剪映转场挂在“前一个片段”上；这里按枚举名尽力映射，映射失败就忽略。
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
    """``VideoEditSkill`` 的 pyJianYingDraft 实现，产出可编辑草稿目录。"""

    async def render(self, timeline: dict, *, draft_name: str, output_root: str | None = None) -> EditResult:
        plan = build_draft_plan(timeline)
        try:
            return await asyncio.to_thread(_build_draft, plan, draft_name, output_root)
        except Exception as exc:  # noqa: BLE001 - boundary: normalize all render errors
            logger.exception("jianying render failed draft_name=%s", draft_name)
            return EditResult(ok=False, error=str(exc))
