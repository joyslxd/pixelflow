"""按 sedance 短剧/广告 Skill 命令流推进脚本创作阶段。"""

from __future__ import annotations

import hashlib
import json
import logging
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

ScriptSkillStage = Literal[
    "start",
    "plan",
    "characters",
    "outline",
    "episode",
    "review",
    "compliance",
    "export",
]

STAGE_ORDER: tuple[ScriptSkillStage, ...] = (
    "start",
    "plan",
    "characters",
    "outline",
    "episode",
    "review",
    "compliance",
    "export",
)

STAGE_TITLES: dict[ScriptSkillStage, str] = {
    "start": "选题与创作目标 /start",
    "plan": "三幕结构与爽点 /plan",
    "characters": "角色/场景/道具设定 /characters",
    "outline": "分镜大纲 /outline",
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
        "1) 角色设定：主角/配角/产品拟人；每人含视觉形象、身份、核心标签、性格、金句；\n"
        "2) 场景设定：本片关键场景清单；每场含名称、时空背景、陈设细节、光线氛围、可拍要点；\n"
        "3) 道具与产品设定：用具体品牌/产品名做三级标题（如「蓝妹啤酒」），禁止用「核心产品」「产品」「商品」作标题；"
        "每项含名称、外观材质、品牌露出、使用动作。\n"
        "输出 Markdown，且必须包含三个二级标题：## 角色设定、## 场景设定、## 道具与产品设定。"
        "广告片至少 1 个产品道具；场景不得省略为“室内/室外”空泛描述。"
    ),
    "outline": (
        "根据上游结果完成 /outline：给出镜头/分集目录，标注关键钩子与 CTA。"
        "若是单条广告，按秒级镜头列表输出。镜头中引用的场景/道具名称需与上游设定一致。输出 Markdown。"
    ),
    "episode": (
        "根据上游全部结果完成 /episode：写出完整可拍摄脚本。"
        "格式：时长、画幅、镜头列表（时间、景别、运镜、画面、旁白、屏幕文案、行动引导）。"
        "画面描述须点名上游场景与道具/产品，禁止只写角色动作。必须覆盖用户故事主线与结尾 CTA。输出 Markdown。"
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
        f"【当前阶段】{STAGE_TITLES[stage]}\n"
        f"【阶段任务】{STAGE_PROMPTS[stage]}\n\n"
        f"【用户输入】\n{user_story}\n"
    )
    if prior_text:
        human += f"\n【上游产物】\n{prior_text}\n"
    human += "\n只输出 Markdown，不要解释过程。"
    try:
        model = create_chat_model(thinking_enabled=False, streaming=True)
    except TypeError:
        model = create_chat_model(thinking_enabled=False)
    messages = [
        (
            "system",
            "你是短剧/广告视频编剧助手，严格遵循用户故事与上游产物推进当前阶段。"
            "禁止输出与用户输入无关的模板化带货文案。",
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
    """执行 /start→…→/export 中的单阶段。"""

    spec = VideoToolSpec(
        name="run_script_skill_stage",
        description="按 sedance 脚本 Skill 命令流执行单个创作阶段",
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
                on_token=context.emit_thinking_delta,
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
