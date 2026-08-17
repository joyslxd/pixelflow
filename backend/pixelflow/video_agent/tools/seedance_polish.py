"""将剧本正文润色为可给 Seedance 视频模型执行的分镜提示词。

方案 2：独立已注册 Tool，由 VideoAgent 按缺口调度；Skill 正文只在 Tool 内加载，
不进入 Agent 系统提示，也不假装「调度 Skill 文件」。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from deerflow.models import create_chat_model

from pixelflow.generate.seedance_prompt import load_seedance_guidance
from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.production_fields import user_latest_input

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

logger = logging.getLogger(__name__)

POLISH_TIMEOUT_SECONDS = 180.0
_TOOL_NAME = "polish_seedance_shot_prompts"
_STAGE_ID = "episode"
_STAGE_TITLE = "生成剧本正文 /episode"


class PolishSeedanceShotPromptsInput(BaseModel):
    """可选补充说明；正文始终从 Workspace 的 episode/export/script 读取。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    focus: str = Field(
        default="",
        max_length=4_000,
        description="可选润色侧重，例如强调商品特写或压短对白；可空。",
    )


def _pipeline(payload: Mapping[str, object]) -> dict[str, dict[str, JsonValue]]:
    raw = payload.get("script_pipeline")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, JsonValue]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            result[key] = dict(value)
    return result


def _stage_content(pipeline: Mapping[str, Mapping[str, JsonValue]], stage_id: str) -> str:
    item = pipeline.get(stage_id)
    if not isinstance(item, Mapping):
        return ""
    content = item.get("content")
    return str(content).strip() if isinstance(content, str) else ""


def resolve_polish_source_markdown(payload: Mapping[str, object]) -> tuple[str, str]:
    """返回 (正文, 来源标签)。优先未润色原文，避免重复叠润色。"""

    pipeline = _pipeline(payload)
    episode = pipeline.get(_STAGE_ID)
    if isinstance(episode, Mapping):
        pre = episode.get("pre_polish_content")
        if isinstance(pre, str) and pre.strip():
            return pre.strip(), "episode.pre_polish"
        content = episode.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip(), "episode"
    for stage_id in ("export", "outline"):
        text = _stage_content(pipeline, stage_id)
        if text:
            return text, stage_id
    script = payload.get("script")
    if isinstance(script, Mapping):
        content = str(script.get("content") or "").strip()
        if content:
            return content, "script"
    latest = user_latest_input(payload)
    if latest:
        return latest, "latest_input"
    return "", ""


def build_polish_system_prompt() -> str:
    """Tool 执行 Prompt：角色 + PixelFlow 合同 + seedance-prompt Skill 摘录。"""

    return (
        "你是 PixelFlow 的 Seedance 分镜提示词润色助手。\n"
        "任务：把上游「剧本正文 /episode」润色成可直接交给 Seedance 系列视频模型生成的分镜提示词。\n"
        "硬约束：\n"
        "1. 不得改变分镜数量、顺序与各镜时长/时间码；不得新增卖点、价格、功效承诺或未出现的人物/品牌。\n"
        "2. 忠实用户故事与设定；设定集中的角色/场景/道具名称继续用 @实体名 引用。\n"
        "3. 每个分镜输出可拍摄镜头描述：每个段落以本镜内部整数秒范围开头（如 0-4秒：…），"
        "多段从 0 秒连续覆盖到本镜结束；禁止 ms、毫秒、小数时间码。\n"
        "4. 每段显式使用标签：地点：、主体：、动作：、景别：、运镜：、光影：、声音：、收束：；"
        "旁白/对白写在对应秒段的「声音」或单独「旁白（对白）」字段中。\n"
        "5. 只输出 Markdown 镜头正文，不要解释过程，不要包代码围栏。\n"
        "6. 若原文已是合格 Seedance 镜头描述，只做最小必要规范化，不要无故重写剧情。\n\n"
        "Seedance 系列 Skill 规则摘录（必须执行）：\n"
        f"{load_seedance_guidance()}"
    )


def build_polish_human_prompt(
    *,
    source_markdown: str,
    characters_markdown: str = "",
    focus: str = "",
    video_model: str = "",
) -> str:
    parts = [
        "【待润色剧本正文】",
        source_markdown.strip(),
    ]
    if characters_markdown.strip():
        parts.extend(["", "【设定集（角色/场景/道具，仅供引用名）】", characters_markdown.strip()])
    model = video_model.strip() or "（Workspace 未写明，保持通用 Seedance 规则，勿臆造型号能力）"
    parts.extend(["", f"【当前视频模型】{model}"])
    note = focus.strip()
    if note:
        parts.extend(["", f"【本轮润色侧重】{note}"])
    parts.extend(["", "请输出润色后的完整镜头 Markdown。"])
    return "\n".join(parts)


def _creation_video_model(payload: Mapping[str, object]) -> str:
    for key in ("creation_contract", "production_fields", "brief"):
        block = payload.get(key)
        if not isinstance(block, Mapping):
            continue
        model = str(block.get("video_model") or "").strip()
        if model:
            return model
    return ""


def _artifact_ref(workspace_id: str, digest_source: str) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}:seedance-polish:{digest_source}".encode()
    ).hexdigest()[:32]
    return f"artifact:video-script-seedance-polish-{digest}"


_CODE_FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


def _strip_code_fence(text: str) -> str:
    match = _CODE_FENCE_RE.match(text.strip())
    return match.group(1).strip() if match else text.strip()


def _with_episode_heading(markdown: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return stripped
    return f"## {_STAGE_TITLE}\n\n{stripped}"


async def _generate_polished_markdown(
    *,
    source_markdown: str,
    characters_markdown: str,
    focus: str,
    video_model: str,
    on_token: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    from pixelflow.video_agent.thinking_stream import stream_chat_tokens

    try:
        model = create_chat_model(thinking_enabled=False, streaming=True)
    except TypeError:
        model = create_chat_model(thinking_enabled=False)
    messages = [
        ("system", build_polish_system_prompt()),
        (
            "human",
            build_polish_human_prompt(
                source_markdown=source_markdown,
                characters_markdown=characters_markdown,
                focus=focus,
                video_model=video_model,
            ),
        ),
    ]

    async def on_content(delta: str) -> None:
        if on_token is not None:
            await on_token(delta)

    try:
        _reasoning, markdown = await stream_chat_tokens(
            model=model,
            messages=messages,
            on_content=on_content,
            timeout_sec=POLISH_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise VideoToolValidationError(
            f"Seedance 分镜提示词润色超时（{int(POLISH_TIMEOUT_SECONDS)}秒），请稍后重试"
        ) from exc
    markdown = _strip_code_fence(markdown.strip())
    if not markdown:
        raise VideoToolValidationError("Seedance 分镜提示词润色结果为空")
    return markdown


class PolishSeedanceShotPromptsTool:
    """把 episode 剧本正文润色为 Seedance 可执行分镜提示词。"""

    spec = VideoToolSpec(
        name=_TOOL_NAME,
        description=(
            "在剧本正文（episode）已就绪后，按 seedance-prompt Skill 把镜头描述润色成"
            "可给 Seedance 视频模型生成的提示词，并写回 script_pipeline.episode。"
            "成稿导入后或 run_script_skill_stage(episode) 完成后、prepare_scene_packages 之前调用；"
            "不要用本 Tool 重新创作故事，也不要用它替代 import_script / generate_scenes。"
        ),
        input_model=PolishSeedanceShotPromptsInput,
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
        try:
            request = PolishSeedanceShotPromptsInput.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VideoToolValidationError("Seedance 润色参数无效") from exc

        source_markdown, source_label = resolve_polish_source_markdown(
            context.workspace.payload
        )
        if not source_markdown:
            raise VideoToolValidationError(
                "当前还没有可润色的剧本正文。请先 import_script 或 run_script_skill_stage(episode)。"
            )

        prior = _pipeline(context.workspace.payload)
        characters = _stage_content(prior, "characters")
        video_model = _creation_video_model(context.workspace.payload)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "source": source_markdown,
                    "characters": characters,
                    "focus": request.focus.strip(),
                    "video_model": video_model,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

        existing = prior.get(_STAGE_ID)
        if (
            isinstance(existing, dict)
            and existing.get("seedance_polished") is True
            and existing.get("polish_fingerprint") == fingerprint
            and isinstance(existing.get("content"), str)
            and str(existing["content"]).strip()
        ):
            summary = (
                str(existing.get("change_summary") or "").strip()
                or "已复用 Seedance 分镜提示词润色结果"
            )
            refs = ()
            ref = existing.get("artifact_ref")
            if isinstance(ref, str) and ref.strip():
                refs = (ref.strip(),)
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary=summary,
                artifact_refs=refs,
            )

        await context.emit_progress(
            "正在按 Seedance Skill 润色分镜提示词…",
            phase="seedance_polish_start",
        )
        await context.emit_progress(
            "调用大模型改写镜头描述…",
            phase="seedance_polish_model",
        )

        try:
            from pixelflow.video_agent.tools.script_skill_pipeline import (
                make_generation_progress_on_token,
            )

            polished = await _generate_polished_markdown(
                source_markdown=source_markdown,
                characters_markdown=characters,
                focus=request.focus,
                video_model=video_model,
                on_token=make_generation_progress_on_token(
                    context.emit_progress,
                    phase="seedance_polish_stream",
                    heartbeat_message="分镜润色仍在进行…",
                ),
            )
        except VideoToolValidationError as exc:
            if "超时" in str(exc):
                return VideoToolResult(tool_name=self.spec.name, public_summary=str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Seedance 润色失败 source=%s error_type=%s error=%s",
                source_label,
                type(exc).__name__,
                str(exc)[:300],
            )
            raise VideoToolValidationError(
                "Seedance 分镜提示词润色失败，请稍后重试"
            ) from exc

        polished = _with_episode_heading(polished)
        artifact_ref = _artifact_ref(context.workspace.workspace_id, fingerprint)
        change_summary = (
            f"已按 seedance-prompt 润色分镜提示词（来源 {source_label}，约 {len(polished)} 字）"
        )

        # 首次润色保留文学稿；重复润色继续以 pre_polish 为准。
        pre_polish = source_markdown
        if isinstance(existing, dict):
            previous_pre = existing.get("pre_polish_content")
            if isinstance(previous_pre, str) and previous_pre.strip():
                pre_polish = previous_pre.strip()

        stage_record: dict[str, JsonValue] = {
            "stage": _STAGE_ID,
            "title": _STAGE_TITLE,
            "content": polished,
            "pre_polish_content": pre_polish,
            "artifact_ref": artifact_ref,
            "request_fingerprint": fingerprint,
            "polish_fingerprint": fingerprint,
            "seedance_polished": True,
            "polish_source": source_label,
            "change_summary": change_summary,
            "source": "seedance_polish",
        }
        next_pipeline: dict[str, JsonValue] = {**prior, _STAGE_ID: stage_record}
        workspace_patch: dict[str, JsonValue] = {"script_pipeline": next_pipeline}

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
            "source": "seedance_polish",
            "version": version,
            "status": "draft",
            "review_required": True,
            "content": polished,
            "missing_requirements": [],
            "request_fingerprint": fingerprint,
            "seedance_polished": True,
        }
        workspace_patch["script"] = script
        workspace_patch["script_versions"] = [*versions, script]

        await context.emit_progress(
            "分镜提示词润色完成，已写回脚本预览",
            phase="seedance_polish_done",
        )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=change_summary,
            workspace_patch=workspace_patch,
            artifact_refs=(artifact_ref,),
        )


__all__ = [
    "PolishSeedanceShotPromptsInput",
    "PolishSeedanceShotPromptsTool",
    "build_polish_human_prompt",
    "build_polish_system_prompt",
    "resolve_polish_source_markdown",
]
