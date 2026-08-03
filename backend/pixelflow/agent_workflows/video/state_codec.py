"""五类视频 Workflow 权威状态的规范 JSON 编解码。"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, cast

from pydantic import ConfigDict, Field, JsonValue, field_serializer, field_validator

from pixelflow.agent_runtime.contracts import ExternalJobRef, WorkflowRecord, WorkflowStatus
from pixelflow.agent_runtime.contracts.base import ContractModel

from .delivery import VideoDeliveryWorkflowService, VideoDeliveryWorkflowState
from .planning import (
    VideoPlanAuthoritySnapshot,
    VideoPlanningStage,
    VideoPlanningWorkflowService,
    VideoPlanningWorkflowState,
)
from .postproduction import (
    VideoPostProductionStage,
    VideoPostProductionWorkflowService,
    VideoPostProductionWorkflowState,
)
from .scene_packages import (
    VideoScenePackageAuthoritySnapshot,
    VideoScenePackageStage,
    VideoScenePackageWorkflowService,
    VideoScenePackageWorkflowState,
)
from .video_generation import (
    VideoSceneGenerationStage,
    VideoSceneGenerationWorkflowService,
    VideoSceneGenerationWorkflowState,
)


class VideoWorkflowStateKind(StrEnum):
    """区分五类可恢复视频领域状态。"""

    PLANNING = "planning"
    SCENE_PACKAGE = "scene_package"
    SCENE_GENERATION = "scene_generation"
    POSTPRODUCTION = "postproduction"
    DELIVERY = "delivery"


class _FrozenJsonList(tuple[object, ...]):
    """保留 JSON 数组比较语义，同时移除原地修改能力。"""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, (list, tuple)) and tuple(self) == tuple(other)

    __hash__ = None


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return _FrozenJsonList(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(child) for child in value]
    return value


class VideoWorkflowStateEnvelope(ContractModel):
    """保存状态身份、乐观版本、规范载荷摘要和最近动作游标。"""

    # Plan Markdown 必须原样保存，信封字符串边界由 codec 显式执行更严格的无空白校验。
    model_config = ConfigDict(
        frozen=True,
        revalidate_instances="always",
        str_strip_whitespace=False,
    )

    schema_version: Literal[1] = 1
    workflow_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    state_kind: VideoWorkflowStateKind
    workflow_version: int = Field(ge=1)
    context_version: int = Field(ge=1)
    payload: Mapping[str, JsonValue]
    payload_sha256: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$",
        description="schema v1 复用历史字段名保存完整规范信封摘要，而不是只摘要 payload。",
    )
    last_turn_id: str = Field(min_length=1)
    last_action_key: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("payload", mode="before")
    @classmethod
    def copy_payload_for_validation(cls, value: object) -> object:
        """重验证现有实例时先解冻复制，避免信任原实例及其可变别名。"""

        del cls
        return _thaw_json(value)

    def model_post_init(self, context: object, /) -> None:
        """复制并递归冻结载荷，避免调用方别名污染持久化状态。"""

        del context
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @field_serializer("payload")
    def serialize_payload(self, value: object) -> object:
        """序列化时恢复普通 JSON 容器，供 Repository 稳定落库。"""

        return _thaw_json(value)


type VideoWorkflowState = (
    VideoPlanningWorkflowState
    | VideoScenePackageWorkflowState
    | VideoSceneGenerationWorkflowState
    | VideoPostProductionWorkflowState
    | VideoDeliveryWorkflowState
)

_COMMON_FIELDS = {
    "workflow_id",
    "conversation_id",
    "current_stage",
    "status",
    "stage_version",
    "context_version",
    "created_at",
    "updated_at",
}
_PLANNING_FIELDS = _COMMON_FIELDS | {
    "intake_context",
    "form_values",
    "creative_directions",
    "selected_direction",
    "active_plan",
}
_SCENE_PACKAGE_FIELDS = _COMMON_FIELDS | {
    "source_plan",
    "source_plan_artifact_ref",
    "scene_package",
}
_SCENE_GENERATION_FIELDS = _COMMON_FIELDS | {
    "source_scene_package",
    "scene_packages",
    "scene_videos",
    "failed_scenes",
    "generation_requests",
    "pending_operations",
    "operation_attempts",
    "terminal_claims",
    "edited_scene_ids",
    "dirty_scene_ids",
}
_POSTPRODUCTION_FIELDS = _COMMON_FIELDS | {
    "generation_state",
    "merge_request",
    "merged_video",
    "merge_error",
    "quality_review",
    "quality_feedback",
    "pending_operation",
    "terminal_result_hash",
    "terminal_claims",
    "operation_attempts",
    "finalized_by_user",
}
_DELIVERY_FIELDS = _COMMON_FIELDS | {
    "postproduction_state",
    "jianying_draft_records",
    "operation_attempts",
    "pending_jianying_request",
    "pending_jianying_operation",
    "pending_operation",
    "final_video_delivery",
}


def _validate_json(value: object, *, field_name: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} 必须是合法 JSON，不能包含 NaN 或 Infinity")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} 的 JSON 对象键必须是字符串")
            _validate_json(child, field_name=field_name)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _validate_json(child, field_name=field_name)
        return
    raise ValueError(f"{field_name} 必须是合法 JSON 数据")


def _json_copy(value: object, *, field_name: str) -> JsonValue:
    thawed = _thaw_json(value)
    _validate_json(thawed, field_name=field_name)
    try:
        encoded = json.dumps(
            thawed,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是合法 JSON 数据") from exc
    return cast(JsonValue, json.loads(encoded))


def _canonical_json(value: object, *, field_name: str) -> str:
    normalized = _json_copy(value, field_name=field_name)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_payload_sha256(payload: Mapping[str, JsonValue]) -> str:
    """计算不受对象键顺序影响的规范 SHA-256。"""

    encoded = _canonical_json(payload, field_name="视频 Workflow payload")
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def canonical_video_workflow_envelope_sha256(
    envelope: VideoWorkflowStateEnvelope,
) -> str:
    """摘要除自身摘要字段外的完整规范信封签名体。"""

    signature_body = envelope.model_dump(mode="json", exclude={"payload_sha256"})
    encoded = _canonical_json(signature_body, field_name="视频 Workflow 规范信封签名体")
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _encode_common(state: VideoWorkflowState) -> dict[str, JsonValue]:
    return {
        "workflow_id": state.workflow_id,
        "conversation_id": state.conversation_id,
        "current_stage": state.current_stage.value,
        "status": state.status.value,
        "stage_version": state.stage_version,
        "context_version": state.context_version,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
    }


def _encode_planning(state: VideoPlanningWorkflowState) -> dict[str, JsonValue]:
    payload = _encode_common(state)
    payload.update(
        intake_context=cast(JsonValue, state.intake_context),
        form_values=cast(JsonValue, state.form_values),
        creative_directions=cast(JsonValue, state.creative_directions),
        selected_direction=cast(JsonValue, state.selected_direction),
        active_plan=cast(JsonValue, state.active_plan.to_dict()) if state.active_plan else None,
    )
    return payload


def _encode_scene_package(state: VideoScenePackageWorkflowState) -> dict[str, JsonValue]:
    payload = _encode_common(state)
    payload.update(
        source_plan=cast(JsonValue, state.source_plan.to_dict()),
        source_plan_artifact_ref=state.source_plan_artifact_ref,
        scene_package=cast(JsonValue, state.scene_package.to_dict()),
    )
    return payload


def _encode_scene_generation(state: VideoSceneGenerationWorkflowState) -> dict[str, JsonValue]:
    payload = _encode_common(state)
    payload.update(
        source_scene_package=cast(JsonValue, _encode_scene_package(state._source_state)),
        scene_packages=cast(JsonValue, state.scene_packages),
        scene_videos=cast(JsonValue, state.scene_videos),
        failed_scenes=cast(JsonValue, state.failed_scenes),
        generation_requests=cast(JsonValue, state.generation_requests),
        pending_operations=cast(JsonValue, [item.model_dump(mode="json") for item in state.pending_operations]),
        operation_attempts=cast(JsonValue, state.operation_attempts),
        terminal_claims=cast(JsonValue, state.terminal_claims),
        edited_scene_ids=cast(JsonValue, state.edited_scene_ids),
        dirty_scene_ids=cast(JsonValue, state.dirty_scene_ids),
    )
    return payload


def _encode_postproduction(state: VideoPostProductionWorkflowState) -> dict[str, JsonValue]:
    pending = state.pending_operation
    payload = _encode_common(state)
    payload.update(
        generation_state=cast(JsonValue, _encode_scene_generation(state.generation_state)),
        merge_request=cast(JsonValue, state.merge_request),
        merged_video=cast(JsonValue, state.merged_video),
        merge_error=cast(JsonValue, state.merge_error),
        quality_review=cast(JsonValue, state.quality_review),
        quality_feedback=state.quality_feedback,
        pending_operation=cast(JsonValue, pending.model_dump(mode="json")) if pending else None,
        terminal_result_hash=state._terminal_result_hash,
        terminal_claims=cast(JsonValue, state.terminal_claims),
        operation_attempts=cast(JsonValue, state.operation_attempts),
        finalized_by_user=state.finalized_by_user,
    )
    return payload


def _encode_delivery(state: VideoDeliveryWorkflowState) -> dict[str, JsonValue]:
    pending = state.pending_operation
    payload = _encode_common(state)
    payload.update(
        postproduction_state=cast(JsonValue, _encode_postproduction(state.postproduction_state)),
        jianying_draft_records=cast(JsonValue, state.jianying_draft_records),
        operation_attempts=cast(JsonValue, state.operation_attempts),
        pending_jianying_request=cast(JsonValue, state.pending_jianying_request),
        pending_jianying_operation=cast(JsonValue, state.pending_jianying_operation),
        pending_operation=cast(JsonValue, pending.model_dump(mode="json")) if pending else None,
        final_video_delivery=cast(JsonValue, state.final_video_delivery),
    )
    return payload


_ENCODERS: dict[VideoWorkflowStateKind, Callable[[VideoWorkflowState], dict[str, JsonValue]]] = {
    VideoWorkflowStateKind.PLANNING: cast(Callable[[VideoWorkflowState], dict[str, JsonValue]], _encode_planning),
    VideoWorkflowStateKind.SCENE_PACKAGE: cast(Callable[[VideoWorkflowState], dict[str, JsonValue]], _encode_scene_package),
    VideoWorkflowStateKind.SCENE_GENERATION: cast(Callable[[VideoWorkflowState], dict[str, JsonValue]], _encode_scene_generation),
    VideoWorkflowStateKind.POSTPRODUCTION: cast(Callable[[VideoWorkflowState], dict[str, JsonValue]], _encode_postproduction),
    VideoWorkflowStateKind.DELIVERY: cast(Callable[[VideoWorkflowState], dict[str, JsonValue]], _encode_delivery),
}


def _kind_for_state(state: VideoWorkflowState) -> VideoWorkflowStateKind:
    if isinstance(state, VideoPlanningWorkflowState):
        return VideoWorkflowStateKind.PLANNING
    if isinstance(state, VideoScenePackageWorkflowState):
        return VideoWorkflowStateKind.SCENE_PACKAGE
    if isinstance(state, VideoSceneGenerationWorkflowState):
        return VideoWorkflowStateKind.SCENE_GENERATION
    if isinstance(state, VideoPostProductionWorkflowState):
        return VideoWorkflowStateKind.POSTPRODUCTION
    if isinstance(state, VideoDeliveryWorkflowState):
        return VideoWorkflowStateKind.DELIVERY
    raise ValueError("不支持的视频 Workflow 状态类型")


def _required_keys(payload: Mapping[str, JsonValue], expected: set[str], *, state_name: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{state_name} payload 字段不完整或包含额外字段：missing={missing}, unknown={unknown}")


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field_name} 必须是非空且无首尾空白的字符串")
    return value


def _integer(value: object, *, field_name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} 必须是大于等于 {minimum} 的整数")
    return value


def _datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是带时区的 ISO 时间字符串")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是带时区的 ISO 时间字符串") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat() != value:
        raise ValueError(f"{field_name} 必须是规范的带时区 ISO 时间字符串")
    return parsed


def _mapping(value: object, *, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必须是 JSON 对象")
    normalized = _json_copy(value, field_name=field_name)
    if not isinstance(normalized, dict):
        raise ValueError(f"{field_name} 必须是 JSON 对象")
    return cast(dict[str, JsonValue], normalized)


def _list(value: object, *, field_name: str) -> list[JsonValue]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} 必须是 JSON 数组")
    normalized = _json_copy(value, field_name=field_name)
    if not isinstance(normalized, list):
        raise ValueError(f"{field_name} 必须是 JSON 数组")
    return cast(list[JsonValue], normalized)


def _nullable_mapping(value: object, *, field_name: str) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    return _mapping(value, field_name=field_name)


def _optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name=field_name)


def _common_values(
    payload: Mapping[str, JsonValue],
    *,
    stage_type: type[StrEnum],
) -> dict[str, object]:
    try:
        stage = stage_type(_text(payload["current_stage"], field_name="current_stage"))
    except ValueError as exc:
        raise ValueError("current_stage 不是受支持的领域枚举") from exc
    try:
        status = WorkflowStatus(_text(payload["status"], field_name="status"))
    except ValueError as exc:
        raise ValueError("status 不是受支持的 Workflow 枚举") from exc
    created_at = _datetime(payload["created_at"], field_name="created_at")
    updated_at = _datetime(payload["updated_at"], field_name="updated_at")
    if updated_at < created_at:
        raise ValueError("Workflow 更新时间不能早于创建时间")
    return {
        "workflow_id": _text(payload["workflow_id"], field_name="workflow_id"),
        "conversation_id": _text(payload["conversation_id"], field_name="conversation_id"),
        "current_stage": stage,
        "status": status,
        "stage_version": _integer(payload["stage_version"], field_name="stage_version"),
        "context_version": _integer(payload["context_version"], field_name="context_version"),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _validate_nested_lineage(
    *,
    parent: VideoWorkflowState,
    child: VideoWorkflowState,
    relation: str,
) -> None:
    """校验外层状态与完整来源状态的身份、版本及时间谱系。"""

    if parent.workflow_id != child.workflow_id or parent.conversation_id != child.conversation_id:
        raise ValueError(f"{relation} 的 Workflow 与对话谱系不一致")
    if parent.created_at != child.created_at:
        raise ValueError(f"{relation} 的创建时间谱系不一致")
    if child.stage_version > parent.stage_version or child.context_version > parent.context_version:
        raise ValueError(f"{relation} 的来源版本不能晚于外层状态谱系")
    if child.updated_at > parent.updated_at:
        raise ValueError(f"{relation} 的来源更新时间不能晚于外层状态谱系")


def _decode_planning(payload: Mapping[str, JsonValue]) -> VideoPlanningWorkflowState:
    _required_keys(payload, _PLANNING_FIELDS, state_name="planning")
    active_payload = _nullable_mapping(payload["active_plan"], field_name="active_plan")
    active_plan = VideoPlanAuthoritySnapshot(active_payload) if active_payload is not None else None
    if active_plan is not None:
        active_plan.validate()
    common = _common_values(payload, stage_type=VideoPlanningStage)
    state = VideoPlanningWorkflowState(**common,
        _intake_context_json=_canonical_json(_mapping(payload["intake_context"], field_name="intake_context"), field_name="intake_context"),
        _form_values_json=_canonical_json(_mapping(payload["form_values"], field_name="form_values"), field_name="form_values"),
        _creative_directions_json=_canonical_json(_list(payload["creative_directions"], field_name="creative_directions"), field_name="creative_directions"),
        _selected_direction_json=_canonical_json(_mapping(payload["selected_direction"], field_name="selected_direction"), field_name="selected_direction"),
        _active_plan=active_plan,
    )
    service = VideoPlanningWorkflowService()
    service.validate_state(state)
    service.to_workflow_record(state)
    return state


def _decode_scene_package(payload: Mapping[str, JsonValue]) -> VideoScenePackageWorkflowState:
    _required_keys(payload, _SCENE_PACKAGE_FIELDS, state_name="scene_package")
    source_plan = VideoPlanAuthoritySnapshot(_mapping(payload["source_plan"], field_name="source_plan"))
    source_plan.validate()
    common = _common_values(payload, stage_type=VideoScenePackageStage)
    state = VideoScenePackageWorkflowState(**common,
        _source_plan=source_plan,
        _source_plan_artifact_ref=_text(payload["source_plan_artifact_ref"], field_name="source_plan_artifact_ref"),
        _scene_package=VideoScenePackageAuthoritySnapshot(_mapping(payload["scene_package"], field_name="scene_package")),
    )
    VideoScenePackageWorkflowService().to_workflow_record(state)
    return state


def _external_job(value: object, *, field_name: str) -> ExternalJobRef:
    try:
        return ExternalJobRef.model_validate(_mapping(value, field_name=field_name))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 不符合 ExternalJobRef 合同") from exc


def _decode_scene_generation(payload: Mapping[str, JsonValue]) -> VideoSceneGenerationWorkflowState:
    _required_keys(payload, _SCENE_GENERATION_FIELDS, state_name="scene_generation")
    source_state = _decode_scene_package(_mapping(payload["source_scene_package"], field_name="source_scene_package"))
    pending = tuple(
        _external_job(item, field_name="pending_operations")
        for item in _list(payload["pending_operations"], field_name="pending_operations")
    )
    common = _common_values(payload, stage_type=VideoSceneGenerationStage)
    state = VideoSceneGenerationWorkflowState(**common,
        _source_state=source_state,
        _scene_packages_json=_canonical_json(_list(payload["scene_packages"], field_name="scene_packages"), field_name="scene_packages"),
        _scene_videos_json=_canonical_json(_list(payload["scene_videos"], field_name="scene_videos"), field_name="scene_videos"),
        _failed_scenes_json=_canonical_json(_list(payload["failed_scenes"], field_name="failed_scenes"), field_name="failed_scenes"),
        _generation_requests_json=_canonical_json(_list(payload["generation_requests"], field_name="generation_requests"), field_name="generation_requests"),
        _operation_attempts_json=_canonical_json(_mapping(payload["operation_attempts"], field_name="operation_attempts"), field_name="operation_attempts"),
        _terminal_claims_json=_canonical_json(_list(payload["terminal_claims"], field_name="terminal_claims"), field_name="terminal_claims"),
        _edited_scene_ids_json=_canonical_json(_list(payload["edited_scene_ids"], field_name="edited_scene_ids"), field_name="edited_scene_ids"),
        _dirty_scene_ids_json=_canonical_json(_list(payload["dirty_scene_ids"], field_name="dirty_scene_ids"), field_name="dirty_scene_ids"),
        _pending_operations=pending,
    )
    _validate_nested_lineage(
        parent=state,
        child=source_state,
        relation="场景包到分镜生成",
    )
    VideoSceneGenerationWorkflowService().to_workflow_record(state)
    return state


def _decode_postproduction(payload: Mapping[str, JsonValue]) -> VideoPostProductionWorkflowState:
    _required_keys(payload, _POSTPRODUCTION_FIELDS, state_name="postproduction")
    pending_payload = _nullable_mapping(payload["pending_operation"], field_name="pending_operation")
    finalized = payload["finalized_by_user"]
    if not isinstance(finalized, bool):
        raise ValueError("finalized_by_user 必须是布尔值")
    common = _common_values(payload, stage_type=VideoPostProductionStage)
    generation_state = _decode_scene_generation(_mapping(payload["generation_state"], field_name="generation_state"))
    state = VideoPostProductionWorkflowState(**common,
        _generation_state=generation_state,
        _merge_request_json=_canonical_json(_mapping(payload["merge_request"], field_name="merge_request"), field_name="merge_request"),
        _merged_video_json=_canonical_json(_nullable_mapping(payload["merged_video"], field_name="merged_video"), field_name="merged_video"),
        _merge_error_json=_canonical_json(_nullable_mapping(payload["merge_error"], field_name="merge_error"), field_name="merge_error"),
        _quality_review_json=_canonical_json(_nullable_mapping(payload["quality_review"], field_name="quality_review"), field_name="quality_review"),
        _quality_feedback_json=_canonical_json(_optional_text(payload["quality_feedback"], field_name="quality_feedback"), field_name="quality_feedback"),
        _pending_operation=(_external_job(pending_payload, field_name="pending_operation") if pending_payload is not None else None),
        _terminal_result_hash=_optional_text(payload["terminal_result_hash"], field_name="terminal_result_hash"),
        _terminal_claims_json=_canonical_json(_list(payload["terminal_claims"], field_name="terminal_claims"), field_name="terminal_claims"),
        _operation_attempts_json=_canonical_json(_mapping(payload["operation_attempts"], field_name="operation_attempts"), field_name="operation_attempts"),
        finalized_by_user=finalized,
    )
    _validate_nested_lineage(
        parent=state,
        child=generation_state,
        relation="分镜生成到视频后处理",
    )
    VideoPostProductionWorkflowService().to_workflow_record(state)
    return state


def _decode_delivery(payload: Mapping[str, JsonValue]) -> VideoDeliveryWorkflowState:
    _required_keys(payload, _DELIVERY_FIELDS, state_name="delivery")
    pending_payload = _nullable_mapping(payload["pending_operation"], field_name="pending_operation")
    pending_jianying_operation = _nullable_mapping(payload["pending_jianying_operation"], field_name="pending_jianying_operation")
    pending_request = _nullable_mapping(payload["pending_jianying_request"], field_name="pending_jianying_request")
    common = _common_values(payload, stage_type=VideoPostProductionStage)
    postproduction_state = _decode_postproduction(_mapping(payload["postproduction_state"], field_name="postproduction_state"))
    state = VideoDeliveryWorkflowState(**common,
        _postproduction_state=postproduction_state,
        _jianying_draft_records_json=_canonical_json(_mapping(payload["jianying_draft_records"], field_name="jianying_draft_records"), field_name="jianying_draft_records"),
        _operation_attempts_json=_canonical_json(_mapping(payload["operation_attempts"], field_name="operation_attempts"), field_name="operation_attempts"),
        _pending_operation=(_external_job(pending_payload, field_name="pending_operation") if pending_payload is not None else None),
        _pending_jianying_operation_json=_canonical_json(pending_jianying_operation, field_name="pending_jianying_operation"),
        _final_video_delivery_json=_canonical_json(_nullable_mapping(payload["final_video_delivery"], field_name="final_video_delivery"), field_name="final_video_delivery"),
    )
    _validate_nested_lineage(
        parent=state,
        child=postproduction_state,
        relation="视频后处理到交付",
    )
    if _canonical_json(state.pending_jianying_request, field_name="恢复后的 pending_jianying_request") != _canonical_json(
        pending_request,
        field_name="payload pending_jianying_request",
    ):
        raise ValueError("pending_jianying_request 与完整剪映 Operation 请求不一致")
    VideoDeliveryWorkflowService().to_workflow_record(state)
    return state


_DECODERS: dict[VideoWorkflowStateKind, Callable[[Mapping[str, JsonValue]], VideoWorkflowState]] = {
    VideoWorkflowStateKind.PLANNING: _decode_planning,
    VideoWorkflowStateKind.SCENE_PACKAGE: _decode_scene_package,
    VideoWorkflowStateKind.SCENE_GENERATION: _decode_scene_generation,
    VideoWorkflowStateKind.POSTPRODUCTION: _decode_postproduction,
    VideoWorkflowStateKind.DELIVERY: _decode_delivery,
}


def project_video_workflow_state(state: VideoWorkflowState) -> WorkflowRecord:
    """按具体状态类型调用 M11 权威投影与领域校验。"""

    kind = _kind_for_state(state)
    if kind is VideoWorkflowStateKind.PLANNING:
        return VideoPlanningWorkflowService().to_workflow_record(cast(VideoPlanningWorkflowState, state))
    if kind is VideoWorkflowStateKind.SCENE_PACKAGE:
        return VideoScenePackageWorkflowService().to_workflow_record(cast(VideoScenePackageWorkflowState, state))
    if kind is VideoWorkflowStateKind.SCENE_GENERATION:
        return VideoSceneGenerationWorkflowService().to_workflow_record(cast(VideoSceneGenerationWorkflowState, state))
    if kind is VideoWorkflowStateKind.POSTPRODUCTION:
        return VideoPostProductionWorkflowService().to_workflow_record(cast(VideoPostProductionWorkflowState, state))
    return VideoDeliveryWorkflowService().to_workflow_record(cast(VideoDeliveryWorkflowState, state))


def encode_video_workflow_state(
    *,
    user_id: str,
    state: VideoWorkflowState,
    workflow_version: int,
    last_turn_id: str,
    last_action_key: str,
) -> VideoWorkflowStateEnvelope:
    """校验领域状态后生成深度只读、可稳定序列化的状态信封。"""

    projection = project_video_workflow_state(state)
    kind = _kind_for_state(state)
    payload = cast(dict[str, JsonValue], _json_copy(_ENCODERS[kind](state), field_name=f"{kind.value} payload"))
    candidate = VideoWorkflowStateEnvelope(
        workflow_id=projection.workflow_id,
        conversation_id=projection.conversation_id,
        user_id=_text(user_id, field_name="user_id"),
        state_kind=kind,
        workflow_version=_integer(workflow_version, field_name="workflow_version"),
        context_version=projection.context_version,
        payload=payload,
        payload_sha256="sha256:" + "0" * 64,
        last_turn_id=_text(last_turn_id, field_name="last_turn_id"),
        last_action_key=_text(last_action_key, field_name="last_action_key"),
        created_at=projection.created_at,
        updated_at=projection.updated_at,
    )
    return candidate.model_copy(
        update={
            "payload_sha256": canonical_video_workflow_envelope_sha256(candidate),
        }
    )


def decode_video_workflow_state(envelope: VideoWorkflowStateEnvelope) -> VideoWorkflowState:
    """校验 schema、摘要和跨层身份后严格恢复 M11 领域状态。"""

    if not isinstance(envelope.schema_version, int) or isinstance(envelope.schema_version, bool) or envelope.schema_version != 1:
        raise ValueError("schema_version 仅支持 1")
    _integer(envelope.workflow_version, field_name="workflow_version")
    _integer(envelope.context_version, field_name="context_version")
    _text(envelope.workflow_id, field_name="workflow_id")
    _text(envelope.conversation_id, field_name="conversation_id")
    _text(envelope.user_id, field_name="user_id")
    _text(envelope.last_turn_id, field_name="last_turn_id")
    _text(envelope.last_action_key, field_name="last_action_key")
    if not isinstance(envelope.created_at, datetime) or envelope.created_at.tzinfo is None or envelope.created_at.utcoffset() is None:
        raise ValueError("created_at 必须包含时区")
    if (
        not isinstance(envelope.updated_at, datetime)
        or envelope.updated_at.tzinfo is None
        or envelope.updated_at.utcoffset() is None
        or envelope.updated_at < envelope.created_at
    ):
        raise ValueError("信封更新时间必须包含时区且不能早于创建时间")
    try:
        kind = VideoWorkflowStateKind(envelope.state_kind)
    except ValueError as exc:
        raise ValueError("state_kind 不是受支持的视频状态类型") from exc
    payload = _mapping(envelope.payload, field_name="视频 Workflow payload")
    normalized = VideoWorkflowStateEnvelope.model_validate(envelope)
    if canonical_video_workflow_envelope_sha256(normalized) != normalized.payload_sha256:
        raise ValueError("视频 Workflow 规范信封摘要不一致")
    envelope = normalized
    payload = _mapping(envelope.payload, field_name="视频 Workflow payload")
    state = _DECODERS[kind](payload)
    projection = project_video_workflow_state(state)
    if projection.workflow_id != envelope.workflow_id:
        raise ValueError("payload workflow_id 与信封不一致")
    if projection.conversation_id != envelope.conversation_id:
        raise ValueError("payload conversation_id 与信封不一致")
    if projection.context_version != envelope.context_version:
        raise ValueError("payload context_version 与信封不一致")
    if projection.created_at != envelope.created_at or projection.updated_at != envelope.updated_at:
        raise ValueError("payload 时间与信封不一致")
    return state


__all__ = [
    "VideoWorkflowState",
    "VideoWorkflowStateEnvelope",
    "VideoWorkflowStateKind",
    "canonical_payload_sha256",
    "canonical_video_workflow_envelope_sha256",
    "decode_video_workflow_state",
    "encode_video_workflow_state",
    "project_video_workflow_state",
]
