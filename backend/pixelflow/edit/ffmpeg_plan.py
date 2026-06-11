"""build_ffmpeg_args — translate a DraftPlan into a deterministic ffmpeg argv (pure logic).

Each segment is trimmed to its planned duration, normalized onto the pixel
canvas (scale + pad, unified fps), optionally gets its 花字 caption burned in
via drawtext, then everything is concatenated into one H.264 mp4. The FFmpeg
render skill executes the argv one-to-one without doing any编排 of its own.

v1 scope (deliberate): no transitions (xfade reshapes the total duration).
Each segment's source audio is preserved and concatenated alongside the video
(seedance clips carry an aac track); captions are only burned when a font file
is provided.

Pure and deterministic: no I/O, fully testable offline.
"""

from __future__ import annotations

from .models import DraftPlan

# A clip whose duration is within this many seconds of its segment target needs
# no trim, so re-encoding just to shave fractions isn't worth it.
_PASSTHROUGH_DURATION_EPSILON = 0.5


def passthrough_eligible(plan: DraftPlan, probe: dict, *, has_caption: bool) -> bool:
    """Whether the lone source clip can be used as-is, skipping the ffmpeg re-encode.

    Eligible only when there is exactly one segment, no caption to burn, and the
    probed source already matches the target canvas/fps and duration — i.e.
    ffmpeg would do nothing but a lossy re-encode. ``probe`` carries the source's
    ``width``/``height``/``fps``/``duration``.
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
    """Escape ffmpeg drawtext specials (backslash first, then : ' %)."""
    out = text.replace("\\", "\\\\")
    for ch in (":", "'", "%"):
        out = out.replace(ch, "\\" + ch)
    return out


def build_ffmpeg_args(plan: DraftPlan, input_paths: list[str], output_path: str, *, font_file: str | None = None) -> list[str]:
    """Build the full ffmpeg argv rendering ``plan`` over local ``input_paths``."""
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
        # Preserve each source's audio, trimmed to the same span as its video.
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
