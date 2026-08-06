"""VideoAgent 镜头检查、修订、素材替换与定向生成工具。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.contracts.plan import VideoAgentContract

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

_ARTIFACT_PATTERN = r"^artifact:[A-Za-z0-9._:-]+$"
_SCENE_ASSET_REFERENCE_FIELDS = frozenset(
    {
        "asset_refs",
        "assets",
        "character_refs",
        "location_refs",
        "material_refs",
        "prop_refs",
        "reference_asset_refs",
        "scene_asset_refs",
    }
)


class SceneIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str = Field(min_length=1, max_length=128)


class SceneMutablePatch(BaseModel):
    """只允许用户修改会影响单镜生成语义的公开字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    storyline: str | None = Field(default=None, max_length=4_000)
    prompt: str | None = Field(default=None, max_length=10_000)
    narration: str | None = Field(default=None, max_length=4_000)
    narration_text: str | None = Field(default=None, max_length=4_000)
    onscreen_text: str | None = Field(default=None, max_length=2_000)
    transition: str | None = Field(default=None, max_length=512)
    shot_type: str | None = Field(default=None, max_length=256)
    camera_movement: str | None = Field(default=None, max_length=512)
    duration_sec: float | None = Field(default=None, gt=0, le=15)
    asset_refs: tuple[str, ...] | None = Field(default=None, max_length=12)

    @model_validator(mode="after")
    def validate_patch(self) -> SceneMutablePatch:
        if not self.model_fields_set:
            raise ValueError("镜头补丁不能为空")
        if any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError("镜头补丁不能把字段写为 null")
        if self.asset_refs is not None:
            for value in self.asset_refs:
                if not _is_artifact_ref(value):
                    raise ValueError("asset_refs 必须是内部 Artifact 引用")
            if len(set(self.asset_refs)) != len(self.asset_refs):
                raise ValueError("asset_refs 不能重复")
        return self


class PatchSceneInput(SceneIdInput):
    patch: SceneMutablePatch


class AssetReplacement(VideoAgentContract):
    source_asset_ref: str = Field(pattern=_ARTIFACT_PATTERN, max_length=256)
    target_asset_ref: str = Field(pattern=_ARTIFACT_PATTERN, max_length=256)

    @model_validator(mode="after")
    def validate_distinct_assets(self) -> AssetReplacement:
        if self.source_asset_ref == self.target_asset_ref:
            raise ValueError("替换前后素材不能相同")
        return self


class ReplaceProjectAssetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    replacements: tuple[AssetReplacement, ...] = Field(min_length=1, max_length=20)


class GenerateScenesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    variant_count: int = Field(default=3, ge=1, le=3)
    attempt: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> GenerateScenesInput:
        if len(set(self.scene_ids)) != len(self.scene_ids):
            raise ValueError("scene_ids 不能重复")
        return self


class ReviewGeneratedSceneInput(SceneIdInput):
    variant_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject"]


class SceneGenerationJob(VideoAgentContract):
    """定向生成 Operation Port 返回的安全任务引用。"""

    job_id: str = Field(min_length=1, max_length=128)
    scene_id: str = Field(min_length=1, max_length=128)
    variant_index: int = Field(ge=1, le=3)
    status: Literal["polling", "succeeded"]
    variant_id: str | None = Field(default=None, max_length=128)
    artifact_ref: str | None = Field(default=None, pattern=_ARTIFACT_PATTERN, max_length=256)
    video_url: str | None = Field(default=None, max_length=4_096)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_terminal_result(self) -> SceneGenerationJob:
        if self.status == "succeeded" and (
            self.variant_id is None
            or self.artifact_ref is None
            or self.video_url is None
            or self.completed_at is None
        ):
            raise ValueError("已完成生成任务必须包含版本、产物、视频和完成时间")
        if self.video_url is not None:
            parsed = urlparse(self.video_url)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("镜头视频必须是无签名参数的安全HTTPS URL")
        return self


class SceneGenerationOperationPort(Protocol):
    """隔离工具与M06 Operation、一次性凭据及供应商Client。"""

    async def start_scene_variant(
        self,
        context: VideoToolContext,
        *,
        scene: Mapping[str, JsonValue],
        variant_index: int,
        attempt: int,
    ) -> SceneGenerationJob: ...


class InspectSceneTool:
    spec = VideoToolSpec(
        name="inspect_scene",
        description="读取单个镜头的公开质检证据与可修复建议",
        input_model=SceneIdInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("qc",),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(SceneIdInput, arguments, "镜头检查参数无效")
        scene = _required_scene(context.workspace.payload, request.scene_id)
        qc = _qc_records(context.workspace.payload.get("qc"))
        existing = qc.get(request.scene_id, {})
        issues = _text_list(existing.get("issues") or scene.get("qc_issues"))
        evidence_refs = _artifact_refs(
            existing.get("evidence_refs"),
            scene.get("artifact_refs"),
            scene.get("variants"),
        )
        affected_assets = _artifact_refs(
            existing.get("affected_assets"),
            scene.get("asset_refs"),
            scene.get("assets"),
        )
        suggestion = str(
            existing.get("repair_suggestion")
            or scene.get("repair_suggestion")
            or "核对镜头描述、引用素材和质检问题后再定向生成。"
        )[:2_000]
        evidence: dict[str, JsonValue] = {
            "scene_id": request.scene_id,
            "issues": issues,
            "evidence_refs": evidence_refs,
            "repair_suggestion": suggestion,
            "affected_assets": affected_assets,
            "status": "repairable" if issues else "inspected",
        }
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"镜头 {request.scene_id} 已整理 {len(issues)} 项公开质检证据",
            workspace_patch={"qc": {**qc, request.scene_id: evidence}},
            artifact_refs=tuple(evidence_refs),
        )


class PatchSceneTool:
    spec = VideoToolSpec(
        name="patch_scene",
        description="只修改指定镜头允许变更的创作字段",
        input_model=PatchSceneInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("scenes", "dirty_scene_ids", "qc"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(PatchSceneInput, arguments, "镜头补丁参数无效")
        scenes = _scene_records(context.workspace.payload.get("scenes"))
        target = _find_scene(scenes, request.scene_id)
        patch = request.patch.model_dump(mode="json", exclude_unset=True)
        updated = {**target, **patch, "edit_status": "待重新生成"}
        next_scenes = [
            updated if scene.get("scene_id") == request.scene_id else scene
            for scene in scenes
        ]
        dirty = _ordered_unique(
            [*_text_list(context.workspace.payload.get("dirty_scene_ids")), request.scene_id]
        )
        qc = _qc_records(context.workspace.payload.get("qc"))
        previous_qc = qc.get(request.scene_id, {})
        qc[request.scene_id] = {
            **previous_qc,
            "scene_id": request.scene_id,
            "status": "dirty",
            "repair_suggestion": str(
                previous_qc.get("repair_suggestion") or "按当前镜头补丁定向重新生成。"
            )[:2_000],
        }
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"镜头 {request.scene_id} 已更新并标记为待重新生成",
            workspace_patch={
                "scenes": next_scenes,
                "dirty_scene_ids": dirty,
                "qc": qc,
            },
        )


class ReplaceProjectAssetsTool:
    spec = VideoToolSpec(
        name="replace_project_assets",
        description="替换项目内部素材引用并标记受影响镜头",
        input_model=ReplaceProjectAssetsInput,
        cost_level=VideoToolCostLevel.DESTRUCTIVE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("scenes", "dirty_scene_ids", "asset_replacements"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(
            ReplaceProjectAssetsInput,
            arguments,
            "素材替换参数无效",
        )
        available_refs = set(
            _artifact_refs(
                context.workspace.payload.get("assets"),
                context.workspace.payload.get("materials"),
            )
        )
        replacements = {
            item.source_asset_ref: item.target_asset_ref
            for item in request.replacements
        }
        missing_targets = sorted(set(replacements.values()).difference(available_refs))
        if missing_targets:
            raise VideoToolValidationError("替换目标素材不存在")
        scenes = _scene_records(context.workspace.payload.get("scenes"))
        affected: list[str] = []
        next_scenes: list[dict[str, JsonValue]] = []
        for scene in scenes:
            next_scene, changed = _replace_scene_asset_refs(scene, replacements)
            next_scenes.append(next_scene)
            scene_id = str(scene.get("scene_id") or "")
            if changed and scene_id:
                affected.append(scene_id)
        if not affected:
            raise VideoToolValidationError("没有镜头引用待替换素材")
        dirty = _ordered_unique(
            [*_text_list(context.workspace.payload.get("dirty_scene_ids")), *affected]
        )
        audit = [
            {
                "source_asset_ref": source,
                "target_asset_ref": target,
                "affected_scene_ids": affected,
            }
            for source, target in replacements.items()
        ]
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已替换素材引用，影响 {len(affected)} 个镜头",
            workspace_patch={
                "scenes": next_scenes,
                "dirty_scene_ids": dirty,
                "asset_replacements": audit,
            },
            artifact_refs=tuple(replacements.values()),
            requires_confirmation=True,
        )


class GenerateScenesTool:
    spec = VideoToolSpec(
        name="generate_scenes",
        description="按镜头和版本数量启动可恢复的定向生成任务",
        input_model=GenerateScenesInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("scenes", "dirty_scene_ids", "assets"),
    )

    def __init__(
        self,
        *,
        operation_port: SceneGenerationOperationPort | None = None,
    ) -> None:
        self._operation_port = operation_port

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(GenerateScenesInput, arguments, "镜头生成参数无效")
        scenes = _scene_records(context.workspace.payload.get("scenes"))
        selected = [_find_scene(scenes, scene_id) for scene_id in request.scene_ids]
        if self._operation_port is None:
            raise VideoToolExecutionError("镜头生成Operation尚未装配")
        jobs_by_scene: dict[str, list[SceneGenerationJob]] = {
            scene_id: [] for scene_id in request.scene_ids
        }
        for scene in selected:
            scene_id = str(scene["scene_id"])
            for variant_index in range(1, request.variant_count + 1):
                job = await self._operation_port.start_scene_variant(
                    context,
                    scene=scene,
                    variant_index=variant_index,
                    attempt=request.attempt,
                )
                if job.scene_id != scene_id or job.variant_index != variant_index:
                    raise VideoToolExecutionError("镜头生成Operation结果身份不一致")
                jobs_by_scene[scene_id].append(job)
        next_scenes: list[dict[str, JsonValue]] = []
        for scene in scenes:
            scene_id = str(scene.get("scene_id") or "")
            jobs = jobs_by_scene.get(scene_id)
            if jobs is None:
                next_scenes.append(scene)
                continue
            existing_variants = _record_list(scene.get("variants"))
            generated_variants = [
                {
                    "variant_id": job.variant_id,
                    "artifact_ref": job.artifact_ref,
                    "video_url": job.video_url,
                    "review_status": "pending",
                    "completed_at": (
                        job.completed_at.isoformat() if job.completed_at else None
                    ),
                    "source_job_id": job.job_id,
                }
                for job in jobs
                if job.status == "succeeded"
            ]
            next_scenes.append(
                {
                    **scene,
                    "generation_jobs": [job.model_dump(mode="json") for job in jobs],
                    "variants": [*existing_variants, *generated_variants],
                    "edit_status": (
                        "等待版本审核" if generated_variants else "重新生成中"
                    ),
                }
            )
        assets = _record_list(context.workspace.payload.get("assets"))
        generated_assets = [
            {
                "artifact_ref": job.artifact_ref,
                "media_type": "video",
                "url": job.video_url,
                "source_job_id": job.job_id,
                "scene_id": job.scene_id,
                "variant_id": job.variant_id,
            }
            for jobs in jobs_by_scene.values()
            for job in jobs
            if job.status == "succeeded"
        ]
        generated_refs = {
            str(item["artifact_ref"])
            for item in generated_assets
            if item.get("artifact_ref")
        }
        workspace_patch: dict[str, JsonValue] = {
            "scenes": next_scenes,
            "dirty_scene_ids": _ordered_unique(
                [
                    *_text_list(context.workspace.payload.get("dirty_scene_ids")),
                    *request.scene_ids,
                ]
            ),
        }
        if generated_assets:
            workspace_patch["assets"] = [
                *[
                    item
                    for item in assets
                    if item.get("artifact_ref") not in generated_refs
                ],
                *generated_assets,
            ]
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已为 {len(selected)} 个镜头启动 {request.variant_count} 版定向生成",
            workspace_patch=workspace_patch,
            artifact_refs=tuple(
                job.artifact_ref
                for jobs in jobs_by_scene.values()
                for job in jobs
                if job.artifact_ref is not None
            ),
            pending_operation_job_ids=tuple(
                job.job_id
                for jobs in jobs_by_scene.values()
                for job in jobs
                if job.status == "polling"
            ),
            requires_confirmation=True,
        )


class ReviewGeneratedScenesTool:
    spec = VideoToolSpec(
        name="review_generated_scenes",
        description="审核指定镜头版本并保留其他镜头和历史版本",
        input_model=ReviewGeneratedSceneInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("scenes", "dirty_scene_ids", "qc"),
    )

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(
            ReviewGeneratedSceneInput,
            arguments,
            "镜头版本审核参数无效",
        )
        scenes = _scene_records(context.workspace.payload.get("scenes"))
        target = _find_scene(scenes, request.scene_id)
        variants = _record_list(target.get("variants"))
        if not any(item.get("variant_id") == request.variant_id for item in variants):
            raise VideoToolValidationError("镜头版本不存在")
        next_variants: list[dict[str, JsonValue]] = []
        for item in variants:
            is_target = item.get("variant_id") == request.variant_id
            if request.decision == "approve":
                next_variants.append(
                    {
                        **item,
                        "selected": is_target,
                        "review_status": (
                            "approved"
                            if is_target
                            else item.get("review_status", "pending")
                        ),
                    }
                )
            else:
                next_variants.append(
                    {
                        **item,
                        **(
                            {"selected": False, "review_status": "rejected"}
                            if is_target
                            else {}
                        ),
                    }
                )
        updated: dict[str, JsonValue] = {**target, "variants": next_variants}
        dirty = _text_list(context.workspace.payload.get("dirty_scene_ids"))
        qc = _qc_records(context.workspace.payload.get("qc"))
        if request.decision == "approve":
            updated.update(
                {
                    "approved_variant_id": request.variant_id,
                    "edit_status": "重新生成完成",
                    "regenerated_at": self._clock().isoformat(),
                }
            )
            dirty = [scene_id for scene_id in dirty if scene_id != request.scene_id]
            if request.scene_id in qc:
                qc[request.scene_id] = {
                    **qc[request.scene_id],
                    "status": "resolved",
                    "resolved_variant_id": request.variant_id,
                    "resolved_at": updated["regenerated_at"],
                }
        elif updated.get("approved_variant_id") == request.variant_id:
            updated.pop("approved_variant_id", None)
        next_scenes = [
            updated if scene.get("scene_id") == request.scene_id else scene
            for scene in scenes
        ]
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=(
                f"镜头 {request.scene_id} 已选用版本 {request.variant_id}"
                if request.decision == "approve"
                else f"镜头 {request.scene_id} 已废弃版本 {request.variant_id}"
            ),
            workspace_patch={
                "scenes": next_scenes,
                "dirty_scene_ids": dirty,
                "qc": qc,
            },
        )


def _validate(model: type[BaseModel], arguments: Mapping[str, object], message: str):
    try:
        return model.model_validate(dict(arguments))
    except ValidationError as exc:
        raise VideoToolValidationError(message) from exc


def _scene_records(value: object) -> list[dict[str, JsonValue]]:
    records = _record_list(value)
    seen: set[str] = set()
    for record in records:
        scene_id = str(record.get("scene_id") or "").strip()
        if not scene_id or scene_id in seen:
            raise VideoToolValidationError("工作区镜头身份无效")
        seen.add(scene_id)
    return records


def _record_list(value: object) -> list[dict[str, JsonValue]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _find_scene(
    scenes: Sequence[dict[str, JsonValue]],
    scene_id: str,
) -> dict[str, JsonValue]:
    scene = next((item for item in scenes if item.get("scene_id") == scene_id), None)
    if scene is None:
        raise VideoToolValidationError("镜头不存在")
    return scene


def _required_scene(
    payload: Mapping[str, object],
    scene_id: str,
) -> dict[str, JsonValue]:
    return _find_scene(_scene_records(payload.get("scenes")), scene_id)


def _qc_records(value: object) -> dict[str, dict[str, JsonValue]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(scene_id): dict(record)
        for scene_id, record in value.items()
        if isinstance(scene_id, str) and isinstance(record, dict)
    }


def _text_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _ordered_unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _is_artifact_ref(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("artifact:"):
        return False
    suffix = value.removeprefix("artifact:")
    return bool(suffix) and all(
        character.isalnum() or character in "._:-" for character in suffix
    )


def _artifact_refs(*values: object) -> list[str]:
    found: list[str] = []

    def visit(value: object) -> None:
        if _is_artifact_ref(value):
            found.append(str(value))
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    for value in values:
        visit(value)
    return _ordered_unique(found)


def _replace_artifact_values(
    value: JsonValue,
    replacements: Mapping[str, str],
) -> tuple[JsonValue, bool]:
    if isinstance(value, str):
        replacement = replacements.get(value)
        return (replacement, True) if replacement is not None else (value, False)
    if isinstance(value, list):
        changed = False
        result: list[JsonValue] = []
        for item in value:
            next_item, item_changed = _replace_artifact_values(item, replacements)
            result.append(next_item)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, dict):
        changed = False
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            next_item, item_changed = _replace_artifact_values(item, replacements)
            result[key] = next_item
            changed = changed or item_changed
        return result, changed
    return value, False


def _replace_scene_asset_refs(
    scene: Mapping[str, JsonValue],
    replacements: Mapping[str, str],
) -> tuple[dict[str, JsonValue], bool]:
    """只替换声明为输入素材的字段，保留历史版本和生成任务证据。"""

    result = dict(scene)
    changed = False
    for field_name in _SCENE_ASSET_REFERENCE_FIELDS:
        if field_name not in result:
            continue
        value, field_changed = _replace_artifact_values(
            result[field_name],
            replacements,
        )
        result[field_name] = value
        changed = changed or field_changed
    return result, changed
