"""导入用户脚本与生成版本化创意草稿的受控工具。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from pixelflow.video_agent.adapters import PixelFlowVideoDomainAdapter, VideoDomainAdapter
from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.production_fields import (
    analyze_production_fields_with_llm,
    apply_production_fields_to_script,
)

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)


class ImportScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    markdown: str = Field(min_length=1, max_length=100_000)

    @field_validator("markdown")
    @classmethod
    def normalize_markdown(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("脚本内容不能为空")
        return normalized


class BrainstormScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_info: dict[str, JsonValue] = Field(default_factory=dict)
    video_params: dict[str, JsonValue] = Field(default_factory=dict)
    creative_direction: str = Field(default="", max_length=100_000)


def _validated[T: BaseModel](model: type[T], arguments: Mapping[str, object]) -> T:
    try:
        return model.model_validate(dict(arguments))
    except ValidationError as exc:
        raise VideoToolValidationError("脚本工具参数无效") from exc


def _fingerprint(tool_name: str, payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{tool_name}:{canonical}".encode()).hexdigest()


def _script_versions(payload: Mapping[str, object]) -> list[dict[str, JsonValue]]:
    raw = payload.get("script_versions")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _existing_script(
    payload: Mapping[str, object],
    request_fingerprint: str,
) -> Mapping[str, object] | None:
    script = payload.get("script")
    if isinstance(script, dict) and script.get("request_fingerprint") == request_fingerprint:
        return script
    return None


def _next_version(versions: list[dict[str, JsonValue]]) -> int:
    values = [item.get("version") for item in versions]
    numbers = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return max(numbers, default=0) + 1


def _artifact_ref(workspace_id: str, request_fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}:{request_fingerprint}".encode()
    ).hexdigest()[:32]
    return f"artifact:video-script-{digest}"


def _replay_result(tool_name: str, script: Mapping[str, object]) -> VideoToolResult:
    artifact_ref = str(script.get("artifact_ref") or "")
    refs = (artifact_ref,) if artifact_ref.startswith("artifact:") else ()
    return VideoToolResult(
        tool_name=tool_name,
        public_summary=f"已复用脚本版本 {script.get('version', 1)}",
        artifact_refs=refs,
    )


class ImportScriptTool:
    spec = VideoToolSpec(
        name="import_script",
        description="导入并标准化用户提供的完整视频脚本",
        input_model=ImportScriptInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=(
            "script",
            "script_versions",
            "script_pipeline",
            "script_entry_path",
            "form_values",
            "awaiting_production_fields",
        ),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        payload = dict(arguments)
        # 思考流/Planner 常只给 tool_name；正文由服务端从 workspace / latest_input 注入。
        if not str(payload.get("markdown") or "").strip():
            script = context.workspace.payload.get("script")
            markdown = ""
            if isinstance(script, dict):
                raw = script.get("content")
                if isinstance(raw, str):
                    markdown = raw.strip()
            if not markdown:
                latest = context.workspace.payload.get("latest_input")
                if isinstance(latest, str):
                    marker = "\n\n【本轮指令】"
                    text = latest.strip()
                    if marker in text:
                        head, _, _ = text.partition(marker)
                        markdown = head.strip() or text
                    else:
                        markdown = text
            if markdown:
                payload["markdown"] = markdown
        request = _validated(ImportScriptInput, payload)
        fingerprint = _fingerprint(self.spec.name, request.model_dump(mode="json"))
        existing = _existing_script(context.workspace.payload, fingerprint)
        if existing is not None:
            return _replay_result(self.spec.name, existing)

        versions = _script_versions(context.workspace.payload)
        version = _next_version(versions)
        # 成稿正文往往不含画幅/CTA；必须合并工作区与【本轮指令】再判定，避免覆盖用户已补字段。
        field_text = request.markdown
        latest = context.workspace.payload.get("latest_input")
        if isinstance(latest, str) and "【本轮指令】" in latest:
            field_text = f"{request.markdown}\n\n{latest.strip()[-1_200:]}"
        analysis = await analyze_production_fields_with_llm(text=field_text)
        artifact_ref = _artifact_ref(context.workspace.workspace_id, fingerprint)
        script_payload = apply_production_fields_to_script(
            {
                "artifact_ref": artifact_ref,
                "source": "user_import",
                "version": version,
                "status": "ready",
                "review_required": False,
                "content": request.markdown,
                "request_fingerprint": fingerprint,
            },
            analysis,
            workspace_payload=context.workspace.payload,
        )
        script: dict[str, JsonValue] = {
            str(key): value  # type: ignore[misc]
            for key, value in script_payload.items()
        }
        missing = [
            str(item)
            for item in (script.get("missing_requirements") or [])
            if str(item).strip()
        ]
        duration_sec = script.get("duration_sec")
        if not isinstance(duration_sec, int):
            duration_sec = analysis.duration_sec
        summary = f"已导入脚本版本 {version}"
        if isinstance(duration_sec, int):
            summary += f"（已识别时长 {duration_sec} 秒）"
        if missing:
            summary += f"；仍缺少：{'、'.join(missing)}"

        workspace_patch: dict[str, JsonValue] = {
            "script": script,
            "script_versions": [*versions, script],
        }
        form_values = context.workspace.payload.get("form_values")
        next_form = dict(form_values) if isinstance(form_values, dict) else {}
        ratio = script.get("aspect_ratio") or script.get("video_ratio")
        if isinstance(ratio, str) and ratio.strip():
            next_form["video_ratio"] = ratio.strip()
        cta = script.get("ending_cta")
        if isinstance(cta, str) and cta.strip():
            next_form["ending_cta"] = cta.strip()
        if next_form:
            workspace_patch["form_values"] = next_form
        if not missing:
            workspace_patch["awaiting_production_fields"] = False
        # 成稿导入后必须走结构化拆解 Tool 逻辑：角色/场景/道具 + 分镜提示词。
        # 禁止只靠 Intake 自然语言「看起来完整」就宣称可推进。
        try:
            await context.emit_progress(
                "正在拆解角色、场景、道具与分镜提示词…",
                phase="import_structure_extract",
            )
            from pixelflow.video_agent.tools.script_skill_pipeline import (
                extract_imported_script_structure,
            )

            structure_stages = await extract_imported_script_structure(
                markdown=request.markdown,
                workspace_id=context.workspace.workspace_id,
                on_token=context.emit_thinking_delta,
            )
            prior_pipeline = context.workspace.payload.get("script_pipeline")
            pipeline: dict[str, JsonValue] = (
                dict(prior_pipeline) if isinstance(prior_pipeline, dict) else {}
            )
            # 用户成稿进 episode，拆解结果进 characters/outline。
            pipeline["episode"] = {
                "stage": "episode",
                "title": "用户成稿 /episode",
                "content": request.markdown,
                "source": "user_complete_script",
                "change_summary": "导入用户成稿作为 episode 权威正文",
            }
            pipeline.update(structure_stages)
            workspace_patch["script_pipeline"] = pipeline
            workspace_patch["script_entry_path"] = "polish"
            summary += "；已拆解角色/场景/道具与分镜提示词"
        except Exception as exc:  # noqa: BLE001
            # 拆解失败不回滚导入；公开摘要提示需重试拆解。
            summary += f"；结构化拆解未完成（{type(exc).__name__}），可继续补字段或重试导入"
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=summary,
            workspace_patch=workspace_patch,
            artifact_refs=(artifact_ref,),
        )


class BrainstormScriptTool:
    spec = VideoToolSpec(
        name="brainstorm_script",
        description="根据商品、视频参数和创意方向生成版本化脚本草稿",
        input_model=BrainstormScriptInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=(
            "script",
            "script_versions",
            "script_pipeline",
            "script_entry_path",
            "form_values",
            "awaiting_production_fields",
        ),
    )

    def __init__(self, *, adapter: VideoDomainAdapter | None = None) -> None:
        self._adapter = adapter or PixelFlowVideoDomainAdapter()

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validated(BrainstormScriptInput, arguments)
        request_payload = request.model_dump(mode="json")
        fingerprint = _fingerprint(self.spec.name, request_payload)
        existing = _existing_script(context.workspace.payload, fingerprint)
        if existing is not None:
            return _replay_result(self.spec.name, existing)

        await context.emit_progress(
            "正在整理商品信息与创意方向…",
            phase="prepare_inputs",
        )
        workspace_product = context.workspace.payload.get("product_info")
        workspace_params = context.workspace.payload.get("video_params")
        product_info = {
            **(dict(workspace_product) if isinstance(workspace_product, dict) else {}),
            **request.product_info,
        }
        video_params = {
            **(dict(workspace_params) if isinstance(workspace_params, dict) else {}),
            **request.video_params,
        }
        reference_analysis = context.workspace.payload.get("reference_analysis")
        latest_input = context.workspace.payload.get("latest_input")
        creative_direction = (
            latest_input.strip()
            if isinstance(latest_input, str) and latest_input.strip()
            else request.creative_direction
        )
        await context.emit_progress(
            "调用创意脚本 Skill（brief_generate）…",
            phase="invoke_skill",
        )
        await context.emit_progress(
            "已交给大模型生成脚本草稿，请稍候…",
            phase="await_model",
        )
        markdown = (
            await self._adapter.brainstorm_script(
                product_info=product_info,
                video_params=video_params,
                creative_direction=creative_direction,
                reference_analysis=(
                    dict(reference_analysis)
                    if isinstance(reference_analysis, dict)
                    else None
                ),
            )
        ).strip()
        if not markdown:
            raise VideoToolValidationError("创意脚本结果为空")

        await context.emit_progress(
            "正在整理镜头、旁白与行动引导…",
            phase="format_draft",
        )
        versions = _script_versions(context.workspace.payload)
        version = _next_version(versions)
        artifact_ref = _artifact_ref(context.workspace.workspace_id, fingerprint)
        analysis = await analyze_production_fields_with_llm(text=markdown)
        script: dict[str, JsonValue] = {
            "artifact_ref": artifact_ref,
            "source": "agent_brainstorm",
            "version": version,
            "status": "draft",
            "review_required": True,
            "content": markdown,
            "missing_requirements": list(analysis.missing),
            "request_fingerprint": fingerprint,
        }
        if analysis.duration_sec is not None:
            script["duration_sec"] = analysis.duration_sec
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已生成创意脚本草稿版本 {version}",
            workspace_patch={"script": script, "script_versions": [*versions, script]},
            artifact_refs=(artifact_ref,),
        )
