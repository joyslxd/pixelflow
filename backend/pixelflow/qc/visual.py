"""Visual semantic QC via Ark multimodal models."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

from pixelflow.qc.models import QCItem
from pixelflow.skills.ark_seed.client import DEFAULT_VISION_QC_MODEL, ArkSeedClient

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [part.get("text") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
            return "\n".join(parts)
    for key in ("content", "text", "message", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _parse_verdict(text: str) -> tuple[str, str]:
    match = _JSON_RE.search(text or "")
    raw = match.group(0) if match else text
    try:
        data = json.loads(raw)
    except Exception:
        lowered = (text or "").lower()
        if any(token in lowered for token in ("fail", "不一致", "变形", "跑偏", "错误")):
            return "fail", text.strip()[:240] or "视觉模型判断产品存在明显不一致"
        if any(token in lowered for token in ("pass", "一致", "正常", "通过")):
            return "pass", text.strip()[:240] or "视觉模型判断产品一致性通过"
        return "warn", text.strip()[:240] or "视觉模型未返回可解析结论，需人工复核"

    verdict = str(data.get("status") or data.get("verdict") or data.get("result") or "").lower()
    if verdict in {"pass", "passed", "ok", "true", "一致", "通过"}:
        status = "pass"
    elif verdict in {"fail", "failed", "bad", "false", "不一致", "未通过"}:
        status = "fail"
    else:
        status = "warn"
    reason = str(data.get("message") or data.get("reason") or data.get("explanation") or "").strip()
    return status, reason[:240] or f"视觉模型返回结论: {verdict or 'warn'}"


def _frame_data_url(video_path: str, second: float) -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not os.path.exists(video_path):
        return None
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(second, 0.0):.2f}",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    encoded = base64.b64encode(proc.stdout).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _sample_frame_urls(video_path: str, duration: float | None = None) -> list[str]:
    if not video_path or video_path.startswith(("http://", "https://")):
        return []
    duration = duration or 0.0
    seconds = [1.0] if duration <= 2 else [max(0.5, duration * 0.15), duration * 0.5, max(0.5, duration * 0.85)]
    frames: list[str] = []
    for second in seconds:
        frame = _frame_data_url(video_path, second)
        if frame:
            frames.append(frame)
    return frames[:3]


async def product_consistency_check(
    *,
    product_image_url: str,
    final_video_url: str,
    brief: dict,
    video_duration: float | None = None,
    client: ArkSeedClient | None = None,
) -> QCItem:
    """Compare the product reference image with sampled final-video frames."""
    if not product_image_url:
        return QCItem(item="产品一致性/变形", status="warn", message="缺少商品参考图，无法自动判断产品一致性")
    frame_urls = _sample_frame_urls(final_video_url, video_duration)
    if not frame_urls:
        return QCItem(item="产品一致性/变形", status="warn", message="未能抽取成片画面帧，需人工复核产品是否变形")

    model = os.environ.get("ARK_VISION_QC_MODEL") or DEFAULT_VISION_QC_MODEL
    prompt = (
        "你是电商短视频质检员。请比较第一张商品参考图与后续成片截图，判断商品主体的颜色、结构、形状、关键特征是否保持一致，"
        "是否出现明显变形、跑偏、错物、颜色严重变化。只返回 JSON: "
        '{"status":"pass|warn|fail","message":"中文原因，80字内"}。'
        f"\nBrief摘要: {json.dumps({'product': brief.get('product_name') or brief.get('title'), 'global_visual': brief.get('global_visual')}, ensure_ascii=False)[:600]}"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": product_image_url}}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in frame_urls)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        ark = client or ArkSeedClient()
        raw = await asyncio.to_thread(ark.chat_completions, payload)
        status, reason = _parse_verdict(_extract_text(raw))
        return QCItem(item="产品一致性/变形", status=status, message=f"视觉模型({model})判定: {reason}")
    except Exception as exc:  # noqa: BLE001 - QC must remain non-blocking
        logger.warning("visual product consistency QC failed: %s", exc, exc_info=True)
        return QCItem(item="产品一致性/变形", status="warn", message=f"视觉模型质检失败，需人工复核: {exc}")
