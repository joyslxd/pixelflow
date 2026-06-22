"""Borgrise 视频生成 skill（Shape B：进程内执行）。

这是对 ``run_generation`` 脚本的异步薄封装。``run_generation`` 是 Borgrise API
合同的单一来源，包含鉴权、自定义请求头、端点、轮询等细节。这里不复制供应商
协议，只负责把同步阻塞函数丢到线程里执行，并把返回值映射成 PixelFlow 统一
Result DTO。

``run_generation`` 只读取供应商 base URL、projectId、轮询超时等非用户身份配置。
真正的用户身份来自入口请求的 content-app ``Authorization``，由网关写入
ContextVar 后在这里透传给生成接口。也就是说，这个 skill 不允许再使用配置文件里的
固定 token 或账号密码去替用户扣费。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from pixelflow.skills.base import GenerationResult, StoryboardResult
from pixelflow.skills.borgrise import run_generation

logger = logging.getLogger(__name__)


def _to_result(raw: dict[str, Any]) -> GenerationResult:
    """把 ``run_generation`` 的原始 dict 映射成统一 ``GenerationResult``。"""
    if not raw or raw.get("error"):
        return GenerationResult(
            ok=False,
            task_id=raw.get("task_id") if raw else None,
            error=(raw.get("message") if raw else "empty response") or "generation failed",
            raw=raw or {},
        )
    url = raw.get("video_url") or raw.get("image_url") or raw.get("url")
    if not url:
        return GenerationResult(
            ok=False,
            task_id=raw.get("task_id"),
            error=raw.get("message") or "generation returned no video url",
            raw=raw,
        )
    return GenerationResult(ok=True, url=url, task_id=raw.get("task_id"), raw=raw)


def _extract_shots(raw: Any) -> list[dict[str, Any]]:
    """从供应商响应中宽松提取 storyboard shots。

    博观拆解接口常把列表嵌在 ``data.result.video_url.segments``；这里也兼容
    ``shots``、``storyboard``、``scenes`` 等更简单形态，降低供应商响应变化的影响。
    """
    if not isinstance(raw, dict):
        return []
    for key in ("shots", "storyboard", "storyboards", "scenes", "segments"):
        value = raw.get(key)
        if isinstance(value, list) and value:
            return [s if isinstance(s, dict) else {"description": str(s)} for s in value]
    for key in ("data", "result", "video_url"):
        nested = raw.get(key)
        if isinstance(nested, list) and nested:
            return [s if isinstance(s, dict) else {"description": str(s)} for s in nested]
        if isinstance(nested, dict):
            shots = _extract_shots(nested)
            if shots:
                return shots
    return []


def _parse_time_range(time_range: Any) -> float:
    """解析 ``"4-22s"`` 这类时间范围对应的秒数，无法解析时返回 0。"""
    nums = re.findall(r"\d+(?:\.\d+)?", str(time_range or ""))
    if len(nums) >= 2:
        return max(0.0, round(float(nums[1]) - float(nums[0]), 2))
    return 0.0


def _normalize_segment(seg: Any) -> dict[str, Any]:
    """把博观 segment 映射成 PixelFlow 更稳定的 shot 形态。

    博观字段可能是 camelCase 或中文语义字段。这里把供应商差异挡在 skill 边界，
    让下游纯逻辑 ``summarize_storyboards`` 只读取稳定的下划线字段。
    """
    if not isinstance(seg, dict):
        return {"visual_description": str(seg)}
    return {
        "visual_description": seg.get("visualContent") or seg.get("visual_description") or "",
        "narration_text": seg.get("voiceContent") or "",
        "onscreen_text": seg.get("subtitle") or "",
        "shot_type": seg.get("shotType") or "",
        "camera_movement": seg.get("cameraMovement") or "",
        "duration": _parse_time_range(seg.get("timeRange")),
        "time_range": seg.get("timeRange") or "",
    }


def _decompose_blocking(video_url: str) -> dict[str, Any]:
    """调用参考视频拆解端点；如果返回异步任务，则继续轮询。

    拆解使用视觉模型 ``gemini-3-flash-preview``，并需要和生成接口相同的自定义
    请求头。这个函数是阻塞调用，外层必须用 ``asyncio.to_thread`` offload。
    """
    headers = run_generation.get_headers(model="gemini-3-flash-preview", bill_type=1, duration=1, size="all")
    result = run_generation.make_request("/creative/decompose_video_to_storyboard?projectId=1", {"video_url": video_url}, custom_headers=headers)
    task_id = run_generation.extract_task_id(result)
    if task_id and not _extract_shots(result):
        # 参考视频拆解属于“视频分析”任务，通常比图片生成久、但不应套用视频生成 1 小时上限。
        result = run_generation.poll_task(task_id, default_timeout=run_generation.VIDEO_ANALYSIS_POLL_TIMEOUT)
    return result


async def _run(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> GenerationResult:
    """在线程中运行阻塞的 ``run_generation`` 调用，并归一化失败。

    ``run_generation`` 可能因为缺 token、网络错误、供应商异常而抛出。这里统一转成
    ``GenerationResult(ok=False)``，避免某个片段失败时直接冲垮整个 GENERATE 阶段。
    """
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise %s failed", getattr(fn, "__name__", "call"))
        return GenerationResult(ok=False, error=str(exc))
    return _to_result(raw)


class BorgriseSkill:
    """进程内 Borgrise 实现，同时实现视频生成和参考视频拆解能力。"""

    async def image_to_video(
        self,
        image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.image_to_video, **kwargs)

    async def extend_video(
        self,
        video_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "video_url": video_url,
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.extend_video, **kwargs)

    async def decompose_video_to_storyboard(self, video_url: str) -> StoryboardResult:
        """把参考视频拆解成供应商 storyboard（博观拆解）。"""
        try:
            raw = await asyncio.to_thread(_decompose_blocking, video_url)
        except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
            logger.exception("borgrise decompose failed url=%s", video_url)
            return StoryboardResult(ok=False, error=str(exc))
        if not raw or raw.get("error"):
            return StoryboardResult(ok=False, error=(raw.get("message") if raw else "empty response") or "decompose failed", raw=raw or {})
        shots = [_normalize_segment(s) for s in _extract_shots(raw)]
        if not shots:
            return StoryboardResult(ok=False, error="no shots in decompose response", raw=raw)
        return StoryboardResult(ok=True, shots=shots, raw=raw)
