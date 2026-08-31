"""脚本与分镜准备 Tool：只写入权威 Workspace，不触发计费 Provider。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from pixelflow.video.contracts import VideoToolResult
from pixelflow.video.workspace.payload import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceAssetRecord,
    WorkspaceCreationContract,
    WorkspaceCreativeBrief,
    WorkspacePromptPackage,
    material_asset_records,
    migrate_workspace_payload,
)

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

MAX_STORYBOARD_SCENE_COUNT = 120
MAX_SCENE_DURATION_SEC = 30
MIN_SCENE_DURATION_SEC = 4


class StoryboardSceneInput(BaseModel):
    """一个待写入 Workspace 的可执行分镜。"""

    model_config = ConfigDict(extra="allow", frozen=True)

    scene_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=10_000)
    duration_sec: int = Field(ge=MIN_SCENE_DURATION_SEC, le=MAX_SCENE_DURATION_SEC)
    # 每段必须通过稳定 asset_id 声明人物、产品、场景或道具依赖；正文中的 @引用只是可读提示，
    # 不能替代这组结构化引用。
    reference_asset_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    title: str | None = Field(default=None, max_length=256)
    storyline: str | None = Field(default=None, max_length=4_000)
    narration: str | None = Field(default=None, max_length=4_000)
    onscreen_text: str | None = Field(default=None, max_length=2_000)
    shot_type: str | None = Field(default=None, max_length=256)
    camera_movement: str | None = Field(default=None, max_length=512)


class PlannedAssetInput(BaseModel):
    """本轮新规划的待生成资产；不能伪造已有素材的状态或 Artifact。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=128)
    slot: str | None = Field(default=None, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=256)
    generation_prompt: str = Field(min_length=1, max_length=20_000)
    reference_asset_ids: tuple[str, ...] = Field(default=(), max_length=32)

    def to_workspace_asset(self) -> WorkspaceAssetRecord:
        """由 Gateway 固定待生成资产的运行态，模型不能提交这些字段。"""

        return WorkspaceAssetRecord(
            asset_id=self.asset_id,
            slot=self.slot,
            kind=self.kind,
            role=self.role,
            origin="planned_generation",
            generation_prompt=self.generation_prompt,
            state="planned",
            reference_asset_ids=self.reference_asset_ids,
            usable_for_video=False,
        )


class ExistingMaterialAssetUpdate(BaseModel):
    """对已上传素材的受限语义补充，身份和运行态始终以 Workspace 为准。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=128)
    slot: str | None = Field(default=None, max_length=64)
    kind: str | None = Field(default=None, min_length=1, max_length=64)
    role: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_semantic_patch(self) -> ExistingMaterialAssetUpdate:
        if not self.model_fields_set - {"asset_id"}:
            raise ValueError("已有素材语义补充至少需要 slot、kind 或 role 之一")
        return self


class PrepareScenePackagesInput(BaseModel):
    """脚本与分镜一次性准备；长片总时长由业务计划和 Provider 能力决定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(min_length=1, max_length=8_000)
    scenes: tuple[StoryboardSceneInput, ...] = Field(
        min_length=1,
        max_length=MAX_STORYBOARD_SCENE_COUNT,
    )
    creative_brief: WorkspaceCreativeBrief | None = None
    narrative_plan: dict[str, JsonValue] = Field(default_factory=dict)
    # 该字段只接收本轮待生成资产。已有素材的 Artifact、状态和可用性来自 Workspace，
    # 模型只能通过 asset_updates 补充其产品/角色/场景等语义。
    asset_registry: tuple[PlannedAssetInput, ...] = ()
    asset_updates: tuple[ExistingMaterialAssetUpdate, ...] = Field(default=(), max_length=120)
    # 可与分镜一起冻结；若模型/档案尚未选定，Agent 也可稍后用
    # set_video_generation_contract 单独写入，避免为补参数重写整份脚本。
    creation_contract: WorkspaceCreationContract | None = None

    @model_validator(mode="after")
    def validate_duration_and_ids(self) -> PrepareScenePackagesInput:
        scene_ids = [scene.scene_id.strip() for scene in self.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("分镜 scene_id 不能重复")
        update_ids = [item.asset_id.strip() for item in self.asset_updates]
        if len(set(update_ids)) != len(update_ids):
            raise ValueError("已有素材语义补充 asset_id 不能重复")
        return self


def _validate_and_canonicalize_scene_references(
    scenes: list[dict[str, JsonValue]],
    asset_by_id: Mapping[str, Mapping[str, JsonValue]],
) -> None:
    """拒绝未登记资产，并允许模型以 source_material_id 引用用户素材。"""

    aliases: dict[str, str] = {}
    for asset_id, asset in asset_by_id.items():
        aliases[asset_id] = asset_id
        source_material_id = str(asset.get("source_material_id") or "").strip()
        if source_material_id:
            aliases[source_material_id] = asset_id
    for scene in scenes:
        scene_id = str(scene.get("scene_id") or scene.get("segment_id") or "").strip()
        raw_references = scene.get("reference_asset_ids")
        references = [str(item).strip() for item in raw_references] if isinstance(raw_references, list) else []
        canonical = [aliases[reference] for reference in references if reference in aliases]
        if not canonical or len(canonical) != len(references):
            raise VideoToolValidationError(f"分镜 {scene_id or '未命名'} 引用了未登记资产")
        # 保留声明顺序并去重；顺序会成为后续 Provider 参考图绑定顺序。
        scene["reference_asset_ids"] = list(dict.fromkeys(canonical))


def _apply_existing_material_updates(
    asset_by_id: dict[str, dict[str, JsonValue]],
    updates: tuple[ExistingMaterialAssetUpdate, ...],
) -> None:
    """只允许更新已上传素材的语义字段，拒绝模型伪造资产身份或运行态。"""

    for update in updates:
        asset = asset_by_id.get(update.asset_id)
        if asset is None or asset.get("origin") != "existing_material":
            raise VideoToolValidationError(
                "已有素材语义补充目标无效；请使用当前 Workspace 中已上传素材的 asset_id"
            )
        patch = update.model_dump(mode="json", exclude_unset=True)
        for key in ("slot", "kind", "role"):
            if key in patch:
                asset[key] = patch[key]


class StoryboardRevisionPatch(BaseModel):
    """单个分镜的局部修订字段；未提供字段保持原值。"""

    model_config = ConfigDict(extra="allow", frozen=True)

    segment_id: str = Field(min_length=1, max_length=128)
    prompt: str | None = Field(default=None, max_length=20_000)
    duration_sec: int | None = Field(default=None, ge=4, le=30)
    generation_mode: Literal["independent", "extend", "reference"] | None = None
    reference_asset_ids: tuple[str, ...] | None = Field(default=None, max_length=32)
    continuity_from: str | None = Field(default=None, max_length=128)
    transition_out: str | None = Field(default=None, max_length=2_000)
    era: str | None = Field(default=None, max_length=512)
    camera: str | None = Field(default=None, max_length=2_000)
    sound: str | None = Field(default=None, max_length=2_000)
    hard_constraints: tuple[str, ...] | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_non_empty(self) -> StoryboardRevisionPatch:
        if not self.model_fields_set - {"segment_id"}:
            raise ValueError("分镜修订不能为空")
        return self


class ReviseStoryboardInput(BaseModel):
    """批量局部修订分镜，不触发图片或视频生成。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str | None = Field(default=None, max_length=128)
    revisions: tuple[StoryboardRevisionPatch, ...] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_unique_segments(self) -> ReviseStoryboardInput:
        ids = [item.segment_id.strip() for item in self.revisions]
        if len(set(ids)) != len(ids):
            raise ValueError("分镜修订 segment_id 不能重复")
        return self


class PrepareScenePackagesTool:
    """将已确认的脚本和分镜设计原子写入当前 Workspace。"""

    spec = VideoToolSpec(
        name="prepare_scene_packages",
        description=(
            "写入脚本和分镜包；面向 Seedance 2.5 时，先使用已加载的导演/提示词 Skill "
            "将每段 prompt 编排为可提交的完整正文，再原样写入，不得仅写摘要；"
            "asset_registry 只登记新的待生成素材（必须含 generation_prompt）；用户已上传素材已经"
            "由 Gateway 登记，若要补充产品/角色/场景语义，只能用 asset_updates 提交其 asset_id、"
            "slot、kind 或 role，不能提交或覆盖 Artifact、状态、来源。每段 reference_asset_ids "
            "必须只引用当前资产表的 asset_id；"
            "单镜最长 30 秒，长片生成由 M06 批次拆分，完成后再请求生成确认。"
        ),
        input_model=PrepareScenePackagesInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=(
            "workspace_schema_version",
            "creative_brief",
            "narrative_plan",
            "asset_registry",
            "creation_contract",
            "prompt_packages",
            "script",
            "scenes",
            "scene_packages",
            "scenes_replace",
            "dirty_scene_ids",
        ),
        model_observation_keys=(
            "scene_count",
            "total_duration_sec",
            "workspace_revision_required",
            "validation_fields",
            "validation_hints",
        ),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = PrepareScenePackagesInput.model_validate(dict(arguments))
        except Exception as exc:
            raise VideoToolValidationError("脚本或分镜参数无效") from exc

        payload = migrate_workspace_payload(context.workspace.payload)
        previous_script = payload.get("script")
        script_data = dict(previous_script) if isinstance(previous_script, Mapping) else {}
        script_data.update({"content": request.script.strip(), "status": "已编辑"})
        scenes: list[dict[str, JsonValue]] = []
        for scene in request.scenes:
            item = scene.model_dump(mode="json", exclude_none=True)
            item.update(
                {
                    "segment_id": item.get("segment_id") or item["scene_id"],
                    "sequence": item.get("sequence") or len(scenes) + 1,
                    "generation_mode": item.get("generation_mode") or "independent",
                    "edit_status": "待生成",
                    "generation_jobs": [],
                    "variants": [],
                }
            )
            scenes.append(item)
        scene_ids = [str(item["scene_id"]) for item in scenes]
        total = sum(int(item["duration_sec"]) for item in scenes)
        existing_assets = payload.get("asset_registry")
        asset_registry = (
            [dict(item) for item in existing_assets if isinstance(item, Mapping)]
            if isinstance(existing_assets, list)
            else []
        )
        asset_by_id = {
            str(item.get("asset_id")): item
            for item in asset_registry
            if str(item.get("asset_id") or "").strip()
        }
        material_assets = material_asset_records(payload)
        material_asset_ids = {str(item["asset_id"]) for item in material_assets}
        for asset in request.asset_registry:
            if asset.asset_id in asset_by_id or asset.asset_id in material_asset_ids:
                raise VideoToolValidationError(
                    "asset_registry 只能登记新的待生成资产；已有素材请使用 asset_updates"
                )
            asset_by_id[asset.asset_id] = asset.to_workspace_asset().model_dump(mode="json")
        # 已上传材料不是模型可自由伪造的资产：Gateway 从权威 Workspace 派生稳定引用。
        # Agent 可以通过 asset_updates 补充产品/角色/道具等语义，但不能覆盖材料来源、
        # Artifact 或就绪状态。
        for material_asset in material_assets:
            asset_id = str(material_asset["asset_id"])
            proposed = asset_by_id.get(asset_id)
            if proposed is not None:
                for key in ("slot", "kind", "role", "reference_asset_ids"):
                    if proposed.get(key) is not None:
                        material_asset[key] = proposed[key]
            asset_by_id[asset_id] = material_asset
        _apply_existing_material_updates(asset_by_id, request.asset_updates)
        if not asset_by_id:
            raise VideoToolValidationError("请先登记至少一个已有素材或待生成素材")
        _validate_and_canonicalize_scene_references(scenes, asset_by_id)
        prompt_packages = [
            WorkspacePromptPackage.model_validate(item).model_dump(mode="json")
            for item in scenes
        ]
        brief = payload.get("creative_brief")
        creative_brief = dict(brief) if isinstance(brief, Mapping) else {}
        if request.creative_brief is not None:
            creative_brief.update(request.creative_brief.model_dump(mode="json", exclude_none=True))
        narrative_plan = dict(payload.get("narrative_plan") or {})
        narrative_plan.update(request.narrative_plan)
        narrative_plan["script"] = request.script.strip()
        current_contract = payload.get("creation_contract")
        creation_contract = (
            request.creation_contract.model_dump(mode="json")
            if request.creation_contract is not None
            else (dict(current_contract) if isinstance(current_contract, Mapping) else None)
        )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已准备 {len(scenes)} 个分镜，总时长 {total} 秒；长片将按 M06 批次拆分，下一步可请求生成确认。",
            workspace_patch={
                "workspace_schema_version": WORKSPACE_SCHEMA_VERSION,
                "creative_brief": creative_brief,
                "narrative_plan": narrative_plan,
                "asset_registry": list(asset_by_id.values()),
                **({"creation_contract": creation_contract} if creation_contract is not None else {}),
                "prompt_packages": prompt_packages,
                "script": script_data,
                "scenes": scenes,
                "scene_packages": scenes,
                "scenes_replace": True,
                "dirty_scene_ids": scene_ids,
            },
            model_observation={
                "scene_count": len(scenes),
                "total_duration_sec": total,
                "workspace_revision_required": True,
            },
        )


class ReviseStoryboardTool:
    """批量修改已存在分镜，并将受影响的旧视频标记为 stale。"""

    spec = VideoToolSpec(
        name="revise_storyboard",
        description="批量局部修订分镜 Prompt 和生产字段；保留旧资产并标记待重新生成，不自动触发生成。",
        input_model=ReviseStoryboardInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("scenes", "scene_packages", "prompt_packages", "dirty_scene_ids"),
        model_observation_keys=("affected_segment_ids", "stale_video_count", "workspace_revision_required"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = ReviseStoryboardInput.model_validate(dict(arguments))
        payload = migrate_workspace_payload(context.workspace.payload)
        brief = payload.get("creative_brief")
        if request.option_id is not None:
            active_option = str(brief.get("active_option_id") or "") if isinstance(brief, Mapping) else ""
            if active_option and active_option != request.option_id:
                raise VideoToolValidationError("分镜修订版本不是当前选中的创意版本")

        raw_scenes = payload.get("scenes") or payload.get("scene_packages")
        scenes = [dict(item) for item in raw_scenes if isinstance(item, Mapping)] if isinstance(raw_scenes, list) else []
        packages_raw = payload.get("prompt_packages")
        packages = [dict(item) for item in packages_raw if isinstance(item, Mapping)] if isinstance(packages_raw, list) else []
        by_segment = {
            str(item.get("segment_id") or item.get("scene_id") or "").strip(): item
            for item in scenes
        }
        package_by_segment = {
            str(item.get("segment_id") or item.get("scene_id") or "").strip(): item
            for item in packages
        }
        missing = [item.segment_id for item in request.revisions if item.segment_id not in by_segment]
        if missing:
            raise VideoToolValidationError(f"分镜不存在：{', '.join(missing[:4])}")
        raw_assets = payload.get("asset_registry")
        asset_ids = {
            str(item.get("asset_id") or "").strip()
            for item in raw_assets
            if isinstance(item, Mapping)
        } if isinstance(raw_assets, list) else set()

        stale_count = 0
        affected: list[str] = []
        for revision in request.revisions:
            segment_id = revision.segment_id
            changes = revision.model_dump(mode="json", exclude_unset=True)
            changes.pop("segment_id", None)
            scene = by_segment[segment_id]
            scene.update(changes)
            scene["segment_id"] = scene.get("segment_id") or segment_id
            scene["edit_status"] = "待重新生成"
            scene["video_asset_state"] = "stale"
            variants = scene.get("variants")
            if isinstance(variants, list):
                stale_count += len(variants)
            package = package_by_segment.get(segment_id)
            if package is not None:
                package.update(changes)
                package["segment_id"] = package.get("segment_id") or segment_id
                references = package.get("reference_asset_ids")
                if not isinstance(references, list) or not references or any(
                    str(asset_id).strip() not in asset_ids for asset_id in references
                ):
                    raise VideoToolValidationError(f"分镜 {segment_id} 必须引用已登记资产")
                package["state"] = "planned"
            affected.append(segment_id)

        dirty = payload.get("dirty_scene_ids")
        dirty_ids = [str(item) for item in dirty] if isinstance(dirty, list) else []
        for segment_id in affected:
            if segment_id not in dirty_ids:
                dirty_ids.append(segment_id)
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已修订 {len(affected)} 个分镜，保留旧视频并标记为待重新生成。",
            workspace_patch={
                "scenes": scenes,
                "scene_packages": scenes,
                "prompt_packages": packages,
                "dirty_scene_ids": dirty_ids,
            },
            model_observation={
                "affected_segment_ids": affected,
                "stale_video_count": stale_count,
                "workspace_revision_required": True,
            },
        )


class CreateStoryboardTool(PrepareScenePackagesTool):
    """prepare_scene_packages 的语义别名，便于模型按自然语言选择工具名。"""

    spec = VideoToolSpec(
        **{
            **PrepareScenePackagesTool.spec.__dict__,
            "name": "create_storyboard",
            "description": (
                "创建或覆盖当前项目分镜；asset_registry 仅登记新的待生成资产，用户上传素材"
                "只能通过 asset_updates 补充语义，不能覆盖其 Artifact、状态或来源；单镜最长 30 秒，"
                "长片生成由 M06 批次拆分。"
            ),
        }
    )


__all__ = [
    "CreateStoryboardTool",
    "ExistingMaterialAssetUpdate",
    "MAX_SCENE_DURATION_SEC",
    "MAX_STORYBOARD_SCENE_COUNT",
    "PlannedAssetInput",
    "PrepareScenePackagesInput",
    "PrepareScenePackagesTool",
    "StoryboardSceneInput",
]
