"""按 sedance 短剧/广告 Skill 命令流推进脚本创作阶段。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from deerflow.models import create_chat_model

from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.production_fields import (
    missing_creative_production_fields_async,
    user_latest_input,
)

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

logger = logging.getLogger(__name__)

# 单阶段大模型生成超时；避免合规等步骤无限挂起导致执行卡假忙碌。
SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS = 180.0

# 成稿拆解正文进 script_pipeline，Thought 只跟阶段进度，不跟全文 token。
IMPORT_STRUCTURE_PROGRESS_MILESTONES: tuple[tuple[str, str], ...] = (
    ("## 角色", "正在整理角色设定…"),
    ("## 场景", "正在整理场景设定…"),
    ("## 道具", "正在整理道具与产品设定…"),
    ("## 剧本正文", "正在整理分镜表…"),
    ("| 时间 |", "正在写入镜头列表…"),
)


def make_generation_progress_on_token(
    emit_progress: Callable[..., Awaitable[None]],
    *,
    phase: str,
    milestones: tuple[tuple[str, str], ...] = (),
    heartbeat_every_chars: int = 1800,
    heartbeat_message: str = "仍在生成中…",
) -> Callable[[str], Awaitable[None]]:
    """把生成 token 转成短进度文案，禁止把模型正文灌进 Thought。

    Thought / reasoning 通道只应出现阶段提示；完整 Markdown 由 script_pipeline
    与预览面板承接。长生成时用字数心跳避免「思考中」假死。
    """

    phase_key = phase.strip()
    seen: set[str] = set()
    buffer = ""
    since_heartbeat = 0

    async def on_token(delta: str) -> None:
        nonlocal buffer, since_heartbeat
        piece = delta or ""
        if not piece or not phase_key:
            return
        buffer += piece
        if len(buffer) > 12_000:
            buffer = buffer[-6_000:]
        for marker, message in milestones:
            if marker in seen:
                continue
            if marker in buffer:
                seen.add(marker)
                await emit_progress(message, phase=phase_key)
        since_heartbeat += len(piece)
        if heartbeat_every_chars > 0 and since_heartbeat >= heartbeat_every_chars:
            since_heartbeat = 0
            await emit_progress(heartbeat_message, phase=phase_key)

    return on_token

ScriptSkillStage = Literal[
    "start",
    "plan",
    "characters",
    "episode",
    "review",
    "compliance",
    "export",
]

STAGE_ORDER: tuple[ScriptSkillStage, ...] = (
    "start",
    "plan",
    "characters",
    "episode",
    "review",
    "compliance",
    "export",
)

STAGE_TITLES: dict[ScriptSkillStage, str] = {
    "start": "选题与创作目标 /start",
    "plan": "三幕结构与爽点 /plan",
    "characters": "角色/场景/道具设定 /characters",
    "episode": "生成剧本正文 /episode",
    "review": "五维自检 /review",
    "compliance": "合规检查 /compliance",
    "export": "导出脚本产物 /export",
}

STAGE_PROMPTS: dict[ScriptSkillStage, str] = {
    "start": (
        "根据用户输入完成 /start：提炼题材、平台、时长、画幅、一句话卖点与创作目标。"
        "输出 Markdown，含：题材、目标平台、时长、画幅、核心梗、目标受众、转化目标。"
        "必须忠实用户故事，不得编造无关商品卖点。"
        "若用户已给出时长，如实写出，不要改写；"
        "若用户未明确画幅（如 9:16/16:9/1:1 或竖屏/横屏），画幅必须写「待用户确认」，禁止擅自填默认画幅；"
        "若用户未明确结尾行动引导（下单/进直播间/私信等），转化目标必须写「待用户确认」，禁止编造 CTA。"
        "开头用 2～4 句写出「可确认的创意方向摘要」（故事钩子、情绪主线、为何有意思），"
        "方便用户判断是否同意后再继续写结构。"
    ),
    "plan": (
        "根据用户输入与上游 /start 结果完成 /plan：写出三幕结构、付费/情绪卡点、爽点矩阵。"
        "输出 Markdown。若是 60s 广告，压缩为起-承-转-合四段时间轴。"
    ),
    "characters": (
        "根据上游结果完成设定集（命令名仍为 /characters，但必须覆盖角色+场景+道具）：\n"
        "1) 角色设定：主角/配角/产品拟人；每人用三级标题写具体人名（### 安然），"
        "其下再用列表写视觉形象、身份、核心标签、性格、金句；禁止把「视觉特征/动作习惯」当标题；\n"
        "2) 场景设定：本片关键场景清单；每场用三级标题写具体场景名，下列表写时空背景、陈设细节、光线氛围、可拍要点；\n"
        "3) 道具与产品设定：用具体品牌/产品名做三级标题（如「蓝妹啤酒」），禁止用「核心产品」「产品」「商品」作标题；"
        "每项含名称、外观材质、品牌露出、使用动作。\n"
        "输出 Markdown，且必须包含三个二级标题：## 角色设定、## 场景设定、## 道具与产品设定。"
        "广告片至少 1 个产品道具；场景不得省略为“室内/室外”空泛描述。"
    ),
    "episode": (
        "根据上游全部结果完成 /episode：写出完整可拍摄脚本。"
        "写作质量遵循 bgrs（BGEC-SD2 / sedance-video-prompts-skill）Skill 的时长、对白、视听语言与节奏铁律；"
        "但交付形态必须是 PixelFlow 六列 Markdown 表，禁止 △ 文学剧本格式。"
        "格式：时长、画幅、镜头列表（时间、景别、运镜、画面、旁白/对白、屏幕文案、行动引导）。"
        "画面描述须点名上游场景与道具/产品，禁止只写角色动作。"
        "凡引用设定集中的角色/场景/道具名称，画面字段必须写成 @实体名"
        "（如 形象参考@安然、地点@会议室、道具@氧气防晒），禁止只写裸名「安然盯着…」。"
        "必须覆盖用户故事主线与结尾 CTA。输出 Markdown。"
    ),
    "review": (
        "根据上游脚本完成 /review 五维自检：钩子、节奏、角色清晰度、转化引导、可拍性；"
        "并额外检查：是否缺少场景设定、是否缺少道具/产品设定、镜头是否可对应到设定名称。"
        "若上游 episode 标注为用户成稿（或 prior 仅有 episode），以用户原文为权威："
        "只列问题与改写建议，不要整篇重写。"
        "列出问题与修改建议。输出 Markdown。"
    ),
    "compliance": (
        "根据上游脚本完成 /compliance：对照公开传播红线做简要合规排查，"
        "只给风险提示与改写建议，不删改故事主线。输出 Markdown。"
        "用户成稿场景下同样只提示，不擅自替换镜头正文。"
    ),
    "export": (
        "汇总上游产物，输出最终可交付脚本 Markdown，章节顺序固定为：\n"
        "1) 标题与规格 2) 角色设定 3) 场景设定 4) 道具与产品设定 5) 大纲 6) 完整镜头脚本 7) 合规备注。\n"
        "角色/场景/道具三块必须齐全；若上游缺失，根据故事补全后再导出。"
        "若 episode 来自用户成稿：镜头正文以用户成稿为准，结合 review/compliance 建议做最小必要修订，"
        "并在合规备注中说明「基于用户成稿导出」。"
        "这是用户将看到的终稿，必须连贯、顺序正确、忠于用户输入。"
    ),
}


def _entity_title_rules() -> str:
    """角色/场景/道具命名硬约束：供 stage 与 import 拆解共用。"""

    return (
        "实体命名硬约束：每个可出镜实体必须用三级标题写具体名称"
        "（如 ### 安然、### 办公室梳妆台、### 氧气防晒）；"
        "视觉特征/动作习惯/人物弧光/时段/光线/功能等只能作为该实体下的列表字段，"
        "禁止把字段名或身份长句当成标题。"
    )


def build_import_structure_system_prompt() -> str:
    """成稿导入拆解+审核+优化：一次产出多阶段 Markdown，供 script_pipeline 预览。

    写作细则复用 STAGE_PROMPTS；剧本正文段与 run_script_skill_stage(episode)
    同样注入 bgrs 摘录 + 六列合同。切分器按固定二级标题写入 characters/…
    """

    from pixelflow.video_agent.skills.bgrs_episode_guidance import (
        build_episode_six_column_contract,
        load_bgrs_episode_guidance,
    )

    episode_bgrs = (
        f"{build_episode_six_column_contract()}\n"
        f"【bgrs Skill 写作指导摘录】\n{load_bgrs_episode_guidance()}"
    )
    return (
        "你是 PixelFlow 成稿拆解与优化助手。用户已提供完整或接近完整的拍摄脚本。"
        "请在忠实原文的前提下完成：设定拆解 → 分镜整理 → 五维自检 → 合规提示 → 导出终稿。"
        "禁止只写一句笼统摘要；禁止大幅删镜或改写剧情主线。\n"
        f"{_entity_title_rules()}\n"
        "输出 Markdown，且必须依次包含以下六个二级标题（标题单独成行，不可改名）：\n"
        "## 角色/场景/道具设定\n"
        f"{STAGE_PROMPTS['characters']}\n"
        "## 剧本正文\n"
        "在忠实用户成稿的前提下整理可拍摄镜头正文；时间码与镜数尽量与成稿一致，"
        "只做结构规范化与可拍性补全，禁止另起炉灶重写故事。"
        "本节必须按 bgrs Skill + PixelFlow 六列合同输出镜头表，禁止 △ 文学剧本或散文场次。\n"
        f"{STAGE_PROMPTS['episode']}\n"
        f"{episode_bgrs}\n"
        "## 五维自检\n"
        f"{STAGE_PROMPTS['review']}\n"
        "## 合规检查\n"
        f"{STAGE_PROMPTS['compliance']}\n"
        "## 导出终稿\n"
        f"{STAGE_PROMPTS['export']}\n"
        "额外规则：\n"
        "1) 时间码与镜头数尽量与成稿一致，不得大幅删镜或重写剧情。\n"
        "2) 忠实原文，不编造未出现的人物/品牌/卖点。\n"
        "3) 道具标题禁止「核心产品」「产品」「商品」「分镜提示词」。\n"
        "4) 五维自检与合规只列问题与改写建议，不要在这两节整篇重写脚本。\n"
        "5) 导出终稿才汇总可交付全文，并注明「基于用户成稿导出」。\n"
        "6) 「剧本正文」必须是六列 Markdown 表；不得把用户成稿原样粘贴充数。\n"
    )


# 兼容旧引用；运行时 extract 每次调用 build_import_structure_system_prompt()。
_IMPORT_STRUCTURE_SYSTEM = None

# 导入拆解稿二级标题 → script_pipeline 阶段；含历史别名以兼容旧模型输出。
_IMPORT_SECTION_STAGE_PATTERNS: tuple[tuple[ScriptSkillStage, tuple[str, ...]], ...] = (
    (
        "characters",
        (
            r"角色/场景/道具设定",
            r"角色设定\s*[&＆]\s*道具\s*[&＆]\s*场景设定",
            r"角色设定",
            r"设定集",
        ),
    ),
    (
        "outline",
        (
            r"分镜大纲",
            r"分镜提示词",
            r"镜头提示词",
        ),
    ),
    (
        "episode",
        (
            r"剧本正文",
            r"分镜脚本",
            r"完整镜头脚本",
            r"镜头脚本",
        ),
    ),
    (
        "review",
        (
            r"五维自检",
            r"脚本评审",
            r"评审",
        ),
    ),
    (
        "compliance",
        (
            r"合规检查",
            r"脚本合规检查",
            r"合规",
        ),
    ),
    (
        "export",
        (
            r"导出终稿",
            r"最终可交付脚本(?:\s*Markdown)?",
            r"导出脚本产物",
            r"终稿",
        ),
    ),
)


def _match_import_section_stage(heading: str) -> ScriptSkillStage | None:
    """把模型输出的二级标题映射到 script_pipeline 阶段 id。"""

    title = heading.strip().lstrip("#").strip()
    if not title:
        return None
    for stage, patterns in _IMPORT_SECTION_STAGE_PATTERNS:
        for pattern in patterns:
            if re.fullmatch(pattern, title, flags=re.IGNORECASE):
                return stage
    return None


def _split_import_structure_markdown(markdown: str) -> dict[str, str]:
    """把综合拆解稿按「已识别阶段标题」切成多段，供 script_pipeline 预览。

    未映射的二级标题（如旧稿中的 ## 场景设定）并入上一阶段，避免设定被截断。
    兼容旧输出：仅有设定正文 + ## 分镜提示词 时仍映射为 characters
    """

    text = markdown.strip()
    if not text:
        return {}

    heading_re = re.compile(r"^##\s+(.+?)\s*$", flags=re.M)
    matches = list(heading_re.finditer(text))
    boundaries: list[tuple[ScriptSkillStage, int]] = []
    for match in matches:
        stage = _match_import_section_stage(match.group(1))
        if stage is not None:
            boundaries.append((stage, match.start()))

    if not boundaries:
        return {"characters": text}

    staged: dict[str, list[str]] = {}
    first_stage, first_pos = boundaries[0]
    if first_pos > 0 and first_stage == "outline":
        preamble = text[:first_pos].strip()
        if preamble:
            if not re.search(r"##\s*角色设定", preamble):
                preamble = f"## 角色设定\n\n（待从成稿补全）\n\n{preamble}".strip()
            staged["characters"] = [preamble]

    for index, (stage, start) in enumerate(boundaries):
        end = boundaries[index + 1][1] if index + 1 < len(boundaries) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            staged.setdefault(stage, []).append(chunk)

    result = {
        stage: "\n\n".join(parts).strip()
        for stage, parts in staged.items()
        if any(part.strip() for part in parts)
    }
    if not result:
        return {"characters": text}
    return result


async def extract_imported_script_structure(
    *,
    markdown: str,
    workspace_id: str,
    on_token: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, dict[str, JsonValue]]:
    """导入成稿后强制结构化拆解/审核/优化，写入多阶段 script_pipeline 产物。"""

    body = markdown.strip()
    if not body:
        raise VideoToolValidationError("成稿为空，无法拆解")
    from pixelflow.video_agent.thinking_stream import stream_chat_tokens

    try:
        model = create_chat_model(thinking_enabled=False, streaming=True)
    except TypeError:
        model = create_chat_model(thinking_enabled=False)
    messages = [
        ("system", build_import_structure_system_prompt()),
        (
            "human",
            f"【用户成稿】\n{body[:12_000]}\n\n"
            "按系统提示依次输出六个二级标题对应的阶段 Markdown；"
            "其中「剧本正文」必须是六列镜头表（时间/景别/运镜/画面/旁白对白/屏幕文案/行动引导），"
            "不要解释过程。",
        ),
    ]
    chunks: list[str] = []

    async def on_content(delta: str) -> None:
        chunks.append(delta)
        if on_token is not None:
            await on_token(delta)

    try:
        _, answer = await stream_chat_tokens(
            model=model,
            messages=messages,
            on_content=on_content,
            timeout_sec=SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise VideoToolValidationError(
            f"成稿结构化拆解超时（{int(SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS)}秒），请稍后重试"
        ) from exc
    combined = ("".join(chunks) or (answer or "")).strip()
    if not combined:
        raise VideoToolValidationError("成稿结构化拆解结果为空")

    staged_markdown = _split_import_structure_markdown(combined)
    if not staged_markdown.get("characters", "").strip():
        staged_markdown["characters"] = combined

    fingerprint = hashlib.sha256(
        json.dumps(
            {"kind": "import_structure", "story": body[:4_000]},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()

    change_summaries: dict[ScriptSkillStage, str] = {
        "characters": "从用户成稿拆解并优化角色/场景/道具设定",
        "episode": "按 bgrs Skill 与六列合同整理可拍摄剧本正文",
        "review": "对成稿做五维自检与改写建议",
        "compliance": "对成稿做合规风险提示",
        "export": "汇总导出基于用户成稿的可交付终稿",
    }
    result: dict[str, dict[str, JsonValue]] = {}
    for stage_name, content in staged_markdown.items():
        if stage_name not in STAGE_TITLES:
            continue
        stage: ScriptSkillStage = stage_name  # type: ignore[assignment]
        body_md = content.strip()
        if not body_md:
            continue
        result[stage] = {
            "stage": stage,
            "title": STAGE_TITLES[stage],
            "content": _with_stage_heading(stage, body_md),
            "artifact_ref": _artifact_ref(workspace_id, stage, fingerprint),
            "request_fingerprint": f"{fingerprint}:{stage}",
            "source": "import_structure",
            "change_summary": change_summaries.get(stage, f"从用户成稿生成 {STAGE_TITLES[stage]}"),
        }
    if "characters" not in result:
        raise VideoToolValidationError("成稿结构化拆解缺少角色/场景/道具设定")
    return result


def _stage_system_prompt(stage: ScriptSkillStage) -> str:
    """单阶段执行 Prompt：角色定位 + STAGE_PROMPTS 精华 + 命名硬约束。

    写作质量在此落地；Agent 系统提示只负责是否调用本 Tool / 选哪个 stage。
    episode 额外注入 bgrs Skill 摘录，并强制六列输出合同。
    """

    parts = [
        (
            "你是短剧/广告视频编剧助手，严格遵循用户故事与上游产物推进当前阶段。"
            "禁止输出与用户输入无关的模板化带货文案。"
            "只输出当前阶段 Markdown，不要解释过程。"
        ),
        f"【阶段任务】{STAGE_PROMPTS[stage]}",
    ]
    if stage in {"characters", "export"}:
        parts.append(_entity_title_rules())
    if stage == "episode":
        from pixelflow.video_agent.skills.bgrs_episode_guidance import (
            build_episode_six_column_contract,
            load_bgrs_episode_guidance,
        )

        parts.append(build_episode_six_column_contract())
        parts.append("【bgrs Skill 写作指导摘录】\n" + load_bgrs_episode_guidance())
    return "\n".join(parts)


_RUN_SCRIPT_SKILL_STAGE_DESCRIPTION = (
    "按需执行脚本创作/优化的单个阶段（不是必须跑完八阶段）。"
    "参数 stage 取 start|plan|characters|episode|review|compliance|export；"
    "按 Workspace 缺口与用户意图选择：从创意起步用 start→plan；补设定用 characters；"
    "写/改正文用 episode；自检用 review；合规用 compliance；汇总终稿用 export。"
    "成稿粘贴请改用 import_script，不要用本 Tool 重导入。"
)


class ScriptSkillStageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: ScriptSkillStage
    creative_direction: str = Field(default="", max_length=100_000)


def _validated(arguments: Mapping[str, object]) -> ScriptSkillStageInput:
    try:
        return ScriptSkillStageInput.model_validate(dict(arguments))
    except ValidationError as exc:
        raise VideoToolValidationError("脚本阶段参数无效") from exc


def _pipeline(payload: Mapping[str, object]) -> dict[str, dict[str, JsonValue]]:
    raw = payload.get("script_pipeline")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, JsonValue]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            result[key] = dict(value)
    return result


def _user_story(context: VideoToolContext, request: ScriptSkillStageInput) -> str:
    latest = context.workspace.payload.get("latest_input")
    if isinstance(latest, str) and latest.strip():
        return latest.strip()
    return request.creative_direction.strip()


def _artifact_ref(workspace_id: str, stage: str, digest_source: str) -> str:
    digest = hashlib.sha256(f"{workspace_id}:{stage}:{digest_source}".encode()).hexdigest()[
        :32
    ]
    return f"artifact:video-script-{stage}-{digest}"


def _change_summary(stage: ScriptSkillStage, markdown: str) -> str:
    headings = [
        line.lstrip("#").strip()
        for line in markdown.splitlines()
        if line.lstrip().startswith("#")
    ]
    cleaned = [
        item.removeprefix("/").strip()
        for item in headings
        if item and STAGE_TITLES[stage] not in item
    ]
    if cleaned:
        preview = "、".join(cleaned[:3])
        return f"新增「{STAGE_TITLES[stage]}」：{preview}"
    char_count = len(markdown.strip())
    return f"新增「{STAGE_TITLES[stage]}」约 {char_count} 字"


def _with_stage_heading(stage: ScriptSkillStage, markdown: str) -> str:
    heading = f"## {STAGE_TITLES[stage]}"
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return stripped
    return f"{heading}\n\n{stripped}"


async def _generate_stage_markdown(
    *,
    stage: ScriptSkillStage,
    user_story: str,
    prior: Mapping[str, Mapping[str, JsonValue]],
    on_token: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    from pixelflow.video_agent.thinking_stream import stream_chat_tokens

    prior_text = "\n\n".join(
        f"## 上游 {STAGE_TITLES[name]}\n{prior[name].get('content', '')}"
        for name in STAGE_ORDER
        if name in prior and name != stage and prior[name].get("content")
    )
    human = (
        f"【当前阶段】{STAGE_TITLES[stage]}\n\n"
        f"【用户输入】\n{user_story}\n"
    )
    if prior_text:
        human += f"\n【上游产物】\n{prior_text}\n"
    human += "\n按系统提示完成本阶段，只输出 Markdown。"
    try:
        model = create_chat_model(thinking_enabled=False, streaming=True)
    except TypeError:
        model = create_chat_model(thinking_enabled=False)
    messages = [
        (
            "system",
            _stage_system_prompt(stage),
        ),
        ("human", human),
    ]

    async def on_content(delta: str) -> None:
        if on_token is not None:
            await on_token(delta)

    try:
        _reasoning, markdown = await stream_chat_tokens(
            model=model,
            messages=messages,
            on_content=on_content,
            timeout_sec=SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise VideoToolValidationError(
            f"{STAGE_TITLES[stage]} 超时（{int(SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS)}秒），请稍后重试"
        ) from exc
    markdown = markdown.strip()
    if not markdown:
        raise VideoToolValidationError(f"{STAGE_TITLES[stage]} 结果为空")
    return markdown


class RunScriptSkillStageTool:
    """执行脚本创作/优化的单个阶段（按需，非固定流水线）。"""

    spec = VideoToolSpec(
        name="run_script_skill_stage",
        description=_RUN_SCRIPT_SKILL_STAGE_DESCRIPTION,
        input_model=ScriptSkillStageInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("script_pipeline", "script", "script_versions"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validated(arguments)
        stage = request.stage
        user_story = _user_story(context, request)
        if not user_story:
            raise VideoToolValidationError("缺少用户创作输入")

        await context.emit_progress(
            f"正在执行 {STAGE_TITLES[stage]}…",
            phase=f"skill_{stage}_start",
        )
        await context.emit_progress(
            f"调用脚本 Skill 阶段 {stage}，交给大模型生成…",
            phase=f"skill_{stage}_model",
        )

        prior = _pipeline(context.workspace.payload)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "stage": stage,
                    "story": user_story,
                    "prior": {key: prior.get(key, {}).get("content") for key in STAGE_ORDER},
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        existing = prior.get(stage)
        if (
            isinstance(existing, dict)
            and existing.get("request_fingerprint") == fingerprint
            and isinstance(existing.get("content"), str)
            and existing["content"].strip()
        ):
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary=(
                    str(existing["change_summary"])
                    if isinstance(existing.get("change_summary"), str)
                    and str(existing["change_summary"]).strip()
                    else f"已复用 {STAGE_TITLES[stage]}"
                ),
                artifact_refs=(
                    (str(existing["artifact_ref"]),)
                    if isinstance(existing.get("artifact_ref"), str)
                    else ()
                ),
            )

        try:
            markdown = await _generate_stage_markdown(
                stage=stage,
                user_story=user_story,
                prior=prior,
                on_token=make_generation_progress_on_token(
                    context.emit_progress,
                    phase=f"skill_{stage}_stream",
                    heartbeat_message=f"{STAGE_TITLES[stage]}仍在生成…",
                ),
            )
        except VideoToolValidationError as exc:
            # 超时返回可完成摘要，让后续阶段可继续，而不是把执行卡永远挂住。
            if "超时" in str(exc):
                return VideoToolResult(
                    tool_name=self.spec.name,
                    public_summary=str(exc),
                )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "脚本 Skill 阶段失败 stage=%s error_type=%s error=%s",
                stage,
                type(exc).__name__,
                str(exc)[:300],
            )
            raise VideoToolValidationError(
                f"{STAGE_TITLES[stage]} 生成失败，请稍后重试"
            ) from exc

        await context.emit_progress(
            f"正在整理 {STAGE_TITLES[stage]} 产物…",
            phase=f"skill_{stage}_format",
        )

        artifact_ref = _artifact_ref(
            context.workspace.workspace_id,
            stage,
            fingerprint,
        )
        markdown = _with_stage_heading(stage, markdown)
        change_summary = _change_summary(stage, markdown)
        stage_record: dict[str, JsonValue] = {
            "stage": stage,
            "title": STAGE_TITLES[stage],
            "content": markdown,
            "artifact_ref": artifact_ref,
            "request_fingerprint": fingerprint,
            "change_summary": change_summary,
        }
        next_pipeline = {**prior, stage: stage_record}
        workspace_patch: dict[str, JsonValue] = {"script_pipeline": next_pipeline}
        artifact_refs = (artifact_ref,)

        if stage in {"episode", "export"}:
            versions_raw = context.workspace.payload.get("script_versions")
            versions = [
                dict(item)
                for item in (versions_raw if isinstance(versions_raw, list) else [])
                if isinstance(item, dict)
            ]
            version = max(
                (
                    item.get("version")
                    for item in versions
                    if isinstance(item.get("version"), int)
                ),
                default=0,
            ) + 1
            script: dict[str, JsonValue] = {
                "artifact_ref": artifact_ref,
                "source": f"skill_{stage}",
                "version": version,
                "status": "draft" if stage == "episode" else "ready",
                "review_required": stage == "episode",
                "content": markdown,
                "missing_requirements": [],
                "request_fingerprint": fingerprint,
            }
            workspace_patch["script"] = script
            workspace_patch["script_versions"] = [*versions, script]

        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=change_summary,
            workspace_patch=workspace_patch,
            artifact_refs=artifact_refs,
        )


class ConfirmScriptCreativeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConfirmScriptCreativeTool:
    """Path A：/start 完成后等人确认选题创意，再继续后续 Skill 阶段。

    Path B 导入脚本后若误排到本工具：允许用 workspace.script 作为可确认内容，
    避免「缺少 start → 笼统工具参数无效」。
    """

    spec = VideoToolSpec(
        name="confirm_script_creative",
        description="确认选题与创意方向后继续脚本创作",
        input_model=ConfirmScriptCreativeInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("script_pipeline",),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        del arguments
        prior = _pipeline(context.workspace.payload)
        start = prior.get("start")
        content = ""
        artifact_ref = ""
        if isinstance(start, dict):
            raw = start.get("content")
            if isinstance(raw, str):
                content = raw.strip()
            ref = start.get("artifact_ref")
            if isinstance(ref, str):
                artifact_ref = ref
        # Path B：无 /start 时回退到已导入脚本正文。
        if not content:
            script = context.workspace.payload.get("script")
            if isinstance(script, dict):
                raw = script.get("content")
                if isinstance(raw, str):
                    content = raw.strip()
                ref = script.get("artifact_ref")
                if isinstance(ref, str) and ref.strip():
                    artifact_ref = ref.strip()
        if not content:
            raise VideoToolValidationError(
                "缺少可确认的选题创意或已导入脚本，请先完成创作或导入脚本。"
            )

        # 优先信任工作区已落库的缺项（补字段短链路会刷新）；短句「同意」不要再 LLM 误判。
        script_obj = context.workspace.payload.get("script")
        if isinstance(script_obj, dict) and "missing_requirements" in script_obj:
            raw_missing = script_obj.get("missing_requirements")
            gaps = (
                [str(item).strip() for item in raw_missing if str(item).strip()]
                if isinstance(raw_missing, list)
                else []
            )
        else:
            user_text = user_latest_input(context.workspace.payload)
            gaps = await missing_creative_production_fields_async(content, user_text)
        if gaps:
            raise VideoToolValidationError(
                "请先补充："
                + "、".join(gaps)
                + "。可直接回复例如：画幅 9:16，结尾引导进直播间下单。"
            )

        summary_lines = [
            line.strip(" #-*")
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("```")
        ][:6]
        preview = "；".join(summary_lines)[:280] if summary_lines else content[:280]
        next_pipeline = {
            **prior,
            "creative_confirmed": {
                "stage": "creative_confirmed",
                "title": "确认选题创意",
                "content": content,
                "artifact_ref": artifact_ref,
                "change_summary": "用户已确认选题创意，继续创作结构",
                "confirmed": True,
            },
        }
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已确认选题创意：{preview}",
            workspace_patch={"script_pipeline": next_pipeline},
            artifact_refs=(artifact_ref,) if artifact_ref else (),
        )
