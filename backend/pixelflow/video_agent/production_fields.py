"""创作确认前的生产字段探测：时长 / 画幅 / 结尾行动引导。

以用户原文（latest_input）为准；字段有无与缺项一律由 LLM 判定，禁止本地正则猜字段。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CLARIFY_MARKER = "还需要你确认"
ALLOWED_MISSING = ("视频画幅", "结尾行动引导")
PRODUCTION_FIELDS_TIMEOUT_SEC = 20.0
PRODUCTION_FIELDS_INPUT_MAX_CHARS = 6_000
PRODUCTION_FIELDS_FOLLOWUP_MAX_CHARS = 800

_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def normalize_user_text(text: str) -> str:
    """仅做全角标点归一，不做字段语义判定。"""

    raw = (text or "").strip()
    if not raw:
        return ""
    return (
        raw.replace("\u3000", " ")
        .replace("：", ":")
        .replace("∶", ":")
        .replace("﹕", ":")
    )


def user_latest_input(payload: Mapping[str, object] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    latest = payload.get("latest_input")
    return latest.strip() if isinstance(latest, str) else ""


def workspace_missing_requirements(
    payload: Mapping[str, object] | None,
) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    script = payload.get("script")
    if not isinstance(script, dict):
        return []
    raw = script.get("missing_requirements")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def workspace_has_script_content(payload: Mapping[str, object] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    script = payload.get("script")
    if isinstance(script, dict):
        content = script.get("content")
        if isinstance(content, str) and content.strip():
            return True
    latest = user_latest_input(payload)
    return len(latest) >= 80


def looks_like_production_field_reply(
    content: str,
    *,
    workspace_payload: Mapping[str, object] | None = None,
) -> bool:
    """短补丁是否应按「补生产字段」合并上下文。

    不解析 9:16 / CTA 内容（禁止正则猜字段）；只看「短回复 + 工作区已有脚本/缺项」。
    """

    text = normalize_user_text(content)
    if not text or len(text) > 240:
        return False
    if workspace_payload is None:
        # 无工作区时：极短补丁留给历史合并层处理，不在此用正则认字段。
        return len(text) <= 48
    if workspace_missing_requirements(workspace_payload):
        return True
    if workspace_has_script_content(workspace_payload):
        return True
    return False


@dataclass(frozen=True)
class ProductionFieldsAnalysis:
    """LLM 对生产字段的结构化结论。"""

    duration_sec: int | None
    missing: tuple[str, ...]
    has_aspect_ratio: bool
    has_ending_cta: bool


def _parse_analysis_payload(raw: str) -> ProductionFieldsAnalysis | None:
    text = (raw or "").strip()
    if not text:
        return None
    candidate = text
    match = _JSON_OBJECT_RE.search(text)
    if match is not None:
        candidate = match.group(0)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None

    duration: int | None = None
    raw_duration = payload.get("duration_sec")
    if raw_duration is not None:
        try:
            value = int(raw_duration)
        except (TypeError, ValueError):
            value = None
        if value is not None and 1 <= value <= 3600:
            duration = value

    has_aspect = bool(payload.get("has_aspect_ratio"))
    has_cta = bool(payload.get("has_ending_cta"))
    missing_raw = payload.get("missing")
    missing: list[str] = []
    if isinstance(missing_raw, list):
        for item in missing_raw:
            label = str(item).strip()
            if label in ALLOWED_MISSING and label not in missing:
                missing.append(label)
    if has_aspect:
        missing = [item for item in missing if item != "视频画幅"]
    elif "视频画幅" not in missing:
        missing.append("视频画幅")
    if has_cta:
        missing = [item for item in missing if item != "结尾行动引导"]
    elif "结尾行动引导" not in missing:
        missing.append("结尾行动引导")
    missing = [item for item in missing if item in ALLOWED_MISSING]
    return ProductionFieldsAnalysis(
        duration_sec=duration,
        missing=tuple(missing),
        has_aspect_ratio=has_aspect,
        has_ending_cta=has_cta,
    )


def _create_fields_model(factory: Callable[..., Any]) -> Any:
    try:
        return factory(thinking_enabled=False, streaming=False)
    except TypeError:
        return factory(thinking_enabled=False)


def build_production_fields_excerpt(text: str) -> str:
    """构造给 LLM 的输入：有【本轮指令】时优先指令，避免整篇脚本拖垮超时。"""

    raw = normalize_user_text(text)
    if not raw:
        return ""
    marker = "【本轮指令】"
    if marker in raw:
        head, _, instruction = raw.partition(marker)
        instruction = instruction.strip()
        # 只带脚本头尾一点上下文，主判本轮补丁。
        head = head.strip()
        if len(head) > 400:
            head = head[:200].rstrip() + "\n…\n" + head[-200:].lstrip()
        bundled = f"{head}\n\n{marker}{instruction}".strip()
        if len(bundled) <= PRODUCTION_FIELDS_FOLLOWUP_MAX_CHARS:
            return bundled
        return f"{marker}{instruction}"[:PRODUCTION_FIELDS_FOLLOWUP_MAX_CHARS]
    if len(raw) <= PRODUCTION_FIELDS_INPUT_MAX_CHARS:
        return raw
    return raw[:PRODUCTION_FIELDS_INPUT_MAX_CHARS].rstrip() + "\n…（截断）"


def format_production_fields_update_notice(
    analysis: ProductionFieldsAnalysis,
    *,
    script_version: int | None = None,
) -> str:
    """补字段跟进后的对话框摘要（不再触发 import 版本号递增）。"""

    bits: list[str] = []
    if analysis.duration_sec is not None:
        bits.append(f"时长 {analysis.duration_sec} 秒")
    if analysis.has_aspect_ratio:
        bits.append("画幅已确认")
    if analysis.has_ending_cta:
        bits.append("结尾引导已确认")
    prefix = "已更新生产字段"
    if script_version is not None:
        prefix = f"已更新脚本版本 {script_version} 的生产字段"
    if bits:
        prefix = f"{prefix}（{'；'.join(bits)}）"
    if analysis.missing:
        items = "、".join(analysis.missing)
        return (
            f"{prefix}；仍缺少：{items}\n"
            f"请直接在对话框回复上述缺失项（{items}），我再继续。"
        )
    return f"{prefix}。生产字段已齐，可在右侧脚本预览底部确认后继续，或告诉我下一步。"


async def analyze_production_fields_with_llm(
    *,
    text: str,
    model: Any | None = None,
    model_factory: Callable[..., Any] | None = None,
    timeout_sec: float = PRODUCTION_FIELDS_TIMEOUT_SEC,
) -> ProductionFieldsAnalysis:
    """用 LLM 抽取总时长并判断画幅/CTA 是否已具备；禁止正则猜字段。"""

    from deerflow.models import create_chat_model

    raw = normalize_user_text(text)
    if not raw:
        return ProductionFieldsAnalysis(
            duration_sec=None,
            missing=ALLOWED_MISSING,
            has_aspect_ratio=False,
            has_ending_cta=False,
        )

    excerpt = build_production_fields_excerpt(raw)
    chat = model
    if chat is None:
        factory = model_factory or create_chat_model
        chat = _create_fields_model(factory)

    messages = [
        (
            "system",
            "你是电商短视频生产字段助手。只根据用户原文/脚本判断，输出 JSON，不要其他文字。\n"
            "规则：\n"
            "1) duration_sec：整片总时长（秒）。分镜「0—10秒」「170—180秒」是局部时间码；"
            "有连续时间码时取最后一个结束秒。用户写「180s/180秒/时长180」也算。"
            "无法判断则 null。\n"
            "2) has_aspect_ratio：是否已有画幅（9:16/9：16/16:9/竖屏/横屏/1:1 等）；"
            "【本轮指令】里的画幅优先。\n"
            "3) has_ending_cta：已有结尾行动引导，或用户说「结尾不变/CTA保持/沿用」；"
            "若用户明确说「结尾不需要引导/不要CTA/无需行动引导/不用引导」，"
            "也视为已确认（has_ending_cta=true，不要再追问）。\n"
            "4) missing：只能从 [\"视频画幅\",\"结尾行动引导\"] 中选仍缺的项；"
            "不要把「视频时长」放入 missing。\n"
            "JSON 形状："
            "{\"duration_sec\": <int|null>, \"has_aspect_ratio\": <bool>, "
            "\"has_ending_cta\": <bool>, \"missing\": [<string>]}",
        ),
        ("human", f"【用户输入】\n{excerpt}\n"),
    ]

    async def _invoke() -> str:
        # 字段判定要快：优先一次性 invoke，避免 astream 拖慢。
        invoke = getattr(chat, "ainvoke", None)
        if invoke is not None:
            message = await invoke(list(messages))
        else:
            message = await asyncio.to_thread(chat.invoke, list(messages))
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, Mapping):
                    piece = part.get("text")
                    if isinstance(piece, str) and piece:
                        parts.append(piece)
            return "".join(parts)
        return str(content or "")

    try:
        answer = await asyncio.wait_for(_invoke(), timeout=timeout_sec)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "LLM 生产字段分析失败 error_type=%s",
            type(exc).__name__,
        )
        return ProductionFieldsAnalysis(
            duration_sec=None,
            missing=ALLOWED_MISSING,
            has_aspect_ratio=False,
            has_ending_cta=False,
        )

    parsed = _parse_analysis_payload(answer)
    if parsed is None:
        logger.warning("LLM 生产字段分析返回无法解析")
        return ProductionFieldsAnalysis(
            duration_sec=None,
            missing=ALLOWED_MISSING,
            has_aspect_ratio=False,
            has_ending_cta=False,
        )
    return parsed


async def missing_creative_production_fields_async(
    *texts: str,
    model: Any | None = None,
    model_factory: Callable[..., Any] | None = None,
) -> list[str]:
    """异步缺项探测（LLM）。"""

    combined = "\n".join(
        normalize_user_text(part) for part in texts if isinstance(part, str) and part.strip()
    )
    analysis = await analyze_production_fields_with_llm(
        text=combined,
        model=model,
        model_factory=model_factory,
    )
    return list(analysis.missing)


def format_creative_confirm_clarification(
    *,
    user_text: str = "",
    missing: Sequence[str] | None = None,
    duration_sec: int | None = None,
) -> str:
    """拼到确认卡 cost_summary：已知时长 + 缺失项追问。

    missing / duration_sec 须由调用方传入 LLM 分析结果；此处不再正则猜字段。
    """

    del user_text
    gaps = [item for item in (missing or ()) if item in ALLOWED_MISSING]
    if not gaps:
        return ""

    lines: list[str] = []
    if duration_sec is not None and 1 <= int(duration_sec) <= 3600:
        lines.append(f"已识别时长：{int(duration_sec)}秒。")
    lines.append(f"{CLARIFY_MARKER}：")
    examples = {
        "视频画幅": "例如 9:16 竖屏、16:9 横屏或 1:1",
        "结尾行动引导": "例如 进直播间、下方小黄车下单、私信咨询",
    }
    for index, label in enumerate(gaps, start=1):
        hint = examples.get(label, "")
        lines.append(f"{index}. {label}" + (f"（{hint}）" if hint else ""))
    lines.append("请先在对话框直接回复上述项，再点「同意创意继续」。")
    return "\n".join(lines)


def creative_confirm_cost_summary(
    *,
    user_text: str = "",
    preview: str = "",
    missing: Sequence[str] | None = None,
    duration_sec: int | None = None,
) -> str:
    """选题创意确认卡完整摘要（预览 + 引导 + 缺字段追问）。"""

    guidance = (
        "请确认选题与创意方向是否合适。"
        "同意后继续写三幕结构与角色设定；"
        "不满意可直接用自然语言说明想怎么改，我会重新从选题开始。"
    )
    parts: list[str] = []
    preview_text = preview.strip()
    if preview_text:
        parts.append(f"创意方向：{preview_text}")
    parts.append(guidance)
    clarification = format_creative_confirm_clarification(
        user_text=user_text,
        missing=missing,
        duration_sec=duration_sec,
    )
    if clarification:
        parts.append(clarification)
    return "\n\n".join(parts)


__all__ = [
    "ALLOWED_MISSING",
    "CLARIFY_MARKER",
    "ProductionFieldsAnalysis",
    "analyze_production_fields_with_llm",
    "build_production_fields_excerpt",
    "creative_confirm_cost_summary",
    "format_creative_confirm_clarification",
    "format_production_fields_update_notice",
    "looks_like_production_field_reply",
    "missing_creative_production_fields_async",
    "normalize_user_text",
    "user_latest_input",
    "workspace_has_script_content",
    "workspace_missing_requirements",
]
