"""视频采集、方向和 Plan 审核阶段的确定性 Workflow Service。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pixelflow.agent_runtime.contracts import WorkflowKind, WorkflowRecord, WorkflowStatus
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.contract import VideoCreationContract
from pixelflow.creative.plan_markdown import PlanMarkdownResult
from pixelflow.creative.scene_blueprint import normalize_scene_blueprints, scene_blueprint_durations
from pixelflow.intake.forms import CreativeDirection, FormValidationResult

_REVISION_IMMUTABLE_CONTRACT_FIELDS = (
    "version",
    "intent",
    "video_model_mode",
    "video_model",
    "video_model_capabilities",
    "image_model",
    "image_model_capabilities",
)


class VideoPlanningStage(StrEnum):
    """M11.1 冻结的前置规划阶段。"""

    INTAKE = "intake"
    FORM_CANCELLED = "form_cancelled"
    DIRECTION_GENERATION = "direction_generation"
    DIRECTION_REVIEW = "direction_review"
    PLAN_GENERATION = "plan_generation"
    PLAN_REVIEW = "plan_review"
    PLAN_APPROVED = "plan_approved"


@dataclass(frozen=True, slots=True, init=False)
class VideoPlanAuthoritySnapshot:
    """以规范 JSON 保存 Plan 全量权威数据，阻断可变引用反向污染。"""

    _payload_json: str = field(repr=False)
    checksum: str

    def __init__(self, payload: Mapping[str, Any]) -> None:
        payload_json = _canonical_json(payload, field_name="Plan 权威快照")
        object.__setattr__(self, "_payload_json", payload_json)
        object.__setattr__(self, "checksum", hashlib.sha256(payload_json.encode("utf-8")).hexdigest())

    @classmethod
    def from_plan_result(cls, result: PlanMarkdownResult) -> VideoPlanAuthoritySnapshot:
        """校验现有 Plan Service 结果并冻结为可恢复的业务快照。"""

        return cls(_validated_plan_payload(result))

    @property
    def plan_markdown(self) -> str:
        return str(self._payload()["plan_markdown"])

    @property
    def plan_version(self) -> int:
        return int(self._payload()["plan_version"])

    @property
    def plan_history(self) -> list[dict[str, Any]]:
        return list(self._payload()["plan_history"])

    @property
    def creation_contract(self) -> dict[str, Any]:
        return dict(self._payload()["creation_contract"])

    @property
    def scene_durations_sec(self) -> list[int]:
        return list(self._payload()["scene_durations_sec"])

    @property
    def scene_blueprints(self) -> list[dict[str, Any]]:
        return list(self._payload()["scene_blueprints"])

    @property
    def asset_manifest(self) -> dict[str, list[dict[str, str]]]:
        return dict(self._payload()["asset_manifest"])

    @property
    def restored_from_version(self) -> int | None:
        value = self._payload()["restored_from_version"]
        return int(value) if value is not None else None

    def to_dict(self) -> dict[str, Any]:
        """返回独立副本，供 checkpointer 或 Artifact Repository 持久化。"""

        return self._payload()

    def validate(self) -> None:
        """重新校验恢复或直接构造的快照，防止历史权威数据被绕过。"""

        payload = self._payload()
        required_fields = {
            "plan_markdown",
            "plan_version",
            "plan_history",
            "creation_contract",
            "scene_durations_sec",
            "scene_blueprints",
            "asset_manifest",
            "restored_from_version",
        }
        if set(payload) != required_fields:
            raise ValueError("Plan 权威快照字段必须完整且不得包含额外字段")
        candidate = PlanMarkdownResult(
            output_type="video",
            plan_markdown=payload["plan_markdown"],
            template_path=Path("video-plan-authority-snapshot.md"),
            plan_version=payload["plan_version"],
            plan_history=payload["plan_history"],
            creation_contract=payload["creation_contract"],
            scene_durations_sec=payload["scene_durations_sec"],
            scene_blueprints=payload["scene_blueprints"],
            asset_manifest=payload["asset_manifest"],
            restored_from_version=payload["restored_from_version"],
        )
        validated = _validated_plan_payload(candidate)
        if _canonical_json(validated, field_name="重新校验 Plan 权威快照") != self._payload_json:
            raise ValueError("Plan 权威快照与规范化权威数据不一致")

    def _payload(self) -> dict[str, Any]:
        return json.loads(self._payload_json)


@dataclass(frozen=True, slots=True)
class VideoPlanningWorkflowState:
    """M11.1 的业务状态；消息摘要不得替换这些字段。"""

    workflow_id: str
    conversation_id: str
    current_stage: VideoPlanningStage
    status: WorkflowStatus
    stage_version: int
    context_version: int
    created_at: datetime
    updated_at: datetime
    _intake_context_json: str = field(repr=False)
    _form_values_json: str = field(repr=False)
    _creative_directions_json: str = field(repr=False)
    _selected_direction_json: str = field(repr=False)
    _active_plan: VideoPlanAuthoritySnapshot | None = field(default=None, repr=False)

    @property
    def intake_context(self) -> dict[str, Any]:
        return json.loads(self._intake_context_json)

    @property
    def form_values(self) -> dict[str, Any]:
        return json.loads(self._form_values_json)

    @property
    def creative_directions(self) -> list[dict[str, Any]]:
        return json.loads(self._creative_directions_json)

    @property
    def selected_direction(self) -> dict[str, Any]:
        return json.loads(self._selected_direction_json)

    @property
    def active_plan(self) -> VideoPlanAuthoritySnapshot | None:
        return self._active_plan

    @property
    def active_plan_artifact_ref(self) -> str | None:
        """生成稳定逻辑引用，后续切片由 Artifact Repository 落实际载荷。"""

        if self._active_plan is None:
            return None
        workflow_key = quote(self.workflow_id, safe="-_.")
        return (
            f"artifact:video-plan:{workflow_key}:"
            f"v{self._active_plan.plan_version}:{self._active_plan.checksum[:16]}"
        )


class VideoPlanningWorkflowService:
    """对应 Java Application Service：只执行视频前置规划的合法状态转换。"""

    def start(
        self,
        *,
        workflow_id: str,
        conversation_id: str,
        intent: str,
        intake_context: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """登记已识别的视频意图，但不跳过需求表单。"""

        if intent != "video":
            raise ValueError("视频 Workflow 只能接收 video intent")
        normalized_workflow_id = _required_text(workflow_id, "workflow_id")
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        timestamp = _timestamp(now)
        state = VideoPlanningWorkflowState(
            workflow_id=normalized_workflow_id,
            conversation_id=normalized_conversation_id,
            current_stage=VideoPlanningStage.INTAKE,
            status=WorkflowStatus.DRAFT,
            stage_version=1,
            context_version=1,
            created_at=timestamp,
            updated_at=timestamp,
            _intake_context_json=_canonical_json(intake_context or {}, field_name="intake_context"),
            _form_values_json=_canonical_json({}, field_name="form_values"),
            _creative_directions_json=_canonical_json([], field_name="creative_directions"),
            _selected_direction_json=_canonical_json({}, field_name="selected_direction"),
        )
        self.validate_state(state)
        return state

    def confirm_intake(
        self,
        state: VideoPlanningWorkflowState,
        validation: FormValidationResult,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """确认完整视频表单，并进入创意方向生成阶段。"""

        _require_stage(state, VideoPlanningStage.INTAKE)
        if validation.intent != "video":
            raise ValueError("采集结果不是 video intent")
        if not validation.is_complete or validation.missing_fields or validation.terminated:
            raise ValueError("视频需求表单必须完整且未终止")
        confirmed_contract = VideoCreationContract.model_validate(validation.values)
        if not confirmed_contract.confirmed_by_user:
            raise ValueError("视频创作合同必须经过用户确认")
        if any(
            value is not None
            for value in (
                confirmed_contract.scene_image_ratio,
                confirmed_contract.scene_image_size,
                confirmed_contract.scene_image_spec_source,
            )
        ):
            raise ValueError("采集表单不能提前写入 Plan 场景图规格")
        return self._advance(
            state,
            stage=VideoPlanningStage.DIRECTION_GENERATION,
            status=WorkflowStatus.RUNNING,
            form_values=validation.values,
            now=now,
        )

    def cancel_intake(
        self,
        state: VideoPlanningWorkflowState,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """把需求表单右上角关闭动作固化为不可继续的取消终态。"""

        _require_stage(state, VideoPlanningStage.INTAKE)
        return self._advance(
            state,
            stage=VideoPlanningStage.FORM_CANCELLED,
            status=WorkflowStatus.CANCELLED,
            now=now,
        )

    def publish_directions(
        self,
        state: VideoPlanningWorkflowState,
        directions: Sequence[CreativeDirection | Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """发布恰好三个创意方向并等待用户手动选择。"""

        _require_stage(state, VideoPlanningStage.DIRECTION_GENERATION)
        normalized = _normalize_directions(directions)
        return self._advance(
            state,
            stage=VideoPlanningStage.DIRECTION_REVIEW,
            status=WorkflowStatus.AWAITING_USER,
            creative_directions=normalized,
            now=now,
        )

    def select_direction(
        self,
        state: VideoPlanningWorkflowState,
        direction_id: str,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """只接受当前方向列表中的显式选择，不设置倒计时默认值。"""

        _require_stage(state, VideoPlanningStage.DIRECTION_REVIEW)
        normalized_id = _required_text(direction_id, "direction_id")
        selected = next(
            (item for item in state.creative_directions if item.get("direction_id") == normalized_id),
            None,
        )
        if selected is None:
            raise ValueError(f"创意方向不存在：{normalized_id}")
        return self._advance(
            state,
            stage=VideoPlanningStage.PLAN_GENERATION,
            status=WorkflowStatus.RUNNING,
            selected_direction=selected,
            now=now,
        )

    def regenerate_directions(
        self,
        state: VideoPlanningWorkflowState,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """显式返回方向生成阶段，不保留上一批候选或选择。"""

        _require_stage(state, VideoPlanningStage.DIRECTION_REVIEW)
        return self._advance(
            state,
            stage=VideoPlanningStage.DIRECTION_GENERATION,
            status=WorkflowStatus.RUNNING,
            creative_directions=[],
            selected_direction={},
            now=now,
        )

    def publish_initial_plan(
        self,
        state: VideoPlanningWorkflowState,
        result: PlanMarkdownResult,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """发布首版 Plan 权威快照；失败结果不能进入人工审核。"""

        _require_stage(state, VideoPlanningStage.PLAN_GENERATION)
        if result.error:
            raise ValueError("Plan 生成失败，不能发布权威快照")
        snapshot = VideoPlanAuthoritySnapshot.from_plan_result(result)
        _validate_initial_plan_contract(state, snapshot)
        versions = _history_versions(snapshot.plan_history)
        if snapshot.plan_version != 1 or versions != [1] or snapshot.restored_from_version is not None:
            raise ValueError("初始 Plan 必须从唯一 v1 历史开始")
        return self._advance(
            state,
            stage=VideoPlanningStage.PLAN_REVIEW,
            status=WorkflowStatus.AWAITING_USER,
            active_plan=snapshot,
            now=now,
        )

    def publish_revision(
        self,
        state: VideoPlanningWorkflowState,
        result: PlanMarkdownResult,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """修订只能追加下一历史版本，不能篡改任何旧版本。"""

        _require_stage(state, VideoPlanningStage.PLAN_REVIEW)
        current = _required_active_plan(state)
        if result.error:
            raise ValueError("Plan 修订失败，已保留当前权威版本")
        candidate = VideoPlanAuthoritySnapshot.from_plan_result(result)
        current_history = current.plan_history
        candidate_history = candidate.plan_history
        expected_version = max(_history_versions(current_history)) + 1
        if candidate.plan_version != expected_version:
            raise ValueError(f"Plan 修订版本必须连续追加为 v{expected_version}")
        if candidate.restored_from_version is not None:
            raise ValueError("Plan 修订不能伪装成历史恢复")
        if len(candidate_history) != len(current_history) + 1:
            raise ValueError("Plan 修订必须且只能追加一个历史版本")
        if _canonical_json(candidate_history[:-1], field_name="候选 Plan 历史") != _canonical_json(
            current_history,
            field_name="当前 Plan 历史",
        ):
            raise ValueError("Plan 修订不得重写已有历史版本")
        if int(candidate_history[-1]["version"]) != expected_version:
            raise ValueError("Plan 修订历史版本必须连续")
        current_immutable_contract = {
            field_name: current.creation_contract[field_name]
            for field_name in _REVISION_IMMUTABLE_CONTRACT_FIELDS
        }
        candidate_immutable_contract = {
            field_name: candidate.creation_contract[field_name]
            for field_name in _REVISION_IMMUTABLE_CONTRACT_FIELDS
        }
        if _canonical_json(
            candidate_immutable_contract,
            field_name="修订 Plan 不可变创作合同",
        ) != _canonical_json(
            current_immutable_contract,
            field_name="当前 Plan 不可变创作合同",
        ):
            raise ValueError("Plan 修订不得漂移已确认的不可变合同字段，包括模型及能力快照")
        return self._advance(
            state,
            stage=VideoPlanningStage.PLAN_REVIEW,
            status=WorkflowStatus.AWAITING_USER,
            active_plan=candidate,
            now=now,
        )

    def restore_plan(
        self,
        state: VideoPlanningWorkflowState,
        result: PlanMarkdownResult,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """切换到所选历史 Plan，不追加重复版本。"""

        _require_stage(state, VideoPlanningStage.PLAN_REVIEW)
        current = _required_active_plan(state)
        if result.error:
            raise ValueError("Plan 恢复失败，已保留当前权威版本")
        candidate = VideoPlanAuthoritySnapshot.from_plan_result(result)
        if _canonical_json(candidate.plan_history, field_name="恢复 Plan 历史") != _canonical_json(
            current.plan_history,
            field_name="当前 Plan 历史",
        ):
            raise ValueError("Plan 恢复不能新增或重写历史版本")
        if candidate.restored_from_version != candidate.plan_version:
            raise ValueError("Plan 恢复版本与 restored_from_version 不一致")
        if candidate.plan_version not in _history_versions(current.plan_history):
            raise ValueError("Plan 恢复目标不在当前历史中")
        return self._advance(
            state,
            stage=VideoPlanningStage.PLAN_REVIEW,
            status=WorkflowStatus.AWAITING_USER,
            active_plan=candidate,
            now=now,
        )

    def approve_plan(
        self,
        state: VideoPlanningWorkflowState,
        *,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        """持久化用户显式同意动作，避免等待审核状态被误当成已审核。"""

        _require_stage(state, VideoPlanningStage.PLAN_REVIEW)
        if state.status is not WorkflowStatus.AWAITING_USER:
            raise ValueError("只有等待人工审核的 Plan 才能记录用户同意")
        _required_active_plan(state).validate()
        return self._advance(
            state,
            stage=VideoPlanningStage.PLAN_APPROVED,
            status=WorkflowStatus.RUNNING,
            now=now,
        )

    def to_workflow_record(self, state: VideoPlanningWorkflowState) -> WorkflowRecord:
        """投影通用 Runtime DTO；完整 Plan 仍留在业务快照而非合同摘要。"""

        self.validate_state(state)
        active_plan = state.active_plan
        artifact_ref = state.active_plan_artifact_ref
        return WorkflowRecord(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            kind=WorkflowKind.VIDEO,
            status=state.status,
            current_stage=state.current_stage.value,
            stage_version=state.stage_version,
            creation_contract_snapshot=(active_plan.creation_contract if active_plan is not None else {}),
            pending_external_job=None,
            latest_artifact_refs=([artifact_ref] if artifact_ref is not None else []),
            context_version=state.context_version,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    def validate_state(self, state: VideoPlanningWorkflowState) -> None:
        """无副作用重验完整规划状态，供业务转换和持久化恢复共同调用。"""

        _required_text(state.workflow_id, "workflow_id")
        _required_text(state.conversation_id, "conversation_id")
        if not isinstance(state.current_stage, VideoPlanningStage):
            raise ValueError("规划状态 current_stage 不受支持")
        if not isinstance(state.status, WorkflowStatus):
            raise ValueError("规划状态 status 不受支持")
        if isinstance(state.stage_version, bool) or not isinstance(state.stage_version, int) or state.stage_version < 1:
            raise ValueError("规划状态 stage_version 必须是正整数")
        if isinstance(state.context_version, bool) or not isinstance(state.context_version, int) or state.context_version < 1:
            raise ValueError("规划状态 context_version 必须是正整数")
        if state.stage_version != state.context_version:
            raise ValueError("规划状态 stage_version 与 context_version 必须同步推进")
        minimum_version = {
            VideoPlanningStage.INTAKE: 1,
            VideoPlanningStage.FORM_CANCELLED: 2,
            VideoPlanningStage.DIRECTION_GENERATION: 2,
            VideoPlanningStage.DIRECTION_REVIEW: 3,
            VideoPlanningStage.PLAN_GENERATION: 4,
            VideoPlanningStage.PLAN_REVIEW: 5,
            VideoPlanningStage.PLAN_APPROVED: 6,
        }[state.current_stage]
        if state.stage_version < minimum_version:
            raise ValueError("规划状态版本早于当前阶段的最小合法版本")
        if state.current_stage is VideoPlanningStage.INTAKE and state.stage_version != 1:
            raise ValueError("采集初始规划状态版本必须为 1")
        if state.current_stage is VideoPlanningStage.FORM_CANCELLED and state.stage_version != 2:
            raise ValueError("表单取消规划状态版本必须为 2")
        created_at = _timestamp(state.created_at)
        updated_at = _timestamp(state.updated_at)
        if updated_at < created_at:
            raise ValueError("规划状态更新时间不能早于创建时间")

        expected_status = {
            VideoPlanningStage.INTAKE: WorkflowStatus.DRAFT,
            VideoPlanningStage.FORM_CANCELLED: WorkflowStatus.CANCELLED,
            VideoPlanningStage.DIRECTION_GENERATION: WorkflowStatus.RUNNING,
            VideoPlanningStage.DIRECTION_REVIEW: WorkflowStatus.AWAITING_USER,
            VideoPlanningStage.PLAN_GENERATION: WorkflowStatus.RUNNING,
            VideoPlanningStage.PLAN_REVIEW: WorkflowStatus.AWAITING_USER,
            VideoPlanningStage.PLAN_APPROVED: WorkflowStatus.RUNNING,
        }[state.current_stage]
        if state.status is not expected_status:
            raise ValueError("规划状态的阶段与 WorkflowStatus 不一致")

        form_values = state.form_values
        directions = state.creative_directions
        selected = state.selected_direction
        active_plan = state.active_plan
        if state.current_stage in {VideoPlanningStage.INTAKE, VideoPlanningStage.FORM_CANCELLED}:
            if form_values or directions or selected or active_plan is not None:
                raise ValueError("采集或取消阶段不得提前持有规划字段或 Plan")
            return

        confirmed_contract = VideoCreationContract.model_validate(form_values)
        if not confirmed_contract.confirmed_by_user:
            raise ValueError("规划状态必须保留用户确认的视频创作合同")
        if state.current_stage is VideoPlanningStage.DIRECTION_GENERATION:
            if directions or selected or active_plan is not None:
                raise ValueError("方向生成阶段不得保留候选、选择或 Plan")
            return

        normalized_directions = _normalize_directions(directions)
        if _canonical_json(normalized_directions, field_name="规范创意方向") != _canonical_json(
            directions,
            field_name="规划状态创意方向",
        ):
            raise ValueError("规划状态创意方向不是规范权威数据")
        if state.current_stage is VideoPlanningStage.DIRECTION_REVIEW:
            if selected or active_plan is not None:
                raise ValueError("方向审核阶段不得提前选择方向或持有 Plan")
            return

        if not selected or all(
            _canonical_json(selected, field_name="已选创意方向")
            != _canonical_json(direction, field_name="候选创意方向")
            for direction in normalized_directions
        ):
            raise ValueError("规划状态的已选方向必须来自当前三个候选方向")
        if state.current_stage is VideoPlanningStage.PLAN_GENERATION:
            if active_plan is not None:
                raise ValueError("Plan 生成阶段不得提前持有活动 Plan")
            return

        if active_plan is None:
            raise ValueError("Plan 审核或批准阶段必须持有活动 Plan")
        active_plan.validate()
        _validate_initial_plan_contract(state, active_plan)

    def _advance(
        self,
        state: VideoPlanningWorkflowState,
        *,
        stage: VideoPlanningStage,
        status: WorkflowStatus,
        form_values: Mapping[str, Any] | None = None,
        creative_directions: Sequence[Mapping[str, Any]] | None = None,
        selected_direction: Mapping[str, Any] | None = None,
        active_plan: VideoPlanAuthoritySnapshot | None = None,
        now: datetime | None = None,
    ) -> VideoPlanningWorkflowState:
        self.validate_state(state)
        timestamp = _timestamp(now)
        if timestamp < state.updated_at:
            raise ValueError("Workflow 更新时间不能早于当前状态")
        result = VideoPlanningWorkflowState(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            current_stage=stage,
            status=status,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            created_at=state.created_at,
            updated_at=timestamp,
            _intake_context_json=state._intake_context_json,
            _form_values_json=(
                _canonical_json(form_values, field_name="form_values")
                if form_values is not None
                else state._form_values_json
            ),
            _creative_directions_json=(
                _canonical_json(creative_directions, field_name="creative_directions")
                if creative_directions is not None
                else state._creative_directions_json
            ),
            _selected_direction_json=(
                _canonical_json(selected_direction, field_name="selected_direction")
                if selected_direction is not None
                else state._selected_direction_json
            ),
            _active_plan=active_plan if active_plan is not None else state.active_plan,
        )
        self.validate_state(result)
        return result


def _validated_plan_payload(result: PlanMarkdownResult) -> dict[str, Any]:
    if result.output_type != "video":
        raise ValueError("Plan 权威快照只能接收 video 结果")
    plan_markdown = _required_document(result.plan_markdown, "plan_markdown")
    if isinstance(result.plan_version, bool) or not isinstance(result.plan_version, int) or result.plan_version < 1:
        raise ValueError("Plan 版本必须是正整数")

    contract = _validated_contract(result.creation_contract)
    total_duration = int(contract["video_duration_sec"])
    try:
        blueprints = normalize_scene_blueprints(
            result.scene_blueprints,
            total_duration_sec=total_duration,
        )
    except ValueError as exc:
        raise ValueError(f"Plan 分镜时间线无效：{exc}") from exc
    if _canonical_json(blueprints, field_name="规范分镜") != _canonical_json(
        result.scene_blueprints,
        field_name="原始分镜",
    ):
        raise ValueError("Plan 分镜不是规范化权威蓝图")

    durations = scene_blueprint_durations(blueprints)
    if durations != result.scene_durations_sec or sum(durations) != total_duration:
        raise ValueError("Plan 分镜时长必须与蓝图及创作合同精确一致")
    try:
        manifest = normalize_asset_manifest(result.asset_manifest, blueprints)
    except ValueError as exc:
        raise ValueError(f"Plan 资产清单无效：{exc}") from exc
    if _canonical_json(manifest, field_name="规范资产清单") != _canonical_json(
        result.asset_manifest,
        field_name="原始资产清单",
    ):
        raise ValueError("Plan 资产清单不是规范化权威清单")

    history = _validated_history(result.plan_history)
    current = next((item for item in history if int(item["version"]) == result.plan_version), None)
    if current is None:
        raise ValueError("当前 Plan 版本不在历史中")
    restored_from = result.restored_from_version
    if restored_from is not None and (
        isinstance(restored_from, bool) or not isinstance(restored_from, int) or restored_from < 1
    ):
        raise ValueError("restored_from_version 必须是正整数")
    historical_markdown = str(current["plan_markdown"])
    if restored_from is not None and plan_markdown.strip() == historical_markdown.strip():
        # 旧恢复 Service 会清理文档首尾空白；业务快照仍以历史原文为准。
        plan_markdown = historical_markdown

    current_authority = {
        "plan_markdown": current["plan_markdown"],
        "creation_contract": current["creation_contract"],
        "scene_durations_sec": current["scene_durations_sec"],
        "scene_blueprints": current["scene_blueprints"],
        "asset_manifest": current["asset_manifest"],
    }
    published_authority = {
        "plan_markdown": plan_markdown,
        "creation_contract": contract,
        "scene_durations_sec": durations,
        "scene_blueprints": blueprints,
        "asset_manifest": manifest,
    }
    if _canonical_json(current_authority, field_name="当前历史权威数据") != _canonical_json(
        published_authority,
        field_name="待发布权威数据",
    ):
        raise ValueError("当前 Plan 与同版本历史权威快照不一致")

    return {
        "plan_markdown": plan_markdown,
        "plan_version": result.plan_version,
        "plan_history": history,
        "creation_contract": contract,
        "scene_durations_sec": durations,
        "scene_blueprints": blueprints,
        "asset_manifest": manifest,
        "restored_from_version": restored_from,
    }


def _validate_initial_plan_contract(
    state: VideoPlanningWorkflowState,
    snapshot: VideoPlanAuthoritySnapshot,
) -> None:
    """首版 Plan 只能补充受支持的场景图规格，不能漂移用户确认合同。"""

    confirmed = VideoCreationContract.model_validate(state.form_values).model_dump(exclude_none=True)
    candidate = snapshot.creation_contract
    scene_spec_fields = {
        "scene_image_ratio",
        "scene_image_size",
        "scene_image_spec_source",
    }
    candidate_base = {key: value for key, value in candidate.items() if key not in scene_spec_fields}
    if _canonical_json(candidate_base, field_name="Plan 基础创作合同") != _canonical_json(
        confirmed,
        field_name="采集确认创作合同",
    ):
        raise ValueError("初始 Plan 创作合同必须继承采集阶段的用户确认值")

def _validated_history(history: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(history, list) or not history:
        raise ValueError("Plan 历史不能为空")
    normalized: list[dict[str, Any]] = []
    versions: list[int] = []
    required_fields = {
        "version",
        "plan_markdown",
        "creation_contract",
        "scene_durations_sec",
        "scene_blueprints",
        "asset_manifest",
    }
    for position, item in enumerate(history, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"Plan 历史第 {position} 项必须是对象")
        missing = sorted(required_fields.difference(item))
        if missing:
            raise ValueError(f"Plan 历史第 {position} 项缺少权威字段：{', '.join(missing)}")
        version = item.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("Plan 历史版本必须是正整数")
        contract = _validated_contract(item.get("creation_contract"))
        total_duration = int(contract["video_duration_sec"])
        try:
            blueprints = normalize_scene_blueprints(
                item.get("scene_blueprints"),
                total_duration_sec=total_duration,
            )
            manifest = normalize_asset_manifest(item.get("asset_manifest"), blueprints)
        except ValueError as exc:
            raise ValueError(f"Plan 历史 v{version} 权威数据无效：{exc}") from exc
        if _canonical_json(blueprints, field_name=f"Plan 历史 v{version} 规范分镜") != _canonical_json(
            item.get("scene_blueprints"),
            field_name=f"Plan 历史 v{version} 原始分镜",
        ):
            raise ValueError(f"Plan 历史 v{version} 分镜蓝图不得被静默规范化")
        if _canonical_json(manifest, field_name=f"Plan 历史 v{version} 规范资产清单") != _canonical_json(
            item.get("asset_manifest"),
            field_name=f"Plan 历史 v{version} 原始资产清单",
        ):
            raise ValueError(f"Plan 历史 v{version} 资产清单不得被静默规范化")
        durations = scene_blueprint_durations(blueprints)
        if item.get("scene_durations_sec") != durations or sum(durations) != total_duration:
            raise ValueError(f"Plan 历史 v{version} 分镜时长不一致")
        normalized_item = json.loads(_canonical_json(item, field_name=f"Plan 历史 v{version}"))
        normalized_item.update(
            {
                "version": version,
                "plan_markdown": _required_document(item.get("plan_markdown"), f"Plan 历史 v{version} 正文"),
                "creation_contract": contract,
                "scene_durations_sec": durations,
                "scene_blueprints": blueprints,
                "asset_manifest": manifest,
            }
        )
        normalized.append(normalized_item)
        versions.append(version)
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError("Plan 历史版本必须从 v1 连续递增且保持唯一")
    return normalized


def _validated_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Plan 创作合同必须是对象")
    normalized = VideoCreationContract.model_validate(value).model_dump(exclude_none=True)
    if _canonical_json(normalized, field_name="规范创作合同") != _canonical_json(
        value,
        field_name="原始创作合同",
    ):
        raise ValueError("Plan 创作合同包含未知字段或未规范值")
    if not normalized["confirmed_by_user"]:
        raise ValueError("Plan 创作合同必须保留用户确认状态")
    scene_spec_fields = {
        "scene_image_ratio",
        "scene_image_size",
        "scene_image_spec_source",
    }
    present_fields = scene_spec_fields.intersection(normalized)
    if present_fields and present_fields != scene_spec_fields:
        raise ValueError("Plan 场景图规格必须同时包含比例、尺寸和来源")
    if present_fields:
        image_capabilities = normalized["image_model_capabilities"]
        if normalized["scene_image_ratio"] not in image_capabilities["aspect_ratios"]:
            raise ValueError("Plan 场景图规格比例不在图片模型支持范围内")
        if normalized["scene_image_size"] not in image_capabilities["sizes"]:
            raise ValueError("Plan 场景图规格尺寸不在图片模型支持范围内")
    return normalized


def _normalize_directions(
    directions: Sequence[CreativeDirection | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(directions, (str, bytes)) or len(directions) != 3:
        raise ValueError("视频创意方向必须恰好包含 3 个候选")
    normalized: list[dict[str, Any]] = []
    identifiers: list[str] = []
    for position, direction in enumerate(directions, start=1):
        if isinstance(direction, CreativeDirection):
            item = direction.to_dict()
        elif isinstance(direction, Mapping):
            item = dict(direction)
        else:
            raise ValueError(f"创意方向第 {position} 项必须是对象")
        identifier = _required_text(item.get("direction_id"), f"创意方向 {position} direction_id")
        item["direction_id"] = identifier
        item["title"] = _required_text(item.get("title"), f"创意方向 {position} title")
        item["description"] = _required_text(item.get("description"), f"创意方向 {position} description")
        normalized.append(json.loads(_canonical_json(item, field_name=f"创意方向 {position}")))
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("创意方向 direction_id 必须唯一")
    return normalized


def _required_active_plan(state: VideoPlanningWorkflowState) -> VideoPlanAuthoritySnapshot:
    if state.active_plan is None:
        raise ValueError("当前 Workflow 缺少 Plan 权威快照")
    return state.active_plan


def _require_stage(state: VideoPlanningWorkflowState, expected: VideoPlanningStage) -> None:
    if state.current_stage is not expected:
        raise ValueError(
            f"Workflow 当前阶段为 {state.current_stage.value}，不能执行仅属于 {expected.value} 的动作"
        )


def _history_versions(history: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(item["version"]) for item in history]


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Workflow 时间必须包含时区")
    return timestamp


def _required_text(value: Any, field_name: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _required_document(value: Any, field_name: str) -> str:
    """只判断文档是否为空，保留 Plan Markdown 的全部原始字符。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    return value


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
