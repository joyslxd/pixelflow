"""PixelFlow v2 plan.md 模板填充纯逻辑。

这个模块对应设计文档里的 PlanTemplateFillSkill 和 PlanConsistencyCheckSkill
的本地确定性实现。它读取项目内固定模板路径，输出前端可审核的 Markdown，
不调用 LLM、数据库或博观接口。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CreationIntent = Literal["video", "image"]

PLAN_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "public"
    / "borgrise-creative-assistant-v2"
    / "templates"
    / "plan.md"
)


@dataclass(frozen=True)
class PlanMarkdownResult:
    output_type: CreationIntent
    plan_markdown: str
    template_path: Path = PLAN_TEMPLATE_PATH
    consistency_issues: list[str] = field(default_factory=list)
    review_timeout_sec: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_type": self.output_type,
            "plan_markdown": self.plan_markdown,
            "template_path": self.template_path.as_posix(),
            "consistency_issues": self.consistency_issues,
            "review_timeout_sec": self.review_timeout_sec,
        }


def build_plan_markdown(
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
) -> PlanMarkdownResult:
    _ensure_template_available()
    issues = _consistency_issues(intent, form_values, selected_direction)
    context = intake_context or {}
    profile = _merged_profile(product_creative_profile or {}, context)
    if intent == "image":
        markdown = _build_image_plan(form_values, selected_direction, profile, materials or [], context)
    else:
        markdown = _build_video_plan(form_values, selected_direction, profile, materials or [], context)
    return PlanMarkdownResult(output_type=intent, plan_markdown=markdown, consistency_issues=issues)


def _ensure_template_available() -> None:
    text = PLAN_TEMPLATE_PATH.read_text(encoding="utf-8")
    required_sections = ["## 一、选题方向", "## 十、开发输出要求"]
    missing = [section for section in required_sections if section not in text]
    if missing:
        raise ValueError(f"plan.md 模板缺少固定章节：{', '.join(missing)}")


def _consistency_issues(intent: CreationIntent, form_values: dict[str, Any], selected_direction: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not selected_direction.get("direction_id"):
        issues.append("缺少 selected_direction.direction_id")
    if not selected_direction.get("title"):
        issues.append("缺少 selected_direction.title")
    if intent == "video":
        for field_name in ["product_info", "product_category", "target_audience", "conversion_goal"]:
            if not _text(form_values.get(field_name)):
                issues.append(f"视频表单缺少 {field_name}")
    if intent == "image":
        for field_name in ["image_goal", "image_type", "image_usage", "image_style", "image_size"]:
            if not _text(form_values.get(field_name)):
                issues.append(f"图片表单缺少 {field_name}")
    return issues


def _build_video_plan(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
) -> str:
    product = _context_text_value(intake_context, "product_subject") or _text(form_values.get("product_info"), "未命名产品")
    original_prompt = _context_text_value(intake_context, "source_prompt")
    industry_type = _context_text_value(intake_context, "industry_type")
    creation_goal = _context_text_value(intake_context, "creation_goal")
    category = _text(form_values.get("product_category"), "未分类")
    audience = _text(form_values.get("target_audience"), "目标用户")
    goal = _text(form_values.get("conversion_goal"), "完成转化")
    direction_title = _text(selected_direction.get("title"), "推荐创意方向")
    direction_description = _text(selected_direction.get("description"), "围绕产品卖点组织完整创作方案。")
    visual_anchor = _visual_anchor(selected_direction, product_creative_profile)
    material_summary = _material_summary(materials)
    duration_seconds = _infer_duration_seconds(form_values, selected_direction, product_creative_profile, materials)
    shot_ranges = _shot_ranges(duration_seconds)
    return f"""# {product}｜{direction_title}

## 一、选题方向

原始需求：{original_prompt or "未提供"}  
产品主体：{product}  
创作目标：{creation_goal or product}  
行业类型：{industry_type or category}  
内容类型：AD 投放短视频。  
人物/场景冲突：{audience} 在高频使用场景中遇到明确痛点，需要一个可信解决方案。  
产品/商品能力：{product} 作为 {category} 产品，用 {visual_anchor} 建立记忆点。  
结果反转：从问题焦虑转为可感知的解决结果，并自然导向 {goal}。

产品定位：{product} = 面向 {audience} 的 {category} 转化型内容主角。  
产品剧情角色：解决方案和关键道具，负责推动冲突解决和结果证明。  
系列记忆句：看见问题，马上想到 {product}。

---

## 二、选题优势

- **爆点机制**：前三秒抛出真实痛点；中段放大使用压力；产品在解决节点首次露出；用结果证明降低犹豫；结尾引导 {goal}
- **人群**：{audience}｜A3 兴趣到 A4 转化
- **依据**：表单品类为 {category}，创意方向为「{direction_title}」，素材基础为 {material_summary}
- **转化逻辑链**：冲突起点 -> 问题升级 -> 产品介入 -> 效果证明 -> 用户信任并执行 {goal}
- **产品剧情检验**：通过 -- 去掉 {product} 后，剧情无法完成反转和转化收口
- **系列延展性**：可系列化；可延展到通勤场景、家庭场景、直播间预热场景

---

## 三、视频规格

- 任务类型：AD 投放短视频
- 画幅：9:16 竖屏
- 时长：{duration_seconds} 秒
- 时间轴：00:00-{_timecode(duration_seconds)}
- 风格：信息流广告风格
- 调性：真实、紧凑、可信、有转化推动力
- 投放平台：抖音
- 转化目标：{goal}
- 投放方式：信息流广告

---

## 四、角色列表

- 主角用户：{audience}，真实生活状态，表达当前痛点和犹豫，承担代入作用
- 旁白/字幕：清晰指出问题、卖点和行动提示，承担节奏推进作用
- 场景环境：围绕 {visual_anchor} 组织画面，承担可信背景作用
- {product}：外观清晰、卖点明确、视觉锚点为 {visual_anchor}，在剧情中承担解决方案作用

---

## 五、镜头列表

- 镜头1-「{shot_ranges[0]}」
  - 画面：近景展示目标用户遇到痛点，构图紧凑，光线真实，快速制造停留理由
  - 文案：你是不是也遇到过这个问题？音效：轻微提示音

- 镜头2-「{shot_ranges[1]}」
  - 画面：切到具体使用场景，展示问题升级和用户犹豫
  - 文案：问题不是忍一忍就过去，而是需要一个更直接的解决方式。音效：节奏推进

- 镜头3-「{shot_ranges[2]}」
  - 画面：{product} 首次清晰露出，围绕 {visual_anchor} 展示核心卖点
  - 文案：这就是我现在用的 {product}。音效：产品露出提示

- 镜头4-「{shot_ranges[3]}」
  - 画面：连续展示产品使用过程、细节和结果反馈
  - 文案：重点不是夸张承诺，而是把真实变化看清楚。音效：节奏加快

- 镜头5-「{shot_ranges[4]}」
  - 画面：产品定格和行动入口同屏出现，收束到 {goal}
  - 文案：想要同款体验，现在就去了解 {product}。音效：收束提示音

---

## 六、背景音乐

- 前半段「{_timecode(0)}-{_timecode(max(1, round(duration_seconds * 0.27)))}」：轻微紧张感节奏，制造冲突和停留
- 中段「{_timecode(max(1, round(duration_seconds * 0.27)))}-{_timecode(max(2, round(duration_seconds * 0.73)))}」：节奏逐渐加快，推动产品证明过程
- 后段「{_timecode(max(2, round(duration_seconds * 0.73)))}-{_timecode(duration_seconds)}」：清晰明亮的收束音乐，完成转化收口

---

## 七、前3秒钩子

用 {audience} 的真实痛点作为强冲突开场，让用户立刻判断“这说的是我”，并愿意继续看产品如何解决。

---

## 八、产品露出设计

- 首次露出时间：00:08
- 露出方式：问题解决时自然出现
- 露出画面：产品外观、核心功能点、使用动作和结果反馈
- 露出目的：推动剧情、证明卖点、建立信任、引导 {goal}

---

## 九、转化收口

- 转化动作：{goal}
- 转化话术：想要更快解决这个问题，现在就去了解 {product}
- 转化画面：产品图、行动入口、关键卖点和使用结果同屏
- 注意事项：避免夸大承诺，避免虚假优惠，避免绝对化违规词

---

## 十、开发输出要求

生成该 plan.md 后，后续创作生成 Agent 必须以本方案为权威合同。镜头列表必须按时间轴执行，每个镜头都要包含镜头编号、时间段、画面描述、文案/台词/旁白和音效。创意方向说明：{direction_description}
"""


def _infer_duration_seconds(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any],
    materials: list[dict[str, Any]],
) -> int:
    text_parts = [
        _text(form_values.get("duration")),
        _text(form_values.get("duration_seconds")),
        _text(product_creative_profile.get("core_message")),
        _text(product_creative_profile.get("duration")),
        _text(selected_direction.get("title")),
        _text(selected_direction.get("description")),
        _material_summary(materials),
    ]
    text = "\n".join(part for part in text_parts if part)
    minute_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分|minute|minutes|min)", text, flags=re.IGNORECASE)
    if minute_match:
        return _clamp_duration_seconds(float(minute_match.group(1)) * 60)
    second_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec|secs|second|seconds)", text, flags=re.IGNORECASE)
    if second_match:
        return _clamp_duration_seconds(float(second_match.group(1)))
    return 30


def _clamp_duration_seconds(value: float) -> int:
    return max(1, min(180, round(value)))


def _shot_ranges(duration_seconds: int) -> list[str]:
    cut_points = [
        0,
        max(1, round(duration_seconds * 0.10)),
        max(2, round(duration_seconds * 0.27)),
        max(3, round(duration_seconds * 0.47)),
        max(4, round(duration_seconds * 0.73)),
        duration_seconds,
    ]
    for index in range(1, len(cut_points)):
        if cut_points[index] <= cut_points[index - 1]:
            cut_points[index] = cut_points[index - 1] + 1
    cut_points[-1] = duration_seconds
    return [f"{_timecode(cut_points[index])}-{_timecode(cut_points[index + 1])}" for index in range(5)]


def _timecode(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def _build_image_plan(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
) -> str:
    image_goal = _context_text_value(intake_context, "creation_goal") or _text(form_values.get("image_goal"), "图片创作目标")
    product_subject = _context_text_value(intake_context, "product_subject") or image_goal
    original_prompt = _context_text_value(intake_context, "source_prompt")
    industry_type = _context_text_value(intake_context, "industry_type")
    requested_count = _context_int_value(intake_context, "requested_output_count") or 1
    image_type = _text(form_values.get("image_type"), "图片")
    usage = _text(form_values.get("image_usage"), "内容使用")
    style = _text(form_values.get("image_style"), "自由发挥")
    size = _text(form_values.get("image_size"), "自动适配")
    direction_title = _text(selected_direction.get("title"), "推荐创意方向")
    direction_description = _text(selected_direction.get("description"), "围绕图片目标组织完整创作方案。")
    visual_anchor = _visual_anchor(selected_direction, product_creative_profile)
    material_summary = _material_summary(materials)
    return f"""# {image_goal}｜{direction_title}

## 一、选题方向

原始需求：{original_prompt or "未提供"}  
产品主体：{product_subject}  
创作目标：{image_goal}  
行业类型：{industry_type or "general"}  
生成数量：{requested_count} 张  
内容类型：图片生成。  
人物/场景冲突：围绕 {usage} 的第一眼注意力建立画面焦点。  
产品/商品能力：通过 {visual_anchor} 让主题更容易被识别和记住。  
结果反转：从普通图片需求升级为可直接用于 {usage} 的成品视觉。

产品定位：{product_subject} = 面向 {usage} 的 {image_type} 主体，创作目标为 {image_goal}。  
产品剧情角色：视频生成不适用；图片中承担主视觉主体和信息焦点。  
系列记忆句：一眼看到重点，一张图完成表达。

---

## 二、选题优势

- **爆点机制**：用主体焦点吸引视线；用风格和构图制造记忆；用信息层级服务 {usage}
- **人群**：{usage} 触达用户｜A1 认知到 A3 兴趣
- **依据**：图片类型为 {image_type}，图片风格为 {style}，素材基础为 {material_summary}
- **转化逻辑链**：画面吸引 -> 信息识别 -> 风格建立 -> 信任形成 -> 用户继续点击或停留
- **产品剧情检验**：通过 -- 去掉主体后，图片无法表达 {image_goal}
- **系列延展性**：可系列化；可延展到封面图、详情页配图、活动视觉

---

## 三、视频规格

- 任务类型：图片生成
- 画幅：{size}
- 时长：视频生成不适用
- 风格：{style}
- 调性：清晰、稳定、可发布
- 投放平台：按 {usage} 选择
- 转化目标：提升点击、停留和信息理解
- 投放方式：图片物料投放或内容发布

---

## 四、角色列表

- 主视觉主体：{image_goal}，承担第一视觉焦点
- 场景环境：围绕 {visual_anchor} 组织背景和道具关系
- 信息元素：标题、辅助文案或视觉标签，承担快速理解作用
- 产品/商品名称：按 {product_subject} 呈现，视觉锚点为 {visual_anchor}

---

## 五、镜头列表

- 镜头1-「静态画面」
  - 画面：主体居中或黄金分割构图，围绕 {visual_anchor} 建立风格，整体符合 {style}
  - 文案：根据 {usage} 保留标题或留白。音效：视频生成不适用

---

## 六、背景音乐

- 前半段「不适用」：视频生成不适用
- 中段「不适用」：视频生成不适用
- 后段「不适用」：视频生成不适用

---

## 七、前3秒钩子

视频生成不适用；图片第一眼钩子是清晰主体、强风格和明确的信息层级。

---

## 八、产品露出设计

- 首次露出时间：静态画面首屏即露出
- 露出方式：主体视觉直接呈现
- 露出画面：{product_subject}、关键风格元素和 {visual_anchor}
- 露出目的：让用户快速理解主题，并服务 {usage}

---

## 九、转化收口

- 转化动作：根据 {usage} 承接点击、发布、详情页浏览或活动引导
- 转化话术：围绕图片目标添加短标题或行动提示
- 转化画面：主体、关键信息和视觉锚点统一呈现
- 注意事项：避免夸大承诺，避免虚假优惠，避免绝对化违规词

---

## 十、开发输出要求

生成该 plan.md 后，后续图片生成 Agent 必须以本方案为权威合同。图片生成参数必须继承图片类型、用途、风格、尺寸和创意方向。创意方向说明：{direction_description}
"""


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value)


def _context_text_value(intake_context: dict[str, Any], key: str) -> str:
    value = intake_context.get(key)
    return _text(value)


def _context_int_value(intake_context: dict[str, Any], key: str) -> int | None:
    try:
        value = int(intake_context.get(key))
    except (TypeError, ValueError):
        return None
    return max(1, min(10, value))


def _merged_profile(product_creative_profile: dict[str, Any], intake_context: dict[str, Any]) -> dict[str, Any]:
    context_profile = intake_context.get("product_creative_profile")
    if not isinstance(context_profile, dict):
        return product_creative_profile
    return {**context_profile, **product_creative_profile}


def _visual_anchor(selected_direction: dict[str, Any], product_creative_profile: dict[str, Any]) -> str:
    direction_data = selected_direction.get("data")
    if isinstance(direction_data, dict) and _text(direction_data.get("visual_anchor")):
        return _text(direction_data.get("visual_anchor"))
    anchors = product_creative_profile.get("visual_anchor_keywords")
    if isinstance(anchors, list) and anchors:
        return "、".join(_text(anchor) for anchor in anchors[:3] if _text(anchor)) or "产品质感、真实使用、转化动作"
    return "产品质感、真实使用、转化动作"


def _material_summary(materials: list[dict[str, Any]]) -> str:
    if not materials:
        return "暂无额外素材，主要依据用户表单和创意方向"
    return f"{len(materials)} 个素材，后续生成时按素材类型引用"
