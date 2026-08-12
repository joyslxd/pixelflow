"""场景包准备与参考图生成的受控 V2 Tool。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from pixelflow.creative.contract import build_video_creation_contract
from pixelflow.creative.script_shots import extract_script_shot_entries
from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.contracts.plan import VideoAgentContract
from pixelflow.video_agent.quota import build_start_quota_interrupt_id

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

# Planner 未显式传时长时的默认值；真实时长应从合同/表单/脚本推断。
_DEFAULT_TARGET_DURATION_MS = 30_000


class PrepareScenePackagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_markdown: str = Field(default="", max_length=200_000)
    target_duration_ms: int = Field(default=30_000, ge=1_000, le=600_000)
    attempt: int = Field(default=1, ge=1, le=10)


class GenerateSceneAssetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_model: str = Field(default="seeddream-5.0", min_length=1, max_length=128)
    image_ratio: str = Field(default="9:16", min_length=1, max_length=32)
    image_size: str = Field(default="2K", min_length=1, max_length=32)
    reference_brief: str = Field(default="", max_length=4_000)
    attempt: int = Field(default=1, ge=1, le=10)
    target_assets: tuple[dict[str, JsonValue], ...] = ()


class ScenePackageOperationJob(VideoAgentContract):
    job_id: str = Field(min_length=1, max_length=64)
    status: Literal["polling", "start_paused_quota", "succeeded"]
    result: dict[str, JsonValue] = Field(default_factory=dict)


class ScenePackageOperationPort(Protocol):
    async def start_prepare_scene_packages(
        self,
        context: VideoToolContext,
        *,
        plan_markdown: str,
        form_values: dict[str, JsonValue],
        selected_direction: dict[str, JsonValue],
        materials: list[dict[str, JsonValue]],
        target_duration_ms: int,
        attempt: int,
    ) -> ScenePackageOperationJob: ...


class SceneAssetOperationPort(Protocol):
    async def start_generate_scene_assets(
        self,
        context: VideoToolContext,
        *,
        global_assets: dict[str, JsonValue],
        scene_packages: list[dict[str, JsonValue]],
        materials: list[dict[str, JsonValue]],
        image_model: str,
        image_ratio: str,
        image_size: str,
        reference_brief: str,
        target_assets: list[dict[str, JsonValue]],
        attempt: int,
    ) -> ScenePackageOperationJob: ...


def _as_mapping(value: Any) -> dict[str, JsonValue]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _pipeline_stage_content(payload: Mapping[str, Any], stage_id: str) -> str:
    pipeline = payload.get("script_pipeline")
    if not isinstance(pipeline, Mapping):
        return ""
    item = pipeline.get(stage_id)
    if isinstance(item, Mapping):
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _script_markdown(payload: Mapping[str, Any]) -> str:
    script = payload.get("script")
    if isinstance(script, Mapping):
        content = script.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    for stage in ("export", "episode", "outline", "characters"):
        content = _pipeline_stage_content(payload, stage)
        if content:
            return content
    latest = payload.get("latest_input")
    return str(latest).strip() if isinstance(latest, str) else ""


def _asset_package_plan_markdown(payload: Mapping[str, Any], explicit: str) -> str:
    """终稿 + 设定集：对齐前端 buildAssetPackagePlanMarkdown，避免丢掉角色/场景/道具。"""

    base = explicit.strip() or _script_markdown(payload)
    characters = _pipeline_stage_content(payload, "characters")
    export_stage = _pipeline_stage_content(payload, "export")
    primary = export_stage or base
    has_character_heading = bool(re.search(r"#{1,3}\s*[^\n]*角色设定", primary))
    has_scene_heading = bool(re.search(r"#{1,3}\s*[^\n]*场景设定", primary))
    has_prop_heading = bool(re.search(r"#{1,3}\s*[^\n]*道具", primary))
    needs_settings = not (has_character_heading and has_scene_heading and has_prop_heading)
    if characters and needs_settings:
        return f"{characters}\n\n---\n\n{primary}".strip()
    if characters and primary:
        snippet = characters[: min(60, len(characters))]
        if snippet and snippet not in primary and has_character_heading:
            return f"{characters}\n\n---\n\n{primary}".strip()
    return primary or base or characters


def _positive_duration_sec(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        duration = int(value)
    except (TypeError, ValueError):
        return None
    if 4 <= duration <= 300:
        return duration
    return None


def _infer_declared_duration_sec(markdown: str) -> int | None:
    text = markdown.strip()
    patterns = (
        r"总?时长[*\s]*[：:]\s*[*\s]*(\d+)\s*(?:秒|s)",
        r"(\d+)\s*秒\s*(?:成片|视频|短剧|竖屏|广告)",
        r"duration\s*[:=]\s*(\d+)\s*s\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        duration = _positive_duration_sec(match.group(1))
        if duration is not None:
            return duration
    return None


def _infer_timeline_end_sec(markdown: str) -> int | None:
    text = markdown.strip()
    max_end = 0
    for match in re.finditer(
        r"(\d{1,2}):(\d{2}):(\d{2})\s*[-–—~]\s*(\d{1,2}):(\d{2}):(\d{2})",
        text,
    ):
        total = int(match.group(4)) * 3600 + int(match.group(5)) * 60 + int(match.group(6))
        if total > max_end:
            max_end = total
    for match in re.finditer(
        r"(?<![:\d])(\d{1,2}):(\d{2})\s*[-–—~]\s*(\d{1,2}):(\d{2})(?![:\d])",
        text,
    ):
        total = int(match.group(3)) * 60 + int(match.group(4))
        if total > max_end:
            max_end = total
    if 4 <= max_end <= 300:
        return max_end
    return None


def _infer_duration_sec_from_script(markdown: str, *, fallback: int = 30) -> int:
    """从脚本时长声明或时间轴末尾推断总秒数，对齐前端 inferVideoDurationSecFromScript。"""

    declared = _infer_declared_duration_sec(markdown)
    if declared is not None:
        return declared
    timeline = _infer_timeline_end_sec(markdown)
    if timeline is not None:
        return timeline
    return fallback


def _resolve_target_duration_ms(
    payload: Mapping[str, Any],
    *,
    plan_markdown: str,
    request_duration_ms: int,
) -> int:
    contract = payload.get("creation_contract")
    if isinstance(contract, Mapping):
        from_contract = _positive_duration_sec(contract.get("video_duration_sec"))
        if from_contract is not None:
            return from_contract * 1000
    form_values = payload.get("form_values")
    if isinstance(form_values, Mapping):
        from_form = _positive_duration_sec(form_values.get("video_duration_sec"))
        if from_form is not None:
            return from_form * 1000
    # 时长声明优先于时间轴；同时扫终稿与 script.content，避免设定集拼接丢掉「时长：N秒」。
    script_content = _script_markdown(payload)
    for text in (plan_markdown, script_content):
        declared = _infer_declared_duration_sec(text)
        if declared is not None:
            return declared * 1000
    # 成稿镜头列表末尾时间码：14 镜脚本常无「时长：」字段，但有 00:00-02:02。
    shot_entries = extract_script_shot_entries(plan_markdown) or extract_script_shot_entries(script_content)
    if shot_entries:
        shot_total = max(int(item["end_sec"]) for item in shot_entries)
        if 4 <= shot_total <= 300:
            return shot_total * 1000
    timeline_candidates = [
        value
        for value in (
            _infer_timeline_end_sec(plan_markdown),
            _infer_timeline_end_sec(script_content),
        )
        if value is not None
    ]
    if timeline_candidates:
        return max(timeline_candidates) * 1000
    if isinstance(request_duration_ms, int) and request_duration_ms >= 1_000:
        return min(request_duration_ms, 600_000)
    return _DEFAULT_TARGET_DURATION_MS


def _resolve_prepare_form_and_contract(
    payload: Mapping[str, Any],
    *,
    plan_markdown: str,
    target_duration_ms: int,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """合并工作区表单与默认合同，对齐旧 prepare-scene-packages 路由。"""

    form_values = _as_mapping(payload.get("form_values") or payload.get("product_info"))
    if "product_info" not in form_values and isinstance(payload.get("product_info"), Mapping):
        form_values = {
            **form_values,
            "product_info": dict(payload["product_info"]),  # type: ignore[arg-type]
        }
    existing_contract = _as_mapping(payload.get("creation_contract"))
    duration_sec = max(4, min(300, int(target_duration_ms / 1000)))
    seed: dict[str, Any] = {
        **existing_contract,
        **form_values,
        "video_duration_sec": duration_sec,
    }
    if not str(seed.get("product_info") or "").strip():
        seed["product_info"] = "脚本成片产品"
    seed.setdefault("video_usage", "宣传片")
    seed.setdefault("video_ratio", form_values.get("video_ratio") or existing_contract.get("video_ratio") or "9:16")
    seed.setdefault("scene_image_ratio", seed.get("video_ratio") or "9:16")
    seed.setdefault("scene_image_size", "4K")
    seed.setdefault("scene_image_spec_source", "deterministic_fallback")
    contract = build_video_creation_contract(seed).model_dump(mode="json")
    merged_form: dict[str, JsonValue] = {
        **form_values,
        "video_duration_sec": contract["video_duration_sec"],
        "video_ratio": contract["video_ratio"],
        "video_model": contract["video_model"],
        "video_size": contract["video_size"],
        "video_sound": contract["video_sound"],
        "image_model": contract["image_model"],
        "scene_image_ratio": contract.get("scene_image_ratio") or "9:16",
        "scene_image_size": contract.get("scene_image_size") or "4K",
        "video_usage": contract["video_usage"],
        "visual_style": contract.get("visual_style") or "",
    }
    if "product_info" not in merged_form:
        merged_form["product_info"] = "脚本成片产品"
    return merged_form, contract


class PrepareScenePackagesTool:
    spec = VideoToolSpec(
        name="prepare_scene_packages",
        description="从已确认脚本生成结构化视频资产包（角色/场景/道具与分镜包）",
        input_model=PrepareScenePackagesInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        # 用途：Registry 只允许 patch 声明过的根键；成功/轮询路径还会写 job、确认位与额度中断。
        workspace_mutations=(
            "global_assets",
            "scenes",
            "scene_packages",
            "creation_contract",
            "target_duration_ms",
            "script_plan_confirmed",
            "scene_package_job",
            "quota_interrupt",
        ),
    )

    def __init__(self, *, operation_port: ScenePackageOperationPort | None = None) -> None:
        self._operation_port = operation_port

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = PrepareScenePackagesInput.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VideoToolValidationError("prepare_scene_packages 参数无效") from exc
        if self._operation_port is None:
            raise VideoToolExecutionError("场景包 Operation 尚未装配")

        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        plan_markdown = _asset_package_plan_markdown(payload, request.plan_markdown)
        if not plan_markdown:
            raise VideoToolValidationError("当前工作区没有可生成资产包的脚本")

        target_duration_ms = _resolve_target_duration_ms(
            payload,
            plan_markdown=plan_markdown,
            request_duration_ms=request.target_duration_ms,
        )
        form_values, creation_contract = _resolve_prepare_form_and_contract(
            payload,
            plan_markdown=plan_markdown,
            target_duration_ms=target_duration_ms,
        )
        selected_direction = _as_mapping(payload.get("selected_direction"))
        if not selected_direction:
            selected_direction = {
                "direction_id": "video-agent-script-confirmed",
                "title": "脚本成片",
                "description": "基于已确认脚本生成视频资产包",
                "recommended": True,
                "tags": ["script"],
                "data": {},
            }
        materials = _as_list(payload.get("materials"))

        try:
            job = await self._operation_port.start_prepare_scene_packages(
                context,
                plan_markdown=plan_markdown,
                form_values=form_values,
                selected_direction=selected_direction,
                materials=materials,
                target_duration_ms=target_duration_ms,
                attempt=request.attempt,
            )
        except VideoToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VideoToolExecutionError("场景包准备失败") from exc

        if job.status in {"polling", "start_paused_quota"}:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="场景包准备任务已启动",
                workspace_patch={
                    "scene_package_job": {
                        "job_id": job.job_id,
                        "plan_step_id": context.step_id,
                        "status": job.status,
                    },
                    "creation_contract": creation_contract,
                    "target_duration_ms": target_duration_ms,
                    **(
                        {
                            "quota_interrupt": {
                                "quota_interrupt_id": build_start_quota_interrupt_id(job.job_id),
                                "plan_id": context.plan_id,
                                "step_id": context.step_id,
                                "job_id": job.job_id,
                                "quota_pause_revision": 0,
                                "phase": "start",
                                "state": "paused",
                                "reason_code": "provider_quota_insufficient",
                            }
                        }
                        if job.status == "start_paused_quota"
                        else {"quota_interrupt": None}
                    ),
                },
                pending_operation_job_ids=(job.job_id,),
            )

        result = job.result if isinstance(job.result, Mapping) else {}
        global_assets = result.get("global_assets") if isinstance(result.get("global_assets"), Mapping) else {}
        scene_packages = result.get("scene_packages") if isinstance(result.get("scene_packages"), list) else []
        result_contract = creation_contract
        raw_contract = result.get("creation_contract")
        if isinstance(raw_contract, Mapping) and _positive_duration_sec(raw_contract.get("video_duration_sec")):
            result_contract = dict(raw_contract)
        result_duration = result.get("target_duration_ms")
        if not isinstance(result_duration, int) or result_duration < 1_000:
            result_duration = target_duration_ms
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=str(result.get("message") or f"已生成 {len(scene_packages)} 个分镜包"),
            workspace_patch={
                "global_assets": dict(global_assets),
                "scenes": list(scene_packages),
                "scene_packages": list(scene_packages),
                "creation_contract": result_contract,
                "target_duration_ms": result_duration,
                "script_plan_confirmed": True,
                "scene_package_job": {
                    "job_id": job.job_id,
                    "plan_step_id": context.step_id,
                    "status": "succeeded",
                },
                "quota_interrupt": None,
            },
            pending_operation_job_ids=(),
        )


class GenerateSceneAssetsTool:
    spec = VideoToolSpec(
        name="generate_scene_assets",
        description="为资产包生成角色/场景/道具参考图，并更新资产版本",
        input_model=GenerateSceneAssetsInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        # 用途：与 prepare 相同，成功/轮询路径会写 job、失败清单与额度中断，必须纳入声明。
        workspace_mutations=(
            "global_assets",
            "scenes",
            "scene_packages",
            "asset_versions",
            "scene_asset_failures",
            "scene_asset_job",
            "quota_interrupt",
        ),
    )

    def __init__(self, *, operation_port: SceneAssetOperationPort | None = None) -> None:
        self._operation_port = operation_port

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = GenerateSceneAssetsInput.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VideoToolValidationError("generate_scene_assets 参数无效") from exc
        if self._operation_port is None:
            raise VideoToolExecutionError("参考图 Operation 尚未装配")

        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        global_assets = _as_mapping(payload.get("global_assets"))
        scene_packages = _as_list(payload.get("scene_packages") or payload.get("scenes"))
        if not global_assets and not scene_packages:
            raise VideoToolValidationError("当前工作区没有可生成参考图的资产包")

        try:
            job = await self._operation_port.start_generate_scene_assets(
                context,
                global_assets=global_assets,
                scene_packages=scene_packages,
                materials=_as_list(payload.get("materials")),
                image_model=request.image_model,
                image_ratio=request.image_ratio,
                image_size=request.image_size,
                reference_brief=request.reference_brief,
                target_assets=[dict(item) for item in request.target_assets],
                attempt=request.attempt,
            )
        except VideoToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VideoToolExecutionError("参考图生成失败") from exc

        if job.status in {"polling", "start_paused_quota"}:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="参考图生成任务已启动",
                workspace_patch={
                    "scene_asset_job": {
                        "job_id": job.job_id,
                        "plan_step_id": context.step_id,
                        "status": job.status,
                        "image_model": request.image_model,
                    },
                    **(
                        {
                            "quota_interrupt": {
                                "quota_interrupt_id": build_start_quota_interrupt_id(job.job_id),
                                "plan_id": context.plan_id,
                                "step_id": context.step_id,
                                "job_id": job.job_id,
                                "quota_pause_revision": 0,
                                "phase": "start",
                                "state": "paused",
                                "reason_code": "provider_quota_insufficient",
                            }
                        }
                        if job.status == "start_paused_quota"
                        else {"quota_interrupt": None}
                    ),
                },
                pending_operation_job_ids=(job.job_id,),
            )

        result = job.result if isinstance(job.result, Mapping) else {}
        next_global = result.get("global_assets") if isinstance(result.get("global_assets"), Mapping) else global_assets
        next_scenes = result.get("scene_packages") if isinstance(result.get("scene_packages"), list) else scene_packages
        failed = result.get("failed_assets") if isinstance(result.get("failed_assets"), list) else []
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=str(result.get("message") or "参考图生成完成"),
            workspace_patch={
                "global_assets": dict(next_global),
                "scenes": list(next_scenes),
                "scene_packages": list(next_scenes),
                "scene_asset_failures": list(failed),
                "scene_asset_job": {
                    "job_id": job.job_id,
                    "plan_step_id": context.step_id,
                    "status": "succeeded",
                    "image_model": request.image_model,
                },
                "quota_interrupt": None,
            },
            pending_operation_job_ids=(),
        )
