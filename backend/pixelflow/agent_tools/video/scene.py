"""VideoAgent 镜头检查、修订、素材替换与定向生成工具。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator, model_validator

from pixelflow.generation_jobs.service import GenerationJobService
from pixelflow.video.contracts import VideoAgentContract, VideoToolResult

from .contracts import (
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
    # Seedance 单镜最长 30 秒；整条视频的总时长由分镜编排合同另行限制。
    duration_sec: float | None = Field(default=None, gt=0, le=30)
    duration_ms: int | None = Field(default=None, ge=1_000, le=30_000)
    title: str | None = Field(default=None, max_length=256)
    # FE 分镜面板编辑镜头描述文本；落库时同步到 shot_description.text 与 prompt。
    # 允许模型误传 {text, mentions} 对象，只取 text。
    shot_description: str | None = Field(default=None, max_length=10_000)
    # FE「参考素材：character-1、scene-x」；写入 reference_asset_ids，并尽量对齐 mentions。
    reference_asset_ids: tuple[str, ...] | None = Field(default=None, max_length=12)
    asset_refs: tuple[str, ...] | None = Field(default=None, max_length=12)

    @field_validator("shot_description", mode="before")
    @classmethod
    def coerce_shot_description(cls, value: object) -> object:
        if isinstance(value, Mapping):
            text = value.get("text")
            return str(text) if text is not None else ""
        return value

    @field_validator("reference_asset_ids", mode="before")
    @classmethod
    def coerce_reference_asset_ids(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            parts = [part.strip() for part in re.split(r"[、,，\s]+", value) if part.strip()]
            return tuple(parts[:12])
        if isinstance(value, (list, tuple)):
            parts = [str(item).strip() for item in value if str(item).strip()]
            return tuple(parts[:12])
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> SceneMutablePatch:
        if not self.model_fields_set:
            raise ValueError("镜头补丁不能为空")
        if any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError("镜头补丁不能把字段写为 null")
        if self.reference_asset_ids is not None:
            if any(not item.strip() for item in self.reference_asset_ids):
                raise ValueError("reference_asset_ids 不能包含空标识")
            if len(set(self.reference_asset_ids)) != len(self.reference_asset_ids):
                raise ValueError("reference_asset_ids 不能重复")
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


class SceneAssetReplacementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["digital_human", "image_asset", "local_upload"]
    display_image_url: str = Field(min_length=1, max_length=4_096)
    generation_reference_url: str = Field(min_length=1, max_length=4_096)
    third_asset_id: str | None = Field(default=None, max_length=256)
    asset_type: str | None = Field(default=None, max_length=64)
    content_asset_id: str | None = Field(default=None, max_length=256)
    asset_name: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_references(self) -> SceneAssetReplacementPatch:
        if not _is_https_url(self.display_image_url):
            raise ValueError("display_image_url 必须是 HTTPS 图片")
        if self.source == "digital_human":
            third_asset_id = str(self.third_asset_id or "").strip().removeprefix("asset://")
            if not third_asset_id or self.generation_reference_url != f"asset://{third_asset_id}":
                raise ValueError("数字人生成引用必须与 third_asset_id 一致")
        elif not _is_https_url(self.generation_reference_url):
            raise ValueError("图片素材生成引用必须是 HTTPS URL")
        return self


class ReplaceSceneAssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_group: Literal["characters", "scenes", "props"]
    asset_id: str = Field(min_length=1, max_length=256)
    replacement: SceneAssetReplacementPatch


class GenerateScenesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Agent 可以选择任意已规划镜头；Gateway Worker 统一限制并发任务数。
    scene_ids: tuple[str, ...] = Field(default=(), max_length=120)
    variant_count: int = Field(default=1, ge=1, le=3)
    attempt: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> GenerateScenesInput:
        if len(set(self.scene_ids)) != len(self.scene_ids):
            raise ValueError("scene_ids 不能重复")
        return self


def _require_complete_creation_contract(payload: Mapping[str, object]) -> None:
    """在创建 GenerationJob 前拒绝不完整的 Provider 路由。"""

    contract = payload.get("creation_contract")
    source = contract if isinstance(contract, Mapping) else {}
    required = ("video_model", "video_ratio", "video_size", "video_sound")
    missing = [key for key in required if not isinstance(source.get(key), str) or not str(source[key]).strip()]
    if str(source.get("video_sound") or "").strip().lower() not in {"on", "off"}:
        if "video_sound" not in missing:
            missing.append("video_sound")
    if missing:
        raise VideoToolValidationError(
            "当前工作区尚未冻结视频生产合同（缺少 "
            + "、".join(missing)
            + "）；请先由 Agent 选择参数并调用 set_video_generation_contract"
        )


class ReviewGeneratedSceneInput(SceneIdInput):
    variant_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject"]


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
        description="只修改指定镜头允许变更的创作字段，并写入 dirty_scene_ids",
        input_model=PatchSceneInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("scenes", "scene_packages", "dirty_scene_ids", "qc"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(PatchSceneInput, arguments, "镜头补丁参数无效")
        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        scenes = _workspace_scenes(payload)
        target = _find_scene(scenes, request.scene_id)
        patch = request.patch.model_dump(mode="json", exclude_unset=True)
        if "duration_ms" in patch and "duration_sec" not in patch:
            patch["duration_sec"] = float(patch["duration_ms"]) / 1000.0
        if "duration_sec" in patch and "duration_ms" not in patch:
            patch["duration_ms"] = int(round(float(patch["duration_sec"]) * 1000))
        if "narration" in patch and "narration_text" not in patch:
            patch["narration_text"] = patch["narration"]
        # 镜头描述：写入嵌套 shot_description，并补 prompt 供成片生成读取。
        if "shot_description" in patch:
            shot_text = str(patch.pop("shot_description") or "").strip()
            existing_shot = target.get("shot_description")
            if isinstance(existing_shot, Mapping):
                patch["shot_description"] = {
                    **dict(existing_shot),
                    "text": shot_text,
                }
            else:
                patch["shot_description"] = {"text": shot_text, "mentions": []}
            if "prompt" not in patch:
                patch["prompt"] = shot_text
        # 参考素材 ID：写入镜头，并按已有 mentions / 全局资产尽量对齐 chip。
        if "reference_asset_ids" in patch:
            raw_ids = patch.get("reference_asset_ids")
            next_ids = [
                str(item).strip()
                for item in (raw_ids if isinstance(raw_ids, (list, tuple)) else [])
                if str(item).strip()
            ][:12]
            patch["reference_asset_ids"] = next_ids
            shot_obj = patch.get("shot_description")
            if not isinstance(shot_obj, dict):
                existing_shot = target.get("shot_description")
                shot_obj = dict(existing_shot) if isinstance(existing_shot, Mapping) else {"text": "", "mentions": []}
                patch["shot_description"] = shot_obj
            existing_mentions = shot_obj.get("mentions")
            mention_by_id: dict[str, dict[str, object]] = {}
            if isinstance(existing_mentions, list):
                for item in existing_mentions:
                    if not isinstance(item, Mapping):
                        continue
                    asset_id = str(item.get("asset_id") or "").strip()
                    if asset_id:
                        mention_by_id[asset_id] = dict(item)
            global_assets = payload.get("global_assets")
            name_by_id = _global_asset_names(global_assets)
            image_by_id = _global_asset_image_urls(global_assets)
            next_mentions: list[dict[str, object]] = []
            for asset_id in next_ids:
                if asset_id in mention_by_id:
                    mention = dict(mention_by_id[asset_id])
                    if not str(mention.get("image_url") or mention.get("url") or "").strip():
                        image_url = image_by_id.get(asset_id)
                        if image_url:
                            mention["image_url"] = image_url
                    next_mentions.append(mention)
                    continue
                mention: dict[str, object] = {
                    "asset_id": asset_id,
                    "name": name_by_id.get(asset_id) or asset_id,
                }
                image_url = image_by_id.get(asset_id)
                if image_url:
                    mention["image_url"] = image_url
                next_mentions.append(mention)
            shot_obj["mentions"] = next_mentions
            # 同步 image_urls，供 generate_scenes / 旧路径直接读取。
            patch["image_urls"] = [
                str(item.get("image_url") or item.get("url") or "").strip()
                for item in next_mentions
                if str(item.get("image_url") or item.get("url") or "").strip().lower().startswith("https://")
            ][:9]
        updated = {**target, **patch, "edit_status": "待重新生成"}
        next_scenes = [
            updated if scene.get("scene_id") == request.scene_id else scene
            for scene in scenes
        ]
        dirty = _ordered_unique(
            [*_text_list(payload.get("dirty_scene_ids")), request.scene_id]
        )
        qc = _qc_records(payload.get("qc"))
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
            workspace_patch=_scenes_workspace_patch(
                next_scenes,
                dirty_scene_ids=dirty,
                qc=qc,
                only_scene_ids=(request.scene_id,),
            ),
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
        workspace_mutations=("scenes", "scene_packages", "dirty_scene_ids", "asset_replacements"),
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
        scenes = _workspace_scenes(
            context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        )
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
                **_scenes_workspace_patch(
                    next_scenes,
                    dirty_scene_ids=dirty,
                    only_scene_ids=affected,
                ),
                "asset_replacements": audit,
            },
            artifact_refs=tuple(replacements.values()),
            requires_confirmation=True,
        )


class ReplaceSceneAssetTool:
    spec = VideoToolSpec(
        name="replace_scene_asset",
        description="替换场景包中指定的角色、场景或道具素材，并标记引用它的镜头待重新生成",
        input_model=ReplaceSceneAssetInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("global_assets", "scenes", "scene_packages", "dirty_scene_ids"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(
            ReplaceSceneAssetInput,
            arguments,
            "场景包素材替换参数无效",
        )
        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        global_assets = payload.get("global_assets")
        if not isinstance(global_assets, Mapping):
            raise VideoToolValidationError("工作区尚无可替换的场景包素材")
        group_records = _record_list(global_assets.get(request.asset_group))
        replacement = request.replacement.model_dump(mode="json", exclude_none=True)
        found = False
        next_group: list[dict[str, JsonValue]] = []
        for asset in group_records:
            current_id = str(asset.get("asset_id") or asset.get("id") or "").strip()
            if current_id != request.asset_id:
                next_group.append(asset)
                continue
            found = True
            image_key = "three_view_images" if request.asset_group == "characters" else "images"
            next_group.append(
                {
                    **asset,
                    image_key: [replacement["display_image_url"]],
                    "image_url": replacement["display_image_url"],
                    "url": replacement["display_image_url"],
                    "generation_reference_url": replacement["generation_reference_url"],
                    "replacement_source": replacement["source"],
                    **(
                        {"third_asset_id": replacement["third_asset_id"]}
                        if replacement.get("third_asset_id")
                        else {}
                    ),
                    **(
                        {"replacement_asset_type": replacement["asset_type"]}
                        if replacement.get("asset_type")
                        else {}
                    ),
                    **(
                        {"replacement_asset_id": replacement["content_asset_id"]}
                        if replacement.get("content_asset_id")
                        else {}
                    ),
                    **(
                        {"replacement_asset_name": replacement["asset_name"]}
                        if replacement.get("asset_name")
                        else {}
                    ),
                }
            )
        if not found:
            raise VideoToolValidationError("待替换的场景包素材不存在")

        affected: list[str] = []
        next_scenes: list[dict[str, JsonValue]] = []
        for scene in _workspace_scenes(payload):
            next_scene, changed = _replace_scene_package_asset_mention(
                scene,
                asset_id=request.asset_id,
                replacement=replacement,
            )
            if changed:
                scene_id = str(scene.get("scene_id") or "").strip()
                if scene_id:
                    affected.append(scene_id)
                next_scenes.append(next_scene)

        next_global_assets = dict(global_assets)
        next_global_assets[request.asset_group] = next_group
        dirty = _ordered_unique([*_text_list(payload.get("dirty_scene_ids")), *affected])
        workspace_patch: dict[str, JsonValue] = {
            "global_assets": next_global_assets,
            "dirty_scene_ids": dirty,
        }
        if affected:
            workspace_patch.update(
                _scenes_workspace_patch(
                    next_scenes,
                    dirty_scene_ids=dirty,
                    only_scene_ids=affected,
                )
            )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"素材已替换，影响 {len(affected)} 个镜头",
            workspace_patch=workspace_patch,
        )


class GenerateScenesTool:
    spec = VideoToolSpec(
        name="generate_scenes",
        description="仅生成指定脏镜头或明确 scene_ids；未传 scene_ids 时使用 workspace dirty_scene_ids",
        input_model=GenerateScenesInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.GENERATION_JOB,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        # scene_video_progress：提交时写入 0/N，供前端立刻切到分镜视频进度板。
        workspace_mutations=(
            "scenes",
            "scene_packages",
            "dirty_scene_ids",
            "scene_video_progress",
        ),
        model_observation_keys=(
            "status",
            "generation_job_ids",
            "scene_ids",
            "workspace_revision_required",
        ),
    )

    def __init__(
        self,
        *,
        generation_job_service: GenerationJobService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._generation_job_service = generation_job_service
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = _validate(GenerateScenesInput, arguments, "镜头生成参数无效")
        if self._generation_job_service is not None and self._generation_job_service.video_available:
            return await self._execute_generation_jobs(context, request)
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="视频生成能力当前未装配，请等待 Gateway 配置视频 Provider。",
            model_observation={"status": "unavailable", "scene_ids": list(request.scene_ids)},
        )

    async def _execute_generation_jobs(
        self,
        context: VideoToolContext,
        request: GenerateScenesInput,
    ) -> VideoToolResult:
        """创建独立 GenerationJob，并把 queued 任务写入 Workspace。"""

        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        from pixelflow.video.workspace.digest import summarize_scene_asset_status

        asset_status = summarize_scene_asset_status(payload)
        required_assets = int(asset_status["scene_asset_required_count"])
        if required_assets > 0 and asset_status["scene_assets_ready"] is not True:
            raise VideoToolValidationError(
                "参考图仅完成 "
                f"{asset_status['scene_asset_ready_count']}/{required_assets}，"
                "请先继续生成剩余角色/场景/道具参考图"
            )
        scenes = _workspace_scenes(payload)
        scene_ids = list(request.scene_ids) or _text_list(payload.get("dirty_scene_ids"))
        if not scene_ids:
            raise VideoToolValidationError("没有可生成的脏镜头，请先 patch_scene 或传入 scene_ids")
        selected = [_find_scene(scenes, scene_id) for scene_id in scene_ids]
        _require_complete_creation_contract(payload)
        submissions = await self._generation_job_service.submit_videos(
            context,
            scenes=tuple(selected),
            variant_count=request.variant_count,
            attempt=request.attempt,
        )
        jobs_by_scene: dict[str, list[dict[str, JsonValue]]] = {
            scene_id: [] for scene_id in scene_ids
        }
        for submission in submissions:
            if submission.item_id not in jobs_by_scene:
                raise VideoToolExecutionError("视频生成任务身份不一致")
            jobs_by_scene[submission.item_id].append(
                {
                    "job_id": submission.job_id,
                    "scene_id": submission.item_id,
                    "variant_index": submission.variant_index,
                    "status": submission.status.value,
                    "plan_step_id": context.step_id,
                }
            )
        next_scenes: list[dict[str, JsonValue]] = []
        for scene in scenes:
            scene_id = str(scene.get("scene_id") or "")
            scene_jobs = jobs_by_scene.get(scene_id)
            if scene_jobs is None:
                next_scenes.append(scene)
                continue
            next_scenes.append(
                {
                    **scene,
                    "generation_jobs": [
                        *_record_list(scene.get("generation_jobs")),
                        *scene_jobs,
                    ],
                    "variants": _record_list(scene.get("variants")),
                    "edit_status": "重新生成中",
                }
            )
        workspace_patch = _scenes_workspace_patch(
            next_scenes,
            dirty_scene_ids=_ordered_unique(
                [*_text_list(payload.get("dirty_scene_ids")), *scene_ids]
            ),
            only_scene_ids=scene_ids,
        )
        progress_scene_id = scene_ids[0] if len(scene_ids) == 1 else None
        workspace_patch["scene_video_progress"] = {
            "completed": 0,
            "total": len(submissions),
            "scene_id": progress_scene_id,
            "scene_index": next(
                (
                    scene.get("scene_index")
                    for scene in selected
                    if str(scene.get("scene_id") or "") == progress_scene_id
                    and isinstance(scene.get("scene_index"), int)
                ),
                None,
            ),
            "ok": True,
        }
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已创建 {len(submissions)} 个视频生成任务，等待 Gateway Worker 启动。",
            workspace_patch=workspace_patch,
            pending_generation_job_ids=tuple(item.job_id for item in submissions),
            requires_confirmation=True,
            model_observation={
                "status": "submitted",
                "generation_job_ids": [item.job_id for item in submissions],
                "scene_ids": scene_ids,
                "workspace_revision_required": True,
            },
        )


class CreateVideoTool(GenerateScenesTool):
    """创建当前 Workspace 已确认分镜的视频；复用 GenerationJob 提交 Service。"""

    spec = VideoToolSpec(
        name="create_video",
        description=(
            "为当前 Workspace 已确认的指定分镜创建视频生成批次。支持文生、图生、首尾帧、"
            "多参考、编辑与延展模式；具体模式由分镜素材和 generation_mode 决定。"
        ),
        input_model=GenerateScenesInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.GENERATION_JOB,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=(
            "scenes",
            "scene_packages",
            "dirty_scene_ids",
            "scene_video_progress",
        ),
        model_observation_keys=(
            "status",
            "generation_job_ids",
            "scene_ids",
            "workspace_revision_required",
        ),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        result = await super().execute(context, arguments)
        return result.model_copy(update={"tool_name": self.spec.name})


class ReviewGeneratedScenesTool:
    spec = VideoToolSpec(
        name="review_generated_scenes",
        description="审核指定镜头版本并保留其他镜头和历史版本",
        input_model=ReviewGeneratedSceneInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("scenes", "scene_packages", "dirty_scene_ids", "qc"),
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
        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        scenes = _workspace_scenes(payload)
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
        dirty = _text_list(payload.get("dirty_scene_ids"))
        qc = _qc_records(payload.get("qc"))
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
            workspace_patch=_scenes_workspace_patch(
                next_scenes,
                dirty_scene_ids=dirty,
                qc=qc,
                only_scene_ids=(request.scene_id,),
            ),
        )


def _global_asset_names(value: object) -> dict[str, str]:
    """从 global_assets 抽出 asset_id → 展示名，供 patch mentions 对齐。"""

    if not isinstance(value, Mapping):
        return {}
    names: dict[str, str] = {}
    for key in ("characters", "scenes", "props"):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            if not asset_id:
                continue
            name = str(item.get("name") or "").strip() or asset_id
            names[asset_id] = name
    return names


def _global_asset_image_urls(value: object) -> dict[str, str]:
    """从 global_assets 抽出 asset_id → HTTPS 图片 URL。"""

    if not isinstance(value, Mapping):
        return {}
    urls: dict[str, str] = {}
    for key in ("characters", "scenes", "props"):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            if not asset_id or asset_id in urls:
                continue
            url = _first_https_asset_url(item)
            if url:
                urls[asset_id] = url
    return urls


def _first_https_asset_url(item: Mapping[str, object]) -> str | None:
    for key in ("image_url", "url", "generation_reference_url"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip().lower().startswith("https://"):
            return raw.strip()
    for key in ("images", "three_view_images", "image_urls"):
        values = item.get(key)
        if isinstance(values, str) and values.strip().lower().startswith("https://"):
            return values.strip()
        if not isinstance(values, (list, tuple)):
            continue
        for entry in values:
            if isinstance(entry, Mapping):
                for nested in ("url", "image_url", "src"):
                    raw = entry.get(nested)
                    if isinstance(raw, str) and raw.strip().lower().startswith("https://"):
                        return raw.strip()
            elif isinstance(entry, str) and entry.strip().lower().startswith("https://"):
                return entry.strip()
    return None


def _is_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def _replace_scene_package_asset_mention(
    scene: Mapping[str, JsonValue],
    *,
    asset_id: str,
    replacement: Mapping[str, JsonValue],
) -> tuple[dict[str, JsonValue], bool]:
    result = dict(scene)
    reference_ids = _text_list(scene.get("reference_asset_ids"))
    shot = scene.get("shot_description")
    mentions = shot.get("mentions") if isinstance(shot, Mapping) else None
    mention_changed = False
    next_mentions: list[JsonValue] = []
    if isinstance(mentions, list):
        for mention in mentions:
            if not isinstance(mention, Mapping):
                next_mentions.append(mention)
                continue
            mention_id = str(
                mention.get("asset_id") or mention.get("assetId") or mention.get("id") or ""
            ).strip()
            if mention_id != asset_id:
                next_mentions.append(dict(mention))
                continue
            mention_changed = True
            next_mention = {
                **dict(mention),
                "image_url": replacement["display_image_url"],
                "generation_reference_url": replacement["generation_reference_url"],
                "replacement_source": replacement["source"],
            }
            if replacement.get("third_asset_id"):
                next_mention["third_asset_id"] = replacement["third_asset_id"]
            else:
                next_mention.pop("third_asset_id", None)
            next_mentions.append(next_mention)
    changed = asset_id in reference_ids or mention_changed
    if not changed:
        return result, False
    if isinstance(shot, Mapping) and isinstance(mentions, list):
        result["shot_description"] = {**dict(shot), "mentions": next_mentions}
    result["edit_status"] = "待重新生成"
    return result, True


def _validate(model: type[BaseModel], arguments: Mapping[str, object], message: str):
    try:
        return model.model_validate(dict(arguments))
    except ValidationError as exc:
        raise VideoToolValidationError(message) from exc


def _workspace_scenes(payload: Mapping[str, object]) -> list[dict[str, JsonValue]]:
    scenes = _scene_records(payload.get("scenes"))
    if scenes:
        return scenes
    return _scene_records(payload.get("scene_packages"))


def _scenes_workspace_patch(
    scenes: list[dict[str, JsonValue]],
    *,
    dirty_scene_ids: list[str],
    qc: dict[str, dict[str, JsonValue]] | None = None,
    only_scene_ids: Sequence[str] | None = None,
    replace_all: bool = False,
) -> dict[str, JsonValue]:
    """构造镜头补丁。

    only_scene_ids：只写入这些镜，配合 repository 按 id 合并，避免并发生成整表覆盖。
    replace_all：prepare 等全量重建时整表替换。
    """

    selected = scenes
    if only_scene_ids is not None:
        allowed = {str(item).strip() for item in only_scene_ids if str(item).strip()}
        selected = [
            scene
            for scene in scenes
            if str(scene.get("scene_id") or "").strip() in allowed
        ]
    patch: dict[str, JsonValue] = {
        "scenes": selected,
        "scene_packages": selected,
        "dirty_scene_ids": dirty_scene_ids,
    }
    if replace_all:
        patch["scenes_replace"] = True
    if qc is not None:
        patch["qc"] = qc
    return patch


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
    return _find_scene(_workspace_scenes(payload), scene_id)


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
