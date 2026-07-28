"""视频场景包准备逻辑。

这里先提供可测试、无 I/O 的规则版 ScenePackageSkill。后续接入真实 LLM 时，
Router 合同可以保持不变，只替换本模块内部的生成策略。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable
from typing import Any

from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.duration import (
    MAX_SCENE_DURATION_SEC,
    MIN_SCENE_DURATION_SEC,
    scene_time_ranges,
    split_video_duration,
)
from pixelflow.creative.scene_blueprint import normalize_scene_blueprints, validate_asset_requirement_quality
from pixelflow.generate.seedance_prompt import build_seedance_shot_prompt, load_seedance_guidance

DEFAULT_TARGET_DURATION_MS = 30_000
MIN_SCENE_DURATION_MS = MIN_SCENE_DURATION_SEC * 1000
MAX_SCENE_DURATION_MS = MAX_SCENE_DURATION_SEC * 1000
SCENE_PACKAGE_LLM_MODEL_NAME = "deepseek-v4-pro"
ModelFactory = Callable[..., Any]


def prepare_video_scene_packages(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    target_duration_ms: int = DEFAULT_TARGET_DURATION_MS,
    scene_blueprints: list[dict[str, Any]] | None = None,
    asset_manifest: dict[str, Any] | None = None,
    *,
    authority_mode: bool = False,
) -> dict[str, Any]:
    """根据 plan.md 和采集数据生成前端可编辑的视频场景包。"""
    selected_direction = selected_direction or {}
    materials = materials or []
    duration_ms, durations, authoritative_blueprints = _resolve_scene_schedule(
        target_duration_ms,
        scene_blueprints,
    )
    if authoritative_blueprints:
        validate_asset_requirement_quality(authoritative_blueprints)
    scene_count = len(durations)

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
    plan_summary = _summarize_plan(plan_markdown)
    material_urls = extract_material_image_urls(materials)

    scenes = []
    stage_templates = _stage_templates(scene_count)
    if authoritative_blueprints and asset_manifest is not None:
        global_assets = _global_assets_from_plan_manifest(
            asset_manifest,
            authoritative_blueprints,
            form_values,
        )
    else:
        global_assets = _default_global_assets(
            form_values=form_values,
            selected_direction=selected_direction,
            stage_templates=stage_templates,
        )
    if authoritative_blueprints and asset_manifest is None:
        global_assets = _align_global_assets_to_blueprints(global_assets, authoritative_blueprints, form_values)
    elapsed_ms = 0
    for index, duration in enumerate(durations, start=1):
        stage = stage_templates[index - 1]
        blueprint = authoritative_blueprints[index - 1] if authoritative_blueprints else None
        storyline = str(blueprint["storyline"]) if blueprint else (f"{stage['story_prefix']}：围绕{product_name}，面向{target_audience}，结合{direction_title}表达{plan_summary}")
        narration = (
            str(blueprint["narration"])
            if blueprint
            else stage["narration"].format(
                product_name=product_name,
                product_category=product_category,
                conversion_goal=conversion_goal,
            )
        )
        reference_asset_ids = _blueprint_reference_asset_ids(blueprint, global_assets) if blueprint else _default_reference_asset_ids(global_assets, stage["asset_id"])
        shot_description = (
            _normalize_shot_description(
                {"text": str(blueprint["shot_description"])},
                stage=stage,
                start_ms=elapsed_ms,
                duration_ms=duration,
                reference_asset_ids=reference_asset_ids,
                global_assets=global_assets,
            )
            if blueprint
            else _default_shot_description(
                stage=stage,
                start_ms=elapsed_ms,
                duration_ms=duration,
                reference_asset_ids=reference_asset_ids,
                global_assets=global_assets,
            )
        )
        prompt = _scene_visual_style_prompt(global_assets["visual_style"])
        scenes.append(
            {
                "scene_id": str(blueprint["scene_id"]) if blueprint and authority_mode else f"scene-{index}",
                "scene_index": int(blueprint["scene_index"]) if blueprint and authority_mode else index,
                "title": str(blueprint["title"]) if blueprint else stage["name"],
                "duration_ms": duration,
                "storyline": storyline,
                "shot_description": shot_description,
                "reference_asset_ids": reference_asset_ids,
                "prompt": prompt,
                "narration": narration,
                "transition": str(blueprint["transition"]) if blueprint else "按动作完成点衔接下一镜头。",
                "image_urls": material_urls,
                "video_urls": [],
                "audio_urls": [],
            }
        )
        elapsed_ms += duration

    return {
        "ok": True,
        "message": "视频场景包已生成，请前端展示给用户逐场景编辑确认。",
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": duration_ms,
        "global_assets": global_assets,
        "scene_packages": scenes,
    }


async def prepare_video_scene_packages_with_llm(
    form_values: dict[str, Any],
    plan_markdown: str,
    selected_direction: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    target_duration_ms: int = DEFAULT_TARGET_DURATION_MS,
    scene_blueprints: list[dict[str, Any]] | None = None,
    asset_manifest: dict[str, Any] | None = None,
    *,
    model_name: str = SCENE_PACKAGE_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """用 LLM 生成视频场景包，失败时降级到规则版场景包。"""
    selected_direction = selected_direction or {}
    materials = materials or []
    duration_ms, durations, authoritative_blueprints = _resolve_scene_schedule(
        target_duration_ms,
        scene_blueprints,
    )
    if authoritative_blueprints:
        validate_asset_requirement_quality(authoritative_blueprints)
    scene_count = len(durations)
    if authoritative_blueprints and asset_manifest is not None:
        result = prepare_video_scene_packages(
            form_values=form_values,
            plan_markdown=plan_markdown,
            selected_direction=selected_direction,
            materials=materials,
            target_duration_ms=duration_ms,
            scene_blueprints=authoritative_blueprints,
            asset_manifest=asset_manifest,
        )
        result["message"] = "已严格按最终 Plan 生成视频场景包和全局资产，不再进行二次资产分析。"
        result["llm_used"] = False
        result["model_name"] = model_name
        return result
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
                scene_blueprints=authoritative_blueprints,
            ),
            model_name,
            model_factory or _default_model_factory,
        )
        stage_templates = _stage_templates(scene_count)
        global_assets = _normalize_llm_global_assets(payload, form_values, selected_direction, stage_templates)
        if authoritative_blueprints:
            global_assets = _align_global_assets_to_blueprints(global_assets, authoritative_blueprints, form_values)
        scenes = _normalize_llm_scene_packages(
            payload,
            durations,
            form_values,
            selected_direction,
            materials,
            global_assets,
            scene_blueprints=authoritative_blueprints,
        )
        if len(scenes) != scene_count:
            raise ValueError(f"LLM scene package count mismatch: expected {scene_count}, got {len(scenes)}")
        return {
            "ok": True,
            "message": "LLM 已生成视频场景包，请前端展示给用户逐场景编辑确认。",
            "requires_confirmation": True,
            "review_timeout_sec": None,
            "target_duration_ms": duration_ms,
            "global_assets": global_assets,
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
            scene_blueprints=authoritative_blueprints,
            asset_manifest=asset_manifest,
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
    scene_blueprints: list[dict[str, Any]],
) -> str:
    duration_seconds = [duration // 1000 for duration in durations]
    time_ranges = scene_time_ranges(duration_seconds)
    stage_templates = _stage_templates(len(durations))
    default_assets = _default_global_assets(
        form_values=form_values,
        selected_direction=selected_direction,
        stage_templates=stage_templates,
    )
    visual_style = _first_text(
        form_values.get("visual_style"),
        default_assets["visual_style"].get("description"),
    )
    video_ratio = _first_text(form_values.get("video_ratio"), "9:16")
    video_model = _first_text(form_values.get("video_model"), "seedance")
    shot_contracts: list[str] = []
    for index, ((start_second, end_second), stage) in enumerate(zip(time_ranges, stage_templates, strict=True), start=1):
        reference_ids = _default_reference_asset_ids(default_assets, stage["asset_id"])
        shot_contracts.append(
            build_seedance_shot_prompt(
                scene_index=index,
                start_second=start_second,
                end_second=end_second,
                plan_markdown=plan_markdown,
                storyline=f"严格承接 plan.md 的第 {index} 个分镜故事线",
                narration="由当前 plan.md 和故事节奏决定，可为空",
                visual_style=visual_style,
                available_asset_ids=reference_ids,
                video_ratio=video_ratio,
                video_model=video_model,
                include_guidance=False,
                include_plan=False,
            )
        )
    seedance_guidance = load_seedance_guidance()
    shot_contract_text = "\n\n".join(shot_contracts)
    return f"""你是 PixelFlow 创作生成 Agent 的视频场景包 Skill。
请根据 plan.md、表单和创意方向，生成一组可编辑的视频场景片段。

以下是项目内 Seedance 系列 Skill 的强制指导，当前视频模型为 {video_model}：
{seedance_guidance}

硬性要求：
1. 必须返回 {len(durations)} 个 scene_packages，顺序和 durations_ms 完全对应。
2. 每个片段时长必须在 {MIN_SCENE_DURATION_SEC} 到 {MAX_SCENE_DURATION_SEC} 秒之间，并严格使用下面权威 Plan 分镜蓝图的秒段。
3. global_assets 是整片固定资产，必须包含 characters、scenes、props、visual_style。
4. global_assets.characters 只能是人物角色，不能放产品、商品、道具或场景；每个角色必须提供 three_view_prompt，用来生成当前人物的正面、侧面、背面三视图。
5. scene_packages 是逐片段变化内容，只包含 title、storyline、shot_description、reference_asset_ids、prompt、narration。
6. shot_description 包含 text 和 mentions。text 仍是一个字符串，但可包含一个或多个按内容决定的中文段落；每段以局部整数秒范围开头，不要拆成 time_range、location、characters、shot_size、description 等字段。
7. shot_description.text 中引用角色、场景、道具图片时使用 @asset_id，例如 @character-presenter；视觉风格只作为文字描述，不作为图片 mention。
8. shot_description.text 的时间范围必须使用秒，例如 0-10秒、10-15秒；不要使用 ms、毫秒或 00:00.000 这类毫秒时间码。
9. reference_asset_ids 和 shot_description.mentions 最多 9 个，必须来自 global_assets 的角色、场景、道具 asset_id。
10. 产品主体、商品、工具、包装、卖点物件一律放在 global_assets.props，不允许放进 global_assets.characters。
11. 只返回 JSON，不要 Markdown，不要解释。
12. 权威 Plan 分镜蓝图非空时，title、storyline、shot_description、narration、transition、顺序和时长均不得重写；只把 asset_requirements 落成 global_assets，并为镜头描述补充合法 @asset_id 和 mentions。

输出格式：
{{"global_assets":{{
  "characters":[
    {{"asset_id":"character-presenter","name":"人物角色名","description":"人物角色描述","three_view_prompt":"同一个人物角色三视图生成提示词，必须包含正面、侧面、背面"}},
    {{"asset_id":"character-user","name":"另一个人物角色名","description":"另一个人物角色描述","three_view_prompt":"另一个人物角色三视图生成提示词，必须包含正面、侧面、背面"}}
  ],
  "scenes":[{{"asset_id":"scene-opening","name":"场景名","description":"场景描述","image_prompt":"场景图生成提示词"}}],
  "props":[{{"asset_id":"prop-product","name":"道具名","description":"道具描述","image_prompt":"道具图生成提示词"}}],
  "visual_style":{{"asset_id":"style-main","name":"视觉风格名","description":"视觉风格描述","prompt":"视觉风格约束"}}
}},
"scene_packages":[
  {{
    "title":"场景标题",
    "storyline":"故事线",
    "shot_description":{{
      "text":"0-10秒: 地点:@scene-opening 中,角色:@character-presenter 在画面中完成动作,道具:@prop-product 清晰可见。景别和运动方式写在这段话里。",
      "mentions":[
        {{"asset_id":"character-presenter","type":"character","name":"角色名"}},
        {{"asset_id":"scene-opening","type":"scene","name":"场景名"}},
        {{"asset_id":"prop-product","type":"prop","name":"道具名"}}
      ]
    }},
    "reference_asset_ids":["character-presenter","scene-opening","prop-product"],
    "prompt":"分镜片段创作提示词",
    "narration":"旁白"
  }}
]}}

目标总时长秒：{target_duration_ms // 1000}
片段 durations_sec：{json.dumps(duration_seconds, ensure_ascii=False)}
视频画幅：{video_ratio}
权威 Plan 分镜蓝图：{json.dumps(scene_blueprints, ensure_ascii=False)}
逐分镜 Seedance 执行合同：
{shot_contract_text}
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
    global_assets: dict[str, Any],
    *,
    scene_blueprints: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    raw_scenes = payload.get("scene_packages") if isinstance(payload, dict) else payload
    if not isinstance(raw_scenes, list) and not scene_blueprints:
        return []
    if not isinstance(raw_scenes, list):
        raw_scenes = []
    material_urls = extract_material_image_urls(materials)
    normalized: list[dict[str, Any]] = []
    elapsed_ms = 0
    stage_templates = _stage_templates(len(durations))
    for index, duration in enumerate(durations, start=1):
        raw = raw_scenes[index - 1] if index - 1 < len(raw_scenes) and isinstance(raw_scenes[index - 1], dict) else {}
        blueprint = scene_blueprints[index - 1] if scene_blueprints else None
        storyline = str(blueprint["storyline"]) if blueprint else _first_text(raw.get("storyline"), raw.get("story"), raw.get("story_line"))
        narration = str(blueprint["narration"]) if blueprint else _first_text(raw.get("narration"), raw.get("voiceover"), raw.get("voice_over"))
        stage = stage_templates[index - 1]
        reference_asset_ids = _blueprint_reference_asset_ids(blueprint, global_assets) if blueprint else _normalize_reference_asset_ids(raw.get("reference_asset_ids"), global_assets, stage["asset_id"])
        shot_description = _normalize_shot_description(
            {"text": str(blueprint["shot_description"])} if blueprint else raw.get("shot_description"),
            stage=stage,
            start_ms=elapsed_ms,
            duration_ms=duration,
            reference_asset_ids=reference_asset_ids,
            global_assets=global_assets,
        )
        prompt = _scene_visual_style_prompt(global_assets.get("visual_style"))
        if not storyline or not shot_description.get("text") or not prompt:
            return []
        title = str(blueprint["title"]) if blueprint else _first_text(raw.get("title"), f"场景 {index}")
        return_scene = {
            "scene_id": str(blueprint["scene_id"]) if blueprint else f"scene-{index}",
            "scene_index": index,
            "title": title,
            "duration_ms": duration,
            "storyline": storyline,
            "shot_description": shot_description,
            "reference_asset_ids": reference_asset_ids,
            "prompt": prompt,
            "narration": narration,
            "transition": str(blueprint["transition"]) if blueprint else _first_text(raw.get("transition")),
            "image_urls": material_urls,
            "video_urls": [],
            "audio_urls": [],
        }
        normalized.append(return_scene)
        elapsed_ms += duration
    return normalized


def _normalize_llm_global_assets(
    payload: Any,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    stage_templates: list[dict[str, str]],
) -> dict[str, Any]:
    defaults = _default_global_assets(form_values=form_values, selected_direction=selected_direction, stage_templates=stage_templates)
    raw = payload.get("global_assets") if isinstance(payload, dict) and isinstance(payload.get("global_assets"), dict) else {}
    product_name = _first_text(form_values.get("product_info"), form_values.get("product_name"), selected_direction.get("product_name"), "产品")
    product_category = _first_text(form_values.get("product_category"), selected_direction.get("product_category"), "商品")
    return {
        "characters": _normalize_character_asset_list(
            raw.get("characters"),
            fallback=defaults["characters"],
            product_name=product_name,
            product_category=product_category,
        ),
        "scenes": _normalize_global_asset_list(
            raw.get("scenes"),
            fallback=defaults["scenes"],
            default_prefix="scene",
            required_fields=("name", "description", "image_prompt"),
        ),
        "props": _normalize_global_asset_list(
            raw.get("props"),
            fallback=defaults["props"],
            default_prefix="prop",
            required_fields=("name", "description", "image_prompt"),
        ),
        "visual_style": _normalize_visual_style(raw.get("visual_style"), defaults["visual_style"]),
    }


def _global_assets_from_plan_manifest(
    asset_manifest: dict[str, Any],
    scene_blueprints: list[dict[str, Any]],
    form_values: dict[str, Any],
) -> dict[str, Any]:
    """把最终 Plan 清单机械转换为场景包资产，不改名、不增删、不补写语义。"""

    normalized = normalize_asset_manifest(asset_manifest, scene_blueprints)
    characters = [
        {
            **item,
            "three_view_images": [],
        }
        for item in normalized["characters"]
    ]
    scenes = [
        {
            **item,
            "images": [],
        }
        for item in normalized["scenes"]
    ]
    props = [
        {
            **item,
            "images": [],
        }
        for item in normalized["props"]
    ]
    visual_style = _authoritative_visual_style(
        form_values.get("visual_style"),
        {
            "asset_id": "style-main",
            "name": "最终 Plan 视觉风格",
            "description": "严格继承最终 Plan 与创作合同",
            "prompt": "严格继承最终 Plan 与创作合同",
        },
    )
    visual_style["asset_id"] = "style-main"
    return {
        "characters": characters,
        "scenes": scenes,
        "props": props,
        "visual_style": visual_style,
    }


def _align_global_assets_to_blueprints(
    global_assets: dict[str, Any],
    scene_blueprints: list[dict[str, Any]],
    form_values: dict[str, Any],
) -> dict[str, Any]:
    """权威蓝图存在时，只保留并补齐蓝图声明的固定资产。"""

    requirements = _collect_blueprint_asset_requirements(scene_blueprints, form_values)
    aligned = {**global_assets}
    aligned["visual_style"] = _authoritative_visual_style(
        form_values.get("visual_style"),
        global_assets.get("visual_style"),
    )
    used_asset_ids: set[str] = set()
    for collection, asset_type in (("characters", "character"), ("scenes", "scene"), ("props", "prop")):
        existing = global_assets.get(collection)
        existing_items = existing if isinstance(existing, list) else []
        existing_by_name = {_asset_name_key(item.get("name")): item for item in existing_items if isinstance(item, dict) and _asset_name_key(item.get("name"))}
        aligned_assets: list[dict[str, Any]] = []
        for name in requirements[collection]:
            asset = _blueprint_asset(
                name=name,
                asset_type=asset_type,
                existing=existing_by_name.get(_asset_name_key(name)),
                form_values=form_values,
            )
            asset["asset_id"] = _unique_blueprint_asset_id(
                preferred_id=asset["asset_id"],
                asset_type=asset_type,
                name=name,
                used_asset_ids=used_asset_ids,
            )
            used_asset_ids.add(asset["asset_id"])
            aligned_assets.append(asset)
        aligned[collection] = aligned_assets
    visual_style = aligned["visual_style"]
    visual_style["asset_id"] = _unique_blueprint_asset_id(
        preferred_id=_first_text(visual_style.get("asset_id"), "style-main"),
        asset_type="style",
        name=_first_text(visual_style.get("name"), "visual-style"),
        used_asset_ids=used_asset_ids,
    )
    return aligned


def _authoritative_visual_style(value: Any, fallback: Any) -> dict[str, Any]:
    fallback_style = fallback if isinstance(fallback, dict) else {}
    if isinstance(value, dict):
        name = _first_text(value.get("name"), value.get("description"), value.get("prompt"))
        description = _first_text(value.get("description"), value.get("prompt"), name)
        prompt = _first_text(value.get("prompt"), description, name)
    else:
        name = _first_text(value)
        description = name
        prompt = name
    if not name:
        return fallback_style
    return {
        **fallback_style,
        "asset_id": _first_text(fallback_style.get("asset_id"), "style-main"),
        "name": name,
        "description": description,
        "prompt": prompt,
    }


def _collect_blueprint_asset_requirements(
    scene_blueprints: list[dict[str, Any]],
    form_values: dict[str, Any],
) -> dict[str, list[str]]:
    collected: dict[str, list[str]] = {"characters": [], "scenes": [], "props": []}
    seen: dict[str, set[str]] = {key: set() for key in collected}
    product_name = _first_text(form_values.get("product_info"), form_values.get("product_name"))
    for blueprint in scene_blueprints:
        requirements = blueprint.get("asset_requirements")
        if not isinstance(requirements, dict):
            continue
        for collection in collected:
            values = requirements.get(collection)
            if not isinstance(values, list):
                continue
            for value in values:
                name = _first_text(value)
                target_collection = collection
                if collection == "characters" and _looks_like_non_person_asset({"name": name}, product_name):
                    target_collection = "props"
                key = _asset_name_key(name)
                if not key or key in seen[target_collection]:
                    continue
                seen[target_collection].add(key)
                collected[target_collection].append(name)
    return collected


def _blueprint_asset(
    *,
    name: str,
    asset_type: str,
    existing: dict[str, Any] | None,
    form_values: dict[str, Any],
) -> dict[str, Any]:
    asset = dict(existing or {})
    asset["asset_id"] = _first_text(asset.get("asset_id"), _stable_blueprint_asset_id(asset_type, name))
    asset["name"] = name
    if asset_type == "character":
        description = _first_text(asset.get("description"), f"{name}，最终 Plan 指定人物角色，造型和身份在全片保持一致")
        asset["description"] = description
        asset["three_view_prompt"] = _ensure_three_view_prompt(
            _first_text(asset.get("three_view_prompt"), asset.get("image_prompt"), asset.get("prompt")),
            name=name,
            description=description,
            product_name=_first_text(form_values.get("product_info"), form_values.get("product_name")),
            product_category=_first_text(form_values.get("product_category")),
        )
        asset.setdefault("three_view_images", [])
        asset.pop("image_prompt", None)
        return asset

    asset_label = "场景" if asset_type == "scene" else "道具/商品"
    description = _first_text(asset.get("description"), f"{name}，最终 Plan 指定{asset_label}，外观和细节在全片保持一致")
    asset["description"] = description
    asset["image_prompt"] = _first_text(
        asset.get("image_prompt"),
        asset.get("prompt"),
        f"{name}{asset_label}参考图，主体清晰，构图稳定，细节一致，干净背景，无文字和水印",
    )
    asset.setdefault("images", [])
    asset.pop("three_view_prompt", None)
    asset.pop("three_view_images", None)
    return asset


def _stable_blueprint_asset_id(asset_type: str, name: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24]
    suffix = readable or hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{asset_type}-{suffix}"


def _unique_blueprint_asset_id(
    *,
    preferred_id: str,
    asset_type: str,
    name: str,
    used_asset_ids: set[str],
) -> str:
    stable_id = _stable_blueprint_asset_id(asset_type, name)
    candidate = _first_text(preferred_id, stable_id)
    suffix = 1
    while candidate in used_asset_ids:
        candidate = stable_id if suffix == 1 else f"{stable_id}-{suffix}"
        suffix += 1
    return candidate


def _asset_name_key(value: Any) -> str:
    return re.sub(r"\s+", "", _first_text(value)).casefold()


def _blueprint_reference_asset_ids(blueprint: dict[str, Any], global_assets: dict[str, Any]) -> list[str]:
    requirements = blueprint.get("asset_requirements")
    if not isinstance(requirements, dict):
        return []
    lookup: dict[tuple[str, str], str] = {}
    for collection in ("characters", "scenes", "props"):
        assets = global_assets.get(collection)
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name_key = _asset_name_key(asset.get("name"))
            asset_id = _first_text(asset.get("asset_id"), asset.get("id"))
            if name_key and asset_id:
                lookup[(collection, name_key)] = asset_id

    reference_ids: list[str] = []
    for collection in ("characters", "scenes", "props"):
        values = requirements.get(collection)
        if not isinstance(values, list):
            continue
        for value in values:
            name_key = _asset_name_key(value)
            asset_id = lookup.get((collection, name_key))
            if not asset_id:
                asset_id = next(
                    (lookup[(candidate_collection, name_key)] for candidate_collection in ("characters", "scenes", "props") if (candidate_collection, name_key) in lookup),
                    None,
                )
            if asset_id and asset_id not in reference_ids:
                reference_ids.append(asset_id)
    if len(reference_ids) > 9:
        scene_id = _first_text(blueprint.get("scene_id"), "unknown-scene")
        scene_index = blueprint.get("scene_index")
        raise ValueError(f"分镜 {scene_id} scene_index={scene_index} 引用资产共 {len(reference_ids)} 个，最多允许 9 个")
    return reference_ids


def _normalize_character_asset_list(
    value: Any,
    *,
    fallback: list[dict[str, Any]],
    product_name: str,
    product_category: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return fallback
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or _looks_like_non_person_asset(item, product_name):
            continue
        asset_id = _first_text(item.get("asset_id"), item.get("id"), f"character-{index}")
        name = _first_text(item.get("name"), item.get("label"), f"人物角色 {index}")
        description = _first_text(item.get("description"), item.get("role"), item.get("persona"))
        three_view_prompt = _ensure_three_view_prompt(
            _first_text(item.get("three_view_prompt"), item.get("image_prompt"), item.get("prompt")),
            name=name,
            description=description,
            product_name=product_name,
            product_category=product_category,
        )
        if name and description and three_view_prompt:
            normalized.append(
                {
                    **item,
                    "asset_id": asset_id,
                    "name": name,
                    "description": description,
                    "three_view_prompt": three_view_prompt,
                }
            )
    return normalized or fallback


def _looks_like_non_person_asset(item: dict[str, Any], product_name: str) -> bool:
    asset_id = _first_text(item.get("asset_id"), item.get("id")).lower()
    name = _first_text(item.get("name"), item.get("label"))
    description = _first_text(item.get("description"))
    prompt = _first_text(item.get("three_view_prompt"), item.get("image_prompt"), item.get("prompt"))
    text = f"{asset_id} {name} {description} {prompt}"
    normalized_product = product_name.strip()
    if asset_id.startswith("prop-") or "product" in asset_id or "prop" in asset_id:
        return True
    if normalized_product and normalized_product not in {"产品", "商品"} and normalized_product in name:
        return True
    person_markers = ("人物", "角色", "讲解者", "主讲人", "主播", "用户", "顾客", "模特", "学生", "男性", "女性", "男", "女", "person", "human")
    if any(marker in text for marker in person_markers):
        return False
    if normalized_product and normalized_product not in {"产品", "商品"} and normalized_product in text:
        return True
    non_person_markers = ("产品主体", "商品主体", "道具", "产品图", "商品图", "物件", "包装", "设备主体")
    return any(marker in text for marker in non_person_markers)


def _ensure_three_view_prompt(prompt: str, *, name: str, description: str, product_name: str = "", product_category: str = "") -> str:
    descriptor = _safe_person_descriptor(prompt=prompt, description=description, product_name=product_name, product_category=product_category)
    base = f"{name}人物角色三视图，{descriptor}，同一个人物的正面、侧面、背面三视图"
    constraints = "只出现同一个人物，服饰发型五官一致，干净背景，不出现产品、道具、文字或水印"
    return f"{base}，{constraints}"


def _safe_person_descriptor(*, prompt: str, description: str, product_name: str, product_category: str) -> str:
    forbidden_terms = {term for term in (product_name.strip(), product_category.strip()) if term and term not in {"产品", "商品", "未指定品类"}}
    object_markers = {
        "产品",
        "商品",
        "道具",
        "拿着",
        "手持",
        "背着",
        "展示",
        "包装",
        "书包",
        "耳机",
        "洗地机",
        "水杯",
        "平板",
    }
    pieces: list[str] = []
    for source in (description, prompt):
        for piece in re.split(r"[，,。；;、]", _first_text(source)):
            cleaned = piece.strip()
            if not cleaned:
                continue
            if any(term and term in cleaned for term in forbidden_terms):
                continue
            if any(marker in cleaned for marker in object_markers):
                continue
            if cleaned not in pieces:
                pieces.append(cleaned)
    return "，".join(pieces[:4]) or "电商短视频人物角色，真实自然，表情清晰"


def _normalize_global_asset_list(
    value: Any,
    *,
    fallback: list[dict[str, Any]],
    default_prefix: str,
    required_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return fallback
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        asset_id = _first_text(item.get("asset_id"), item.get("id"), f"{default_prefix}-{index}")
        cleaned = {field_name: _first_text(item.get(field_name)) for field_name in required_fields}
        if "image_prompt" in required_fields and not cleaned.get("image_prompt"):
            cleaned["image_prompt"] = _first_text(item.get("three_view_prompt"), item.get("prompt"))
        if all(cleaned.values()):
            cleaned_item = {**item, **cleaned, "asset_id": asset_id}
            cleaned_item.pop("three_view_prompt", None)
            cleaned_item.pop("three_view_images", None)
            normalized.append(cleaned_item)
    return normalized or fallback


def _normalize_visual_style(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return fallback
    name = _first_text(value.get("name"), fallback.get("name"))
    description = _first_text(value.get("description"), value.get("prompt"), fallback.get("description"))
    return {
        **fallback,
        **value,
        "asset_id": _first_text(value.get("asset_id"), value.get("id"), fallback.get("asset_id"), "style-main"),
        "name": name,
        "description": description,
        "prompt": _first_text(value.get("prompt"), fallback.get("prompt"), description),
    }


def _normalize_reference_asset_ids(value: Any, global_assets: dict[str, Any], stage_asset_id: str) -> list[str]:
    known_ids = _global_image_asset_ids(global_assets)
    raw_ids = [str(item).lstrip("@").strip() for item in value] if isinstance(value, list) else []
    reference_ids = [asset_id for asset_id in raw_ids if asset_id and asset_id in known_ids]
    if not reference_ids:
        reference_ids = _default_reference_asset_ids(global_assets, stage_asset_id)
    deduped: list[str] = []
    for asset_id in reference_ids:
        if asset_id in known_ids and asset_id not in deduped:
            deduped.append(asset_id)
    return deduped[:9]


def _normalize_shot_description(
    value: Any,
    *,
    stage: dict[str, str],
    start_ms: int,
    duration_ms: int,
    reference_asset_ids: list[str],
    global_assets: dict[str, Any],
) -> dict[str, Any]:
    fallback = _default_shot_description(
        stage=stage,
        start_ms=start_ms,
        duration_ms=duration_ms,
        reference_asset_ids=reference_asset_ids,
        global_assets=global_assets,
    )
    if not isinstance(value, dict):
        return fallback
    text = _first_text(value.get("text"), value.get("镜头描述"))
    if not text:
        text = _legacy_shot_description_text(value, fallback["text"])
    mentions = _normalize_shot_mentions(value.get("mentions"), reference_asset_ids, global_assets)
    normalized_text = bind_scene_reference_tokens(
        text,
        reference_asset_ids,
        global_assets,
    )
    return {"text": normalized_text, "mentions": mentions}


def bind_scene_reference_tokens(
    text: str,
    reference_asset_ids: list[str],
    global_assets: dict[str, Any],
) -> str:
    """按固定规则规范镜头正文，并只机械绑定已声明的全局资产引用。"""

    return _ensure_reference_asset_tokens(
        _normalize_shot_text(text),
        reference_asset_ids,
        global_assets,
    )


def _ensure_reference_asset_tokens(
    text: str,
    reference_asset_ids: list[str],
    global_assets: dict[str, Any],
) -> str:
    """确保引用图片既有结构化 mentions，也在可编辑镜头文本中可见。"""

    lookup = _global_image_asset_lookup(global_assets)
    missing: list[str] = []
    result = text
    named_assets = sorted(
        ((asset_id, _first_text(lookup.get(asset_id, {}).get("name"))) for asset_id in reference_asset_ids[:9]),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    protected_tokens: dict[str, str] = {}
    for index, (asset_id, _asset_name) in enumerate(named_assets):
        token = f"@{asset_id}"
        if token not in result:
            continue
        placeholder = f"\x00pixelflow-asset-token-{index}\x00"
        result = result.replace(token, placeholder)
        protected_tokens[placeholder] = token
    for index, (asset_id, asset_name) in enumerate(named_assets):
        if asset_name:
            result = result.replace(f"@{asset_name}", f"@{asset_id}")
            token = f"@{asset_id}"
            if token in result:
                placeholder = f"\x00pixelflow-normalized-token-{index}\x00"
                result = result.replace(token, placeholder)
                protected_tokens[placeholder] = token
    for placeholder, token in protected_tokens.items():
        result = result.replace(placeholder, token)
    for asset_id in reference_asset_ids[:9]:
        token = f"@{asset_id}"
        asset_name = _first_text(lookup.get(asset_id, {}).get("name"))
        if token in result:
            continue
        if asset_name and asset_name in result:
            result = result.replace(asset_name, token, 1)
            continue
        missing.append(token)
    if missing:
        suffix = f"参考素材：{'、'.join(missing)}。"
        result = f"{result.rstrip()} {suffix}".strip()
    return result


def _normalize_shot_mentions(value: Any, reference_asset_ids: list[str], global_assets: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = _shot_mentions(reference_asset_ids, global_assets)
    if not isinstance(value, list):
        return defaults
    lookup = _global_image_asset_lookup(global_assets)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        asset_id = _first_text(item.get("asset_id"), item.get("id"), item.get("assetId"))
        image_url = _asset_image_url(item)
        key = asset_id or image_url or f"mention-{index}"
        if not key or key in seen:
            continue
        seen.add(key)
        known = lookup.get(asset_id, {}) if asset_id else {}
        mention = {
            "asset_id": asset_id or key,
            "type": _first_text(item.get("type"), item.get("asset_type"), known.get("type"), "reference"),
            "name": _first_text(item.get("name"), item.get("label"), known.get("name"), asset_id or key),
        }
        if image_url:
            mention["image_url"] = image_url
        elif known.get("image_url"):
            mention["image_url"] = known["image_url"]
        normalized.append(mention)
        if len(normalized) >= 9:
            break
    return normalized or defaults


def _shot_mentions(reference_asset_ids: list[str], global_assets: dict[str, Any]) -> list[dict[str, Any]]:
    lookup = _global_image_asset_lookup(global_assets)
    mentions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset_id in reference_asset_ids:
        asset = lookup.get(asset_id)
        if not asset or asset_id in seen:
            continue
        seen.add(asset_id)
        mention = {
            "asset_id": asset_id,
            "type": asset["type"],
            "name": asset["name"],
        }
        if asset.get("image_url"):
            mention["image_url"] = asset["image_url"]
        mentions.append(mention)
        if len(mentions) >= 9:
            break
    return mentions


def _normalize_at_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    normalized = [_normalize_at_text(item, "") for item in value]
    return [item for item in normalized if item] or fallback


def _normalize_at_text(value: Any, fallback: str) -> str:
    text = _first_text(value)
    if not text:
        return fallback
    return text if text.startswith("@") else f"@{text}"


def _legacy_shot_description_text(value: dict[str, Any], fallback: str) -> str:
    description = _first_text(value.get("description"), value.get("镜头描述"))
    if not description:
        return fallback
    time_range = _first_text(value.get("time_range"), value.get("timeRange"))
    location = _normalize_at_text(value.get("location"), "")
    characters = _normalize_at_list(value.get("characters"), [])
    props = _normalize_at_list(value.get("props"), [])
    shot_size = _first_text(value.get("shot_size"), value.get("shotSize"), value.get("景别"))
    visual_style = _normalize_at_text(value.get("visual_style"), "")
    pieces = []
    if time_range:
        pieces.append(f"{time_range}:")
    if location:
        pieces.append(f"地点:{location} 中,")
    if characters:
        pieces.append(f"角色:{'、'.join(characters)}")
    pieces.append(description)
    if props:
        pieces.append(f"道具:{'、'.join(props)}")
    if shot_size:
        pieces.append(f"景别:{shot_size}")
    if visual_style:
        pieces.append(f"视觉风格:{visual_style}")
    return "".join(pieces)


def _normalize_shot_text(text: str) -> str:
    normalized = _first_text(text)
    normalized = _normalize_shot_time_ranges(normalized)
    for label in ("地点", "角色", "道具", "视觉风格"):
        normalized = re.sub(rf"({label})\s*[：:]\s*@([\w\-\u4e00-\u9fff]+)", r"\1:@\2", normalized)
        normalized = re.sub(rf"({label})\s*@([\w\-\u4e00-\u9fff]+)", r"\1:@\2", normalized)
    return normalized


def _normalize_shot_time_ranges(text: str) -> str:
    """把镜头描述里的毫秒/毫秒时间码归一成用户可读的秒级范围。"""

    def ms_to_second(value: str) -> int:
        number = max(0, int(value))
        return 0 if number == 0 else math.ceil(number / 1000)

    def timecode_to_second(minutes: str, seconds: str, millis: str) -> int:
        total = max(0, int(minutes)) * 60 + max(0, int(seconds))
        return total + (1 if int(millis.ljust(3, "0")[:3]) > 0 else 0)

    def replace_timecode(match: re.Match[str]) -> str:
        start = timecode_to_second(match.group(1), match.group(2), match.group(3))
        end = timecode_to_second(match.group(4), match.group(5), match.group(6))
        if end <= start:
            end = start + 1
        return f"{start}-{end}秒"

    def replace_ms_pair(match: re.Match[str]) -> str:
        start = ms_to_second(match.group(1))
        end = ms_to_second(match.group(2))
        if end <= start:
            end = start + 1
        return f"{start}-{end}秒"

    normalized = re.sub(
        r"\b(\d{1,3}):(\d{2})\.(\d{1,3})\s*[-~—–]\s*(\d{1,3}):(\d{2})\.(\d{1,3})\b",
        replace_timecode,
        text,
    )
    normalized = re.sub(r"\b(\d+)\s*ms\s*[-~—–]\s*(\d+)\s*ms\b", replace_ms_pair, normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(\d+)\s*[-~—–]\s*(\d+)\s*ms\b", replace_ms_pair, normalized, flags=re.IGNORECASE)
    return normalized.replace("毫秒", "秒")


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
    product_category = _first_text(form_values.get("product_category"), "商品")
    name = "主讲人"
    description = f"适合{target_audience}信任的电商短视频讲解者，镜头表现自然可信"
    return {
        "name": name,
        "description": description,
        "three_view_prompt": _ensure_three_view_prompt("", name=name, description=description, product_category=product_category),
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


def _default_global_assets(
    *,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    stage_templates: list[dict[str, str]],
) -> dict[str, Any]:
    product_name = _first_text(form_values.get("product_info"), form_values.get("product_name"), selected_direction.get("product_name"), "产品")
    product_category = _first_text(form_values.get("product_category"), selected_direction.get("product_category"), "商品")
    target_audience = _first_text(form_values.get("target_audience"), selected_direction.get("target_audience"), "目标用户")
    scene_assets: list[dict[str, Any]] = []
    seen_scene_ids: set[str] = set()
    for stage in stage_templates:
        asset_id = stage["asset_id"]
        if asset_id in seen_scene_ids:
            continue
        seen_scene_ids.add(asset_id)
        description = stage["scene_description"].format(product_name=product_name)
        scene_assets.append(
            {
                "asset_id": asset_id,
                "name": stage["name"],
                "description": description,
                "image_prompt": f"{description}，9:16，真实摄影，电商广告质感，主体一致，画面干净",
                "images": [],
            }
        )
    return {
        "characters": [
            {
                "asset_id": "character-presenter",
                "name": "主讲人",
                "description": f"适合{target_audience}信任的电商短视频讲解者，镜头表现自然可信",
                "three_view_prompt": _ensure_three_view_prompt(
                    "",
                    name="主讲人",
                    description=f"适合{target_audience}信任的电商短视频讲解者，镜头表现自然可信",
                    product_name=product_name,
                    product_category=product_category,
                ),
                "three_view_images": [],
            },
            {
                "asset_id": "character-user",
                "name": "目标用户",
                "description": f"{target_audience}中的真实用户代表，用于表达使用前后的情绪变化",
                "three_view_prompt": _ensure_three_view_prompt(
                    "",
                    name="目标用户",
                    description=f"{target_audience}中的真实用户代表，用于表达使用前后的情绪变化",
                    product_name=product_name,
                    product_category=product_category,
                ),
                "three_view_images": [],
            },
        ],
        "scenes": scene_assets,
        "props": [
            {
                "asset_id": "prop-product",
                "name": product_name,
                "description": f"{product_category}商品主体，道具外观在所有场景保持一致",
                "image_prompt": f"{product_name}产品道具图，干净背景，细节清晰，颜色和外观稳定",
                "images": [],
            }
        ],
        "visual_style": {
            "asset_id": "style-main",
            "name": "真实摄影电商广告",
            "description": "整片统一真实摄影质感，光线自然，产品主体清晰，电商转化氛围明确",
            "prompt": "真实摄影、电商广告质感、主体稳定、色彩自然、避免文字乱码和无关物体乱入",
        },
    }


def _exact_scene_durations_ms(target_duration_ms: Any) -> tuple[int, list[int]]:
    if isinstance(target_duration_ms, bool):
        raise ValueError("target video duration must use integer seconds")
    try:
        duration_ms = int(target_duration_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("target video duration must use integer seconds") from exc
    if duration_ms % 1000 != 0:
        raise ValueError("target video duration must not contain milliseconds")
    durations = [duration * 1000 for duration in split_video_duration(duration_ms // 1000)]
    return duration_ms, durations


def _resolve_scene_schedule(
    target_duration_ms: Any,
    scene_blueprints: list[dict[str, Any]] | None,
) -> tuple[int, list[int], list[dict[str, Any]]]:
    duration_ms, fallback_durations = _exact_scene_durations_ms(target_duration_ms)
    if not scene_blueprints:
        return duration_ms, fallback_durations, []
    normalized = normalize_scene_blueprints(
        scene_blueprints,
        total_duration_sec=duration_ms // 1000,
        allow_legacy_global_shot_ranges=True,
    )
    durations = [int(item["duration_sec"]) * 1000 for item in normalized]
    return duration_ms, durations, normalized


def _stage_templates(scene_count: int) -> list[dict[str, str]]:
    base = [
        {
            "asset_id": "scene-opening",
            "name": "开场钩子",
            "story_prefix": "用高对比痛点或结果快速抓住注意力",
            "narration": "还在为{product_category}选择纠结？先看{product_name}解决的这个关键问题。",
            "scene_description": "{product_name}在真实使用场景中被快速展示，开场有强注意力焦点",
            "shot_size": "中近景",
            "shot_action": "讲解者快速展示产品，产品主体和核心卖点第一时间进入画面",
        },
        {
            "asset_id": "scene-proof",
            "name": "卖点证明",
            "story_prefix": "通过产品细节、使用过程和前后对比证明核心卖点",
            "narration": "{product_name}的核心优势在这里，画面直接展示效果，不靠空喊。",
            "scene_description": "{product_name}的核心功能和细节被近景展示，画面清晰稳定",
            "shot_size": "近景",
            "shot_action": "镜头贴近产品细节和使用动作，用连续画面证明核心卖点",
        },
        {
            "asset_id": "scene-conversion",
            "name": "转化收口",
            "story_prefix": "把卖点落到购买或行动理由，并给出明确转化指令",
            "narration": "想了解更多细节，现在就进入直播间，完成{conversion_goal}。",
            "scene_description": "{product_name}与行动提示同框，结尾有清晰转化氛围",
            "shot_size": "中景",
            "shot_action": "产品和行动提示同框出现，讲解者完成收口引导",
        },
    ]
    if scene_count <= len(base):
        return base[:scene_count]
    extras = [
        {
            "asset_id": f"scene-extra-{index}",
            "name": f"补充证明 {index}",
            "story_prefix": "补充一个真实使用理由，增强信任",
            "narration": "{product_name}在更多真实场景里同样适用，减少用户决策顾虑。",
            "scene_description": "{product_name}在补充使用场景中自然出现，保持主体一致",
            "shot_size": "中景",
            "shot_action": "用一个补充使用场景自然展示产品，强调稳定一致",
        }
        for index in range(1, scene_count - len(base) + 1)
    ]
    return [base[0], *extras, *base[1:]]


def _default_shot_description(
    *,
    stage: dict[str, str],
    start_ms: int,
    duration_ms: int,
    reference_asset_ids: list[str],
    global_assets: dict[str, Any],
) -> dict[str, Any]:
    location_id = next((asset_id for asset_id in reference_asset_ids if asset_id.startswith("scene-")), stage["asset_id"])
    character_ids = [asset_id for asset_id in reference_asset_ids if asset_id.startswith("character-")] or ["character-presenter"]
    prop_ids = [asset_id for asset_id in reference_asset_ids if asset_id.startswith("prop-")] or ["prop-product"]
    time_range = _format_time_range(start_ms, start_ms + duration_ms)
    character_text = "、".join(f"@{asset_id}" for asset_id in character_ids)
    prop_text = "、".join(f"@{asset_id}" for asset_id in prop_ids)
    visual_style = global_assets.get("visual_style")
    visual_style_name = _first_text(visual_style.get("name") if isinstance(visual_style, dict) else None, "真实摄影电商广告")
    return {
        "text": (f"{time_range}: 地点:@{location_id} 中,角色:{character_text} {stage.get('shot_action') or stage['scene_description']},道具:{prop_text} 保持清晰可见。景别:{stage.get('shot_size', '中景')}。视觉风格:{visual_style_name}。"),
        "mentions": _shot_mentions(reference_asset_ids, global_assets),
    }


def _default_reference_asset_ids(global_assets: dict[str, Any], stage_asset_id: str) -> list[str]:
    known_ids = _global_image_asset_ids(global_assets)
    character_id = _first_asset_id(global_assets, "characters", "character-presenter")
    prop_id = _first_asset_id(global_assets, "props", "prop-product")
    reference_ids = [character_id, stage_asset_id, prop_id]
    return [asset_id for asset_id in reference_ids if asset_id in known_ids][:9]


def _first_asset_id(global_assets: dict[str, Any], collection: str, fallback: str) -> str:
    value = global_assets.get(collection)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                asset_id = _first_text(item.get("asset_id"), item.get("id"))
                if asset_id:
                    return asset_id
    return fallback


def _global_image_asset_ids(global_assets: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for collection in ("characters", "scenes", "props"):
        value = global_assets.get(collection)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                asset_id = _first_text(item.get("asset_id"), item.get("id"))
                if asset_id:
                    ids.add(asset_id)
    return ids


def _global_image_asset_lookup(global_assets: dict[str, Any]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    collection_types = {
        "characters": "character",
        "scenes": "scene",
        "props": "prop",
    }
    for collection, asset_type in collection_types.items():
        value = global_assets.get(collection)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            asset_id = _first_text(item.get("asset_id"), item.get("id"))
            if not asset_id:
                continue
            lookup[asset_id] = {
                "asset_id": asset_id,
                "type": asset_type,
                "name": _first_text(item.get("name"), item.get("label"), item.get("description"), asset_id),
                "image_url": _asset_image_url(item),
            }
    return lookup


def _asset_image_url(value: dict[str, Any]) -> str:
    direct = _first_text(
        value.get("image_url"),
        value.get("imageUrl"),
        value.get("url"),
        value.get("download_url"),
        value.get("downloadUrl"),
    )
    if direct:
        return direct
    for key in ("images", "image_urls", "three_view_images", "threeViewImages"):
        images = value.get(key)
        if not isinstance(images, list):
            continue
        for image in images:
            if isinstance(image, str) and image.strip():
                return image.strip()
            if isinstance(image, dict):
                nested = _first_text(image.get("url"), image.get("download_url"), image.get("downloadUrl"))
                if nested:
                    return nested
    return ""


def _format_time_range(start_ms: int, end_ms: int) -> str:
    start_seconds = max(0, start_ms) // 1000
    end_seconds = math.ceil(max(0, end_ms) / 1000)
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1
    return f"{start_seconds}-{end_seconds}秒"


def _format_timecode(ms: int) -> str:
    total_seconds, millis = divmod(max(0, ms), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


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
    shot_description: dict[str, Any],
    visual_style: dict[str, Any],
    video_ratio: str,
) -> str:
    shot_prompt = _build_prompt_from_scene_fields(storyline, shot_description, narration, visual_style)
    return (
        f"{stage_name}。生成一段{video_ratio}电商短视频片段，产品是{product_name}，品类是{product_category}，"
        f"目标人群是{target_audience}，转化目标是{conversion_goal}。创意方向：{direction_title}。"
        f"{direction_description}。{shot_prompt}。"
        "要求主体、商品颜色、道具和场景在前后镜头中保持一致；避免人物畸形、手部变形、字幕乱码、无关物体乱入。"
    )


def _build_prompt_from_scene_fields(
    storyline: str,
    shot_description: dict[str, Any],
    narration: str,
    visual_style: Any,
) -> str:
    visual_style_text = ""
    if isinstance(visual_style, dict):
        visual_style_text = _first_text(visual_style.get("prompt"), visual_style.get("description"), visual_style.get("name"))
    else:
        visual_style_text = _first_text(visual_style)
    shot_text = _shot_description_text(shot_description)
    shot_parts = [f"镜头描述：{shot_text}", f"视觉风格：{visual_style_text}"]
    if narration:
        shot_parts.append(f"旁白：{narration}")
    return f"故事线：{storyline}。" + "；".join(part for part in shot_parts if not part.endswith("："))


def _scene_visual_style_prompt(visual_style: Any) -> str:
    """场景包 prompt 仅保存视觉风格，其他创作内容由结构化字段承载。"""
    if isinstance(visual_style, dict):
        value = _first_text(
            visual_style.get("prompt"),
            visual_style.get("description"),
            visual_style.get("name"),
        )
    else:
        value = _first_text(visual_style)
    return f"视觉风格：{value}" if value else ""


def build_authoritative_scene_prompt(
    storyline: str,
    shot_description: dict[str, Any],
    narration: str,
    visual_style: Any,
    *,
    video_model: str,
) -> str:
    """只根据权威分镜字段构造后续执行提示词。"""

    model_name = _first_text(video_model)
    if not model_name:
        raise ValueError("权威场景包执行提示词缺少 video_model")
    prompt = _build_prompt_from_scene_fields(
        storyline,
        shot_description,
        narration,
        visual_style,
    )
    return f"视频模型：{model_name}。{prompt}"


def _shot_description_text(shot_description: dict[str, Any]) -> str:
    text = _first_text(shot_description.get("text"))
    if text:
        return text
    return _legacy_shot_description_text(shot_description, "")


def _summarize_plan(plan_markdown: str) -> str:
    lines = [re.sub(r"^[#\-\d.、\s]+", "", line).strip() for line in str(plan_markdown or "").splitlines()]
    candidates = [line for line in lines if line and not line.startswith("|")]
    return candidates[0][:80] if candidates else "plan.md中的创作方案"


def extract_material_image_urls(materials: list[dict[str, Any]]) -> list[str]:
    """按固定字段顺序提取去重后的素材图片 URL。"""

    urls: list[str] = []
    for material in materials:
        for key in ("url", "image_url", "imageUrl", "download_url", "downloadUrl", "artifact_url", "artifactUrl"):
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
