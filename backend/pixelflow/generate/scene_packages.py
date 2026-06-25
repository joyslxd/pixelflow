"""视频场景包准备逻辑。

这里先提供可测试、无 I/O 的规则版 ScenePackageSkill。后续接入真实 LLM 时，
Router 合同可以保持不变，只替换本模块内部的生成策略。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Callable
from typing import Any

DEFAULT_TARGET_DURATION_MS = 30_000
MAX_SCENE_DURATION_MS = 10_000
PREFERRED_SCENE_DURATION_MS = 10_000
SCENE_PACKAGE_LLM_MODEL_NAME = "deepseek-v4-pro"
ModelFactory = Callable[..., Any]


def prepare_video_scene_packages(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    target_duration_ms: int = DEFAULT_TARGET_DURATION_MS,
) -> dict[str, Any]:
    """根据 plan.md 和采集数据生成前端可编辑的视频场景包。"""
    selected_direction = selected_direction or {}
    materials = materials or []
    duration_ms = _clamp_positive_int(target_duration_ms, DEFAULT_TARGET_DURATION_MS)
    scene_count = _scene_count(duration_ms)
    durations = _split_duration(duration_ms, scene_count)

    product_name = _first_text(
        form_values.get("product_info"),
        form_values.get("product_name"),
        selected_direction.get("product_name"),
        "产品",
    )
    product_category = _first_text(form_values.get("product_category"), selected_direction.get("product_category"), "未指定品类")
    target_audience = _first_text(form_values.get("target_audience"), selected_direction.get("target_audience"), "目标用户")
    conversion_goal = _first_text(form_values.get("conversion_goal"), selected_direction.get("conversion_goal"), "转化")
    direction_title = _first_text(selected_direction.get("title"), selected_direction.get("direction_title"), "创意方向")
    direction_description = _first_text(selected_direction.get("description"), selected_direction.get("direction_description"), "")
    plan_summary = _summarize_plan(plan_markdown)
    material_urls = _extract_material_image_urls(materials)

    scenes = []
    stage_templates = _stage_templates(scene_count)
    for index, duration in enumerate(durations, start=1):
        stage = stage_templates[index - 1]
        storyline = (
            f"{stage['story_prefix']}：围绕{product_name}，面向{target_audience}，"
            f"结合{direction_title}表达{plan_summary}"
        )
        narration = stage["narration"].format(
            product_name=product_name,
            product_category=product_category,
            conversion_goal=conversion_goal,
        )
        prompt = _build_scene_prompt(
            product_name=product_name,
            product_category=product_category,
            target_audience=target_audience,
            conversion_goal=conversion_goal,
            direction_title=direction_title,
            direction_description=direction_description,
            storyline=storyline,
            narration=narration,
            stage_name=stage["name"],
        )
        scenes.append(
            {
                "scene_id": f"scene-{index}",
                "scene_index": index,
                "title": stage["name"],
                "duration_ms": duration,
                "storyline": storyline,
                "characters": [
                    {
                        "name": "讲解者",
                        "description": f"适合{target_audience}信任的电商短视频讲解者，镜头表现自然可信",
                        "three_view_prompt": f"{product_name}短视频讲解者三视图，正面、侧面、背面，统一服饰和发型",
                        "three_view_images": [],
                    }
                ],
                "scene_images": [
                    {
                        "description": stage["scene_description"].format(product_name=product_name),
                        "image_prompt": (
                            f"{stage['scene_description'].format(product_name=product_name)}，"
                            "9:16，真实摄影，电商广告质感"
                        ),
                        "images": [],
                    }
                ],
                "prop_images": [
                    {
                        "name": product_name,
                        "description": f"{product_category}商品主体，道具外观在所有场景保持一致",
                        "image_prompt": f"{product_name}产品道具图，干净背景，细节清晰，颜色和外观稳定",
                        "images": [],
                    }
                ],
                "prompt": prompt,
                "narration": narration,
                "image_urls": material_urls,
                "video_urls": [],
                "audio_urls": [],
            }
        )

    return {
        "ok": True,
        "message": "视频场景包已生成，请前端展示给用户逐场景编辑确认。",
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": duration_ms,
        "scene_packages": scenes,
    }


async def prepare_video_scene_packages_with_llm(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    target_duration_ms: int = DEFAULT_TARGET_DURATION_MS,
    *,
    model_name: str = SCENE_PACKAGE_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """用 LLM 生成视频场景包，失败时降级到规则版场景包。"""
    selected_direction = selected_direction or {}
    materials = materials or []
    duration_ms = _clamp_positive_int(target_duration_ms, DEFAULT_TARGET_DURATION_MS)
    scene_count = _scene_count(duration_ms)
    durations = _split_duration(duration_ms, scene_count)
    try:
        payload = await asyncio.to_thread(
            _invoke_scene_package_model,
            _scene_package_prompt(
                form_values=form_values,
                plan_markdown=plan_markdown,
                selected_direction=selected_direction,
                materials=materials,
                target_duration_ms=duration_ms,
                durations=durations,
            ),
            model_name,
            model_factory or _default_model_factory,
        )
        scenes = _normalize_llm_scene_packages(payload, durations, form_values, selected_direction, materials)
        if len(scenes) != scene_count:
            raise ValueError(f"LLM scene package count mismatch: expected {scene_count}, got {len(scenes)}")
        return {
            "ok": True,
            "message": "LLM 已生成视频场景包，请前端展示给用户逐场景编辑确认。",
            "requires_confirmation": True,
            "review_timeout_sec": None,
            "target_duration_ms": duration_ms,
            "scene_packages": scenes,
            "llm_used": True,
            "model_name": model_name,
        }
    except Exception as exc:  # noqa: BLE001 - LLM boundary must keep the flow usable
        fallback = prepare_video_scene_packages(
            form_values=form_values,
            plan_markdown=plan_markdown,
            selected_direction=selected_direction,
            materials=materials,
            target_duration_ms=duration_ms,
        )
        fallback["message"] = f"{fallback['message']} LLM 场景包生成失败，已使用规则兜底：{exc}"
        fallback["llm_used"] = False
        fallback["model_name"] = model_name
        fallback["error"] = str(exc)
        return fallback


def _default_model_factory(model_name: str, *, attach_tracing: bool = False) -> Any:
    from deerflow.models.factory import create_chat_model

    return create_chat_model(model_name, attach_tracing=attach_tracing)


def _invoke_scene_package_model(prompt: str, model_name: str, model_factory: ModelFactory) -> Any:
    try:
        model = model_factory(model_name, attach_tracing=False)
    except TypeError:
        model = model_factory(model_name)
    response = model.invoke(prompt)
    return _parse_json_payload(getattr(response, "content", response))


def _parse_json_payload(content: Any) -> Any:
    text = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not start_candidates:
            raise
        decoder = json.JSONDecoder()
        payload, _end = decoder.raw_decode(text[min(start_candidates) :])
        return payload


def _scene_package_prompt(
    *,
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any],
    materials: list[dict[str, Any]],
    target_duration_ms: int,
    durations: list[int],
) -> str:
    return f"""你是 PixelFlow 创作生成 Agent 的视频场景包 Skill。
请根据 plan.md、表单和创意方向，生成一组可编辑的视频场景片段。

硬性要求：
1. 必须返回 {len(durations)} 个 scene_packages，顺序和 durations_ms 完全对应。
2. 每个片段时长不能超过 {MAX_SCENE_DURATION_MS} ms。
3. 每个片段都必须包含：title、storyline、characters、scene_images、prop_images、prompt、narration。
4. characters 中至少 1 个角色，必须包含 name、description、three_view_prompt。
5. scene_images 和 prop_images 中至少各 1 个元素，必须包含 description、image_prompt。
6. 只返回 JSON，不要 Markdown，不要解释。

输出格式：
{{"scene_packages":[
  {{
    "title":"场景标题",
    "storyline":"故事线",
    "characters":[{{"name":"角色名","description":"角色描述","three_view_prompt":"角色三视图生成提示词"}}],
    "scene_images":[{{"description":"场景图描述","image_prompt":"场景图生成提示词"}}],
    "prop_images":[{{"name":"道具名","description":"道具描述","image_prompt":"道具图生成提示词"}}],
    "prompt":"分镜片段创作提示词",
    "narration":"旁白"
  }}
]}}

目标总时长 ms：{target_duration_ms}
片段 durations_ms：{json.dumps(durations, ensure_ascii=False)}
表单数据：{json.dumps(form_values, ensure_ascii=False)}
创意方向：{json.dumps(selected_direction, ensure_ascii=False)}
素材集合：{json.dumps(materials, ensure_ascii=False)}
plan.md：{plan_markdown[:6000]}
"""


def _normalize_llm_scene_packages(
    payload: Any,
    durations: list[int],
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_scenes = payload.get("scene_packages") if isinstance(payload, dict) else payload
    if not isinstance(raw_scenes, list):
        return []
    material_urls = _extract_material_image_urls(materials)
    normalized: list[dict[str, Any]] = []
    for index, duration in enumerate(durations, start=1):
        raw = raw_scenes[index - 1] if index - 1 < len(raw_scenes) and isinstance(raw_scenes[index - 1], dict) else {}
        storyline = _first_text(raw.get("storyline"), raw.get("story"), raw.get("story_line"))
        prompt = _first_text(raw.get("prompt"), raw.get("creation_prompt"), raw.get("shot_prompt"))
        narration = _first_text(raw.get("narration"), raw.get("voiceover"), raw.get("voice_over"))
        if not storyline or not prompt or not narration:
            return []
        title = _first_text(raw.get("title"), f"场景 {index}")
        return_scene = {
            "scene_id": f"scene-{index}",
            "scene_index": index,
            "title": title,
            "duration_ms": duration,
            "storyline": storyline,
            "characters": _normalize_scene_items(
                raw.get("characters"),
                fallback=[_default_character(form_values)],
                required_fields=("name", "description", "three_view_prompt"),
            ),
            "scene_images": _normalize_scene_items(
                raw.get("scene_images"),
                fallback=[_default_scene_image(form_values)],
                required_fields=("description", "image_prompt"),
            ),
            "prop_images": _normalize_scene_items(
                raw.get("prop_images"),
                fallback=[_default_prop_image(form_values, selected_direction)],
                required_fields=("name", "description", "image_prompt"),
            ),
            "prompt": prompt,
            "narration": narration,
            "image_urls": material_urls,
            "video_urls": [],
            "audio_urls": [],
        }
        normalized.append(return_scene)
    return normalized


def _normalize_scene_items(value: Any, *, fallback: list[dict[str, Any]], required_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return fallback
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            continue
        cleaned = {field_name: _first_text(item.get(field_name)) for field_name in required_fields}
        if all(cleaned.values()):
            normalized.append({**item, **cleaned})
    return normalized or fallback


def _default_character(form_values: dict[str, Any]) -> dict[str, Any]:
    target_audience = _first_text(form_values.get("target_audience"), "目标用户")
    product_name = _first_text(form_values.get("product_info"), "产品")
    return {
        "name": "讲解者",
        "description": f"适合{target_audience}信任的电商短视频讲解者，镜头表现自然可信",
        "three_view_prompt": f"{product_name}短视频讲解者三视图，正面、侧面、背面，统一服饰和发型",
        "three_view_images": [],
    }


def _default_scene_image(form_values: dict[str, Any]) -> dict[str, Any]:
    product_name = _first_text(form_values.get("product_info"), "产品")
    return {
        "description": f"{product_name}在真实使用场景中被展示",
        "image_prompt": f"{product_name}真实使用场景图，9:16，电商广告质感",
        "images": [],
    }


def _default_prop_image(form_values: dict[str, Any], selected_direction: dict[str, Any]) -> dict[str, Any]:
    product_name = _first_text(form_values.get("product_info"), selected_direction.get("title"), "产品")
    product_category = _first_text(form_values.get("product_category"), "商品")
    return {
        "name": product_name,
        "description": f"{product_category}商品主体，道具外观在所有场景保持一致",
        "image_prompt": f"{product_name}产品道具图，干净背景，细节清晰，颜色和外观稳定",
        "images": [],
    }


def _scene_count(duration_ms: int) -> int:
    count = math.ceil(duration_ms / PREFERRED_SCENE_DURATION_MS)
    return max(1, min(18, count))


def _split_duration(total_ms: int, scene_count: int) -> list[int]:
    durations: list[int] = []
    remaining = total_ms
    for index in range(scene_count, 0, -1):
        duration = min(MAX_SCENE_DURATION_MS, max(1, remaining // index))
        durations.append(duration)
        remaining -= duration
    if remaining > 0:
        for index in range(len(durations)):
            available = MAX_SCENE_DURATION_MS - durations[index]
            if available <= 0:
                continue
            add = min(available, remaining)
            durations[index] += add
            remaining -= add
            if remaining == 0:
                break
    return durations


def _stage_templates(scene_count: int) -> list[dict[str, str]]:
    base = [
        {
            "name": "开场钩子",
            "story_prefix": "用高对比痛点或结果快速抓住注意力",
            "narration": "还在为{product_category}选择纠结？先看{product_name}解决的这个关键问题。",
            "scene_description": "{product_name}在真实使用场景中被快速展示，开场有强注意力焦点",
        },
        {
            "name": "卖点证明",
            "story_prefix": "通过产品细节、使用过程和前后对比证明核心卖点",
            "narration": "{product_name}的核心优势在这里，画面直接展示效果，不靠空喊。",
            "scene_description": "{product_name}的核心功能和细节被近景展示，画面清晰稳定",
        },
        {
            "name": "转化收口",
            "story_prefix": "把卖点落到购买或行动理由，并给出明确转化指令",
            "narration": "想了解更多细节，现在就进入直播间，完成{conversion_goal}。",
            "scene_description": "{product_name}与行动提示同框，结尾有清晰转化氛围",
        },
    ]
    if scene_count <= len(base):
        return base[:scene_count]
    extras = [
        {
            "name": f"补充证明 {index}",
            "story_prefix": "补充一个真实使用理由，增强信任",
            "narration": "{product_name}在更多真实场景里同样适用，减少用户决策顾虑。",
            "scene_description": "{product_name}在补充使用场景中自然出现，保持主体一致",
        }
        for index in range(1, scene_count - len(base) + 1)
    ]
    return [base[0], *extras, *base[1:]]


def _build_scene_prompt(
    *,
    product_name: str,
    product_category: str,
    target_audience: str,
    conversion_goal: str,
    direction_title: str,
    direction_description: str,
    storyline: str,
    narration: str,
    stage_name: str,
) -> str:
    return (
        f"{stage_name}。生成一段9:16电商短视频片段，产品是{product_name}，品类是{product_category}，"
        f"目标人群是{target_audience}，转化目标是{conversion_goal}。创意方向：{direction_title}。"
        f"{direction_description}。故事线：{storyline}。旁白：{narration}。"
        "要求主体、商品颜色、道具和场景在前后镜头中保持一致；避免人物畸形、手部变形、字幕乱码、无关物体乱入。"
    )


def _summarize_plan(plan_markdown: str) -> str:
    lines = [re.sub(r"^[#\-\d.、\s]+", "", line).strip() for line in str(plan_markdown or "").splitlines()]
    candidates = [line for line in lines if line and not line.startswith("|")]
    return candidates[0][:80] if candidates else "plan.md中的创作方案"


def _extract_material_image_urls(materials: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for material in materials:
        for key in ("url", "image_url", "imageUrl", "download_url", "downloadUrl"):
            value = material.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
                urls.append(value)
    return urls


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _clamp_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
