"""Plan template filling, LLM planning, versioning, and strict contracts."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pixelflow.creative.contract import VideoCreationContract, build_video_creation_contract, resolve_scene_image_spec
from pixelflow.creative.duration import scene_time_ranges, split_video_duration
from pixelflow.creative.plan_llm import PLAN_LLM_MODEL_NAME, ModelFactory, generate_plan_payload, revise_plan_payload
from pixelflow.memory import semantic_memory_text

CreationIntent = Literal["video", "image"]

TEMPLATE_DIRECTORY = Path(__file__).resolve().parents[2] / "skills" / "public" / "borgrise-creative-assistant-v2" / "templates"
VIDEO_PLAN_TEMPLATE_PATH = TEMPLATE_DIRECTORY / "plan_video.md"
IMAGE_PLAN_TEMPLATE_PATH = TEMPLATE_DIRECTORY / "plan_image.md"
PLAN_TEMPLATE_PATH = VIDEO_PLAN_TEMPLATE_PATH

_REQUIRED_TEMPLATE_SECTIONS = {
    "video": ("## 一、选题方向", "## 三、视频规格", "## 五、镜头列表"),
    "image": ("## 一、选题方向", "## 三、图片规格", "## 五、主图方案"),
}
_TEMPLATE_SAMPLE_ENTITIES = ("苹果PRO", "林晓", "赵总监", "周洋")


@dataclass(frozen=True)
class PlanMarkdownResult:
    output_type: CreationIntent
    plan_markdown: str
    template_path: Path
    consistency_issues: list[str] = field(default_factory=list)
    review_timeout_sec: int | None = None
    plan_version: int = 1
    plan_history: list[dict[str, Any]] = field(default_factory=list)
    creation_contract: dict[str, Any] = field(default_factory=dict)
    scene_durations_sec: list[int] = field(default_factory=list)
    llm_used: bool = False
    model_name: str = PLAN_LLM_MODEL_NAME
    error: str | None = None
    restored_from_version: int | None = None

    def __post_init__(self) -> None:
        if not self.plan_history:
            object.__setattr__(
                self,
                "plan_history",
                [
                    _history_entry(
                        self.plan_version,
                        self.plan_markdown,
                        self.restored_from_version,
                        creation_contract=self.creation_contract,
                        scene_durations_sec=self.scene_durations_sec,
                    )
                ],
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_type": self.output_type,
            "plan_markdown": self.plan_markdown,
            "template_path": self.template_path.as_posix(),
            "consistency_issues": self.consistency_issues,
            "review_timeout_sec": self.review_timeout_sec,
            "plan_version": self.plan_version,
            "plan_history": self.plan_history,
            "creation_contract": self.creation_contract,
            "scene_durations_sec": self.scene_durations_sec,
            "llm_used": self.llm_used,
            "model_name": self.model_name,
            "error": self.error,
            "restored_from_version": self.restored_from_version,
        }

    def next_version(
        self,
        *,
        plan_markdown: str,
        plan_history: list[dict[str, Any]] | None = None,
        current_version: int | None = None,
        restored_from_version: int | None = None,
        llm_used: bool | None = None,
        error: str | None = None,
        creation_contract: dict[str, Any] | None = None,
        change_source: str | None = None,
    ) -> PlanMarkdownResult:
        history = _normalized_history(plan_history or self.plan_history)
        history_max = max((int(item["version"]) for item in history), default=0)
        version = max(1, int(current_version or self.plan_version), history_max) + 1
        next_contract = copy.deepcopy(
            creation_contract if creation_contract is not None else self.creation_contract
        )
        next_durations = copy.deepcopy(self.scene_durations_sec)
        history.append(
            _history_entry(
                version,
                plan_markdown,
                restored_from_version,
                creation_contract=next_contract,
                scene_durations_sec=next_durations,
                change_source=change_source,
            )
        )
        return replace(
            self,
            plan_markdown=plan_markdown,
            plan_version=version,
            plan_history=history,
            restored_from_version=restored_from_version,
            llm_used=self.llm_used if llm_used is None else llm_used,
            error=error,
            creation_contract=next_contract,
            scene_durations_sec=next_durations,
        )


def publish_manual_plan_edit(
    *,
    intent: CreationIntent,
    edited_plan_markdown: str,
    current_plan_version: int,
    plan_history: list[dict[str, Any]],
    creation_contract: dict[str, Any] | None = None,
    scene_durations_sec: list[int] | None = None,
) -> PlanMarkdownResult:
    """把用户编辑稿原样发布为新的权威 Plan 版本，不调用 LLM。"""
    markdown = str(edited_plan_markdown or "").strip()
    if not markdown:
        raise ValueError("plan.md 内容不能为空")

    contract = copy.deepcopy(creation_contract or {})
    durations = copy.deepcopy(scene_durations_sec or [])
    if intent == "video":
        validated_contract = VideoCreationContract.model_validate(contract)
        contract = validated_contract.model_dump(exclude_none=True)
        expected_duration = validated_contract.video_duration_sec
        if not durations:
            durations = split_video_duration(expected_duration)
        if (
            any(not isinstance(value, int) or isinstance(value, bool) or not 4 <= value <= 15 for value in durations)
            or sum(durations) != expected_duration
        ):
            raise ValueError("当前 Plan 的分镜时长快照与制作合同不一致，请重新生成 Plan")

    base = PlanMarkdownResult(
        output_type=intent,
        plan_markdown=markdown,
        template_path=_template_path(intent),
        plan_version=max(1, current_plan_version),
        plan_history=_normalized_history(plan_history),
        creation_contract=contract,
        scene_durations_sec=durations,
        llm_used=False,
    )
    return base.next_version(
        plan_markdown=markdown,
        plan_history=plan_history,
        current_version=current_plan_version,
        creation_contract=contract,
        change_source="manual_edit",
        llm_used=False,
    )
def build_plan_markdown(
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
) -> PlanMarkdownResult:
    """Build the deterministic fallback Plan with the same production contract."""
    template_path, _ = _load_template(intent)
    profile = _merged_profile(product_creative_profile or {}, intake_context or {})
    issues = _consistency_issues(intent, form_values, selected_direction)
    if intent == "video":
        contract, durations, corrections = _video_contract_and_durations(form_values)
        markdown = _fallback_video_plan(
            form_values,
            selected_direction,
            profile,
            materials or [],
            intake_context or {},
            contract,
            durations,
        )
        return PlanMarkdownResult(
            output_type=intent,
            plan_markdown=markdown,
            template_path=template_path,
            consistency_issues=[*issues, *corrections],
            creation_contract=contract.model_dump(exclude_none=True),
            scene_durations_sec=durations,
        )
    markdown = _fallback_image_plan(form_values, selected_direction, profile, materials or [], intake_context or {})
    return PlanMarkdownResult(
        output_type=intent,
        plan_markdown=markdown,
        template_path=template_path,
        consistency_issues=issues,
        creation_contract=_image_creation_contract(form_values, intake_context or {}),
    )


async def build_plan_markdown_with_llm(
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
    *,
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> PlanMarkdownResult:
    template_path, template_markdown = _load_template(intent)
    profile = _merged_profile(product_creative_profile or {}, intake_context or {})
    context = intake_context or {}
    issues = _consistency_issues(intent, form_values, selected_direction)
    contract: VideoCreationContract | None = None
    durations: list[int] = []
    scene_timeline: list[dict[str, int]] = []
    creation_contract = _image_creation_contract(form_values, context)
    if intent == "video":
        contract = build_video_creation_contract(form_values)
        durations = split_video_duration(contract.video_duration_sec)
        scene_timeline = _scene_timeline(durations)
        creation_contract = contract.model_dump(exclude_none=True)
    try:
        payload = await generate_plan_payload(
            intent=intent,
            template_markdown=template_markdown,
            form_values=form_values,
            selected_direction=selected_direction,
            product_creative_profile=profile,
            materials=materials or [],
            intake_context=context,
            creation_contract=creation_contract,
            scene_timeline=scene_timeline,
            model_name=model_name,
            model_factory=model_factory,
        )
        markdown = _validated_llm_markdown(intent, payload, form_values, selected_direction, context)
        corrections: list[str] = []
        if contract is not None:
            contract, corrections = resolve_scene_image_spec(
                contract,
                _text(payload.get("scene_image_ratio")),
                _text(payload.get("scene_image_size")),
            )
            creation_contract = contract.model_dump(exclude_none=True)
        markdown = _with_execution_contract(intent, markdown, creation_contract, durations)
        return PlanMarkdownResult(
            output_type=intent,
            plan_markdown=markdown,
            template_path=template_path,
            consistency_issues=[*issues, *corrections],
            creation_contract=creation_contract,
            scene_durations_sec=durations,
            llm_used=True,
            model_name=model_name,
        )
    except Exception as exc:  # noqa: BLE001 - Plan generation must degrade to a valid contract
        fallback = build_plan_markdown(intent, form_values, selected_direction, profile, materials, context)
        return replace(fallback, error=str(exc), model_name=model_name)


async def revise_plan_markdown_with_llm(
    *,
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    current_plan_markdown: str,
    current_plan_version: int,
    plan_history: list[dict[str, Any]],
    revision_feedback: str,
    creation_contract: dict[str, Any] | None = None,
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> PlanMarkdownResult:
    template_path, template_markdown = _load_template(intent)
    base = build_plan_markdown(intent, form_values, selected_direction)
    contract: VideoCreationContract | None = None
    durations: list[int] = []
    timeline: list[dict[str, int]] = []
    authoritative_contract = dict(creation_contract or base.creation_contract)
    if intent == "video":
        contract = VideoCreationContract.model_validate(authoritative_contract or build_video_creation_contract(form_values).model_dump())
        durations = split_video_duration(contract.video_duration_sec)
        timeline = _scene_timeline(durations)
    try:
        payload = await revise_plan_payload(
            intent=intent,
            template_markdown=template_markdown,
            current_plan_markdown=current_plan_markdown,
            revision_feedback=revision_feedback,
            form_values=form_values,
            selected_direction=selected_direction,
            creation_contract=authoritative_contract,
            scene_timeline=timeline,
            model_name=model_name,
            model_factory=model_factory,
        )
        markdown = _validated_llm_markdown(intent, payload, form_values, selected_direction, {})
        corrections: list[str] = []
        if contract is not None:
            contract, corrections = resolve_scene_image_spec(
                contract,
                _text(payload.get("scene_image_ratio")) or contract.scene_image_ratio,
                _text(payload.get("scene_image_size")) or contract.scene_image_size,
            )
            authoritative_contract = contract.model_dump(exclude_none=True)
        markdown = _with_execution_contract(intent, markdown, authoritative_contract, durations)
        revised = replace(
            base,
            template_path=template_path,
            consistency_issues=corrections,
            creation_contract=authoritative_contract,
            scene_durations_sec=durations,
            llm_used=True,
            model_name=model_name,
        )
        return revised.next_version(
            plan_markdown=markdown,
            plan_history=plan_history,
            current_version=current_plan_version,
            llm_used=True,
            creation_contract=authoritative_contract,
        )
    except Exception as exc:  # noqa: BLE001
        markdown = f"{current_plan_markdown.rstrip()}\n\n## 本次修改意见\n\n{revision_feedback.strip()}"
        markdown = _with_execution_contract(intent, markdown, authoritative_contract, durations)
        return replace(base, error=str(exc), model_name=model_name).next_version(
            plan_markdown=markdown,
            plan_history=plan_history,
            current_version=current_plan_version,
            llm_used=False,
            error=str(exc),
            creation_contract=authoritative_contract,
        )


def restore_plan_version(
    *,
    intent: CreationIntent,
    current_plan_markdown: str,
    current_plan_version: int,
    plan_history: list[dict[str, Any]],
    restore_version: int,
    creation_contract: dict[str, Any] | None = None,
    scene_durations_sec: list[int] | None = None,
) -> PlanMarkdownResult:
    history = _normalized_history(plan_history)
    source = next((item for item in history if int(item.get("version") or 0) == restore_version), None)
    if source is None:
        raise ValueError(f"plan.md v{restore_version} 不存在，无法回退")
    source_contract = source.get("creation_contract")
    source_durations = source.get("scene_durations_sec")
    resolved_contract = (
        copy.deepcopy(source_contract)
        if "creation_contract" in source and isinstance(source_contract, dict)
        else copy.deepcopy(creation_contract or {})
    )
    resolved_durations = _resolve_history_scene_durations(
        intent,
        source,
        source_durations,
        resolved_contract,
        scene_durations_sec,
    )
    return PlanMarkdownResult(
        output_type=intent,
        plan_markdown=str(source.get("plan_markdown") or ""),
        template_path=_template_path(intent),
        plan_version=restore_version,
        plan_history=history,
        creation_contract=resolved_contract,
        scene_durations_sec=resolved_durations,
        restored_from_version=restore_version,
    )


def _video_contract_and_durations(form_values: dict[str, Any]) -> tuple[VideoCreationContract, list[int], list[str]]:
    contract = build_video_creation_contract(form_values)
    durations = split_video_duration(contract.video_duration_sec)
    contract, corrections = resolve_scene_image_spec(
        contract,
        _text(form_values.get("scene_image_ratio")) or contract.video_ratio,
        _text(form_values.get("scene_image_size")) or "4K",
    )
    return contract, durations, corrections


def _fallback_video_plan(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
    contract: VideoCreationContract,
    durations: list[int],
) -> str:
    product = _context_text_value(intake_context, "product_subject") or _text(form_values.get("product_info"), "未命名产品")
    category = _text(form_values.get("product_category"), "未分类")
    audience = _text(form_values.get("target_audience"), "目标用户")
    conversion_goal = _text(form_values.get("conversion_goal"), "完成转化")
    direction_title = _text(selected_direction.get("title"), "推荐创意方向")
    direction_description = _text(selected_direction.get("description"), "围绕产品卖点组织完整方案。")
    visual_style = contract.visual_style or "真实广告风格"
    anchor = _visual_anchor(selected_direction, profile)
    memory = _memory_summary(profile, intake_context)
    ranges = scene_time_ranges(durations)
    scene_lines = []
    for index, ((start, end), duration) in enumerate(zip(ranges, durations, strict=True), start=1):
        stage = _scene_stage(index, len(durations))
        scene_lines.append(
            f"- 镜头{index}-「{_timecode(start)}-{_timecode(end)}」\n"
            f"  - 画面：{stage}，围绕 {product} 与 {anchor} 推进，采用 {visual_style}，严格持续 {duration} 秒。\n"
            f"  - 文案：围绕「{direction_description}」推进当前信息点；旁白与音效服务 {conversion_goal}。"
        )
    markdown = f"""# {product} — {direction_title}

## 一、选题方向

{direction_description}

产品定位：{product} = 面向 {audience} 的 {category} 内容主角。
产品剧情角色：作为解决问题、证明卖点和推动转化的关键要素。
系列记忆句：看见需求，想到 {product}。

## 二、选题优势

- **爆点机制**：开场建立冲突，中段完成产品证明，结尾收口到 {conversion_goal}
- **人群**：{audience}
- **依据**：{anchor}；素材：{_material_summary(materials)}
- **长期记忆约束**：{memory or "暂无，按本次表单执行"}
- **转化逻辑链**：痛点 -> 产品介入 -> 效果证明 -> {conversion_goal}

## 三、视频规格

- 任务类型：{contract.video_usage}
- 画幅：{contract.video_ratio}
- 时长：{contract.video_duration_sec} 秒
- 时间轴：00:00-{_timecode(contract.video_duration_sec)}
- 视频模型：{contract.video_model}
- 图片模型：{contract.image_model}
- 风格：{visual_style}
- 转化目标：{conversion_goal}

## 四、角色列表

- 主角：{audience} 中具有代表性的人物，只生成真实人物三视图。
- 产品/商品：{product}，作为道具资产，不放入人物角色栏目。
- 场景与道具：围绕 {anchor} 规划，保持全片视觉一致。

## 五、镜头列表

{chr(10).join(scene_lines)}

## 背景音乐

- 前段：建立注意力和冲突。
- 中段：推动产品证明。
- 后段：完成 {conversion_goal} 收口。

## 前3秒钩子

用 {audience} 的高频痛点和明确动作建立悬念，并在 3 秒内让用户理解观看理由。
"""
    return _with_execution_contract("video", markdown, contract.model_dump(exclude_none=True), durations)


def _fallback_image_plan(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
) -> str:
    goal = _context_text_value(intake_context, "creation_goal") or _text(form_values.get("image_goal"), "图片创作目标")
    subject = _context_text_value(intake_context, "product_subject") or goal
    direction_title = _text(selected_direction.get("title"), "推荐创意方向")
    direction_description = _text(selected_direction.get("description"), "围绕主体建立清晰主视觉。")
    usage = _text(form_values.get("image_usage"), "内容发布")
    style = _text(form_values.get("image_style"), "真实摄影")
    size = _text(form_values.get("image_size"), "自动适配")
    image_type = _text(form_values.get("image_type"), "图片")
    anchor = _visual_anchor(selected_direction, profile)
    memory = _memory_summary(profile, intake_context)
    count = _context_int_value(intake_context, "requested_output_count") or _positive_int(form_values.get("image_count"), 1)
    original_prompt = _context_text_value(intake_context, "source_prompt")
    industry_type = _context_text_value(intake_context, "industry_type") or "general"
    markdown = f"""# {goal}｜{direction_title}

## 一、选题方向

{direction_description}

原始需求：{original_prompt or "未提供"}
产品主体：{subject}
创作目标：{goal}
行业类型：{industry_type}
核心表达：围绕 {subject}，用 {anchor} 建立可直接用于 {usage} 的成品视觉。
产品定位：{subject} = 当前画面的唯一核心主体。
记忆句：一眼看到重点，一张图完成表达。

## 二、选题优势

- **爆点机制**：主体聚焦、卖点清楚、风格统一
- **人群与用途**：{usage}
- **素材依据**：{_material_summary(materials)}
- **长期记忆约束**：{memory or "暂无，按本次表单执行"}

## 三、图片规格

- 任务类型：{image_type}
- 尺寸：{size}
- 生成数量：{count} 张
- 用途：{usage}
- 风格：{style}

## 四、画面元素

- 主视觉主体：{subject}
- 场景环境：围绕 {anchor} 组织
- 信息元素：标题、辅助文案或视觉标签按用途保留
- 道具关系：只保留能帮助表达卖点的元素

## 五、主图方案

### 方案A：{direction_title}

**画面**：{direction_description}，主体清晰，构图稳定，符合 {style}。
**主标题**：围绕 {goal} 提炼 18 字以内标题。
**CTA**：根据 {usage} 给出明确行动提示。

## 六、视觉重点

- 主体和核心卖点必须可识别。
- 避免无关文字、水印和虚假承诺。
- 所有生成结果严格继承本方案的用途、风格、尺寸和数量。

## 七、图片钩子

通过 {anchor} 在第一眼建立主题和记忆点。
"""
    return _with_execution_contract("image", markdown, _image_creation_contract(form_values, intake_context), [])


def _with_execution_contract(
    intent: CreationIntent,
    markdown: str,
    creation_contract: dict[str, Any],
    durations: list[int],
) -> str:
    base = markdown.split("\n## 制作执行合同", 1)[0].rstrip()
    if intent == "video":
        ranges = scene_time_ranges(durations)
        timeline = "\n".join(f"- 分镜{index}：{_timecode(start)}-{_timecode(end)}，{duration} 秒" for index, ((start, end), duration) in enumerate(zip(ranges, durations, strict=True), start=1))
        contract_block = f"""## 制作执行合同

- 视频总时长：{creation_contract.get("video_duration_sec")} 秒
- 视频画幅：{creation_contract.get("video_ratio")}
- 视频模型：{creation_contract.get("video_model")}
- 视频清晰度：{creation_contract.get("video_size")}
- 图片模型：{creation_contract.get("image_model")}
- 图片比例：{creation_contract.get("scene_image_ratio")}
- 图片清晰度：{creation_contract.get("scene_image_size")}
- 视频用途：{creation_contract.get("video_usage")}
- 视觉风格：{creation_contract.get("visual_style") or "由当前 Plan 统一约束"}
- 执行规则：角色、场景、道具图片及全部分镜视频必须继承本合同；不得改用其他模型或比例。

### 精确分镜时间线

{timeline}
"""
    else:
        contract_block = f"""## 制作执行合同

- 图片目标：{creation_contract.get("image_goal")}
- 图片类型：{creation_contract.get("image_type")}
- 图片用途：{creation_contract.get("image_usage")}
- 图片风格：{creation_contract.get("image_style")}
- 图片尺寸：{creation_contract.get("image_size")}
- 生成数量：{creation_contract.get("image_count")} 张
- 执行规则：后续图片生成必须严格继承当前 plan.md 和本合同。
"""
    return f"{base}\n\n{contract_block.strip()}\n"


def _validated_llm_markdown(
    intent: CreationIntent,
    payload: dict[str, Any],
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any],
) -> str:
    markdown = _text(payload.get("plan_markdown"))
    if not markdown:
        raise ValueError("Plan LLM response is missing plan_markdown")
    missing = [section for section in _REQUIRED_TEMPLATE_SECTIONS[intent] if section not in markdown]
    if missing:
        raise ValueError(f"Plan LLM response is missing sections: {', '.join(missing)}")
    allowed_text = json.dumps([form_values, selected_direction, intake_context], ensure_ascii=False, default=str)
    leaked = [entity for entity in _TEMPLATE_SAMPLE_ENTITIES if entity in markdown and entity not in allowed_text]
    if leaked:
        raise ValueError(f"Plan LLM copied template sample entities: {', '.join(leaked)}")
    return markdown


def _load_template(intent: CreationIntent) -> tuple[Path, str]:
    path = _template_path(intent)
    text = path.read_text(encoding="utf-8")
    missing = [section for section in _REQUIRED_TEMPLATE_SECTIONS[intent] if section not in text]
    if missing:
        raise ValueError(f"{path.name} 模板缺少固定章节：{', '.join(missing)}")
    return path, text


def _template_path(intent: CreationIntent) -> Path:
    return VIDEO_PLAN_TEMPLATE_PATH if intent == "video" else IMAGE_PLAN_TEMPLATE_PATH


def _consistency_issues(intent: CreationIntent, form_values: dict[str, Any], selected_direction: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not selected_direction.get("direction_id"):
        issues.append("缺少 selected_direction.direction_id")
    if not selected_direction.get("title"):
        issues.append("缺少 selected_direction.title")
    required = ("product_info", "product_category", "target_audience", "conversion_goal") if intent == "video" else ("image_goal", "image_type", "image_usage", "image_style", "image_size")
    for field_name in required:
        if not _text(form_values.get(field_name)):
            issues.append(f"{intent} 表单缺少 {field_name}")
    return issues


def _image_creation_contract(form_values: dict[str, Any], intake_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": "image",
        "image_goal": _context_text_value(intake_context, "creation_goal") or _text(form_values.get("image_goal")),
        "image_type": _text(form_values.get("image_type")),
        "image_usage": _text(form_values.get("image_usage")),
        "image_style": _text(form_values.get("image_style")),
        "image_size": _text(form_values.get("image_size"), "自动适配"),
        "image_count": _context_int_value(intake_context, "requested_output_count") or _positive_int(form_values.get("image_count"), 1),
    }


def _scene_timeline(durations: list[int]) -> list[dict[str, int]]:
    return [{"scene_index": index, "start_sec": start, "end_sec": end, "duration_sec": duration} for index, ((start, end), duration) in enumerate(zip(scene_time_ranges(durations), durations, strict=True), start=1)]


def _scene_stage(index: int, count: int) -> str:
    ratio = index / max(1, count)
    if index == 1:
        return "以强动作和明确痛点开场"
    if ratio <= 0.3:
        return "补充人物目标与使用场景，逐步升级问题"
    if ratio <= 0.65:
        return "让产品自然介入并展示关键使用过程"
    if ratio <= 0.85:
        return "用细节、对比或反馈证明产品价值"
    return "收束结果并给出清晰行动提示"


def _history_entry(
    version: int,
    plan_markdown: str,
    restored_from_version: int | None = None,
    *,
    creation_contract: dict[str, Any] | None = None,
    scene_durations_sec: list[int] | None = None,
    change_source: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "version": version,
        "plan_markdown": plan_markdown,
        "creation_contract": copy.deepcopy(creation_contract or {}),
        "scene_durations_sec": copy.deepcopy(scene_durations_sec or []),
    }
    if restored_from_version is not None:
        item["restored_from_version"] = restored_from_version
    if change_source:
        item["change_source"] = change_source
    return item


def _normalized_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in history:
        if not isinstance(item, dict):
            continue
        version = _positive_int(item.get("version"), 0)
        markdown = _text(item.get("plan_markdown"))
        if version <= 0 or not markdown or version in seen:
            continue
        seen.add(version)
        result.append(copy.deepcopy(item))
    return sorted(result, key=lambda item: int(item["version"]))


def _resolve_history_scene_durations(
    intent: CreationIntent,
    source: dict[str, Any],
    source_durations: Any,
    resolved_contract: dict[str, Any],
    fallback_durations: list[int] | None,
) -> list[int]:
    if "scene_durations_sec" not in source or not isinstance(source_durations, list):
        return copy.deepcopy(fallback_durations or [])
    if intent == "video":
        expected_duration = resolved_contract.get("video_duration_sec")
        is_valid_duration = isinstance(expected_duration, int) and not isinstance(expected_duration, bool)
        is_valid_scenes = all(
            isinstance(value, int) and not isinstance(value, bool) and 4 <= value <= 15
            for value in source_durations
        )
        if not is_valid_duration or not is_valid_scenes or sum(source_durations) != expected_duration:
            return copy.deepcopy(fallback_durations or [])
    try:
        return [int(value) for value in source_durations]
    except (TypeError, ValueError):
        return copy.deepcopy(fallback_durations or [])


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value)


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _context_text_value(intake_context: dict[str, Any], key: str) -> str:
    return _text(intake_context.get(key))


def _context_int_value(intake_context: dict[str, Any], key: str) -> int | None:
    value = _positive_int(intake_context.get(key), 0)
    return max(1, min(10, value)) if value else None


def _merged_profile(product_creative_profile: dict[str, Any], intake_context: dict[str, Any]) -> dict[str, Any]:
    context_profile = intake_context.get("product_creative_profile")
    return {**context_profile, **product_creative_profile} if isinstance(context_profile, dict) else product_creative_profile


def _visual_anchor(selected_direction: dict[str, Any], product_creative_profile: dict[str, Any]) -> str:
    data = selected_direction.get("data")
    if isinstance(data, dict) and _text(data.get("visual_anchor")):
        return _text(data.get("visual_anchor"))
    anchors = product_creative_profile.get("visual_anchor_keywords")
    if isinstance(anchors, list):
        normalized = "、".join(_text(item) for item in anchors[:3] if _text(item))
        if normalized:
            return normalized
    return "产品质感、真实使用、转化动作"


def _memory_summary(product_creative_profile: dict[str, Any], intake_context: dict[str, Any]) -> str:
    return semantic_memory_text(product_creative_profile.get("semantic_memory")) or semantic_memory_text(intake_context.get("semantic_memory"))


def _material_summary(materials: list[dict[str, Any]]) -> str:
    return f"{len(materials)} 个可引用素材" if materials else "暂无额外素材，按表单和创意方向执行"


def _timecode(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{secs:02d}"
