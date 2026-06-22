"""产物质检，纯逻辑实现。

QC 检查的是 GENERATE/EDIT 已经产出的结果，而不是 Brief 计划本身。当前包含两类
检查：

- 片段完整性（阻塞）：每个尝试生成的 segment 都应该有可用 clip。缺失会产生
  ``fail``，让图回到 GENERATE 重试，适合处理第三方偶发失败。
- 时长达标（非阻塞）：剪辑后的总时长应落在 Brief 容忍区间内。重新生成通常不能
  改变 shot 时长，所以不触发重试，只记录 ``warn``。

空 Brief 或没有生成尝试时，完整性检查会自然通过；这种问题属于上游策划/采集，
不是 QC 应该修复的生成缺陷。成片存在本地路径时，会额外用 ffprobe/ffmpeg 做
分辨率和黑屏检测；工具不可用时降级成人工复核警告。
"""

from __future__ import annotations

import re
import shutil
import subprocess

from .models import QCItem, QCResult

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _parse_tolerance(spec: str) -> float:
    """从 ``'+2s'`` 这类容忍度字符串中提取秒数。"""
    m = _NUM.search(spec or "")
    return float(m.group()) if m else 0.0


def _probe_video(path: str) -> dict[str, float | int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=0",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        return {}
    fields = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    return {
        "width": int(fields.get("width") or 0),
        "height": int(fields.get("height") or 0),
        "duration": float(fields.get("duration") or 0),
    }


def _has_black_frames(path: str) -> bool | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path, "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode not in (0, 1):
        return None
    return "black_start:" in proc.stderr


def qc_check(brief: dict, generated_assets: list[dict], timeline: dict, final_video_url: str = "") -> QCResult:
    """评估产物是否通过质检。

    覆盖率比较的是 Timeline 中已经装配的 clips 和 GENERATE 实际尝试过的
    ``generated_assets``。当前生成粒度是 segment，不再是单 shot。
    """
    total_segments = len(generated_assets)
    n_clips = len(timeline.get("clips", []))

    checks: list[QCItem] = []

    coverage_ok = n_clips == total_segments  # 两者同为 0 时视为自然通过。
    score = 1.0 if total_segments == 0 else n_clips / total_segments
    checks.append(
        QCItem(
            item="片段完整性",
            status="pass" if coverage_ok else "fail",
            message=f"{n_clips}/{total_segments} 个片段生成成功",
        )
    )

    target = brief.get("duration_sec", 0)
    if target:
        actual = timeline.get("total_duration", 0.0)
        tol = _parse_tolerance(brief.get("hard_constraints", {}).get("total_duration_tolerance", "+2s"))
        within = abs(actual - target) <= tol
        checks.append(
            QCItem(
                item="时长达标",
                status="pass" if within else "warn",
                message=f"成片 {actual}s / 目标 {target}s (±{tol}s)",
            )
        )

    if final_video_url:
        probe = _probe_video(final_video_url)
        if probe:
            width = int(probe.get("width") or 0)
            height = int(probe.get("height") or 0)
            min_edge = min(width, height)
            checks.append(
                QCItem(
                    item="画面清晰度/分辨率",
                    status="pass" if min_edge >= 720 else "warn",
                    message=f"输出分辨率 {width}x{height}；短边 {'达到' if min_edge >= 720 else '低于'} 720p 基线",
                )
            )
        else:
            checks.append(QCItem(item="画面清晰度/分辨率", status="warn", message="未能读取视频分辨率，需人工复核清晰度"))

        black = _has_black_frames(final_video_url)
        if black is None:
            checks.append(QCItem(item="黑屏/空帧检测", status="warn", message="未能运行黑屏检测，需人工复核"))
        else:
            checks.append(QCItem(item="黑屏/空帧检测", status="fail" if black else "pass", message="检测到连续黑屏片段" if black else "未检测到连续黑屏片段"))
    else:
        checks.append(QCItem(item="画面清晰度/分辨率", status="warn", message="暂无本地成片，无法自动读取分辨率"))
        checks.append(QCItem(item="黑屏/空帧检测", status="warn", message="暂无本地成片，无法自动检测黑屏"))

    checks.append(
        QCItem(
            item="产品一致性/变形",
            status="warn",
            message="当前 P0 未接入视觉语义模型，无法自动判断产品是否变形、颜色/结构是否跑偏；请人工复核，P1 接入 VLM 后自动判定",
        )
    )

    passed = not any(c.status == "fail" for c in checks)
    scored = [1.0 if c.status == "pass" else 0.6 if c.status == "warn" else 0.0 for c in checks]
    score = sum(scored) / len(scored) if scored else score
    return QCResult(passed=passed, score=round(score, 2), check_results=checks)
