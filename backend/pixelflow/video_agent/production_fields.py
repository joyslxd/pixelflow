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


_ALLOWED_RATIOS = frozenset({"9:16", "16:9", "1:1"})
_CONFIRMED_ENDING_CTA = frozenset({"keep", "none", "present"})


def _payload_maps(payload: Mapping[str, object] | None) -> tuple[dict, dict, dict]:
    if not isinstance(payload, Mapping):
        return {}, {}, {}
    script = payload.get("script")
    form = payload.get("form_values")
    contract = payload.get("creation_contract")
    return (
        dict(script) if isinstance(script, dict) else {},
        dict(form) if isinstance(form, dict) else {},
        dict(contract) if isinstance(contract, dict) else {},
    )


def workspace_resolved_aspect_ratio(payload: Mapping[str, object] | None) -> str | None:
    """工作区已落库的画幅（script / form / contract），不做用户原文正则猜测。"""

    script, form, contract = _payload_maps(payload)
    for source in (script, form, contract):
        raw = source.get("aspect_ratio") or source.get("video_ratio")
        if isinstance(raw, str) and raw.strip() in _ALLOWED_RATIOS:
            return raw.strip()
    return None


def workspace_has_ending_cta(payload: Mapping[str, object] | None) -> bool:
    """工作区是否已确认结尾行动引导（含明确不要 CTA）。"""

    return workspace_resolved_ending_cta(payload) is not None


def workspace_resolved_ending_cta(payload: Mapping[str, object] | None) -> str | None:
    """工作区已落库的结尾行动引导取值。"""

    script, form, contract = _payload_maps(payload)
    for source in (script, form, contract):
        raw = source.get("ending_cta")
        if isinstance(raw, str) and raw.strip() in _CONFIRMED_ENDING_CTA:
            return raw.strip()
    return None


def reconcile_missing_with_workspace(
    missing: Sequence[str] | None,
    payload: Mapping[str, object] | None,
) -> list[str]:
    """用工作区已有生产字段剔除 Intake 误报的 missing。"""

    gaps = [str(item).strip() for item in (missing or ()) if str(item).strip()]
    if not gaps:
        return []
    if workspace_resolved_aspect_ratio(payload) is not None:
        gaps = [item for item in gaps if item != "视频画幅"]
    if workspace_has_ending_cta(payload):
        gaps = [item for item in gaps if item != "结尾行动引导"]
    return gaps


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


def looks_like_scene_asset_continue(content: str) -> bool:
    """场景包就绪后，用户表示无参考图、继续生图（非成片）。"""

    text = normalize_user_text(content)
    if not text or len(text) > 80:
        return False
    compact = re.sub(r"\s+", "", text)
    if re.search(r"确认并生成视频|生成视频|生成成片", compact):
        return False
    if re.search(r"没有参考图|无参考图|不需要参考图|无需参考图|没有引用参考", compact):
        return True
    return bool(re.search(r"直接生成", compact) and re.search(r"参考图", compact))


def _looks_like_script_confirm_command(content: str) -> bool:
    """成片确认短令：必须让确认 bootstrap 先走，不能被补字段门闩截胡。"""

    lowered = normalize_user_text(content).casefold()
    if not lowered:
        return False
    # 「确认并生成分镜视频」是 generate_scenes，不是确认脚本；禁止被「确认并生成视频」子串误伤。
    if "确认并生成分镜视频" in lowered or "确认并生成分镜" in lowered:
        return False
    if "重新生成已修改的分镜视频" in lowered or "继续生成失败的分镜视频" in lowered:
        return False
    markers = (
        "确认脚本",
        "确认方案",
        "确认plan",
        "确认执行方案",
        "确认脚本方案",
        "确认脚本plan",
        "确认并生成视频",
        "确认并生成资产包",
        "同意脚本",
        "同意方案",
        "用户已确认当前脚本方案",
    )
    return any(marker.casefold() in lowered for marker in markers)


def _looks_like_generate_scenes_command(content: str) -> bool:
    """工作台「确认并生成分镜视频」短令：不得进补字段门闩。"""

    text = normalize_user_text(content)
    if not text or len(text) > 120:
        return False
    compact = re.sub(r"\s+", "", text)
    if "确认并生成分镜视频" in compact or "确认并生成分镜" in compact:
        return True
    if "重新生成已修改的分镜视频" in compact or "继续生成失败的分镜视频" in compact:
        return True
    if compact in {"生成视频", "生成视频吧", "生成分镜视频", "生成分镜视频吧", "开始生成视频"}:
        return True
    return False


def looks_like_production_field_reply(
    content: str,
    *,
    workspace_payload: Mapping[str, object] | None = None,
) -> bool:
    """短跟进是否可能是「补生产字段」——只做结构门闩，不做话术语义判定。

    允许进入补字段降级的条件：
    - 文本够短（避免把整篇脚本当补丁）；
    - 工作区已在等生产字段，或 script 上已记录 missing。

    禁止：按关键词猜测「这是不是 9:16 / CTA / 没有参考图」。
    业务意图（补字段 vs 生图 vs 成片）由 Intake `target_capability` / `intent` 裁决。
    """

    text = normalize_user_text(content)
    if not text or len(text) > 240:
        return False
    # 「没有参考图」是生图续步，不得当成画幅/CTA 补丁。
    if looks_like_scene_asset_continue(text):
        return False
    # 单镜/全量生成分镜视频：走 generate_scenes bootstrap，禁止补字段截胡。
    if _looks_like_generate_scenes_command(text):
        return False
    # 「确认脚本」等成片确认优先走 prepare bootstrap。
    if _looks_like_script_confirm_command(text):
        return False
    if workspace_payload is None:
        return len(text) <= 48
    if workspace_payload.get("awaiting_production_fields") is True:
        return True
    if workspace_missing_requirements(workspace_payload):
        return True
    return False


@dataclass(frozen=True)
class ProductionFieldsAnalysis:
    """LLM 对生产字段的结构化结论。"""

    duration_sec: int | None
    missing: tuple[str, ...]
    has_aspect_ratio: bool
    has_ending_cta: bool
    aspect_ratio: str | None = None
    ending_cta: str | None = None


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

    aspect_ratio: str | None = None
    raw_ratio = payload.get("aspect_ratio")
    if isinstance(raw_ratio, str) and raw_ratio.strip() in _ALLOWED_RATIOS:
        aspect_ratio = raw_ratio.strip()

    ending_cta: str | None = None
    raw_cta = payload.get("ending_cta")
    if isinstance(raw_cta, str) and raw_cta.strip() in _CONFIRMED_ENDING_CTA:
        ending_cta = raw_cta.strip()

    has_aspect = bool(payload.get("has_aspect_ratio")) or aspect_ratio is not None
    has_cta = bool(payload.get("has_ending_cta")) or ending_cta is not None
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
        aspect_ratio=aspect_ratio,
        ending_cta=ending_cta,
    )


def enrich_analysis_with_choice_replies(
    text: str,
    analysis: ProductionFieldsAnalysis,
) -> ProductionFieldsAnalysis:
    """当 LLM 仍缺项时，解析标准多选序号（A/B、①②③④ / 第一个…第四个）。

    这不是开放话术关键词路由，只承接产品固定追问菜单的点选回执；
    例如「1. 9:16 2. 第三个」中的「第三个」= 留白收束 = ending_cta=none。
    """

    compact = re.sub(r"\s+", "", normalize_user_text(text))
    if not compact:
        return analysis

    aspect_ratio = analysis.aspect_ratio
    has_aspect = analysis.has_aspect_ratio
    if not has_aspect or aspect_ratio is None:
        if re.search(r"9\s*:\s*16|竖屏", compact, flags=re.IGNORECASE):
            aspect_ratio = "9:16"
            has_aspect = True
        elif re.search(r"16\s*:\s*9|横屏", compact, flags=re.IGNORECASE):
            aspect_ratio = "16:9"
            has_aspect = True
        elif re.fullmatch(r"[Bb]|选项[Bb]|选[Bb]", compact):
            aspect_ratio = "9:16"
            has_aspect = True
        elif re.fullmatch(r"[Aa]|选项[Aa]|选[Aa]", compact):
            aspect_ratio = "16:9"
            has_aspect = True

    ending_cta = analysis.ending_cta
    has_cta = analysis.has_ending_cta
    # 避免把「改第三个分镜」误当成 CTA 点选。
    mentions_shot_edit = bool(
        re.search(r"(?:第三个|第3个|③).{0,8}(?:分镜|镜头|场景)", compact)
    )
    if (not has_cta or ending_cta is None) and not mentions_shot_edit:
        if re.search(
            r"第三个|第3个|选项三|选项3|③|３|"
            r"留白收束|不设行动|金句字幕|金句收尾|不用引导|不要cta|无需行动引导",
            compact,
            flags=re.IGNORECASE,
        ):
            ending_cta = "none"
            has_cta = True
        elif re.search(
            r"第一个|第1个|选项一|选项1|①|１|"
            r"电商转化|购买页|促转化|"
            r"第二个|第2个|选项二|选项2|②|２|"
            r"品牌认知|产品名收尾",
            compact,
            flags=re.IGNORECASE,
        ):
            ending_cta = "present"
            has_cta = True
        elif re.search(r"第四个|第4个|选项四|选项4|④|４", compact):
            # 自定义必须附文案；纯点「④」仍视为未确认。
            parts = re.split(r"第四个|第4个|选项四|选项4|④|４", compact, maxsplit=1)
            tail = parts[-1] if parts else ""
            if len(re.sub(r"[\d.:：、.\-]", "", tail)) >= 4:
                ending_cta = "present"
                has_cta = True

    missing: list[str] = []
    if not has_aspect:
        missing.append("视频画幅")
    if not has_cta:
        missing.append("结尾行动引导")

    if (
        aspect_ratio == analysis.aspect_ratio
        and ending_cta == analysis.ending_cta
        and has_aspect == analysis.has_aspect_ratio
        and has_cta == analysis.has_ending_cta
        and tuple(missing) == analysis.missing
    ):
        return analysis
    return ProductionFieldsAnalysis(
        duration_sec=analysis.duration_sec,
        missing=tuple(missing),
        has_aspect_ratio=has_aspect,
        has_ending_cta=has_cta,
        aspect_ratio=aspect_ratio,
        ending_cta=ending_cta,
    )


def apply_production_fields_to_script(
    script: Mapping[str, object] | None,
    analysis: ProductionFieldsAnalysis,
    *,
    workspace_payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """把分析结果与工作区已有画幅/CTA 写入 script，并重算 missing。"""

    next_script = dict(script) if isinstance(script, Mapping) else {}
    base = dict(workspace_payload) if isinstance(workspace_payload, Mapping) else {}
    prior_script = dict(base["script"]) if isinstance(base.get("script"), dict) else {}
    lookup_payload = {
        **base,
        "script": {**prior_script, **next_script},
    }
    ratio = analysis.aspect_ratio or workspace_resolved_aspect_ratio(lookup_payload)
    cta = analysis.ending_cta or workspace_resolved_ending_cta(lookup_payload)
    if ratio is not None:
        next_script["aspect_ratio"] = ratio
        next_script["video_ratio"] = ratio
    if cta is not None:
        next_script["ending_cta"] = cta
    if analysis.duration_sec is not None:
        next_script["duration_sec"] = analysis.duration_sec
    gaps = list(analysis.missing)
    if ratio is not None or analysis.has_aspect_ratio:
        gaps = [item for item in gaps if item != "视频画幅"]
    if cta is not None or analysis.has_ending_cta:
        gaps = [item for item in gaps if item != "结尾行动引导"]
    next_script["missing_requirements"] = gaps
    return next_script


def production_fields_form_patch(analysis: ProductionFieldsAnalysis) -> dict[str, object]:
    """写入 form_values 的画幅/CTA 补丁（仅已解析出的值）。"""

    patch: dict[str, object] = {}
    if analysis.aspect_ratio:
        patch["video_ratio"] = analysis.aspect_ratio
    if analysis.ending_cta:
        patch["ending_cta"] = analysis.ending_cta
    return patch


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
    return (
        f"{prefix}。生产字段已齐，请点击对话中的「在右侧查看脚本」预览并在底部确认后继续，"
        f"或告诉我下一步。"
    )


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
            "3) aspect_ratio：若已有画幅，输出精确值 9:16 / 16:9 / 1:1；竖屏=9:16，横屏=16:9；"
            "无法精确映射则 null。\n"
            "4) has_ending_cta：已有结尾行动引导，或用户说「结尾不变/CTA保持/沿用」；"
            "若用户明确说「结尾不需要引导/不要CTA/无需行动引导/不用引导/不需要」，"
            "也视为已确认（has_ending_cta=true，ending_cta=none）。\n"
            "若上一轮用序号追问结尾 CTA，用户点选时必须落库：\n"
            "①/第一个/电商转化/购买页 → ending_cta=present；\n"
            "②/第二个/品牌认知/产品名收尾 → ending_cta=present；\n"
            "③/第三个/留白收束/不设行动/金句收尾 → ending_cta=none；\n"
            "④/第四个/自定义：附了具体文案 → present，只点序号无文案 → 仍缺。\n"
            "「1. 9:16 2. 第三个」这类分条回复要同时解析画幅与 CTA。\n"
            "5) ending_cta：keep（沿用）/ none（不需要）/ present（有具体引导）；未知则 null。\n"
            "6) missing：只能从 [\"视频画幅\",\"结尾行动引导\"] 中选仍缺的项；"
            "不要把「视频时长」放入 missing。\n"
            "JSON 形状："
            "{\"duration_sec\": <int|null>, \"has_aspect_ratio\": <bool>, "
            "\"aspect_ratio\": <\"9:16\"|\"16:9\"|\"1:1\"|null>, "
            "\"has_ending_cta\": <bool>, "
            "\"ending_cta\": <\"keep\"|\"none\"|\"present\"|null>, "
            "\"missing\": [<string>]}",
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
        return enrich_analysis_with_choice_replies(
            excerpt,
            ProductionFieldsAnalysis(
                duration_sec=None,
                missing=ALLOWED_MISSING,
                has_aspect_ratio=False,
                has_ending_cta=False,
            ),
        )

    parsed = _parse_analysis_payload(answer)
    if parsed is None:
        logger.warning("LLM 生产字段分析返回无法解析")
        return enrich_analysis_with_choice_replies(
            excerpt,
            ProductionFieldsAnalysis(
                duration_sec=None,
                missing=ALLOWED_MISSING,
                has_aspect_ratio=False,
                has_ending_cta=False,
            ),
        )
    return enrich_analysis_with_choice_replies(excerpt, parsed)


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
        "视频画幅": "例如 9:16 竖屏、16:9 横屏，或回复 A/B",
        "结尾行动引导": (
            "可回复①电商转化 / ②品牌认知 / ③留白收束 / ④自定义文案；"
            "或直接写「第三个」「不用引导」"
        ),
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
    "apply_production_fields_to_script",
    "build_production_fields_excerpt",
    "creative_confirm_cost_summary",
    "enrich_analysis_with_choice_replies",
    "format_creative_confirm_clarification",
    "format_production_fields_update_notice",
    "looks_like_production_field_reply",
    "looks_like_scene_asset_continue",
    "missing_creative_production_fields_async",
    "normalize_user_text",
    "production_fields_form_patch",
    "reconcile_missing_with_workspace",
    "user_latest_input",
    "workspace_has_ending_cta",
    "workspace_has_script_content",
    "workspace_missing_requirements",
    "workspace_resolved_aspect_ratio",
    "workspace_resolved_ending_cta",
]
