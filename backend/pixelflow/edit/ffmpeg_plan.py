"""把 DraftPlan 翻译成确定性的 ffmpeg 命令参数，纯逻辑实现。

每个 segment 都会按规划时长裁剪，缩放/补边到目标画布，统一 fps，并在提供字体
文件时通过 drawtext 烧录花字。最后所有片段会 concat 成一个 H.264 mp4。FFmpeg
render skill 只负责执行这里生成的 argv，不再自行做编排。

v1 有意不处理转场：FFmpeg xfade 会改变总时长，容易和 Brief/QC 的时长合同冲突。
每段源音频会按相同时长裁剪并随视频拼接；只有配置字体文件时才烧录花字。

这是纯逻辑，不做 I/O，输出完全由入参决定，方便离线单测。
"""

from __future__ import annotations

from .models import DraftPlan

# 单片段源视频和目标时长差异在该阈值内时，不值得为了几帧差异重新编码。
_PASSTHROUGH_DURATION_EPSILON = 0.5


def passthrough_eligible(plan: DraftPlan, probe: dict, *, has_caption: bool) -> bool:
    """判断唯一源片段是否可以直接复用，跳过 ffmpeg 重编码。

    只有在“恰好一个 segment、没有花字要烧录、源视频画布/fps/时长已经匹配目标”
    时才允许直通。否则 ffmpeg 至少需要裁剪、缩放、补边、烧录文字或拼接。``probe``
    来自 ffprobe，包含源视频的 ``width``、``height``、``fps``、``duration``。
    """
    if len(plan.segments) != 1 or has_caption:
        return False
    seg = plan.segments[0]
    try:
        return (
            int(probe["width"]) == plan.width
            and int(probe["height"]) == plan.height
            and abs(float(probe["fps"]) - plan.fps) < 0.01
            and abs(float(probe["duration"]) - seg.duration) <= _PASSTHROUGH_DURATION_EPSILON
        )
    except (KeyError, TypeError, ValueError):
        return False


def _escape_drawtext(text: str) -> str:
    """转义 ffmpeg drawtext 的特殊字符。

    必须先转义反斜杠，再处理冒号、单引号和百分号。
    """
    out = text.replace("\\", "\\\\")
    for ch in (":", "'", "%"):
        out = out.replace(ch, "\\" + ch)
    return out


def build_ffmpeg_args(plan: DraftPlan, input_paths: list[str], output_path: str, *, font_file: str | None = None) -> list[str]:
    """根据本地输入文件和 ``DraftPlan`` 构建完整 ffmpeg argv。"""
    if not plan.segments:
        raise ValueError("empty plan: no segments to render")
    if len(input_paths) != len(plan.segments):
        raise ValueError(f"input_paths/segments length mismatch: {len(input_paths)} != {len(plan.segments)}")

    args: list[str] = ["ffmpeg", "-y"]
    for path in input_paths:
        args += ["-i", path]

    filters: list[str] = []
    for i, seg in enumerate(plan.segments):
        chain = (
            f"[{i}:v]trim=duration={seg.duration:g},setpts=PTS-STARTPTS,"
            f"scale={plan.width}:{plan.height}:force_original_aspect_ratio=decrease,"
            f"pad={plan.width}:{plan.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={plan.fps}"
        )
        if seg.caption and font_file:
            fontsize = max(plan.height // 18, 16)
            chain += (
                f",drawtext=fontfile={font_file}:text='{_escape_drawtext(seg.caption)}'"
                f":x=(w-text_w)/2:y=h*0.82:fontsize={fontsize}:fontcolor=white:borderw=3:bordercolor=black"
            )
        filters.append(f"{chain}[v{i}]")
        # 保留每个源片段的音频，并裁剪到和视频相同的时长。
        filters.append(f"[{i}:a]atrim=duration={seg.duration:g},asetpts=PTS-STARTPTS[a{i}]")

    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(plan.segments)))
    filters.append(f"{concat_inputs}concat=n={len(plan.segments)}:v=1:a=1[vout][aout]")

    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(plan.fps),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    return args
