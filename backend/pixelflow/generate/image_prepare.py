"""图片生成准备纯逻辑。

这里对应设计文档里的 ImageEndpointDecisionSkill 和 ImagePromptBuildSkill。
它只选择博观图片接口并构造 prompt/参数，不调用供应商，不做轮询。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

ImageMethod = Literal["text_to_image", "multi_reference_image_generation", "image_edit", "multi_image_fusion"]

TEXT_TO_IMAGE_ENDPOINT = "/api/picture/text_to_image"
MULTI_REFERENCE_ENDPOINT = "/api/picture/multi_reference_image_generation"
IMAGE_EDIT_ENDPOINT = "/api/picture/image_edit"
MULTI_IMAGE_FUSION_ENDPOINT = "/api/picture/multi_image_fusion"

ENDPOINT_BY_METHOD: dict[ImageMethod, str] = {
    "text_to_image": TEXT_TO_IMAGE_ENDPOINT,
    "multi_reference_image_generation": MULTI_REFERENCE_ENDPOINT,
    "image_edit": IMAGE_EDIT_ENDPOINT,
    "multi_image_fusion": MULTI_IMAGE_FUSION_ENDPOINT,
}

TEXT_TO_IMAGE_MODEL = "seeddream-5.0"
TEXT_TO_IMAGE_QUALITY = "1080p"
REFERENCE_IMAGE_MODEL = "gpt-image-2"
REFERENCE_IMAGE_QUALITY = "4K"
IMAGE_EDIT_MODEL = "gpt-image-2"
IMAGE_EDIT_QUALITY = "4K"
RATIO_PATTERN = re.compile(r"(\d{1,2})\s*:\s*(\d{1,2})")
SUPPORTED_OUTPUT_RATIOS = {"1:1", "9:16", "16:9"}


@dataclass(frozen=True)
class ImageGenerationPrepareResult:
    ok: bool
    method: ImageMethod
    endpoint: str
    prompt: str
    negative_prompt: str
    params: dict[str, Any]
    images: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    review_timeout_sec: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "method": self.method,
            "endpoint": self.endpoint,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "params": self.params,
            "images": self.images,
            "message": self.message,
            "review_timeout_sec": self.review_timeout_sec,
        }


def prepare_image_generation(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    materials: list[dict[str, Any]] | None = None,
    revision_feedback: str | None = None,
    intake_context: dict[str, Any] | None = None,
) -> ImageGenerationPrepareResult:
    context = intake_context or {}
    image_materials = _image_materials(materials or [])
    method = _decide_method(form_values, plan_markdown, selected_direction, image_materials, revision_feedback, context)
    endpoint = ENDPOINT_BY_METHOD[method]
    prompt = _build_prompt(form_values, plan_markdown, selected_direction, revision_feedback, context)
    negative_prompt = "低清晰度，模糊，水印，错别字，多余文字，畸形手指，变形产品，夸大承诺，违规绝对化表述"
    ratio = _resolve_ratio(form_values, plan_markdown, selected_direction, context)
    image_count = _requested_image_count(form_values, plan_markdown, selected_direction, context)
    reference_urls = [image["url"] for image in image_materials if _text(image.get("url"))]

    if method == "multi_image_fusion":
        width, height = _ratio_pair(ratio)
        if len(reference_urls) < 2:
            return ImageGenerationPrepareResult(
                ok=False,
                method=method,
                endpoint=endpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                params={
                    "image_urls": reference_urls,
                    "ratio": ratio,
                    "width": width,
                    "height": height,
                    "model": TEXT_TO_IMAGE_MODEL,
                    "size": TEXT_TO_IMAGE_QUALITY,
                    "num_images": 1,
                },
                images=[],
                message="多图融合至少需要上传 2 张图片素材。",
            )
        return ImageGenerationPrepareResult(
            ok=True,
            method=method,
            endpoint=endpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            params={
                "image_urls": reference_urls,
                "prompt": prompt,
                "ratio": ratio,
                "width": width,
                "height": height,
                "model": TEXT_TO_IMAGE_MODEL,
                "size": TEXT_TO_IMAGE_QUALITY,
                "num_images": 1,
            },
            images=[],
            message="已准备多图融合参数，下一步可调用博观多图融合接口。",
        )
    if method == "image_edit":
        width, height = _ratio_pair(ratio)
        if not reference_urls:
            return ImageGenerationPrepareResult(
                ok=False,
                method=method,
                endpoint=endpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                params={
                    "image_url": "",
                    "prompt": prompt,
                    "model": IMAGE_EDIT_MODEL,
                    "imageSize": IMAGE_EDIT_QUALITY,
                    "width": width,
                    "height": height,
                    "max_images": image_count,
                },
                message="图片编辑需要先上传一张需要修改的原图。",
            )
        return ImageGenerationPrepareResult(
            ok=True,
            method=method,
            endpoint=endpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            params={
                "image_url": reference_urls[0] if reference_urls else "",
                "prompt": prompt,
                "model": IMAGE_EDIT_MODEL,
                "imageSize": IMAGE_EDIT_QUALITY,
                "width": width,
                "height": height,
                "max_images": image_count,
            },
            message="已准备图片编辑参数，下一步可调用博观图片编辑接口。",
        )
    if method == "multi_reference_image_generation":
        width, height = _ratio_pair(ratio)
        return ImageGenerationPrepareResult(
            ok=True,
            method=method,
            endpoint=endpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            params={
                "prompt": prompt,
                "reference_image_urls": reference_urls,
                "model": REFERENCE_IMAGE_MODEL,
                "width": width,
                "height": height,
                "imageSize": REFERENCE_IMAGE_QUALITY,
                "max_images": image_count,
            },
            message="已准备参考图生图参数，下一步可调用博观参考生成组图接口。",
        )
    return ImageGenerationPrepareResult(
        ok=True,
        method=method,
        endpoint=endpoint,
        prompt=prompt,
        negative_prompt=negative_prompt,
        params={
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "model": TEXT_TO_IMAGE_MODEL,
            "ratio": ratio,
            "size": TEXT_TO_IMAGE_QUALITY,
            "num_images": image_count,
        },
        message="已准备文生图参数，下一步可调用博观文生图接口。",
    )


def _decide_method(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    image_materials: list[dict[str, Any]],
    revision_feedback: str | None,
    intake_context: dict[str, Any] | None = None,
) -> ImageMethod:
    forced = _normalize_method(form_values.get("image_operation") or (intake_context or {}).get("image_operation"))
    if forced:
        return forced
    plan_text = _text(plan_markdown)
    operation_text = " ".join(
        _text(value)
        for value in [
            form_values.get("operation"),
            selected_direction.get("operation"),
            revision_feedback,
            form_values.get("image_goal"),
            selected_direction.get("title"),
            selected_direction.get("description"),
            *(material.get("operation") for material in image_materials),
        ]
    )
    if _has_any(operation_text, ["融合", "合成一张", "fusion", "multi_image_fusion"]) or _has_any(
        plan_text,
        ["多图融合", "融合成一张", "合成一张", "multi_image_fusion"],
    ):
        return "multi_image_fusion"
    if _has_any(operation_text, ["编辑", "修改", "换背景", "修图", "image_edit", "edit"]) or _has_any(
        plan_text,
        ["图片编辑", "图像编辑", "编辑图片", "修改图片", "改图", "换背景", "修图", "替换背景", "调整背景"],
    ):
        return "image_edit"
    if image_materials:
        return "multi_reference_image_generation"
    return "text_to_image"


def _normalize_method(value: Any) -> ImageMethod | None:
    normalized = _text(value).strip().lower().replace("-", "_")
    aliases: dict[str, ImageMethod] = {
        "text_to_image": "text_to_image",
        "text2image": "text_to_image",
        "txt2img": "text_to_image",
        "文生图": "text_to_image",
        "image_edit": "image_edit",
        "edit": "image_edit",
        "图像编辑": "image_edit",
        "图片编辑": "image_edit",
        "改图": "image_edit",
        "修图": "image_edit",
        "reference_image": "multi_reference_image_generation",
        "reference": "multi_reference_image_generation",
        "multi_reference_image_generation": "multi_reference_image_generation",
        "参考图": "multi_reference_image_generation",
        "参考生成": "multi_reference_image_generation",
        "multi_image_fusion": "multi_image_fusion",
        "fusion": "multi_image_fusion",
        "融合": "multi_image_fusion",
        "多图融合": "multi_image_fusion",
    }
    return aliases.get(normalized)


def _build_prompt(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    revision_feedback: str | None,
    intake_context: dict[str, Any],
) -> str:
    product_subject = _context_text_value(intake_context, "product_subject")
    image_goal = _context_text_value(intake_context, "creation_goal") or _text(form_values.get("image_goal"), "图片生成")
    source_prompt = _context_text_value(intake_context, "source_prompt")
    industry_type = _context_text_value(intake_context, "industry_type")
    profile = _context_profile(intake_context)
    visual_anchors = _profile_anchor_text(profile)
    parts = [
        f"图片目标：{image_goal}",
        f"产品主体：{product_subject}" if product_subject else "",
        f"原始需求：{source_prompt}" if source_prompt else "",
        f"行业类型：{industry_type}" if industry_type else "",
        f"垂类核心表达：{_text(profile.get('core_message'))}" if _text(profile.get("core_message")) else "",
        f"视觉锚点：{visual_anchors}" if visual_anchors else "",
        f"图片类型：{_text(form_values.get('image_type'), '未指定')}",
        f"图片用途：{_text(form_values.get('image_usage'), '未指定')}",
        f"图片风格：{_text(form_values.get('image_style'), '自由发挥')}",
        f"图片尺寸：{_text(form_values.get('image_size'), '自动适配')}",
        f"创意方向：{_text(selected_direction.get('title'), '推荐方向')}。{_text(selected_direction.get('description'))}",
        f"plan.md 摘要：{_compact_markdown(plan_markdown)}",
    ]
    if revision_feedback:
        parts.append(f"用户修改意见：{revision_feedback.strip()}")
    parts.append("画面要求：主体清晰，构图稳定，商品或核心元素可识别，避免生成画面文字和水印。")
    return "\n".join(part for part in parts if part.strip())[:3000]


def _image_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for material in materials:
        url = _first_text(material, "url", "image_url", "imageUrl", "download_url", "downloadUrl", "src")
        kind = _first_text(material, "type", "kind", "media_type", "mediaType", "mime_type", "mimeType").lower()
        if url and (
            kind in {"", "image", "picture", "reference_image"}
            or kind.startswith("image")
            or url.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp"))
        ):
            normalized = dict(material)
            normalized["url"] = url
            images.append(normalized)
    return images


def _resolve_ratio(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any] | None = None,
) -> str:
    label = _text(form_values.get("image_size")).strip()
    explicit = _explicit_ratio(label)
    if explicit:
        return explicit
    if not label or _is_auto_size(label) or RATIO_PATTERN.search(label):
        return _auto_ratio(form_values, plan_markdown, selected_direction, intake_context or {})
    return "1:1"


def _explicit_ratio(label: str) -> str:
    match = RATIO_PATTERN.search(label)
    if match:
        ratio = f"{int(match.group(1))}:{int(match.group(2))}"
        return ratio if ratio in SUPPORTED_OUTPUT_RATIOS else ""
    if "正方" in label or "方图" in label:
        return "1:1"
    return ""


def _is_auto_size(label: str) -> bool:
    normalized = label.lower()
    return any(keyword in normalized for keyword in ["auto", "自动", "自适应", "适配"])


def _auto_ratio(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any] | None = None,
) -> str:
    context = _context_text(form_values, plan_markdown, selected_direction, intake_context or {})
    if _has_any(context, ["横版", "横幅", "banner", "大屏", "电脑端", "官网", "头图", "网页", "16:9"]):
        return "16:9"
    if _has_any(context, ["商品主图", "主图", "头像", "logo", "图标", "电商主图", "正方形", "1:1"]):
        return "1:1"
    if _has_any(context, ["竖版", "竖图", "海报", "封面", "小红书", "社媒", "短视频", "种草", "抖音", "快手", "投放", "9:16"]):
        return "9:16"
    if _has_any(context, ["信息流", "4:5", "feed"]):
        return "9:16"
    if _has_any(context, ["人物", "场景图", "3:4"]):
        return "9:16"
    return "1:1"


def _context_text(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any] | None = None,
) -> str:
    parts = [plan_markdown]
    parts.extend(_text(form_values.get(key)) for key in ["image_goal", "image_type", "image_usage", "image_style", "image_size"])
    parts.extend(_text(value) for value in selected_direction.values())
    context = intake_context or {}
    parts.extend(
        _text(context.get(key))
        for key in ["source_prompt", "product_subject", "creation_goal", "industry_type", "requested_output_count"]
    )
    profile = _context_profile(context)
    parts.append(_text(profile.get("core_message")))
    parts.append(_profile_anchor_text(profile))
    return " ".join(part for part in parts if part).lower()


def _requested_image_count(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any] | None = None,
) -> int:
    context_count = _normalize_image_count((intake_context or {}).get("requested_output_count"))
    if context_count > 1:
        return context_count
    explicit = _normalize_image_count(form_values.get("image_count"))
    if explicit > 1:
        return explicit
    inferred = _extract_image_count(_context_text(form_values, plan_markdown, selected_direction, intake_context or {}))
    return inferred or explicit


def _extract_image_count(text: str) -> int | None:
    patterns = [
        r"(\d{1,2})\s*(?:张|幅|个)\s*(?:图片|图|海报|封面|主图|素材图)",
        r"(?:图片|图|海报|封面|主图|素材图)\s*(\d{1,2})\s*(?:张|幅|个)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _normalize_image_count(match.group(1))
    return None


def _normalize_image_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(10, number))


def _ratio_pair(ratio: str) -> tuple[int, int]:
    match = RATIO_PATTERN.fullmatch(ratio.strip())
    if match:
        return int(match.group(1)), int(match.group(2))
    return 1, 1


def _compact_markdown(markdown: str) -> str:
    text = " ".join(line.strip() for line in markdown.splitlines() if line.strip())
    return text[:800]


def _has_any(text: str, keywords: list[str]) -> bool:
    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value)


def _context_text_value(intake_context: dict[str, Any], key: str) -> str:
    return _text(intake_context.get(key))


def _context_profile(intake_context: dict[str, Any]) -> dict[str, Any]:
    profile = intake_context.get("product_creative_profile")
    return profile if isinstance(profile, dict) else {}


def _profile_anchor_text(profile: dict[str, Any]) -> str:
    anchors = profile.get("visual_anchor_keywords")
    if isinstance(anchors, list):
        return "、".join(_text(anchor) for anchor in anchors if _text(anchor))
    return _text(anchors)


def _first_text(values: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(values.get(key))
        if text:
            return text
    return ""
