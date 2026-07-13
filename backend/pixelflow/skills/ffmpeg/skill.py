"""FFmpeg 渲染 skill：EDIT 阶段的无界面 I/O 边界。

它消费 Timeline IR，并直接产出 mp4 成片（``EditResult.kind == "video"``）。
这和剪映 skill 不同：剪映 skill 产出的是可编辑草稿目录。下载视频片段、执行
ffmpeg 子进程都是阻塞 I/O，所以外层会放到 worker thread，避免阻塞 async
event loop。

运行依赖：PATH 中必须有 ``ffmpeg`` 二进制，只有真正使用该 skill 时才需要。
缺少二进制或渲染失败都会归一化成 ``EditResult(ok=False, ...)``，不让 EDIT 阶段
直接崩溃。
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

from pixelflow.edit import build_draft_plan, build_ffmpeg_args, passthrough_eligible
from pixelflow.skills.base import EditResult

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SEC = 60.0
_RENDER_TIMEOUT_SEC = 600.0


def _render_root(output_root: str | None) -> str:
    """解析成片输出目录，并在不存在时创建。"""
    root = output_root or os.environ.get("PIXELFLOW_RENDER_ROOT") or os.path.join(tempfile.gettempdir(), "pixelflow_renders")
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


def _probe(ffprobe: str, path: str) -> dict:
    """通过 ffprobe 探测片段的视频规格：宽、高、fps、时长。"""
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate:format=duration", "-of", "default=noprint_wrappers=1:nokey=0", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    fields: dict[str, str] = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    num, _, den = fields.get("r_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    return {"width": fields.get("width"), "height": fields.get("height"), "fps": fps, "duration": fields.get("duration")}


def _render(timeline: dict, draft_name: str, output_root: str | None) -> EditResult:
    """下载片段并执行 ffmpeg。

    这是阻塞函数，必须由 async 包装层放到线程里执行。
    """
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
    font_file = os.environ.get("PIXELFLOW_CAPTION_FONT") or None

    # 快速路径：单片段已经匹配目标画布/fps/时长且无需花字时，直接复制源文件，
    # 跳过会损失画质的重编码。
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        has_caption = bool(plan.segments[0].caption and font_file)
        if passthrough_eligible(plan, _probe(ffprobe, inputs[0]), has_caption=has_caption):
            shutil.copyfile(inputs[0], output_path)
            logger.info("[pixelflow] ffmpeg passthrough (no re-encode) path=%s", output_path)
            return EditResult(ok=True, output_path=output_path, kind="video")

    args = build_ffmpeg_args(plan, inputs, output_path, font_file=font_file)
    args[0] = ffmpeg

    proc = subprocess.run(args, capture_output=True, text=True, timeout=_RENDER_TIMEOUT_SEC)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-500:]
        return EditResult(ok=False, error=f"ffmpeg 渲染失败 (exit {proc.returncode}): {tail}")
    logger.info("[pixelflow] ffmpeg render saved path=%s clips=%d", output_path, len(plan.segments))
    return EditResult(ok=True, output_path=output_path, kind="video")


class FFmpegEditSkill:
    """``VideoEditSkill`` 的 FFmpeg 实现，产出最终 mp4。"""

    async def render(self, timeline: dict, *, draft_name: str, output_root: str | None = None) -> EditResult:
        try:
            return await asyncio.to_thread(_render, timeline, draft_name, output_root)
        except Exception as exc:  # noqa: BLE001 - boundary: normalize all render errors
            logger.exception("ffmpeg render failed draft_name=%s", draft_name)
            return EditResult(ok=False, error=str(exc))
