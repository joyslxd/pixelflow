"""视频场景包与全局资产图的确定性 Workflow Service。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlsplit

from pixelflow.agent_runtime.contracts import WorkflowKind, WorkflowRecord, WorkflowStatus
from pixelflow.generate.scene_packages import (
    bind_scene_reference_tokens,
    build_authoritative_scene_prompt,
    extract_material_image_urls,
    prepare_video_scene_packages,
)

from .planning import (
    VideoPlanAuthoritySnapshot,
    VideoPlanningStage,
    VideoPlanningWorkflowState,
)

_REVIEW_ASSET_GROUPS = ("characters", "scenes", "props")
_REVIEW_ASSET_PATCH_FIELDS = {
    "source",
    "display_image_url",
    "generation_reference_url",
    "third_asset_id",
    "asset_type",
    "content_asset_id",
    "asset_name",
}
_REVIEW_ASSET_MUTABLE_FIELDS = {
    "three_view_images",
    "images",
    "image_url",
    "url",
    "generation_reference_url",
    "replacement_source",
    "third_asset_id",
    "replacement_asset_type",
    "replacement_asset_id",
    "replacement_asset_name",
    "manual_added",
    "asset_origin",
}


class VideoScenePackageStage(StrEnum):
    """M11.2 冻结的场景包与全局资产阶段。"""

    GENERATE_SCENE_ASSETS = "generate_scene_assets"
    SCENE_PACKAGE_REVIEW = "scene_package_review"


@dataclass(frozen=True, slots=True, init=False)
class VideoScenePackageAuthoritySnapshot:
    """保存场景包、全局资产图及其来源 Plan 校验和。"""

    _payload_json: str = field(repr=False)
    checksum: str

    def __init__(self, payload: Mapping[str, Any]) -> None:
        payload_json = _canonical_json(payload, field_name="场景包权威快照")
        object.__setattr__(self, "_payload_json", payload_json)
        object.__setattr__(self, "checksum", hashlib.sha256(payload_json.encode("utf-8")).hexdigest())

    @property
    def source_plan_checksum(self) -> str:
        return str(self._payload()["source_plan_checksum"])

    @property
    def source_plan_version(self) -> int:
        return int(self._payload()["source_plan_version"])

    @property
    def material_image_urls(self) -> list[str]:
        return list(self._payload()["material_image_urls"])

    @property
    def target_duration_ms(self) -> int:
        return int(self._payload()["target_duration_ms"])

    @property
    def creation_contract(self) -> dict[str, Any]:
        return dict(self._payload()["creation_contract"])

    @property
    def global_assets(self) -> dict[str, Any]:
        return dict(self._payload()["global_assets"])

    @property
    def scene_packages(self) -> list[dict[str, Any]]:
        return list(self._payload()["scene_packages"])

    @property
    def asset_images_generated(self) -> bool:
        return bool(self._payload()["asset_images_generated"])

    def to_dict(self) -> dict[str, Any]:
        """返回隔离副本，避免调用方修改内部场景包或资产图。"""

        return self._payload()

    def _payload(self) -> dict[str, Any]:
        return json.loads(self._payload_json)


@dataclass(frozen=True, slots=True)
class VideoScenePackageWorkflowState:
    """场景包阶段的业务状态，完整权威数据不进入消息摘要。"""

    workflow_id: str
    conversation_id: str
    current_stage: VideoScenePackageStage
    status: WorkflowStatus
    stage_version: int
    context_version: int
    created_at: datetime
    updated_at: datetime
    _source_plan: VideoPlanAuthoritySnapshot = field(repr=False)
    _source_plan_artifact_ref: str = field(repr=False)
    _scene_package: VideoScenePackageAuthoritySnapshot = field(repr=False)

    @property
    def source_plan(self) -> VideoPlanAuthoritySnapshot:
        return self._source_plan

    @property
    def source_plan_artifact_ref(self) -> str:
        return self._source_plan_artifact_ref

    @property
    def scene_package(self) -> VideoScenePackageAuthoritySnapshot:
        return self._scene_package

    @property
    def scene_package_artifact_ref(self) -> str:
        """生成包含来源版本与内容校验和的稳定 Artifact 引用。"""

        workflow_key = quote(self.workflow_id, safe="-_.")
        return f"artifact:video-scene-package:{workflow_key}:plan-v{self.source_plan.plan_version}:{self.scene_package.checksum[:16]}"


class VideoScenePackageWorkflowService:
    """类比 Java Application Service，机械继承已审核 Plan 并发布资产图。"""

    def prepare_from_approved_plan(
        self,
        state: VideoPlanningWorkflowState,
        *,
        materials: Sequence[Mapping[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> VideoScenePackageWorkflowState:
        """消费人工同意的 Plan，生成不可由第二次 LLM 改写的场景包。"""

        if state.current_stage is not VideoPlanningStage.PLAN_APPROVED or state.status is not WorkflowStatus.RUNNING:
            raise ValueError("只有记录用户显式同意的 Plan 才能进入场景包阶段")
        source_plan = state.active_plan
        source_plan_artifact_ref = state.active_plan_artifact_ref
        if source_plan is None or source_plan_artifact_ref is None:
            raise ValueError("场景包阶段缺少 Plan 权威快照")
        timestamp = _timestamp(now)
        if timestamp < state.updated_at:
            raise ValueError("Workflow 更新时间不能早于当前状态")

        _validate_source_plan(source_plan)
        contract = source_plan.creation_contract
        form_values = dict(state.form_values)
        form_values.update(contract)
        material_payload = [dict(item) for item in (materials or [])]
        material_image_urls = _validated_material_image_urls(
            extract_material_image_urls(material_payload),
        )
        prepared = prepare_video_scene_packages(
            form_values=form_values,
            plan_markdown=source_plan.plan_markdown,
            selected_direction=state.selected_direction,
            materials=material_payload,
            target_duration_ms=int(contract["video_duration_sec"]) * 1000,
            scene_blueprints=source_plan.scene_blueprints,
            asset_manifest=source_plan.asset_manifest,
            authority_mode=True,
        )
        snapshot = VideoScenePackageAuthoritySnapshot(
            _validated_prepared_payload(
                source_plan,
                prepared,
                material_image_urls=material_image_urls,
            )
        )
        return VideoScenePackageWorkflowState(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            current_stage=VideoScenePackageStage.GENERATE_SCENE_ASSETS,
            status=WorkflowStatus.RUNNING,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            created_at=state.created_at,
            updated_at=timestamp,
            _source_plan=source_plan,
            _source_plan_artifact_ref=source_plan_artifact_ref,
            _scene_package=snapshot,
        )

    def publish_generated_asset_images(
        self,
        state: VideoScenePackageWorkflowState,
        global_assets: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> VideoScenePackageWorkflowState:
        """校验每个全局资产唯一图片，并机械回填镜头 mentions。"""

        _require_stage(state, VideoScenePackageStage.GENERATE_SCENE_ASSETS)
        _validate_generating_state_authority(state)
        timestamp = _timestamp(now)
        if timestamp < state.updated_at:
            raise ValueError("Workflow 更新时间不能早于当前状态")
        normalized_assets = _validated_generated_assets(
            state.scene_package.global_assets,
            global_assets,
        )
        enriched_scenes = _bind_asset_images_to_mentions(
            state.scene_package.scene_packages,
            normalized_assets,
        )
        payload = state.scene_package.to_dict()
        payload.update(
            {
                "global_assets": normalized_assets,
                "scene_packages": enriched_scenes,
                "asset_images_generated": True,
            }
        )
        snapshot = VideoScenePackageAuthoritySnapshot(payload)
        return VideoScenePackageWorkflowState(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            current_stage=VideoScenePackageStage.SCENE_PACKAGE_REVIEW,
            status=WorkflowStatus.AWAITING_USER,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            created_at=state.created_at,
            updated_at=timestamp,
            _source_plan=state.source_plan,
            _source_plan_artifact_ref=state.source_plan_artifact_ref,
            _scene_package=snapshot,
        )

    def modify_review_scene(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        scene_id: str,
        patch: Mapping[str, Any],
        now: datetime | None = None,
    ) -> VideoScenePackageWorkflowState:
        """仅修改一个权威分镜的可编辑字段，并重算 mentions 与执行提示词。"""

        _validate_scene_package_state_authority(state)
        _require_stage(state, VideoScenePackageStage.SCENE_PACKAGE_REVIEW)
        normalized_id = _required_text(scene_id, "scene_id")
        if not isinstance(patch, Mapping) or not patch:
            raise ValueError("分镜 patch 不能为空")
        allowed = {"storyline", "shot_description", "narration", "reference_asset_ids"}
        if set(patch).difference(allowed):
            raise ValueError("分镜 patch 含有不允许的字段")
        scenes = state.scene_package.scene_packages
        position = next(
            (index for index, item in enumerate(scenes) if item["scene_id"] == normalized_id),
            None,
        )
        if position is None:
            raise ValueError("目标分镜不存在")
        scene = dict(scenes[position])
        if "storyline" in patch:
            scene["storyline"] = _required_text(patch["storyline"], "storyline")
        if "narration" in patch:
            if not isinstance(patch["narration"], str):
                raise ValueError("narration 必须是字符串")
            scene["narration"] = patch["narration"]
        if "reference_asset_ids" in patch:
            scene["reference_asset_ids"] = _validated_reference_ids(
                patch["reference_asset_ids"]
            )
        if "shot_description" in patch:
            shot = patch["shot_description"]
            if not isinstance(shot, Mapping) or set(shot) != {"text"}:
                raise ValueError("shot_description 只能包含 text 字段")
            scene["shot_description"] = {
                "text": _required_text(shot["text"], "shot_description.text"),
                "mentions": [],
            }
        scenes[position] = _rebuild_review_scene(
            scene,
            state.scene_package.global_assets,
            state.scene_package.creation_contract,
        )
        return self._publish_review_revision(
            state,
            global_assets=state.scene_package.global_assets,
            scene_packages=scenes,
            now=now,
        )

    def replace_review_asset(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        asset_group: str,
        asset_id: str,
        asset_patch: Mapping[str, Any],
        now: datetime | None = None,
    ) -> VideoScenePackageWorkflowState:
        """替换一个已有全局资产的图片引用，保留 Plan 冻结身份与描述。"""

        _validate_scene_package_state_authority(state)
        _require_stage(state, VideoScenePackageStage.SCENE_PACKAGE_REVIEW)
        group = _validated_asset_group(asset_group)
        normalized_id = _required_text(asset_id, "asset_id")
        replacement = _validated_review_asset_patch(asset_patch)
        assets = state.scene_package.global_assets
        items = assets[group]
        position = next(
            (index for index, item in enumerate(items) if item["asset_id"] == normalized_id),
            None,
        )
        if position is None:
            raise ValueError("目标全局资产不存在")
        image_field = "three_view_images" if group == "characters" else "images"
        updated_asset = dict(items[position])
        updated_asset.update(
            _asset_replacement_metadata(
                replacement,
                image_field=image_field,
            )
        )
        items[position] = updated_asset
        assets[group] = items
        scenes = [
            _rebuild_review_scene(
                scene,
                assets,
                state.scene_package.creation_contract,
            )
            for scene in state.scene_package.scene_packages
        ]
        return self._publish_review_revision(
            state,
            global_assets=assets,
            scene_packages=scenes,
            now=now,
        )

    def delete_review_asset(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        asset_group: str,
        asset_id: str,
        now: datetime | None = None,
    ) -> VideoScenePackageWorkflowState:
        """清除一个资产图片，并从全部分镜结构化引用中移除该资产。"""

        _validate_scene_package_state_authority(state)
        _require_stage(state, VideoScenePackageStage.SCENE_PACKAGE_REVIEW)
        group = _validated_asset_group(asset_group)
        normalized_id = _required_text(asset_id, "asset_id")
        assets = state.scene_package.global_assets
        items = assets[group]
        position = next(
            (index for index, item in enumerate(items) if item["asset_id"] == normalized_id),
            None,
        )
        if position is None:
            raise ValueError("目标全局资产不存在")
        asset = dict(items[position])
        asset_name = _required_text(asset.get("name"), "asset.name")
        image_field = "three_view_images" if group == "characters" else "images"
        for key in _REVIEW_ASSET_MUTABLE_FIELDS:
            asset.pop(key, None)
        asset[image_field] = []
        items[position] = asset
        assets[group] = items
        scenes: list[dict[str, Any]] = []
        for current in state.scene_package.scene_packages:
            scene = dict(current)
            scene["reference_asset_ids"] = [
                value
                for value in scene["reference_asset_ids"]
                if value != normalized_id
            ]
            shot = dict(scene["shot_description"])
            shot["text"] = str(shot["text"]).replace(f"@{asset_name}", asset_name)
            shot["mentions"] = []
            scene["shot_description"] = shot
            scenes.append(
                _rebuild_review_scene(
                    scene,
                    assets,
                    state.scene_package.creation_contract,
                )
            )
        return self._publish_review_revision(
            state,
            global_assets=assets,
            scene_packages=scenes,
            now=now,
        )

    def add_review_asset(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        asset_group: str,
        asset_id: str,
        asset_patch: Mapping[str, Any],
        now: datetime | None = None,
    ) -> VideoScenePackageWorkflowState:
        """新增一个有稳定 ID 的人工资产，但不隐式绑定到任何分镜。"""

        _validate_scene_package_state_authority(state)
        _require_stage(state, VideoScenePackageStage.SCENE_PACKAGE_REVIEW)
        group = _validated_asset_group(asset_group)
        normalized_id = _required_text(asset_id, "asset_id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", normalized_id):
            raise ValueError("新增资产 asset_id 格式不合法")
        replacement = _validated_review_asset_patch(asset_patch)
        assets = state.scene_package.global_assets
        existing_assets = _asset_lookup(assets)
        visual_style = assets.get("visual_style")
        visual_style_id = (
            visual_style.get("asset_id")
            if isinstance(visual_style, Mapping)
            else None
        )
        if normalized_id in existing_assets or normalized_id == visual_style_id:
            raise ValueError("新增资产 asset_id 必须唯一")
        expected_prefix = {
            "characters": "character-manual-",
            "scenes": "scene-manual-",
            "props": "prop-manual-",
        }[group]
        if not normalized_id.startswith(expected_prefix):
            raise ValueError("新增资产 asset_id 必须使用所属分组前缀")
        name = replacement.get("asset_name") or "新增全局资产"
        if any(item.get("name") == name for item in existing_assets.values()):
            raise ValueError("新增资产名称必须在三类全局资产中唯一")
        content_asset_id = replacement.get("content_asset_id")
        if content_asset_id is not None and any(
            isinstance(item, Mapping)
            and item.get("replacement_asset_id") == content_asset_id
            for collection in ("characters", "scenes", "props")
            for item in assets.get(collection, [])
        ):
            raise ValueError("同一 content asset 不能跨分组重复新增")
        image_field = "three_view_images" if group == "characters" else "images"
        added = {
            "asset_id": normalized_id,
            "name": name,
            "description": name,
            "manual_added": True,
            "asset_origin": "manual_addition",
        }
        added.update(
            _asset_replacement_metadata(
                replacement,
                image_field=image_field,
            )
        )
        assets[group] = [*assets[group], added]
        return self._publish_review_revision(
            state,
            global_assets=assets,
            scene_packages=state.scene_package.scene_packages,
            now=now,
        )

    def _publish_review_revision(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        global_assets: Mapping[str, Any],
        scene_packages: Sequence[Mapping[str, Any]],
        now: datetime | None,
    ) -> VideoScenePackageWorkflowState:
        """发布一次有界人工修改后的新权威快照。"""

        timestamp = _timestamp(now)
        if timestamp < state.updated_at:
            raise ValueError("Workflow 更新时间不能早于当前状态")
        payload = state.scene_package.to_dict()
        revision = payload.get("review_revision", 0)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("场景包 review_revision 不合法")
        payload.update(
            {
                "global_assets": json.loads(
                    _canonical_json(global_assets, field_name="人工修改全局资产")
                ),
                "scene_packages": json.loads(
                    _canonical_json(scene_packages, field_name="人工修改场景包")
                ),
                "review_revision": revision + 1,
            }
        )
        result = replace(
            state,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=timestamp,
            _scene_package=VideoScenePackageAuthoritySnapshot(payload),
        )
        _validate_scene_package_state_authority(result)
        return result

    def cancel(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        now: datetime | None = None,
    ) -> VideoScenePackageWorkflowState:
        """取消场景包阶段，但不删除 Plan、场景包或已生成资产。"""

        _validate_scene_package_state_authority(state)
        if state.status in {WorkflowStatus.CANCELLED, WorkflowStatus.COMPLETED}:
            raise ValueError("已取消或已完成的场景包 Workflow 属于终态，不能再次取消")
        result = replace(
            state,
            status=WorkflowStatus.CANCELLED,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=_cancellation_timestamp(state.updated_at, now),
        )
        _validate_scene_package_state_authority(result)
        return result

    def to_workflow_record(self, state: VideoScenePackageWorkflowState) -> WorkflowRecord:
        """投影 Runtime DTO，保留 Plan 与场景包两个稳定 Artifact 引用。"""

        _validate_scene_package_state_authority(state)
        return WorkflowRecord(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            kind=WorkflowKind.VIDEO,
            status=state.status,
            current_stage=state.current_stage.value,
            stage_version=state.stage_version,
            creation_contract_snapshot=state.scene_package.creation_contract,
            pending_external_job=None,
            latest_artifact_refs=[
                state.source_plan_artifact_ref,
                state.scene_package_artifact_ref,
            ],
            context_version=state.context_version,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )


def _validate_source_plan(source_plan: VideoPlanAuthoritySnapshot) -> None:
    """在切片边界重新校验快照，不信任调用方直接构造的对象。"""

    source_plan.validate()


def _validated_prepared_payload(
    source_plan: VideoPlanAuthoritySnapshot,
    prepared: Mapping[str, Any],
    *,
    material_image_urls: Sequence[str],
) -> dict[str, Any]:
    if prepared.get("ok") is not True:
        raise ValueError("场景包准备结果必须成功")
    if prepared.get("requires_confirmation") is not True or prepared.get("review_timeout_sec") is not None:
        raise ValueError("视频场景包必须人工确认且不得自动确认")
    expected_duration_ms = int(source_plan.creation_contract["video_duration_sec"]) * 1000
    target_duration_ms = _required_int(
        prepared.get("target_duration_ms"),
        "场景包 target_duration_ms",
    )
    if target_duration_ms != expected_duration_ms:
        raise ValueError("场景包总时长必须精确继承 Plan 创作合同")

    global_assets = _validated_prepared_assets(
        source_plan.asset_manifest,
        prepared.get("global_assets"),
        source_plan.creation_contract,
    )
    scene_packages = _validated_scene_packages(
        source_plan.scene_blueprints,
        global_assets,
        prepared.get("scene_packages"),
        material_image_urls=material_image_urls,
        video_model=_required_text(
            source_plan.creation_contract.get("video_model"),
            "Plan video_model",
        ),
    )
    return {
        "source_plan_checksum": source_plan.checksum,
        "source_plan_version": source_plan.plan_version,
        "material_image_urls": list(material_image_urls),
        "target_duration_ms": expected_duration_ms,
        "creation_contract": source_plan.creation_contract,
        "global_assets": global_assets,
        "scene_packages": scene_packages,
        "asset_images_generated": False,
    }


def _validate_generating_state_authority(
    state: VideoScenePackageWorkflowState,
    *,
    allow_cancelled: bool = False,
) -> None:
    """恢复生图阶段时重新建立 Plan 与场景包快照的校验链。"""

    if state.status is not WorkflowStatus.RUNNING and not (
        allow_cancelled and state.status is WorkflowStatus.CANCELLED
    ):
        raise ValueError("全局资产生图阶段必须处于运行状态")
    _validate_source_plan(state.source_plan)
    payload = state.scene_package.to_dict()
    prepared = {
        "ok": True,
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": payload.get("target_duration_ms"),
        "global_assets": payload.get("global_assets"),
        "scene_packages": payload.get("scene_packages"),
    }
    validated = _validated_prepared_payload(
        state.source_plan,
        prepared,
        material_image_urls=_validated_material_image_urls(
            payload.get("material_image_urls"),
        ),
    )
    if _canonical_json(validated, field_name="恢复场景包权威数据") != _canonical_json(
        payload,
        field_name="场景包权威快照",
    ):
        raise ValueError("场景包权威快照与来源 Plan 不一致")


def _validate_scene_package_state_authority(
    state: VideoScenePackageWorkflowState,
) -> None:
    if state.current_stage is VideoScenePackageStage.GENERATE_SCENE_ASSETS:
        _validate_generating_state_authority(state, allow_cancelled=True)
        return
    if state.current_stage is not VideoScenePackageStage.SCENE_PACKAGE_REVIEW:
        raise ValueError("场景包 Workflow 阶段不受支持")
    if state.status not in {
        WorkflowStatus.AWAITING_USER,
        WorkflowStatus.CANCELLED,
    }:
        raise ValueError("场景包审核阶段必须等待用户确认")

    _validate_source_plan(state.source_plan)
    payload = state.scene_package.to_dict()
    if "review_revision" in payload:
        _validate_revised_review_payload(state, payload)
        return
    if payload.get("asset_images_generated") is not True:
        raise ValueError("场景包审核阶段必须已经完成全局资产图片绑定")
    generated_assets = payload.get("global_assets")
    if not isinstance(generated_assets, Mapping):
        raise ValueError("场景包审核阶段缺少全局资产")
    prepared_assets = json.loads(_canonical_json(generated_assets, field_name="审核态全局资产"))
    for collection, image_field in (
        ("characters", "three_view_images"),
        ("scenes", "images"),
        ("props", "images"),
    ):
        items = prepared_assets.get(collection)
        if not isinstance(items, list):
            raise ValueError(f"审核态全局资产 {collection} 必须是数组")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"审核态全局资产 {collection} 只能包含对象")
            item[image_field] = []

    reviewed_scenes = payload.get("scene_packages")
    prepared_scenes = json.loads(_canonical_json(reviewed_scenes, field_name="审核态场景包"))
    if not isinstance(prepared_scenes, list):
        raise ValueError("审核态场景包必须是数组")
    for scene in prepared_scenes:
        if not isinstance(scene, dict):
            raise ValueError("审核态场景包只能包含对象")
        shot = scene.get("shot_description")
        mentions = shot.get("mentions") if isinstance(shot, dict) else None
        if not isinstance(mentions, list):
            raise ValueError("审核态场景包 mentions 必须是数组")
        for mention in mentions:
            if not isinstance(mention, dict):
                raise ValueError("审核态场景包 mention 必须是对象")
            mention.pop("image_url", None)

    prepared = {
        "ok": True,
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": payload.get("target_duration_ms"),
        "global_assets": prepared_assets,
        "scene_packages": prepared_scenes,
    }
    base_payload = _validated_prepared_payload(
        state.source_plan,
        prepared,
        material_image_urls=_validated_material_image_urls(
            payload.get("material_image_urls"),
        ),
    )
    normalized_assets = _validated_generated_assets(
        base_payload["global_assets"],
        generated_assets,
    )
    expected = dict(base_payload)
    expected.update(
        {
            "global_assets": normalized_assets,
            "scene_packages": _bind_asset_images_to_mentions(
                base_payload["scene_packages"],
                normalized_assets,
            ),
            "asset_images_generated": True,
        }
    )
    if _canonical_json(expected, field_name="审核态规范场景包") != _canonical_json(
        payload,
        field_name="审核态场景包权威快照",
    ):
        raise ValueError("审核态场景包权威快照与来源 Plan 不一致")


def _validate_revised_review_payload(
    state: VideoScenePackageWorkflowState,
    payload: Mapping[str, Any],
) -> None:
    """重验人工授权后的场景包，不放宽来源 Plan 和执行合同。"""

    expected_keys = {
        "source_plan_checksum",
        "source_plan_version",
        "material_image_urls",
        "target_duration_ms",
        "creation_contract",
        "global_assets",
        "scene_packages",
        "asset_images_generated",
        "review_revision",
    }
    if set(payload) != expected_keys:
        raise ValueError("人工修改场景包字段必须完整且不得包含额外字段")
    revision = payload.get("review_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("场景包 review_revision 必须是正整数")
    if payload.get("source_plan_checksum") != state.source_plan.checksum:
        raise ValueError("人工修改场景包来源 Plan 校验和不一致")
    if payload.get("source_plan_version") != state.source_plan.plan_version:
        raise ValueError("人工修改场景包来源 Plan 版本不一致")
    if payload.get("creation_contract") != state.source_plan.creation_contract:
        raise ValueError("人工修改不得漂移来源 Plan 创作合同")
    expected_duration = int(state.source_plan.creation_contract["video_duration_sec"]) * 1000
    if payload.get("target_duration_ms") != expected_duration:
        raise ValueError("人工修改不得漂移场景包总时长")
    _validated_material_image_urls(payload.get("material_image_urls"))
    if payload.get("asset_images_generated") is not True:
        raise ValueError("人工修改只能发生在资产图片已生成的审核阶段")

    assets = payload.get("global_assets")
    if not isinstance(assets, Mapping) or set(assets) != {
        "characters",
        "scenes",
        "props",
        "visual_style",
    }:
        raise ValueError("人工修改全局资产字段不合法")
    visual_style = assets.get("visual_style")
    if not isinstance(visual_style, dict) or set(visual_style) != {
        "asset_id",
        "name",
        "description",
        "prompt",
    }:
        raise ValueError("人工修改视觉风格字段不合法")
    _required_text(visual_style.get("asset_id"), "visual_style.asset_id")
    expected_style = _required_text(
        state.source_plan.creation_contract.get("visual_style"),
        "creation_contract.visual_style",
    )
    if any(
        visual_style.get(field_name) != expected_style
        for field_name in ("name", "description", "prompt")
    ):
        raise ValueError("人工修改不得漂移 Plan 视觉风格")
    plan_assets = {
        item["asset_id"]: item
        for collection in _REVIEW_ASSET_GROUPS
        for item in state.source_plan.asset_manifest[collection]
    }
    seen_ids: set[str] = set()
    for group in _REVIEW_ASSET_GROUPS:
        items = assets.get(group)
        if not isinstance(items, list):
            raise ValueError(f"global_assets.{group} 必须是数组")
        image_field = "three_view_images" if group == "characters" else "images"
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"global_assets.{group} 只能包含对象")
            asset_id = _required_text(item.get("asset_id"), f"{group}.asset_id")
            if asset_id in seen_ids:
                raise ValueError("全局资产 asset_id 必须唯一")
            seen_ids.add(asset_id)
            urls = item.get(image_field)
            if not isinstance(urls, list) or len(urls) > 1:
                raise ValueError("人工修改资产图片必须为空或恰好一张")
            if urls:
                _required_https_url(urls[0], f"{group}.{image_field}")
            for key in ("image_url", "url"):
                value = item.get(key)
                if value not in (None, ""):
                    _required_https_url(value, f"{group}.{key}")
            generation_reference = item.get("generation_reference_url")
            if generation_reference not in (None, ""):
                _validated_generation_reference(
                    source=item.get("replacement_source"),
                    value=generation_reference,
                    third_asset_id=item.get("third_asset_id"),
                    field_name=f"{group}.generation_reference_url",
                )
            immutable = {
                key: value
                for key, value in item.items()
                if key not in _REVIEW_ASSET_MUTABLE_FIELDS
            }
            original = plan_assets.get(asset_id)
            if original is not None:
                if immutable != original:
                    raise ValueError("人工替换资产不得改写 Plan 身份或描述")
            else:
                if immutable != {
                    "asset_id": asset_id,
                    "name": item.get("name"),
                    "description": item.get("description"),
                } or item.get("manual_added") is not True or item.get("asset_origin") != "manual_addition":
                    raise ValueError("人工新增资产字段不合法")
                _required_text(item.get("name"), "人工新增资产 name")
                if item.get("description") != item.get("name"):
                    raise ValueError("人工新增资产 description 必须使用规范名称")

    scenes = payload.get("scene_packages")
    blueprints = state.source_plan.scene_blueprints
    if not isinstance(scenes, list) or len(scenes) != len(blueprints):
        raise ValueError("人工修改不得改变场景包数量")
    asset_lookup = _asset_lookup(assets)
    contract = state.source_plan.creation_contract
    for position, (scene, blueprint) in enumerate(
        zip(scenes, blueprints, strict=True),
        start=1,
    ):
        if not isinstance(scene, dict) or set(scene) != {
            "scene_id",
            "scene_index",
            "title",
            "duration_ms",
            "storyline",
            "shot_description",
            "reference_asset_ids",
            "prompt",
            "narration",
            "transition",
            "image_urls",
            "video_urls",
            "audio_urls",
        }:
            raise ValueError(f"人工修改场景包第 {position} 项字段不合法")
        immutable = {
            "scene_id": blueprint["scene_id"],
            "scene_index": blueprint["scene_index"],
            "title": blueprint["title"],
            "duration_ms": int(blueprint["duration_sec"]) * 1000,
            "transition": blueprint["transition"],
            "image_urls": list(payload["material_image_urls"]),
            "video_urls": [],
            "audio_urls": [],
        }
        if any(scene.get(key) != value for key, value in immutable.items()):
            raise ValueError("人工修改不得改变场景身份、时长或输入素材")
        _required_text(scene.get("storyline"), f"场景包第 {position} 项 storyline")
        if not isinstance(scene.get("narration"), str):
            raise ValueError(f"场景包第 {position} 项 narration 必须是字符串")
        references = _validated_reference_ids(scene.get("reference_asset_ids"))
        for asset_id in references:
            asset = asset_lookup.get(asset_id)
            if asset is None or "image_url" not in asset:
                raise ValueError("分镜只能引用当前存在且有图片的全局资产")
        shot = scene.get("shot_description")
        if not isinstance(shot, dict) or set(shot) != {"text", "mentions"}:
            raise ValueError("人工修改 shot_description 字段不合法")
        _required_text(shot.get("text"), "shot_description.text")
        expected_mentions = [
            {
                "asset_id": asset_id,
                "type": asset_lookup[asset_id]["type"],
                "name": asset_lookup[asset_id]["name"],
                "image_url": asset_lookup[asset_id]["image_url"],
            }
            for asset_id in references
        ]
        if shot.get("mentions") != expected_mentions:
            raise ValueError("人工修改 mentions 必须由当前全局资产重算")
        expected_prompt = build_authoritative_scene_prompt(
            scene["storyline"],
            shot,
            scene["narration"],
            assets["visual_style"],
            video_model=_required_text(contract.get("video_model"), "video_model"),
        )
        if scene.get("prompt") != expected_prompt:
            raise ValueError("人工修改场景执行提示词必须由服务端重算")


def _validated_material_image_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("场景包素材图片 URL 必须是数组")
    result: list[str] = []
    for position, image_url in enumerate(value, start=1):
        normalized = _required_https_url(
            image_url,
            f"场景包素材图片 URL 第 {position} 项",
        )
        if normalized != image_url:
            raise ValueError("场景包素材图片 URL 必须是无首尾空白的规范值")
        if normalized in result:
            raise ValueError("场景包素材图片 URL 不得重复")
        result.append(normalized)
    return result


def _validated_prepared_assets(
    manifest: Mapping[str, Any],
    value: Any,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("场景包 global_assets 必须是对象")
    assets = json.loads(_canonical_json(value, field_name="场景包 global_assets"))
    expected_collections = {"characters", "scenes", "props", "visual_style"}
    if set(assets) != expected_collections:
        raise ValueError("场景包 global_assets 字段必须完整且不得包含额外字段")
    all_ids: list[str] = []
    for collection, image_field in (
        ("characters", "three_view_images"),
        ("scenes", "images"),
        ("props", "images"),
    ):
        items = assets.get(collection)
        if not isinstance(items, list):
            raise ValueError(f"global_assets.{collection} 必须是数组")
        stripped: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or item.get(image_field) != []:
                raise ValueError(f"global_assets.{collection} 在生图前不得携带图片")
            stripped.append({key: data for key, data in item.items() if key != image_field})
            all_ids.append(_required_text(item.get("asset_id"), f"{collection}.asset_id"))
        if _canonical_json(stripped, field_name=f"global_assets.{collection}") != _canonical_json(
            manifest.get(collection),
            field_name=f"asset_manifest.{collection}",
        ):
            raise ValueError(f"global_assets.{collection} 必须逐项继承 Plan asset_manifest")

    visual_style = assets.get("visual_style")
    if not isinstance(visual_style, dict):
        raise ValueError("global_assets.visual_style 必须是对象")
    style_id = _required_text(visual_style.get("asset_id"), "visual_style.asset_id")
    expected_style = _required_text(contract.get("visual_style"), "creation_contract.visual_style")
    if set(visual_style) != {"asset_id", "name", "description", "prompt"}:
        raise ValueError("global_assets.visual_style 不得包含额外字段")
    if any(visual_style.get(field_name) != expected_style for field_name in ("name", "description", "prompt")):
        raise ValueError("global_assets.visual_style 必须继承 Plan 创作合同")
    if len(set([*all_ids, style_id])) != len(all_ids) + 1:
        raise ValueError("全局资产和 visual_style 的 asset_id 必须全局唯一")
    return assets


def _validated_scene_packages(
    blueprints: Sequence[Mapping[str, Any]],
    global_assets: Mapping[str, Any],
    value: Any,
    *,
    material_image_urls: Sequence[str],
    video_model: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(blueprints):
        raise ValueError("场景包数量必须与 Plan 分镜蓝图一致")
    asset_lookup = _asset_lookup(global_assets)
    normalized = json.loads(_canonical_json(value, field_name="scene_packages"))
    for position, (scene, blueprint) in enumerate(zip(normalized, blueprints, strict=True), start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"场景包第 {position} 项必须是对象")
        expected_scene_fields = {
            "scene_id",
            "scene_index",
            "title",
            "duration_ms",
            "storyline",
            "shot_description",
            "reference_asset_ids",
            "prompt",
            "narration",
            "transition",
            "image_urls",
            "video_urls",
            "audio_urls",
        }
        if set(scene) != expected_scene_fields:
            raise ValueError(f"场景包第 {position} 项字段必须完整且不得包含额外字段")
        _required_int(scene.get("scene_index"), f"场景包第 {position} 项 scene_index")
        _required_int(scene.get("duration_ms"), f"场景包第 {position} 项 duration_ms")
        exact_fields = {
            "scene_id": blueprint["scene_id"],
            "scene_index": blueprint["scene_index"],
            "title": blueprint["title"],
            "duration_ms": int(blueprint["duration_sec"]) * 1000,
            "storyline": blueprint["storyline"],
            "narration": blueprint["narration"],
            "transition": blueprint["transition"],
        }
        for field_name, expected in exact_fields.items():
            if scene.get(field_name) != expected:
                raise ValueError(f"场景包第 {position} 项 {field_name} 必须逐字继承 Plan")
        expected_refs = _blueprint_asset_ids(blueprint, asset_lookup)
        if len(expected_refs) > 9:
            raise ValueError(f"分镜 {blueprint['scene_id']} scene_index={blueprint['scene_index']} 引用资产共 {len(expected_refs)} 个，最多允许 9 个")
        if scene.get("reference_asset_ids") != expected_refs:
            raise ValueError(f"场景包第 {position} 项资产引用必须继承 Plan")
        shot = scene.get("shot_description")
        expected_shot_text = bind_scene_reference_tokens(
            str(blueprint["shot_description"]),
            expected_refs,
            dict(global_assets),
        )
        if not isinstance(shot, dict) or set(shot) != {"text", "mentions"} or shot.get("text") != expected_shot_text:
            raise ValueError(f"场景包第 {position} 项镜头描述必须逐字继承 Plan")
        mentions = shot.get("mentions")
        if not isinstance(mentions, list) or not all(isinstance(item, dict) for item in mentions) or [item.get("asset_id") for item in mentions] != expected_refs:
            raise ValueError(f"场景包第 {position} 项 mentions 必须与资产引用一致")
        for mention, asset_id in zip(mentions, expected_refs, strict=True):
            asset = asset_lookup[asset_id]
            if mention != {
                "asset_id": asset_id,
                "type": asset["type"],
                "name": asset["name"],
            }:
                raise ValueError(f"场景包第 {position} 项 mention 名称必须继承全局资产")
        expected_prompt = build_authoritative_scene_prompt(
            str(blueprint["storyline"]),
            shot,
            str(blueprint["narration"]),
            global_assets["visual_style"],
            video_model=video_model,
        )
        if scene.get("prompt") != expected_prompt:
            raise ValueError(f"场景包第 {position} 项执行提示词不得改写 Plan")
        if scene.get("video_urls") != [] or scene.get("audio_urls") != []:
            raise ValueError(f"场景包第 {position} 项在视频生成前不得携带音视频结果")
        if scene.get("image_urls") != list(material_image_urls):
            raise ValueError(f"场景包第 {position} 项素材图片必须完整继承输入 materials")
    return normalized


def _validated_generated_assets(expected: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("全局资产生图结果必须是对象")
    assets = json.loads(_canonical_json(value, field_name="全局资产生图结果"))
    if set(assets) != {"characters", "scenes", "props", "visual_style"}:
        raise ValueError("全局资产生图结果字段必须完整且不得包含额外字段")
    for collection, image_field in (
        ("characters", "three_view_images"),
        ("scenes", "images"),
        ("props", "images"),
    ):
        expected_items = expected.get(collection)
        actual_items = assets.get(collection)
        if not isinstance(expected_items, list) or not isinstance(actual_items, list):
            raise ValueError(f"全局资产 {collection} 必须是数组")
        if len(actual_items) != len(expected_items):
            raise ValueError(f"全局资产生图不得增删 {collection}")
        for position, (expected_item, actual_item) in enumerate(
            zip(expected_items, actual_items, strict=True),
            start=1,
        ):
            if not isinstance(expected_item, dict) or not isinstance(actual_item, dict):
                raise ValueError(f"全局资产 {collection} 第 {position} 项必须是对象")
            expected_without_images = {key: data for key, data in expected_item.items() if key != image_field}
            actual_without_images = {key: data for key, data in actual_item.items() if key != image_field}
            if _canonical_json(
                actual_without_images,
                field_name=f"生图 {collection} 第 {position} 项",
            ) != _canonical_json(
                expected_without_images,
                field_name=f"准备 {collection} 第 {position} 项",
            ):
                raise ValueError("全局资产生图不得改写 Plan 名称、说明或提示词")
            urls = actual_item.get(image_field)
            if not isinstance(urls, list) or len(urls) != 1:
                raise ValueError("每个全局资产必须恰好绑定一张生成图片")
            normalized_url = _required_https_url(
                urls[0],
                f"{collection}[{position}].{image_field}",
            )
            if normalized_url != urls[0]:
                raise ValueError("全局资产图片 URL 必须是无首尾空白的规范值")
    if _canonical_json(assets.get("visual_style"), field_name="生图视觉风格") != _canonical_json(
        expected.get("visual_style"),
        field_name="准备视觉风格",
    ):
        raise ValueError("全局资产生图不得改写 visual_style")
    return assets


def _validated_reference_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 9:
        raise ValueError("分镜 reference_asset_ids 必须是最多 9 项的数组")
    normalized = [
        _required_text(item, "reference_asset_ids")
        for item in value
    ]
    if len(set(normalized)) != len(normalized):
        raise ValueError("分镜 reference_asset_ids 不得重复")
    return normalized


def _validated_asset_group(value: Any) -> str:
    group = _required_text(value, "asset_group")
    if group not in _REVIEW_ASSET_GROUPS:
        raise ValueError("asset_group 不受支持")
    return group


def _validated_review_asset_patch(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("资产 patch 不能为空")
    if set(value).difference(_REVIEW_ASSET_PATCH_FIELDS):
        raise ValueError("资产 patch 含有不允许的字段")
    source = _required_text(value.get("source"), "asset_patch.source")
    if source not in {"digital_human", "image_asset", "local_upload"}:
        raise ValueError("资产 patch source 不受支持")
    normalized = {
        "source": source,
        "display_image_url": _required_https_url(
            value.get("display_image_url"),
            "asset_patch.display_image_url",
        ),
    }
    for key in (
        "third_asset_id",
        "asset_type",
        "content_asset_id",
        "asset_name",
    ):
        if key in value:
            normalized[key] = _required_text(value[key], f"asset_patch.{key}")
    normalized["generation_reference_url"] = _validated_generation_reference(
        source=source,
        value=value.get("generation_reference_url"),
        third_asset_id=normalized.get("third_asset_id"),
        field_name="asset_patch.generation_reference_url",
    )
    return normalized


def _asset_replacement_metadata(
    replacement: Mapping[str, str],
    *,
    image_field: str,
) -> dict[str, Any]:
    display_url = replacement["display_image_url"]
    result: dict[str, Any] = {
        image_field: [display_url],
        "image_url": display_url,
        "url": display_url,
        "generation_reference_url": replacement["generation_reference_url"],
        "replacement_source": replacement["source"],
    }
    mappings = {
        "third_asset_id": "third_asset_id",
        "asset_type": "replacement_asset_type",
        "content_asset_id": "replacement_asset_id",
        "asset_name": "replacement_asset_name",
    }
    for source_key, target_key in mappings.items():
        if source_key in replacement:
            result[target_key] = replacement[source_key]
    return result


def _rebuild_review_scene(
    value: Mapping[str, Any],
    global_assets: Mapping[str, Any],
    creation_contract: Mapping[str, Any],
) -> dict[str, Any]:
    scene = json.loads(_canonical_json(value, field_name="待重算分镜"))
    references = _validated_reference_ids(scene.get("reference_asset_ids"))
    lookup = _asset_lookup(global_assets)
    for asset_id in references:
        asset = lookup.get(asset_id)
        if asset is None or "image_url" not in asset:
            raise ValueError("分镜只能引用当前存在且有图片的全局资产")
    shot = scene.get("shot_description")
    if not isinstance(shot, Mapping):
        raise ValueError("shot_description 必须是对象")
    text = _required_text(shot.get("text"), "shot_description.text")
    normalized_shot = {
        "text": text,
        "mentions": [
            {
                "asset_id": asset_id,
                "type": lookup[asset_id]["type"],
                "name": lookup[asset_id]["name"],
                "image_url": lookup[asset_id]["image_url"],
            }
            for asset_id in references
        ],
    }
    storyline = _required_text(scene.get("storyline"), "storyline")
    narration = scene.get("narration")
    if not isinstance(narration, str):
        raise ValueError("narration 必须是字符串")
    scene["reference_asset_ids"] = references
    scene["shot_description"] = normalized_shot
    scene["prompt"] = build_authoritative_scene_prompt(
        storyline,
        normalized_shot,
        narration,
        global_assets["visual_style"],
        video_model=_required_text(
            creation_contract.get("video_model"),
            "video_model",
        ),
    )
    return scene


def _bind_asset_images_to_mentions(
    scene_packages: Sequence[Mapping[str, Any]],
    global_assets: Mapping[str, Any],
) -> list[dict[str, Any]]:
    asset_lookup = _asset_lookup(global_assets)
    enriched = json.loads(_canonical_json(scene_packages, field_name="待回填场景包"))
    for scene in enriched:
        reference_ids = scene["reference_asset_ids"]
        if len(reference_ids) > 9:
            raise ValueError("单分镜最多允许 9 个全局资产引用")
        scene["shot_description"]["mentions"] = [
            {
                "asset_id": asset_id,
                "type": asset_lookup[asset_id]["type"],
                "name": asset_lookup[asset_id]["name"],
                "image_url": asset_lookup[asset_id]["image_url"],
            }
            for asset_id in reference_ids
        ]
    return enriched


def _asset_lookup(global_assets: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for collection, asset_type, image_field in (
        ("characters", "character", "three_view_images"),
        ("scenes", "scene", "images"),
        ("props", "prop", "images"),
    ):
        items = global_assets.get(collection)
        if not isinstance(items, list):
            raise ValueError(f"global_assets.{collection} 必须是数组")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"global_assets.{collection} 只能包含对象")
            asset_id = _required_text(item.get("asset_id"), f"{collection}.asset_id")
            if asset_id in lookup:
                raise ValueError("全局资产 asset_id 必须唯一")
            entry = {
                "type": asset_type,
                "name": _required_text(item.get("name"), f"{collection}.name"),
                "collection": collection,
            }
            urls = item.get(image_field)
            if isinstance(urls, list) and len(urls) == 1:
                entry["image_url"] = str(urls[0])
            lookup[asset_id] = entry
    return lookup


def _blueprint_asset_ids(
    blueprint: Mapping[str, Any],
    asset_lookup: Mapping[str, Mapping[str, str]],
) -> list[str]:
    by_collection_and_name = {(asset["collection"], asset["name"]): asset_id for asset_id, asset in asset_lookup.items()}
    result: list[str] = []
    requirements = blueprint.get("asset_requirements")
    if not isinstance(requirements, Mapping):
        raise ValueError("Plan 分镜缺少 asset_requirements")
    for collection in ("characters", "scenes", "props"):
        values = requirements.get(collection)
        if not isinstance(values, list):
            raise ValueError(f"Plan 分镜 asset_requirements.{collection} 必须是数组")
        for name in values:
            asset_id = by_collection_and_name.get((collection, str(name)))
            if asset_id is None:
                raise ValueError(f"Plan 分镜引用的全局资产不存在：{collection}/{name}")
            if asset_id not in result:
                result.append(asset_id)
    return result


def _require_stage(
    state: VideoScenePackageWorkflowState,
    expected: VideoScenePackageStage,
) -> None:
    if state.status is WorkflowStatus.CANCELLED:
        raise ValueError("已取消的场景包 Workflow 属于终态，不能继续推进")
    if state.current_stage is not expected:
        raise ValueError(f"Workflow 当前阶段为 {state.current_stage.value}，不能执行仅属于 {expected.value} 的动作")


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Workflow 时间必须包含时区")
    return timestamp


def _cancellation_timestamp(
    updated_at: datetime,
    value: datetime | None,
) -> datetime:
    """生成严格前进的取消时间，并拒绝调用方提供倒退时间。"""

    timestamp = _timestamp(value)
    if timestamp < updated_at:
        raise ValueError("Workflow 取消时间不能早于当前状态")
    if timestamp == updated_at:
        return timestamp + timedelta(microseconds=1)
    return timestamp


def _required_text(value: Any, field_name: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} 必须是整数")
    return value


def _required_https_url(value: Any, field_name: str) -> str:
    url = _required_text(value, field_name)
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError(f"{field_name} 必须是 HTTPS URL")
    return url


def _validated_generation_reference(
    *,
    source: Any,
    value: Any,
    third_asset_id: Any,
    field_name: str,
) -> str:
    reference = _required_text(value, field_name)
    if source != "digital_human":
        return _required_https_url(reference, field_name)
    normalized_id = _required_text(third_asset_id, "数字人 third_asset_id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", normalized_id):
        raise ValueError("数字人 third_asset_id 格式不合法")
    expected = f"asset://{normalized_id}"
    if reference != expected:
        raise ValueError("数字人生成引用必须与 third_asset_id 精确匹配")
    return reference


def _canonical_json(value: Any, *, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是合法 JSON 数据") from exc
