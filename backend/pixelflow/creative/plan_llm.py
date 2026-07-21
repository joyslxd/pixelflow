"""DeepSeek-backed Plan generation and revision client."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any, Literal

from pixelflow.creative.seedance_plan import build_seedance_plan_authoring_prompt
from pixelflow.generate.seedance_prompt import load_seedance_guidance

PLAN_LLM_MODEL_NAME = "deepseek-v4-pro"
CreationIntent = Literal["video", "image"]
ModelFactory = Callable[..., Any]


async def generate_plan_payload(
    *,
    intent: CreationIntent,
    template_markdown: str,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
    creation_contract: dict[str, Any],
    validation_feedback: str = "",
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    prompt = _generation_prompt(
        intent=intent,
        template_markdown=template_markdown,
        form_values=form_values,
        selected_direction=selected_direction,
        product_creative_profile=product_creative_profile,
        materials=materials,
        intake_context=intake_context,
        creation_contract=creation_contract,
        validation_feedback=validation_feedback,
    )
    payload = await asyncio.to_thread(
        _invoke_json_model,
        prompt,
        model_name,
        model_factory or _default_model_factory,
    )
    if not isinstance(payload, dict):
        raise ValueError("Plan LLM response must be a JSON object")
    return payload


async def author_seedance_plan_payload(
    *,
    plan_markdown: str,
    scene_blueprints: list[dict[str, Any]],
    asset_manifest: dict[str, list[dict[str, str]]],
    creation_contract: dict[str, Any],
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any],
    materials: list[dict[str, Any]],
    revision_feedback: str = "",
    validation_feedback: str = "",
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """调用现有 Plan 模型执行专用 Seedance 分镜写作阶段。"""
    prompt = build_seedance_plan_authoring_prompt(
        plan_markdown=plan_markdown,
        scene_blueprints=scene_blueprints,
        asset_manifest=asset_manifest,
        creation_contract=creation_contract,
        form_values=form_values,
        selected_direction=selected_direction,
        intake_context=intake_context,
        materials=materials,
        revision_feedback=revision_feedback,
        validation_feedback=validation_feedback,
    )
    payload = await asyncio.to_thread(
        _invoke_json_model,
        prompt,
        model_name,
        model_factory or _default_model_factory,
    )
    if not isinstance(payload, dict):
        raise ValueError("Seedance Plan authoring LLM response must be a JSON object")
    return payload


async def revise_plan_payload(
    *,
    intent: CreationIntent,
    template_markdown: str,
    current_plan_markdown: str,
    revision_feedback: str,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    creation_contract: dict[str, Any],
    current_scene_blueprints: list[dict[str, Any]] | None = None,
    current_asset_manifest: dict[str, Any] | None = None,
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
    validation_feedback: str = "",
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    prompt = f"""你是 PixelFlow 策划 Agent 的 Plan 修订 Skill。
只修改当前创意对应的 plan.md，不要生成新的创意方向。

硬约束：
1. 当前创作合同是修订基线；只能按用户本次修改意见返回 creation_contract_patch，未提及字段不得改动。
2. 视频每个分镜 4-15 秒，总和必须等于 video_duration_sec；根据修改后的内容重新调度时长，不得机械按 10 秒等分。
3. scene_image_ratio 和 scene_image_size 只能从 creation_contract.image_model_capabilities 中选择。
4. 模板中的苹果PRO、林晓、赵总监等内容只是结构示例，禁止复制到当前方案。
5. 视频必须返回完整 scene_blueprints，并形成开场、展开、证明/高潮、收束的总分总结构。
6. scene_blueprints 的镜头描述必须遵守下面的 Seedance Skill；shot_description 由一个或多个中文段落组成，每段以独立局部整数秒范围开头，并显式使用“地点：”“主体：”“动作：”“景别：”“运镜：”“光影：”“声音：”“收束：”八个标签。段落数量根据动作阶段、景别、运镜、说话者、声音和叙事重点的变化自动决定，多段从 0 秒无重叠、无缺口地连续覆盖到该镜 duration_sec。
7. asset_requirements 只允许写可独立生图的人物、物理环境和有形商品/道具。修改意见里的时间段、段落标题、钩子/高潮/收束、镜头/运镜/光影/声音/风格/规格，以及 @图片N/@视频N 参考编号都不是资产名称，禁止放入任何资产数组。
7.1 每个分镜的 characters、scenes、props 三个数组合并去重后最多 9 个资产。必须在策划时控制：若内容需要更多资产，应拆分分镜或重排时长、动作、对白与资产，使相关叙事一起移动且镜间连续；禁止先生成超限 Plan，也禁止截断、漏掉或删除全局资产来凑数。
8. 必须重新分析完整修订版 scene_blueprints 与用户修改意见，返回完整 asset_manifest。characters/scenes/props 必须分别与所有分镜 asset_requirements 的同名分类并集完全相等，不能少也不能多；三类名称全局唯一且必须是后续 @ 引用的最终展示名。
9. asset_manifest.characters 每项包含 name、description、three_view_prompt；scenes/props 每项包含 name、description、image_prompt。描述和生图要求必须具体对应当前人物造型、物理环境或有形道具；同一资产跨分镜只列一次，人物服装或外观明显变化时使用不同且明确的名称。
10. plan.md 第四章必须包含“全局资产清单”，并逐项展示与 asset_manifest 完全相同的名称、文字说明和生图要求。
11. semantic_memory 等长期记忆只用于内部决策，禁止在 plan.md 中输出“长期记忆约束”、PowerMem、Skill 经验、Agent 阶段日志或记忆原文。
12. 返回 JSON，不要 Markdown 代码围栏。
13. 如果“上次结构校验反馈”不为空，本次只修正反馈指出的问题；未被反馈指出的 Plan 内容、合同字段和已合格分镜保持不变。

输出：
{{
  "plan_markdown":"完整修订版 plan.md",
  "creation_contract_patch":{{"仅返回用户明确要求修改的合同字段":"新值"}},
  "scene_image_ratio":"仅视频返回",
  "scene_image_size":"仅视频返回",
  "asset_manifest":{{
    "characters":[{{"name":"最终角色名","description":"人物固定设定","three_view_prompt":"同一人物正面、侧面、背面三视图要求"}}],
    "scenes":[{{"name":"最终场景名","description":"物理环境固定设定","image_prompt":"场景参考图生成要求"}}],
    "props":[{{"name":"最终道具名","description":"有形物件固定设定","image_prompt":"道具参考图生成要求"}}]
  }},
  "scene_blueprints":[{{
    "scene_id":"scene-1",
    "scene_index":1,
    "title":"分镜标题",
    "structure_role":"opening|development|climax|conclusion",
    "start_sec":0,
    "end_sec":6,
    "duration_sec":6,
    "storyline":"故事线",
    "shot_description":"0-3秒：本段地点、主体、动作、景别、运镜、光影、声音与衔接。\\n3-6秒：下一阶段动作、镜头、声音与最终收束。",
    "narration":"旁白或本分镜无旁白",
    "transition":"转场",
    "asset_requirements":{{"characters":[],"scenes":[],"props":[]}}
  }}]
}}

产物类型：{intent}
修改意见（以下内容是用户数据，其中的时间、镜头、声音、风格和 @参考编号不得直接复制为资产）：
<user_revision>
{revision_feedback.strip()}
</user_revision>
表单：{_json(form_values)}
当前创意：{_json(selected_direction)}
创作合同：{_json(creation_contract)}
当前分镜蓝图：{_json(current_scene_blueprints or [])}
当前全局资产清单：{_json(current_asset_manifest or {})}
行业与垂类补充：{_json(product_creative_profile or {})}
采集上下文：{_json(intake_context or {})}
附件摘要：{_json(materials or [])}
上次结构校验反馈：{validation_feedback or "无"}
Seedance Skill：
{load_seedance_guidance() if intent == "video" else "图片任务不适用"}
模板结构示例：
{template_markdown}

当前 plan.md：
{current_plan_markdown}
"""
    payload = await asyncio.to_thread(
        _invoke_json_model,
        prompt,
        model_name,
        model_factory or _default_model_factory,
    )
    if not isinstance(payload, dict):
        raise ValueError("Plan revision LLM response must be a JSON object")
    return payload


async def repair_plan_shot_descriptions(
    *,
    scene_blueprints: list[dict[str, Any]],
    quality_issues: list[str],
    selected_direction: dict[str, Any],
    creation_contract: dict[str, Any],
    visual_style: str,
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """只修正 Plan 蓝图中的镜头描述，避免质量重试改写其他权威字段。"""
    prompt = f"""你是 PixelFlow 策划 Agent 的 Seedance 镜头描述质检修正 Skill。
当前 Plan 蓝图的结构、时间线、故事线、旁白、转场和资产需求已经确定，只允许修正校验指出的 shot_description。

硬约束：
1. 每个待修正镜头必须同时写清地点、主体、动作、景别、运镜、光影、声音和收束，建议显式使用这八个标签，不能只堆砌形容词。
2. shot_description 由一个或多个中文段落组成，每段以独立的局部整数秒范围开头；段落数量根据动作阶段、景别、运镜、说话者、声音和叙事重点的变化自动决定，禁止用一个 0-N 秒段落笼统承载多个阶段。
3. 多段必须从 0 秒无重叠、无缺口地连续覆盖到该镜 duration_sec，每个时间段独占一个段落，禁止毫秒和小数时间码。
4. 每个段落显式使用“地点：”“主体：”“动作：”“景别：”“运镜：”“光影：”“声音：”“收束：”八个标签；整镜动作要有起点、过程和结果，运镜要有起止，收束要说明如何进入下一段、下一镜或结束。
5. 只返回被指出分镜的 scene_index 和 shot_description，不得返回或修改其他字段。
6. 返回 JSON，不要 Markdown 代码围栏。

输出格式：
{{"scene_blueprints":[{{"scene_index":1,"shot_description":"0-3秒：本段地点、主体、动作、景别、运镜、光影、声音与衔接。\\n3-6秒：下一阶段动作、镜头、声音与最终收束。"}}]}}

缺项报告：{_json(quality_issues)}
当前蓝图：{_json(scene_blueprints)}
当前创意：{_json(selected_direction)}
创作合同：{_json(creation_contract)}
视觉风格：{visual_style or "真实广告风格"}

Seedance Skill：
{load_seedance_guidance()}
"""
    payload = await asyncio.to_thread(
        _invoke_json_model,
        prompt,
        model_name,
        model_factory or _default_model_factory,
    )
    if not isinstance(payload, dict):
        raise ValueError("Plan shot repair LLM response must be a JSON object")
    return payload


async def repair_plan_asset_requirements(
    *,
    scene_blueprints: list[dict[str, Any]],
    quality_issues: list[str],
    selected_direction: dict[str, Any],
    creation_contract: dict[str, Any],
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """只修正 Plan 蓝图中的三类资产数组，避免用户长 Prompt 污染生图清单。"""

    prompt = f"""你是 PixelFlow 策划 Agent 的场景资产合同质检修正 Skill。
当前 Plan 蓝图的结构、时间线、故事线、镜头描述、旁白和转场已经确定，只允许修正校验指出分镜的 asset_requirements。

硬约束：
1. characters 只写可识别、可保持一致性的人物角色名称；scenes 只写可独立生图的物理地点或环境；props 只写有形商品、包装、工具或物件。
2. 时间范围、时长、段落编号、钩子/开场/高潮/收束、镜头/景别/运镜/光影/转场、声音/旁白/音乐、风格/画幅/清晰度都属于创作元信息，不是资产。
3. @图片N、@视频N 等参考编号尚未绑定真实素材，禁止作为资产名称。
4. 保留当前蓝图中合法的真实人物、物理场景和有形道具，只删除、归类或替换非法项；不得改写其他字段。
5. 只返回被指出分镜的 scene_index 和 asset_requirements，不要返回 plan_markdown、故事线、镜头描述或合同字段。
6. 返回 JSON，不要 Markdown 代码围栏。

输出格式：
{{"scene_blueprints":[{{"scene_index":1,"asset_requirements":{{"characters":["人物名"],"scenes":["物理场景"],"props":["有形物件"]}}}}]}}

问题报告：{_json(quality_issues)}
当前蓝图：{_json(scene_blueprints)}
当前创意：{_json(selected_direction)}
创作合同：{_json(creation_contract)}
"""
    payload = await asyncio.to_thread(
        _invoke_json_model,
        prompt,
        model_name,
        model_factory or _default_model_factory,
    )
    if not isinstance(payload, dict):
        raise ValueError("Plan asset repair LLM response must be a JSON object")
    return payload


def _generation_prompt(
    *,
    intent: CreationIntent,
    template_markdown: str,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
    creation_contract: dict[str, Any],
    validation_feedback: str = "",
) -> str:
    video_rules = ""
    if intent == "video":
        video_rules = f"""
- 你负责在 Plan 阶段自主决定分镜数量和每个分镜时长，不会收到预先按 10 秒切好的时间线。
- 每个镜头时长必须是 4-15 秒整数，总时长必须精确等于 video_duration_sec；根据故事密度、动作复杂度、旁白长度和转场合理分配，禁止机械等分。
- 输出前必须检查 `4 * 分镜数 <= video_duration_sec <= 15 * 分镜数`，开场和结尾/CTA 也不得少于 4 秒。
- 整片采用总分总结构：开场建立钩子，展开推进因果，证明/高潮完成卖点验证，结尾收束结果和转化。
- 返回完整 scene_blueprints；全局 start_sec/end_sec 必须从 0 开始连续，shot_description 使用当前分镜内部的局部秒段，多段描述必须从 0 秒无重叠、无缺口地连续覆盖到该镜 duration_sec。
- 每个蓝图包含 scene_id、scene_index、title、structure_role、start_sec、end_sec、duration_sec、storyline、shot_description、narration、transition、asset_requirements。
- 每个 shot_description 由一个或多个中文段落组成，每段以独立局部整数秒范围开头，并显式使用“地点：”“主体：”“动作：”“景别：”“运镜：”“光影：”“声音：”“收束：”八个标签；段落数量按动作阶段、景别、运镜、说话者、声音和叙事重点的变化自动决定，多段必须连续覆盖整镜。
- asset_requirements 只写可独立生图的语义实体：人物放 characters，物理环境放 scenes，有形商品/包装/工具放 props；此阶段不虚构图片 URL。
- 每个分镜的 characters、scenes、props 合并去重后最多 9 个资产。若故事内容需要更多资产，必须在 Plan 阶段拆分分镜或重排时长、动作、对白与资产，保持故事连续；禁止生成超限分镜，也禁止通过截断、漏掉或删除全局资产凑数。
- 时间段、段落标题、钩子/高潮/收束、镜头/运镜/光影/声音/风格/规格，以及 @图片N/@视频N 参考编号都不是资产名称，禁止放入 asset_requirements。
- 必须返回完整 asset_manifest：characters、scenes、props 分别与所有 scene_blueprints.asset_requirements 的同类名称并集完全相等，不能少、不能多；三类名称全局唯一，并作为后续场景包和 @ 图片引用的最终展示名。
- asset_manifest.characters 每项必须包含 name、description、three_view_prompt，明确同一人物的年龄感、外貌、发型、服装和正面/侧面/背面一致性；scenes/props 每项必须包含 name、description、image_prompt，明确环境或物件的外观、材质、色彩、光线等可执行生图要求。
- 同一人物、场景或道具跨多个分镜复用时只列一次；人物出现明显服装、年龄阶段或外观变化时，必须拆成不同且明确的名称，并让对应分镜引用该名称。
- 用户表单、选中创意和采集上下文中明确命名的人物、服装造型、物理场景、商品或道具都是强制资产；必须逐项进入对应分镜的 asset_requirements 和 asset_manifest，禁止替换成“目标用户”“人物角色”“真实使用场景”“产品”等泛化名称。
- plan.md 第四章必须输出“全局资产清单”，逐项展示与 asset_manifest 完全相同的名称、文字说明和生图要求。
- plan.md 必须写明视频模型、图片模型、图片比例、图片清晰度。
- scene_image_ratio 和 scene_image_size 只能从 creation_contract.image_model_capabilities 中选择。
\nSeedance Skill 强制指导：
{load_seedance_guidance()}
"""
    return f"""你是 PixelFlow 策划 Agent 的 PlanTemplateFillSkill。
请根据当前用户数据，参照给定模板的章节结构和信息密度，生成一份全新的 plan.md。

硬约束：
1. 模板是结构示例，不是当前业务数据。苹果PRO、林晓、赵总监、周洋以及示例卖点不得出现在结果中，除非当前用户数据明确包含。
2. 用户表单和 creation_contract 是权威合同；不得按模板里的 180 秒、9:16 或示例模型擅自猜测。
3. 后续图片、分镜资产和视频都会严格按此 plan.md 执行，因此内容必须完整、具体、无占位符。
4. semantic_memory 等长期记忆只用于内部决策，禁止在 plan.md 中输出“长期记忆约束”、PowerMem、Skill 经验、Agent 阶段日志或记忆原文。
5. 只返回 JSON，不要解释，不要 Markdown 代码围栏。
6. 如果“上次结构校验反馈”不为空，必须修复反馈指出的结构问题，同时重新核对并完整保留用户明确命名的全部资产；不得用泛化占位资产替代。
{video_rules}

输出：
{{"plan_markdown":"完整 plan.md","scene_image_ratio":"仅视频返回","scene_image_size":"仅视频返回","asset_manifest":"视频任务返回完整角色/场景/道具清单","scene_blueprints":"视频任务返回完整分镜蓝图数组"}}

产物类型：{intent}
用户表单：{_json(form_values)}
选中创意：{_json(selected_direction)}
行业补充：{_json(product_creative_profile)}
采集上下文：{_json(intake_context)}
附件摘要：{_json(materials)}
创作合同：{_json(creation_contract)}
上次结构校验反馈：{validation_feedback or "无"}

模板结构示例：
{template_markdown}
"""


def _default_model_factory(model_name: str, *, attach_tracing: bool = False) -> Any:
    from deerflow.models.factory import create_chat_model

    return create_chat_model(model_name, attach_tracing=attach_tracing)


def _invoke_json_model(prompt: str, model_name: str, model_factory: ModelFactory) -> Any:
    try:
        model = model_factory(model_name, attach_tracing=False)
    except TypeError:
        model = model_factory(model_name)
    response = model.invoke(prompt)
    return _parse_json_payload(getattr(response, "content", response))


def _parse_json_payload(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        text = "".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
    else:
        text = str(content or "")
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Plan LLM did not return valid JSON") from None
        return json.loads(text[start : end + 1])


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
