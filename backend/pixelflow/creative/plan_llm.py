"""DeepSeek-backed Plan generation and revision client."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any, Literal

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
    scene_timeline: list[dict[str, int]],
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
        scene_timeline=scene_timeline,
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


async def revise_plan_payload(
    *,
    intent: CreationIntent,
    template_markdown: str,
    current_plan_markdown: str,
    revision_feedback: str,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    creation_contract: dict[str, Any],
    scene_timeline: list[dict[str, int]],
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    prompt = f"""你是 PixelFlow 策划 Agent 的 Plan 修订 Skill。
只修改当前创意对应的 plan.md，不要生成新的创意方向。

硬约束：
1. 用户确认过的创作合同不可被擅自覆盖；视频镜头必须严格使用给定时间线。
2. 视频每个分镜 4-15 秒，总和必须等于 video_duration_sec。
3. scene_image_ratio 和 scene_image_size 只能从 creation_contract.image_model_capabilities 中选择。
4. 模板中的苹果PRO、林晓、赵总监等内容只是结构示例，禁止复制到当前方案。
5. 返回 JSON，不要 Markdown 代码围栏。

输出：
{{"plan_markdown":"完整修订版 plan.md","scene_image_ratio":"仅视频返回","scene_image_size":"仅视频返回"}}

产物类型：{intent}
修改意见：{revision_feedback.strip()}
表单：{_json(form_values)}
当前创意：{_json(selected_direction)}
创作合同：{_json(creation_contract)}
精确镜头时间线：{_json(scene_timeline)}
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
    scene_timeline: list[dict[str, int]],
) -> str:
    video_rules = ""
    if intent == "video":
        video_rules = """
- 必须严格采用“精确镜头时间线”，不得增加、删除、重叠或改变任何镜头时长。
- 每个镜头时长必须是 4-15 秒，总时长必须精确等于 video_duration_sec。
- plan.md 必须写明视频模型、图片模型、图片比例、图片清晰度。
- scene_image_ratio 和 scene_image_size 只能从 creation_contract.image_model_capabilities 中选择。
"""
    return f"""你是 PixelFlow 策划 Agent 的 PlanTemplateFillSkill。
请根据当前用户数据，参照给定模板的章节结构和信息密度，生成一份全新的 plan.md。

硬约束：
1. 模板是结构示例，不是当前业务数据。苹果PRO、林晓、赵总监、周洋以及示例卖点不得出现在结果中，除非当前用户数据明确包含。
2. 用户表单和 creation_contract 是权威合同；不得按模板里的 180 秒、9:16 或示例模型擅自猜测。
3. 后续图片、分镜资产和视频都会严格按此 plan.md 执行，因此内容必须完整、具体、无占位符。
4. 只返回 JSON，不要解释，不要 Markdown 代码围栏。
{video_rules}

输出：
{{"plan_markdown":"完整 plan.md","scene_image_ratio":"仅视频返回","scene_image_size":"仅视频返回"}}

产物类型：{intent}
用户表单：{_json(form_values)}
选中创意：{_json(selected_direction)}
行业补充：{_json(product_creative_profile)}
采集上下文：{_json(intake_context)}
附件摘要：{_json(materials)}
创作合同：{_json(creation_contract)}
精确镜头时间线：{_json(scene_timeline)}

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
