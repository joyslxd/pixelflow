"""视频分镜生成、部分失败恢复和单镜重生成 Workflow Service。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from pixelflow.agent_runtime.contracts import (
    ExternalJobRef,
    ExternalJobStatus,
    OperationRequest,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.ports import OperationConflictError, OperationPort
from pixelflow.generate.scene_packages import build_authoritative_scene_prompt
from pixelflow.skills.base import is_quota_insufficient

from .scene_packages import (
    VideoScenePackageAuthoritySnapshot,
    VideoScenePackageStage,
    VideoScenePackageWorkflowState,
    _validate_scene_package_state_authority,
)

_MAX_PROVIDER_ATTEMPTS = 3
_EDITABLE_SCENE_FIELDS = {
    "storyline",
    "shot_description",
    "narration",
    "reference_asset_ids",
}
_VIDEO_MODE_ALIASES = {
    "text_to_video": ("文生视频", "text_to_video", "t2v"),
    "image_to_video": ("图生视频", "首帧", "image_to_video", "i2v"),
    "two_image_to_video": ("首尾帧", "two_image_to_video", "first_last_frame", "flf2v"),
    "reference_mode_video": ("全能参考", "reference_mode_video", "omni_reference", "r2v"),
    "edit_video": ("编辑视频", "edit_video"),
    "extend_video": ("延伸视频", "extend_video"),
}
_TERMINAL_OPERATION_STATUSES = {
    ExternalJobStatus.SUCCEEDED,
    ExternalJobStatus.FAILED,
    ExternalJobStatus.TIMEOUT,
    ExternalJobStatus.EXPIRED,
}
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", flags=re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?:authorization|token|api[_ -]?key|secret|password|credential|密钥|凭据|鉴权)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
    flags=re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\bbearer\s+[^\s,;]+", flags=re.IGNORECASE)


class VideoSceneGenerationStage(StrEnum):
    """M11.3 冻结的分镜视频生成和人工复核阶段。"""

    GENERATE_SCENE_VIDEOS = "generate_scene_videos"
    SCENE_VIDEO_REVIEW = "scene_video_review"


@dataclass(frozen=True, slots=True)
class VideoSceneOperationTerminalClaim:
    """视频专用原子终态 claim；把 Operation 与唯一结果摘要绑定。"""

    job: ExternalJobRef
    result_hash: str


class VideoSceneAtomicOperationPort(OperationPort, Protocol):
    """M06 实现前的 fail-closed 扩展边界，要求原子写入唯一视频终态。"""

    async def finalize_scene_operation(
        self,
        *,
        expected: ExternalJobRef,
        target_status: ExternalJobStatus,
        provider_job_id: str | None,
        result_hash: str,
    ) -> VideoSceneOperationTerminalClaim: ...

    async def get_scene_operation_terminal_claim(
        self,
        *,
        job_id: str,
    ) -> VideoSceneOperationTerminalClaim | None: ...


@dataclass(frozen=True, slots=True)
class VideoSceneGenerationWorkflowState:
    """分镜生成状态；成功视频、失败明细和 Operation 均可持久化恢复。"""

    workflow_id: str
    conversation_id: str
    current_stage: VideoSceneGenerationStage
    status: WorkflowStatus
    stage_version: int
    context_version: int
    created_at: datetime
    updated_at: datetime
    _source_state: VideoScenePackageWorkflowState = field(repr=False)
    _scene_packages_json: str = field(repr=False)
    _scene_videos_json: str = field(repr=False)
    _failed_scenes_json: str = field(repr=False)
    _generation_requests_json: str = field(repr=False)
    _operation_attempts_json: str = field(repr=False)
    _terminal_claims_json: str = field(repr=False)
    _edited_scene_ids_json: str = field(repr=False)
    _dirty_scene_ids_json: str = field(repr=False)
    _pending_operations: tuple[ExternalJobRef, ...] = field(repr=False)

    @property
    def source_scene_package(self) -> VideoScenePackageAuthoritySnapshot:
        return self._source_state.scene_package

    @property
    def source_scene_package_artifact_ref(self) -> str:
        return self._source_state.scene_package_artifact_ref

    @property
    def scene_packages(self) -> list[dict[str, Any]]:
        return json.loads(self._scene_packages_json)

    @property
    def scene_videos(self) -> list[dict[str, Any]]:
        return json.loads(self._scene_videos_json)

    @property
    def failed_scenes(self) -> list[dict[str, Any]]:
        return json.loads(self._failed_scenes_json)

    @property
    def generation_requests(self) -> list[dict[str, Any]]:
        return json.loads(self._generation_requests_json)

    @property
    def pending_operations(self) -> list[ExternalJobRef]:
        return [item.model_copy(deep=True) for item in self._pending_operations]

    @property
    def operation_attempts(self) -> dict[str, int]:
        return json.loads(self._operation_attempts_json)

    @property
    def terminal_claims(self) -> list[dict[str, Any]]:
        return json.loads(self._terminal_claims_json)

    @property
    def edited_scene_ids(self) -> list[str]:
        return json.loads(self._edited_scene_ids_json)

    @property
    def dirty_scene_ids(self) -> list[str]:
        return json.loads(self._dirty_scene_ids_json)

    @property
    def quota_insufficient(self) -> bool:
        return any(item.get("quota_insufficient") is True for item in self.failed_scenes)

    @property
    def scene_videos_artifact_ref(self) -> str:
        """生成包含场景包来源和当前结果校验和的稳定 Artifact 引用。"""

        workflow_key = quote(self.workflow_id, safe="-_.")
        result_payload = {
            "source_scene_package_checksum": self.source_scene_package.checksum,
            "scene_packages": self.scene_packages,
            "scene_videos": self.scene_videos,
            "failed_scenes": self.failed_scenes,
        }
        checksum = hashlib.sha256(_canonical_json(result_payload, field_name="分镜视频结果").encode("utf-8")).hexdigest()
        return f"artifact:video-scene-videos:{workflow_key}:{checksum[:16]}"


class VideoSceneGenerationWorkflowService:
    """类比 Java Application Service，通过 OperationPort 编排可恢复分镜任务。"""

    def __init__(self, operation_port: OperationPort | None = None) -> None:
        self._operation_port = operation_port

    async def start_from_reviewed_scene_package(
        self,
        state: VideoScenePackageWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """从人工确认的场景包领取每镜 Operation；重复调用返回同一批任务。"""

        if state.current_stage is not VideoScenePackageStage.SCENE_PACKAGE_REVIEW or state.status is not WorkflowStatus.AWAITING_USER:
            raise ValueError("只有等待人工确认的最新场景包才能生成分镜视频")
        _validate_scene_package_state_authority(state)
        timestamp = _timestamp(now)
        if timestamp < state.updated_at:
            raise ValueError("Workflow 更新时间不能早于当前状态")
        scenes = _validated_source_scenes(state)
        selected_ids = _selected_scene_ids(scenes, None)

        stage_version = state.stage_version + 1
        requests = _generation_requests(scenes, selected_ids, state.scene_package.creation_contract)
        attempts = {scene_id: 1 for scene_id in selected_ids}
        pending = await self._claim_operations(
            workflow_id=state.workflow_id,
            stage_version=stage_version,
            requests=requests,
            operation_attempts=attempts,
            operation_port=operation_port,
        )
        result = VideoSceneGenerationWorkflowState(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            current_stage=VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS,
            status=WorkflowStatus.RUNNING,
            stage_version=stage_version,
            context_version=state.context_version + 1,
            created_at=state.created_at,
            updated_at=timestamp,
            _source_state=state,
            _scene_packages_json=_canonical_json(scenes, field_name="分镜执行快照"),
            _scene_videos_json="[]",
            _failed_scenes_json="[]",
            _generation_requests_json=_canonical_json(requests, field_name="分镜生成请求"),
            _operation_attempts_json=_canonical_json(attempts, field_name="分镜 Operation 次数"),
            _terminal_claims_json="[]",
            _edited_scene_ids_json="[]",
            _dirty_scene_ids_json="[]",
            _pending_operations=tuple(pending),
        )
        _validate_generation_state(result)
        return result

    async def resume(
        self,
        state: VideoSceneGenerationWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """恢复时只查询原 Operation；任务丢失时失败关闭，绝不重新 claim。"""

        _validate_generation_state(state)
        port = self._port(operation_port)
        refreshed: list[ExternalJobRef] = []
        for pending in state.pending_operations:
            existing = await port.get(pending.job_id)
            if existing is None:
                raise ValueError(f"分镜 Operation {pending.job_id} 不存在或已过期，不得自动重新启动")
            _validate_operation_identity(state, pending, existing)
            refreshed.append(existing)
        timestamp = _timestamp(now) if now is not None else state.updated_at
        if timestamp < state.updated_at:
            raise ValueError("Workflow 更新时间不能早于当前状态")
        return replace(state, updated_at=timestamp, _pending_operations=tuple(refreshed))

    async def record_scene_success(
        self,
        state: VideoSceneGenerationWorkflowState,
        *,
        scene_id: str,
        video_url: str,
        provider_job_id: str | None = None,
        raw: Mapping[str, Any] | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """保存单镜成功结果，并在批次全部终结后进入人工复核。"""

        _validate_result_state(state)
        scene = _scene_by_id(state.scene_packages, scene_id)
        pending = _pending_operation(state, scene_id)
        timestamp = _next_timestamp(state, now)
        normalized_url = _required_https_url(video_url, "分镜视频 URL")
        normalized_provider_job_id = _optional_text(provider_job_id)
        port = self._port(operation_port)
        current_operation = await port.get(pending.job_id)
        if current_operation is None:
            raise ValueError(f"分镜 Operation {pending.job_id} 不存在或已过期")
        _validate_operation_identity(state, pending, current_operation)
        resolved_provider_job_id = normalized_provider_job_id or current_operation.provider_job_id
        if resolved_provider_job_id is None:
            raise OperationConflictError("成功 Operation 必须绑定供应商任务 ID")
        request = _request_by_scene_id(state.generation_requests, scene_id)
        mode = request["generation_mode"]
        endpoint = _endpoint_for_mode(mode)
        raw_payload = _safe_json_object(raw or {}, field_name="分镜成功原始结果")
        result_hash = _terminal_result_hash(
            "succeeded",
            {
                "scene_id": scene_id,
                "video_url": normalized_url,
                "mode": mode,
                "endpoint": endpoint,
                "provider_job_id": resolved_provider_job_id,
                "raw": raw_payload,
            },
        )
        terminal_claim = await _finalize_operation(
            port,
            state,
            pending,
            target_status=ExternalJobStatus.SUCCEEDED,
            provider_job_id=resolved_provider_job_id,
            result_hash=result_hash,
        )
        saved = terminal_claim.job
        video = {
            "scene_id": scene_id,
            "scene_index": scene["scene_index"],
            "duration_ms": scene["duration_ms"],
            "mode": mode,
            "endpoint": endpoint,
            "video_url": normalized_url,
            "task_id": saved.provider_job_id,
            "raw": raw_payload,
        }
        videos = [item for item in state.scene_videos if item["scene_id"] != scene_id]
        videos.append(video)
        videos = _validated_scene_videos(videos, state.scene_packages)
        failures = [item for item in state.failed_scenes if item["scene_id"] != scene_id]
        dirty = [item for item in state.dirty_scene_ids if item != scene_id]
        return _after_scene_terminal(
            state,
            scene_videos=videos,
            failed_scenes=failures,
            dirty_scene_ids=dirty,
            completed_scene_id=scene_id,
            terminal_claim=terminal_claim,
            timestamp=timestamp,
        )

    async def record_scene_failure(
        self,
        state: VideoSceneGenerationWorkflowState,
        *,
        scene_id: str,
        error: str,
        attempts: int,
        retryable: bool = True,
        quota_insufficient: bool = False,
        raw: Mapping[str, Any] | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """保存最终失败；可恢复错误必须在三次内部尝试耗尽后才对外暴露。"""

        _validate_result_state(state)
        scene = _scene_by_id(state.scene_packages, scene_id)
        pending = _pending_operation(state, scene_id)
        original_error = _required_text(error, "分镜失败原因")
        normalized_error = _sanitize_sensitive_text(original_error)
        normalized_attempts = _required_int(attempts, "分镜失败 attempts")
        if normalized_attempts < 1 or normalized_attempts > _MAX_PROVIDER_ATTEMPTS:
            raise ValueError("分镜供应商内部尝试次数必须为 1-3")
        raw_payload = _safe_json_object(raw or {}, field_name="分镜失败原始结果")
        quota_insufficient = bool(
            quota_insufficient
            or is_quota_insufficient(raw or {})
            or is_quota_insufficient(original_error)
        )
        if quota_insufficient:
            retryable = False
            if normalized_attempts != 1:
                raise ValueError("额度不足必须立即暂停，同一分镜不得重复调用")
        if not quota_insufficient and _is_non_retryable_failure(original_error, raw or {}):
            if retryable or normalized_attempts != 1:
                raise ValueError("HTTP 4xx、价格配置或能力不匹配属于不可重试失败，只能调用一次")
            retryable = False
        if retryable and not quota_insufficient and normalized_attempts != _MAX_PROVIDER_ATTEMPTS:
            raise ValueError("可恢复分镜异常必须完成最多 3 次尝试后才写入 failed_scenes")
        if not retryable and normalized_attempts != 1:
            raise ValueError("不可重试分镜失败只能调用一次")
        timestamp = _next_timestamp(state, now)
        port = self._port(operation_port)
        failure = {
            "scene_id": scene_id,
            "scene_index": scene["scene_index"],
            "error": normalized_error,
            "attempts": normalized_attempts,
            "retryable": bool(retryable),
            "quota_insufficient": bool(quota_insufficient),
            "not_started_due_to_quota": False,
            "raw": raw_payload,
        }
        result_hash = _terminal_result_hash("failed", failure)
        terminal_claim = await _finalize_operation(
            port,
            state,
            pending,
            target_status=ExternalJobStatus.FAILED,
            result_hash=result_hash,
        )
        failures = [item for item in state.failed_scenes if item["scene_id"] != scene_id]
        failures.append(failure)
        failures.sort(key=lambda item: item["scene_index"])
        if quota_insufficient:
            return await _after_quota_pause(
                state,
                current_failure=failure,
                existing_failures=failures,
                completed_scene_id=scene_id,
                current_terminal_claim=terminal_claim,
                operation_port=port,
                timestamp=timestamp,
            )
        return _after_scene_terminal(
            state,
            scene_videos=state.scene_videos,
            failed_scenes=failures,
            dirty_scene_ids=state.dirty_scene_ids,
            completed_scene_id=scene_id,
            terminal_claim=terminal_claim,
            timestamp=timestamp,
        )

    async def retry_failed_scenes(
        self,
        state: VideoSceneGenerationWorkflowState,
        *,
        scene_ids: Sequence[str] | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """只领取失败或额度暂停分镜的新 Operation，已成功视频原样复用。"""

        _validate_review_state(state)
        failures = {item["scene_id"]: item for item in state.failed_scenes}
        failed_ids = list(failures)
        requested = _selected_subset(failed_ids, scene_ids, field_name="失败分镜")
        blocked = [
            scene_id
            for scene_id in requested
            if failures[scene_id].get("retryable") is not True
            and failures[scene_id].get("quota_insufficient") is not True
        ]
        if blocked:
            raise ValueError(f"不可重试失败必须先修改输入或分镜，不可直接重试：{blocked}")
        targets = [
            scene_id
            for scene_id in requested
            if failures[scene_id].get("retryable") is True
            or failures[scene_id].get("quota_insufficient") is True
        ]
        if not targets:
            raise ValueError("没有可重试的失败分镜")
        return await self._restart_scenes(
            state,
            targets,
            operation_port=operation_port,
            now=now,
        )

    def modify_scene(
        self,
        state: VideoSceneGenerationWorkflowState,
        *,
        scene_id: str,
        patch: Mapping[str, Any],
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """只修改一个明确分镜的可编辑字段，并使该镜旧视频失效。"""

        _validate_review_state(state)
        if not isinstance(patch, Mapping) or not patch:
            raise ValueError("单镜修改 patch 不能为空")
        unsupported = set(patch).difference(_EDITABLE_SCENE_FIELDS)
        if unsupported:
            raise ValueError(f"单镜只允许修改故事线、镜头描述、旁白和参考资产：{sorted(unsupported)}")
        scenes = state.scene_packages
        position = next((index for index, item in enumerate(scenes) if item["scene_id"] == scene_id), None)
        if position is None:
            raise ValueError(f"分镜 {scene_id} 不存在")
        scenes[position] = _apply_scene_patch(
            scenes[position],
            patch,
            global_assets=state.source_scene_package.global_assets,
            contract=state.source_scene_package.creation_contract,
        )
        timestamp = _next_timestamp(state, now)
        edited = list(state.edited_scene_ids)
        if scene_id not in edited:
            edited.append(scene_id)
        dirty = list(state.dirty_scene_ids)
        if scene_id not in dirty:
            dirty.append(scene_id)
        result = replace(
            state,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=timestamp,
            _scene_packages_json=_canonical_json(scenes, field_name="单镜修改执行快照"),
            _scene_videos_json=_canonical_json(
                [item for item in state.scene_videos if item["scene_id"] != scene_id],
                field_name="单镜修改后复用视频",
            ),
            _failed_scenes_json=_canonical_json(
                [item for item in state.failed_scenes if item["scene_id"] != scene_id],
                field_name="单镜修改后失败结果",
            ),
            _terminal_claims_json=_canonical_json(
                [item for item in state.terminal_claims if item["scene_id"] != scene_id],
                field_name="单镜修改后终态 claim",
            ),
            _generation_requests_json="[]",
            _edited_scene_ids_json=_canonical_json(edited, field_name="已修改分镜"),
            _dirty_scene_ids_json=_canonical_json(dirty, field_name="待重生成分镜"),
        )
        _validate_generation_state(result)
        return result

    async def regenerate_modified_scenes(
        self,
        state: VideoSceneGenerationWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """只重生成 dirty 分镜，未修改镜头继续复用原视频。"""

        _validate_review_state(state)
        if not state.dirty_scene_ids:
            raise ValueError("没有需要重生成的已修改分镜")
        return await self._restart_scenes(
            state,
            state.dirty_scene_ids,
            operation_port=operation_port,
            now=now,
        )

    def to_workflow_record(self, state: VideoSceneGenerationWorkflowState) -> WorkflowRecord:
        """投影 Runtime DTO；多镜并行时只暴露最早未完成 Operation 作为恢复入口。"""

        _validate_generation_state(state)
        pending = state.pending_operations
        artifact_refs = [state.source_scene_package_artifact_ref]
        if state.scene_videos or state.failed_scenes:
            artifact_refs.append(state.scene_videos_artifact_ref)
        return WorkflowRecord(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            kind=WorkflowKind.VIDEO,
            status=state.status,
            current_stage=state.current_stage.value,
            stage_version=state.stage_version,
            creation_contract_snapshot=state.source_scene_package.creation_contract,
            pending_external_job=pending[0] if pending else None,
            latest_artifact_refs=artifact_refs,
            context_version=state.context_version,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    async def _restart_scenes(
        self,
        state: VideoSceneGenerationWorkflowState,
        scene_ids: Sequence[str],
        *,
        operation_port: OperationPort | None,
        now: datetime | None,
    ) -> VideoSceneGenerationWorkflowState:
        timestamp = _next_timestamp(state, now)
        stage_version = state.stage_version + 1
        requests = _generation_requests(
            state.scene_packages,
            list(scene_ids),
            state.source_scene_package.creation_contract,
        )
        attempts = state.operation_attempts
        for scene_id in scene_ids:
            attempts[scene_id] = attempts.get(scene_id, 0) + 1
        pending = await self._claim_operations(
            workflow_id=state.workflow_id,
            stage_version=stage_version,
            requests=requests,
            operation_attempts=attempts,
            operation_port=operation_port,
        )
        target_ids = set(scene_ids)
        terminal_claims = [item for item in state.terminal_claims if item["scene_id"] not in target_ids]
        result = replace(
            state,
            current_stage=VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS,
            status=WorkflowStatus.RUNNING,
            stage_version=stage_version,
            context_version=state.context_version + 1,
            updated_at=timestamp,
            _generation_requests_json=_canonical_json(requests, field_name="重试分镜生成请求"),
            _operation_attempts_json=_canonical_json(attempts, field_name="重试 Operation 次数"),
            _terminal_claims_json=_canonical_json(terminal_claims, field_name="重试后保留终态 claim"),
            _pending_operations=tuple(pending),
        )
        _validate_generation_state(result)
        return result

    async def _claim_operations(
        self,
        *,
        workflow_id: str,
        stage_version: int,
        requests: Sequence[Mapping[str, Any]],
        operation_attempts: Mapping[str, int],
        operation_port: OperationPort | None,
    ) -> list[ExternalJobRef]:
        port = self._port(operation_port)
        result: list[ExternalJobRef] = []
        for request in requests:
            scene_id = str(request["scene_id"])
            attempt = int(operation_attempts[scene_id])
            request_json = _canonical_json(request, field_name=f"分镜 {scene_id} Operation 请求")
            request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
            operation = OperationRequest(
                workflow_id=workflow_id,
                stage=f"generate_scene_video:{scene_id}",
                stage_version=stage_version,
                attempt=attempt,
                request_hash=request_hash,
                idempotency_key=_operation_idempotency_key(
                    workflow_id=workflow_id,
                    stage_version=stage_version,
                    scene_id=scene_id,
                    attempt=attempt,
                    request_hash=request_hash,
                ),
            )
            result.append(await port.claim(operation))
        return result

    def _port(self, explicit: OperationPort | None) -> OperationPort:
        port = explicit or self._operation_port
        if port is None:
            raise ValueError("分镜生成必须提供 OperationPort")
        return port

    start = start_from_reviewed_scene_package
    resume_generation = resume
    retry_failed = retry_failed_scenes
    edit_scene = modify_scene
    regenerate_scenes = regenerate_modified_scenes


def _validated_source_scenes(state: VideoScenePackageWorkflowState) -> list[dict[str, Any]]:
    scenes = state.scene_package.scene_packages
    if not scenes:
        raise ValueError("场景包至少需要一个分镜")
    if state.scene_package.asset_images_generated is not True:
        raise ValueError("分镜生成前必须完成全局资产图片")
    _validated_total_duration(scenes, state.scene_package.target_duration_ms)
    return json.loads(_canonical_json(scenes, field_name="来源场景包分镜"))


def _selected_scene_ids(scenes: Sequence[Mapping[str, Any]], scene_ids: Sequence[str] | None) -> list[str]:
    available = [str(item["scene_id"]) for item in scenes]
    return available if scene_ids is None else _selected_subset(available, scene_ids, field_name="待生成分镜")


def _selected_subset(available: Sequence[str], selected: Sequence[str] | None, *, field_name: str) -> list[str]:
    values = list(available) if selected is None else [_required_text(item, field_name) for item in selected]
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name}不得重复")
    unknown = set(values).difference(available)
    if unknown:
        raise ValueError(f"{field_name}不存在：{sorted(unknown)}")
    return [item for item in available if item in values]


def _generation_requests(
    scenes: Sequence[Mapping[str, Any]],
    scene_ids: Sequence[str],
    contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    video_model = _required_text(contract.get("video_model"), "创作合同 video_model")
    ratio = _required_text(contract.get("video_ratio"), "创作合同 video_ratio")
    size = _required_text(contract.get("video_size"), "创作合同 video_size")
    sound = _required_text(contract.get("video_sound"), "创作合同 video_sound")
    supported = _supported_generation_modes(contract)
    supported_durations = _supported_scene_durations(contract)
    result: list[dict[str, Any]] = []
    for scene in scenes:
        if scene["scene_id"] not in scene_ids:
            continue
        duration_ms = _required_int(scene.get("duration_ms"), "分镜 duration_ms")
        if duration_ms % 1000 != 0 or duration_ms < 4_000 or duration_ms > 15_000:
            raise ValueError("分镜时长必须是 4-15 秒的整数秒")
        duration = duration_ms // 1000
        if supported_durations is not None and duration not in supported_durations:
            raise ValueError(f"分镜时长 {duration} 秒不在模型实时支持时长 {sorted(supported_durations)} 内")
        image_urls = _scene_reference_image_urls(scene)
        mode = _scene_generation_mode(scene, image_urls)
        if supported is not None and mode not in supported:
            if mode == "reference_mode_video" and "text_to_video" in supported:
                mode = "text_to_video"
            else:
                raise ValueError(f"当前视频模型能力不支持分镜生成模式 {mode}")
        prompt = _required_text(scene.get("prompt"), "分镜 prompt")
        if len(prompt) > 2_500:
            raise ValueError(f"分镜提示词最多 2500 个字符，当前为 {len(prompt)} 个字符")
        result.append(
            {
                "scene_id": scene["scene_id"],
                "scene_index": scene["scene_index"],
                "duration": duration,
                "duration_ms": duration_ms,
                "prompt": prompt,
                "storyline": _required_text(scene.get("storyline"), "分镜 storyline"),
                "shot_description": _safe_json_object(scene.get("shot_description"), field_name="分镜镜头描述"),
                "narration": str(scene.get("narration") or ""),
                "transition": str(scene.get("transition") or ""),
                "generation_mode": mode,
                "image_urls": image_urls,
                "video_urls": _validated_https_urls(scene.get("video_urls"), "分镜参考视频"),
                "audio_urls": _validated_https_urls(scene.get("audio_urls"), "分镜参考音频"),
                "model": video_model,
                "ratio": ratio,
                "size": size,
                "sound": sound,
            }
        )
    return result


def _supported_generation_modes(contract: Mapping[str, Any]) -> set[str] | None:
    capabilities = contract.get("video_model_capabilities")
    if not isinstance(capabilities, Mapping):
        return None
    values = capabilities.get("generation_types")
    if not isinstance(values, list) or not values:
        return None
    result: set[str] = set()
    for item in values:
        normalized = _normalize_capability(_required_text(item, "generation_types"))
        for mode, aliases in _VIDEO_MODE_ALIASES.items():
            if normalized in {_normalize_capability(alias) for alias in aliases}:
                result.add(mode)
                break
    return result


def _supported_scene_durations(contract: Mapping[str, Any]) -> set[int] | None:
    capabilities = contract.get("video_model_capabilities")
    if not isinstance(capabilities, Mapping):
        return None
    values = capabilities.get("durations_sec")
    if values in (None, []):
        return None
    if not isinstance(values, list):
        raise ValueError("模型实时单分镜时长必须是数组")
    result = {_required_int(item, "模型实时单分镜时长") for item in values}
    if any(item < 4 or item > 15 for item in result):
        raise ValueError("模型实时单分镜时长必须位于 4-15 秒")
    return result


def _scene_generation_mode(scene: Mapping[str, Any], image_urls: Sequence[str]) -> str:
    explicit = _optional_text(scene.get("generation_mode"))
    if explicit:
        return explicit
    text = "\n".join(
        str(item or "")
        for item in (
            scene.get("prompt"),
            scene.get("storyline"),
            scene.get("narration"),
            scene.get("shot_description"),
        )
    ).lower()
    if scene.get("video_urls") and any(item in text for item in ("延伸", "续写", "extend")):
        return "extend_video"
    if scene.get("video_urls") and any(item in text for item in ("编辑", "修改", "调整", "edit")):
        return "edit_video"
    if image_urls or scene.get("video_urls") or scene.get("audio_urls"):
        return "reference_mode_video"
    return "text_to_video"


def _scene_reference_image_urls(scene: Mapping[str, Any]) -> list[str]:
    values = list(scene.get("image_urls") or [])
    shot = scene.get("shot_description")
    mentions = shot.get("mentions") if isinstance(shot, Mapping) else None
    if isinstance(mentions, list):
        values.extend(item.get("image_url") for item in mentions if isinstance(item, Mapping))
    urls = _validated_https_urls(values, "分镜参考图片")
    if len(urls) > 9:
        raise ValueError(f"单分镜最多允许 9 张参考图，当前为 {len(urls)} 张")
    return urls


def _validated_https_urls(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name}必须是数组")
    result: list[str] = []
    for position, item in enumerate(value, start=1):
        url = _required_https_url(item, f"{field_name}第 {position} 项")
        if url != item:
            raise ValueError(f"{field_name}必须使用无首尾空白的规范 URL")
        if url not in result:
            result.append(url)
    return result


def _validated_scene_videos(value: Sequence[Mapping[str, Any]], scenes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("分镜视频必须是数组")
    scene_lookup = {item["scene_id"]: item for item in scenes}
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("分镜视频只能包含对象")
        scene_id = _required_text(item.get("scene_id"), "分镜视频 scene_id")
        scene = scene_lookup.get(scene_id)
        if scene is None:
            raise ValueError(f"分镜视频引用不存在的分镜 {scene_id}")
        normalized = {
            "scene_id": scene_id,
            "scene_index": scene["scene_index"],
            "duration_ms": scene["duration_ms"],
            "mode": _required_text(item.get("mode") or "reference_mode_video", "分镜视频 mode"),
            "endpoint": _required_text(item.get("endpoint") or "/api/video/reference-mode-video", "分镜视频 endpoint"),
            "video_url": _required_https_url(item.get("video_url"), "分镜视频 URL"),
            "task_id": _required_text(item.get("task_id"), "分镜视频供应商 task_id"),
            "raw": _safe_json_object(item.get("raw") or {}, field_name="分镜视频 raw"),
        }
        if item.get("scene_index", scene["scene_index"]) != scene["scene_index"]:
            raise ValueError("分镜视频 scene_index 必须与场景包一致")
        if item.get("duration_ms", scene["duration_ms"]) != scene["duration_ms"]:
            raise ValueError("分镜视频 duration_ms 必须与场景包一致")
        result.append(normalized)
    if len({item["scene_id"] for item in result}) != len(result):
        raise ValueError("同一分镜只能保留一个当前视频")
    return sorted(result, key=lambda item: item["scene_index"])


def _apply_scene_patch(
    scene: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    global_assets: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    updated = json.loads(_canonical_json(scene, field_name="待修改分镜"))
    if "storyline" in patch:
        updated["storyline"] = _required_text(patch["storyline"], "单镜 storyline")
    if "narration" in patch:
        updated["narration"] = _string_value(patch["narration"], "单镜 narration")
    reference_ids = list(updated.get("reference_asset_ids") or [])
    if "reference_asset_ids" in patch:
        value = patch["reference_asset_ids"]
        if not isinstance(value, list):
            raise ValueError("单镜 reference_asset_ids 必须是数组")
        reference_ids = [_required_text(item, "单镜 reference_asset_ids") for item in value]
    asset_lookup = _generated_asset_lookup(global_assets)
    if len(reference_ids) != len(set(reference_ids)) or len(reference_ids) > 9:
        raise ValueError("单镜参考资产必须唯一且最多 9 个")
    unknown = set(reference_ids).difference(asset_lookup)
    if unknown:
        raise ValueError(f"单镜引用了不存在的全局资产：{sorted(unknown)}")

    shot_patch = patch.get("shot_description")
    if shot_patch is not None:
        if isinstance(shot_patch, str):
            shot = dict(updated["shot_description"])
            shot["text"] = _required_text(shot_patch, "单镜 shot_description.text")
        elif isinstance(shot_patch, Mapping):
            if set(shot_patch).difference({"text", "mentions"}):
                raise ValueError("单镜 shot_description 只允许 text 和 mentions")
            shot = dict(updated["shot_description"])
            if "text" in shot_patch:
                shot["text"] = _required_text(shot_patch["text"], "单镜 shot_description.text")
            if "mentions" in shot_patch:
                mentions_value = shot_patch["mentions"]
                if not isinstance(mentions_value, list):
                    raise ValueError("单镜 shot_description.mentions 必须是数组")
                inferred_ids = [
                    _required_text(item.get("asset_id"), "单镜 mention.asset_id")
                    for item in mentions_value
                    if isinstance(item, Mapping)
                ]
                if len(inferred_ids) != len(mentions_value):
                    raise ValueError("单镜 mentions 只能包含对象")
                reference_ids = inferred_ids
        else:
            raise ValueError("单镜 shot_description 必须是字符串或对象")
        updated["shot_description"] = shot

    if len(reference_ids) != len(set(reference_ids)) or len(reference_ids) > 9:
        raise ValueError("单镜参考资产必须唯一且最多 9 个")
    unknown = set(reference_ids).difference(asset_lookup)
    if unknown:
        raise ValueError(f"单镜引用了不存在的全局资产：{sorted(unknown)}")
    updated["reference_asset_ids"] = reference_ids
    updated["shot_description"]["mentions"] = [
        {
            "asset_id": asset_id,
            "type": asset_lookup[asset_id]["type"],
            "name": asset_lookup[asset_id]["name"],
            "image_url": asset_lookup[asset_id]["image_url"],
        }
        for asset_id in reference_ids
    ]
    _validate_edited_shot_text(
        updated["shot_description"]["text"],
        duration_sec=updated["duration_ms"] // 1000,
        reference_asset_ids=reference_ids,
    )
    updated["prompt"] = build_authoritative_scene_prompt(
        updated["storyline"],
        updated["shot_description"],
        updated["narration"],
        global_assets["visual_style"],
        video_model=_required_text(contract.get("video_model"), "创作合同 video_model"),
    )
    return updated


def _generated_asset_lookup(global_assets: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for collection, asset_type, image_field in (
        ("characters", "character", "three_view_images"),
        ("scenes", "scene", "images"),
        ("props", "prop", "images"),
    ):
        items = global_assets.get(collection)
        if not isinstance(items, list):
            raise ValueError(f"全局资产 {collection} 必须是数组")
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError(f"全局资产 {collection} 只能包含对象")
            asset_id = _required_text(item.get("asset_id"), "全局资产 asset_id")
            urls = item.get(image_field)
            if not isinstance(urls, list) or len(urls) != 1:
                raise ValueError("参与分镜生成的全局资产必须恰好有一张图片")
            lookup[asset_id] = {
                "type": asset_type,
                "name": _required_text(item.get("name"), "全局资产 name"),
                "image_url": _required_https_url(urls[0], "全局资产图片 URL"),
            }
    return lookup


def _after_scene_terminal(
    state: VideoSceneGenerationWorkflowState,
    *,
    scene_videos: Sequence[Mapping[str, Any]],
    failed_scenes: Sequence[Mapping[str, Any]],
    dirty_scene_ids: Sequence[str],
    completed_scene_id: str,
    terminal_claim: VideoSceneOperationTerminalClaim,
    timestamp: datetime,
) -> VideoSceneGenerationWorkflowState:
    pending = [item for item in state.pending_operations if _scene_id_from_operation(item) != completed_scene_id]
    requests = [item for item in state.generation_requests if item["scene_id"] != completed_scene_id]
    if pending:
        stage = VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS
        status = (
            WorkflowStatus.PAUSED_QUOTA
            if state.status is WorkflowStatus.PAUSED_QUOTA
            or any(item.get("quota_insufficient") is True for item in failed_scenes)
            else WorkflowStatus.RUNNING
        )
    else:
        stage = VideoSceneGenerationStage.SCENE_VIDEO_REVIEW
        status = (
            WorkflowStatus.PAUSED_QUOTA
            if any(item.get("quota_insufficient") is True for item in failed_scenes)
            else WorkflowStatus.AWAITING_USER
        )
    terminal_claims = [
        item for item in state.terminal_claims if item["scene_id"] != completed_scene_id
    ]
    terminal_claims.append(
        _terminal_claim_payload(
            completed_scene_id,
            stage_version=state.stage_version,
            claim=terminal_claim,
        )
    )
    terminal_claims.sort(key=lambda item: _scene_by_id(state.scene_packages, item["scene_id"])["scene_index"])
    result = replace(
        state,
        current_stage=stage,
        status=status,
        context_version=state.context_version + 1,
        updated_at=timestamp,
        _scene_videos_json=_canonical_json(scene_videos, field_name="分镜视频结果"),
        _failed_scenes_json=_canonical_json(failed_scenes, field_name="失败分镜结果"),
        _generation_requests_json=_canonical_json(requests, field_name="剩余分镜请求"),
        _terminal_claims_json=_canonical_json(terminal_claims, field_name="分镜终态 claim"),
        _dirty_scene_ids_json=_canonical_json(dirty_scene_ids, field_name="剩余待重生成分镜"),
        _pending_operations=tuple(pending),
    )
    _validate_generation_state(result)
    return result


async def _after_quota_pause(
    state: VideoSceneGenerationWorkflowState,
    *,
    current_failure: Mapping[str, Any],
    existing_failures: Sequence[Mapping[str, Any]],
    completed_scene_id: str,
    current_terminal_claim: VideoSceneOperationTerminalClaim,
    operation_port: OperationPort,
    timestamp: datetime,
) -> VideoSceneGenerationWorkflowState:
    """首个额度失败立即关闭本批其余 Operation，并把未启动分镜标为可恢复。"""

    failures_by_id = {item["scene_id"]: dict(item) for item in existing_failures}
    failures_by_id[completed_scene_id] = dict(current_failure)
    terminal_claims_by_id = {item["scene_id"]: dict(item) for item in state.terminal_claims}
    terminal_claims_by_id[completed_scene_id] = _terminal_claim_payload(
        completed_scene_id,
        stage_version=state.stage_version,
        claim=current_terminal_claim,
    )
    remaining = [
        item
        for item in state.pending_operations
        if _scene_id_from_operation(item) != completed_scene_id
    ]
    started_operations: list[ExternalJobRef] = []
    for pending in remaining:
        current = await operation_port.get(pending.job_id)
        if current is None:
            raise ValueError(f"额度暂停时分镜 Operation {pending.job_id} 不存在或已过期")
        _validate_operation_identity(state, pending, current)
        scene_id = _scene_id_from_operation(pending)
        scene = _scene_by_id(state.scene_packages, scene_id)
        can_freeze_as_unstarted = (
            current.provider_job_id is None
            and current.status in {ExternalJobStatus.CREATED, ExternalJobStatus.FAILED}
        )
        if not can_freeze_as_unstarted:
            if current.status in _TERMINAL_OPERATION_STATUSES:
                raise OperationConflictError("额度暂停遇到已终结的并发 Operation，必须先恢复权威结果")
            started_operations.append(current)
            continue
        failure = {
            "scene_id": scene["scene_id"],
            "scene_index": scene["scene_index"],
            "error": "本轮因额度不足尚未生成",
            "attempts": 0,
            "retryable": True,
            "quota_insufficient": True,
            "not_started_due_to_quota": True,
            "raw": {},
        }
        terminal_claim = await _finalize_operation(
            operation_port,
            state,
            pending,
            target_status=ExternalJobStatus.FAILED,
            result_hash=_terminal_result_hash("quota_not_started", failure),
        )
        failures_by_id[scene["scene_id"]] = failure
        terminal_claims_by_id[scene["scene_id"]] = _terminal_claim_payload(
            scene["scene_id"],
            stage_version=state.stage_version,
            claim=terminal_claim,
        )

    failures = sorted(failures_by_id.values(), key=lambda item: item["scene_index"])
    started_ids = {_scene_id_from_operation(item) for item in started_operations}
    remaining_requests = [
        item for item in state.generation_requests if item["scene_id"] in started_ids
    ]
    terminal_claims = sorted(
        terminal_claims_by_id.values(),
        key=lambda item: _scene_by_id(state.scene_packages, item["scene_id"])["scene_index"],
    )
    result = replace(
        state,
        current_stage=(
            VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS
            if started_operations
            else VideoSceneGenerationStage.SCENE_VIDEO_REVIEW
        ),
        status=WorkflowStatus.PAUSED_QUOTA,
        context_version=state.context_version + 1,
        updated_at=timestamp,
        _failed_scenes_json=_canonical_json(failures, field_name="额度暂停失败分镜"),
        _generation_requests_json=_canonical_json(remaining_requests, field_name="额度暂停后运行中分镜请求"),
        _terminal_claims_json=_canonical_json(terminal_claims, field_name="额度暂停终态 claim"),
        _pending_operations=tuple(started_operations),
    )
    _validate_generation_state(result)
    return result


def _validate_generation_state(state: VideoSceneGenerationWorkflowState) -> None:
    _validate_scene_package_state_authority(state._source_state)
    if state.workflow_id != state._source_state.workflow_id or state.conversation_id != state._source_state.conversation_id:
        raise ValueError("分镜生成状态必须属于来源场景包的同一 Workflow 和对话")
    scenes = state.scene_packages
    edited_scene_ids = state.edited_scene_ids
    dirty_scene_ids = state.dirty_scene_ids
    if len(edited_scene_ids) != len(set(edited_scene_ids)):
        raise ValueError("已授权修改分镜 ID 不得重复")
    if len(dirty_scene_ids) != len(set(dirty_scene_ids)) or set(dirty_scene_ids).difference(edited_scene_ids):
        raise ValueError("待重生成分镜必须唯一且属于已授权修改谱系")
    _validated_current_scenes(
        state._source_state.scene_package.scene_packages,
        scenes,
        edited_scene_ids,
        global_assets=state.source_scene_package.global_assets,
        contract=state.source_scene_package.creation_contract,
    )
    _validated_total_duration(scenes, state.source_scene_package.target_duration_ms)
    original_videos = state.scene_videos
    videos = _validated_scene_videos(original_videos, scenes)
    if _canonical_json(videos, field_name="规范分镜视频") != _canonical_json(original_videos, field_name="当前分镜视频"):
        raise ValueError("分镜视频必须已经过规范化和敏感信息清洗")
    for video in videos:
        expected_request = _generation_requests(
            scenes,
            [video["scene_id"]],
            state.source_scene_package.creation_contract,
        )[0]
        expected_mode = expected_request["generation_mode"]
        if video["mode"] != expected_mode or video["endpoint"] != _endpoint_for_mode(expected_mode):
            raise ValueError("分镜成功结果的 mode 和 endpoint 必须逐项绑定权威生成请求")
    failures = state.failed_scenes
    _validate_failures(failures, scenes)
    if {item["scene_id"] for item in videos}.intersection(item["scene_id"] for item in failures):
        raise ValueError("同一分镜不能同时是成功和失败结果")
    pending = state.pending_operations
    pending_scene_ids = [_scene_id_from_operation(item) for item in pending]
    generation_requests = state.generation_requests
    request_scene_ids = [item["scene_id"] for item in generation_requests]
    if pending_scene_ids != request_scene_ids:
        raise ValueError("pending Operation 必须与当前分镜生成请求逐项对应")
    expected_requests = _generation_requests(
        scenes,
        pending_scene_ids,
        state.source_scene_package.creation_contract,
    )
    if _canonical_json(generation_requests, field_name="当前 pending 生成请求") != _canonical_json(
        expected_requests,
        field_name="权威 pending 生成请求",
    ):
        raise ValueError("pending 分镜必须逐项继承权威生成请求")
    if len(pending_scene_ids) != len(set(pending_scene_ids)):
        raise ValueError("同一分镜同一时刻只能有一个 pending Operation")
    for item in pending:
        if item.workflow_id != state.workflow_id:
            raise ValueError("pending Operation 不属于当前 Workflow")
        scene_id = _scene_id_from_operation(item)
        if item.attempt != state.operation_attempts.get(scene_id):
            raise ValueError("pending Operation attempt 必须与权威尝试次数一致")
        request = _request_by_scene_id(expected_requests, scene_id)
        request_hash = hashlib.sha256(
            _canonical_json(request, field_name=f"分镜 {scene_id} 权威 Operation 请求").encode("utf-8")
        ).hexdigest()
        expected_key = _operation_idempotency_key(
            workflow_id=state.workflow_id,
            stage_version=state.stage_version,
            scene_id=scene_id,
            attempt=item.attempt,
            request_hash=request_hash,
        )
        if item.idempotency_key != expected_key:
            raise ValueError("pending Operation 幂等键必须与当前 stage 和 attempt 的业务身份一致")
    terminal_claims = _validated_terminal_claims(state, scenes)
    pending_ids = set(pending_scene_ids)
    expected_terminal_ids = (
        {item["scene_id"] for item in videos}
        | {item["scene_id"] for item in failures}
    ).difference(pending_ids)
    if set(terminal_claims) != expected_terminal_ids:
        raise ValueError("成功和失败结果必须逐镜绑定唯一终态 claim")
    for video in videos:
        scene_id = video["scene_id"]
        if scene_id in pending_ids:
            continue
        claim = terminal_claims[scene_id]
        expected_hash = _terminal_result_hash(
            "succeeded",
            {
                "scene_id": scene_id,
                "video_url": video["video_url"],
                "mode": video["mode"],
                "endpoint": video["endpoint"],
                "provider_job_id": video["task_id"],
                "raw": video["raw"],
            },
        )
        if (
            claim.job.status is not ExternalJobStatus.SUCCEEDED
            or claim.job.provider_job_id != video["task_id"]
            or claim.result_hash != expected_hash
        ):
            raise ValueError("分镜成功结果必须与持久化终态 claim 完全一致")
    for failure in failures:
        scene_id = failure["scene_id"]
        if scene_id in pending_ids:
            continue
        claim = terminal_claims[scene_id]
        result_type = "quota_not_started" if failure["not_started_due_to_quota"] else "failed"
        if (
            claim.job.status is not ExternalJobStatus.FAILED
            or claim.result_hash != _terminal_result_hash(result_type, failure)
        ):
            raise ValueError("分镜失败结果必须与持久化终态 claim 完全一致")
    covered_scene_ids = (
        {item["scene_id"] for item in videos}
        | {item["scene_id"] for item in failures}
        | set(pending_scene_ids)
        | set(dirty_scene_ids)
    )
    expected_scene_ids = {item["scene_id"] for item in scenes}
    if covered_scene_ids != expected_scene_ids:
        raise ValueError("每个分镜必须处于成功、失败、运行中或待重生成状态")
    if state.current_stage is VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS:
        if state.status not in {WorkflowStatus.RUNNING, WorkflowStatus.PAUSED_QUOTA} or not pending:
            raise ValueError("分镜生成阶段必须处于运行或额度暂停状态并持有 pending Operation")
        if state.status is WorkflowStatus.PAUSED_QUOTA and not state.quota_insufficient:
            raise ValueError("生成中的额度暂停状态必须保留额度失败分镜")
    elif state.current_stage is VideoSceneGenerationStage.SCENE_VIDEO_REVIEW:
        if state.status not in {WorkflowStatus.AWAITING_USER, WorkflowStatus.PAUSED_QUOTA} or pending:
            raise ValueError("分镜复核阶段必须等待用户或暂停额度且不得保留 pending Operation")
        if state.status is WorkflowStatus.PAUSED_QUOTA and not state.quota_insufficient:
            raise ValueError("额度暂停状态必须保留可恢复的额度失败分镜")
    else:
        raise ValueError("不支持的分镜生成阶段")


def _validated_terminal_claims(
    state: VideoSceneGenerationWorkflowState,
    scenes: Sequence[Mapping[str, Any]],
) -> dict[str, VideoSceneOperationTerminalClaim]:
    """恢复终态 claim，并校验 Operation 身份、阶段、摘要和规范载荷。"""

    value = state.terminal_claims
    if not isinstance(value, list):
        raise ValueError("分镜终态 claim 必须是数组")
    scene_ids = {item["scene_id"] for item in scenes}
    result: dict[str, VideoSceneOperationTerminalClaim] = {}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"scene_id", "stage_version", "result_hash", "job"}:
            raise ValueError("分镜终态 claim 必须使用冻结字段集合")
        scene_id = _required_text(item.get("scene_id"), "终态 claim scene_id")
        if scene_id not in scene_ids or scene_id in result:
            raise ValueError("分镜终态 claim 必须唯一引用当前分镜")
        stage_version = _required_int(item.get("stage_version"), "终态 claim stage_version")
        if stage_version < 1 or stage_version > state.stage_version:
            raise ValueError("分镜终态 claim stage_version 超出当前 Workflow 范围")
        result_hash = _required_text(item.get("result_hash"), "终态 claim result_hash")
        if re.fullmatch(r"[0-9a-f]{64}", result_hash) is None:
            raise ValueError("分镜终态 claim result_hash 必须是 SHA-256")
        try:
            job = ExternalJobRef.model_validate(item.get("job"))
        except (TypeError, ValueError) as exc:
            raise ValueError("分镜终态 claim job 不符合 Operation 合同") from exc
        if job.workflow_id != state.workflow_id or _scene_id_from_operation(job) != scene_id:
            raise ValueError("分镜终态 claim Operation 不属于当前 Workflow 或分镜")
        if job.status not in _TERMINAL_OPERATION_STATUSES:
            raise ValueError("分镜终态 claim 必须保存已终结 Operation")
        expected_key = _operation_idempotency_key(
            workflow_id=state.workflow_id,
            stage_version=stage_version,
            scene_id=scene_id,
            attempt=job.attempt,
            request_hash="终态 claim 已单独绑定结果摘要",
        )
        if job.idempotency_key != expected_key:
            raise ValueError("分镜终态 claim 幂等键与原业务操作身份不一致")
        claim = VideoSceneOperationTerminalClaim(job=job, result_hash=result_hash)
        if _canonical_json(item, field_name="当前终态 claim") != _canonical_json(
            _terminal_claim_payload(scene_id, stage_version=stage_version, claim=claim),
            field_name="规范终态 claim",
        ):
            raise ValueError("分镜终态 claim 必须是规范持久化载荷")
        result[scene_id] = claim
    return result


def _validated_current_scenes(
    source_scenes: Sequence[Mapping[str, Any]],
    current_scenes: Sequence[Mapping[str, Any]],
    edited_scene_ids: Sequence[str],
    *,
    global_assets: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    if len(source_scenes) != len(current_scenes):
        raise ValueError("单镜修改不得增删分镜")
    edited = set(edited_scene_ids)
    source_ids = [item["scene_id"] for item in source_scenes]
    if edited.difference(source_ids):
        raise ValueError("已修改分镜 ID 不属于来源场景包")
    immutable_fields = {
        "scene_id",
        "scene_index",
        "title",
        "duration_ms",
        "transition",
        "image_urls",
        "video_urls",
        "audio_urls",
    }
    for source, current in zip(source_scenes, current_scenes, strict=True):
        scene_id = source["scene_id"]
        if current["scene_id"] != scene_id:
            raise ValueError("单镜修改不得调整分镜顺序或身份")
        if scene_id not in edited:
            if _canonical_json(current, field_name="未修改分镜") != _canonical_json(source, field_name="来源分镜"):
                raise ValueError("未标记修改的分镜必须逐字继承来源场景包")
            continue
        for field_name in immutable_fields:
            if current.get(field_name) != source.get(field_name):
                raise ValueError(f"单镜修改不得改写 {field_name}")
        if set(current) != set(source):
            raise ValueError("单镜修改不得增删供应商字段")
        storyline = _required_text(current.get("storyline"), "单镜 storyline")
        narration = _string_value(current.get("narration"), "单镜 narration")
        reference_ids = current.get("reference_asset_ids")
        if not isinstance(reference_ids, list):
            raise ValueError("单镜 reference_asset_ids 必须是数组")
        normalized_ids = [_required_text(item, "单镜 reference_asset_ids") for item in reference_ids]
        if len(normalized_ids) != len(set(normalized_ids)) or len(normalized_ids) > 9:
            raise ValueError("单镜参考资产必须唯一且最多 9 个")
        lookup = _generated_asset_lookup(global_assets)
        if set(normalized_ids).difference(lookup):
            raise ValueError("单镜引用了不存在的全局资产")
        shot = current.get("shot_description")
        if not isinstance(shot, Mapping) or set(shot) != {"text", "mentions"}:
            raise ValueError("单镜 shot_description 必须完整包含 text 和 mentions")
        _required_text(shot.get("text"), "单镜 shot_description.text")
        _validate_edited_shot_text(
            shot["text"],
            duration_sec=current["duration_ms"] // 1000,
            reference_asset_ids=normalized_ids,
        )
        expected_mentions = [
            {
                "asset_id": asset_id,
                "type": lookup[asset_id]["type"],
                "name": lookup[asset_id]["name"],
                "image_url": lookup[asset_id]["image_url"],
            }
            for asset_id in normalized_ids
        ]
        if shot.get("mentions") != expected_mentions:
            raise ValueError("单镜 mentions 必须与全局资产和 reference_asset_ids 一致")
        expected_prompt = build_authoritative_scene_prompt(
            storyline,
            shot,
            narration,
            global_assets["visual_style"],
            video_model=_required_text(contract.get("video_model"), "创作合同 video_model"),
        )
        if current.get("prompt") != expected_prompt:
            raise ValueError("单镜修改后的 Prompt 必须由权威字段机械重建")


def _validate_failures(value: Any, scenes: Sequence[Mapping[str, Any]]) -> None:
    if not isinstance(value, list):
        raise ValueError("failed_scenes 必须是数组")
    scene_lookup = {item["scene_id"]: item for item in scenes}
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("failed_scenes 只能包含对象")
        scene_id = _required_text(item.get("scene_id"), "failed_scenes.scene_id")
        if scene_id in seen or scene_id not in scene_lookup:
            raise ValueError("failed_scenes 必须唯一引用当前分镜")
        seen.add(scene_id)
        if item.get("scene_index") != scene_lookup[scene_id]["scene_index"]:
            raise ValueError("failed_scenes.scene_index 必须与场景包一致")
        error = _required_text(item.get("error"), "failed_scenes.error")
        if error != _sanitize_sensitive_text(error):
            raise ValueError("failed_scenes.error 必须清除敏感信息")
        if not isinstance(item.get("retryable"), bool) or not isinstance(item.get("quota_insufficient"), bool):
            raise ValueError("failed_scenes 重试和额度标记必须是布尔值")
        if not isinstance(item.get("not_started_due_to_quota"), bool):
            raise ValueError("failed_scenes 必须声明是否因额度不足未启动")
        attempts = _required_int(item.get("attempts"), "failed_scenes.attempts")
        not_started = item["not_started_due_to_quota"]
        if not_started:
            if attempts != 0 or item["quota_insufficient"] is not True or item["retryable"] is not True:
                raise ValueError("因额度不足未启动的分镜必须以零次调用保持可恢复")
        elif attempts < 1 or attempts > _MAX_PROVIDER_ATTEMPTS:
            raise ValueError("failed_scenes.attempts 必须为 1-3")
        raw = item.get("raw")
        if not isinstance(raw, Mapping) or _safe_json_object(raw, field_name="failed_scenes.raw") != raw:
            raise ValueError("failed_scenes.raw 必须是已清洗的安全对象")


def _validate_edited_shot_text(
    value: Any,
    *,
    duration_sec: int,
    reference_asset_ids: Sequence[str],
) -> None:
    text = _required_text(value, "单镜 shot_description.text")
    if re.search(r"(?:ms\b|毫秒|\d{1,2}:\d{2}\.\d+)", text, flags=re.IGNORECASE):
        raise ValueError("单镜镜头描述不能使用毫秒时间码")
    ranges = [
        (int(match.group("start")), int(match.group("end")))
        for match in re.finditer(
            r"(?P<start>\d+)\s*(?:[-~—至])\s*(?P<end>\d+)\s*秒",
            text,
        )
    ]
    cursor = 0
    for start, end in ranges:
        if start != cursor or end <= start:
            raise ValueError("单镜镜头描述时间范围必须无重叠、无缺口")
        cursor = end
    if not ranges or cursor != duration_sec:
        raise ValueError(f"单镜镜头描述时间范围必须从 0 秒连续覆盖到 {duration_sec} 秒")
    mentioned_ids = set(re.findall(r"@([A-Za-z0-9_.:-]+)", text))
    unknown = mentioned_ids.difference(reference_asset_ids)
    if unknown:
        raise ValueError(f"单镜镜头描述引用了未声明资产：{sorted(unknown)}")


def _validated_total_duration(scenes: Sequence[Mapping[str, Any]], target_duration_ms: int) -> None:
    durations = [_required_int(item.get("duration_ms"), "分镜 duration_ms") for item in scenes]
    if any(item % 1000 != 0 or item < 4_000 or item > 15_000 for item in durations):
        raise ValueError("每个分镜必须是 4-15 秒的整数秒")
    if sum(durations) != target_duration_ms:
        raise ValueError("分镜总时长必须精确等于权威场景包总时长")


def _validate_result_state(state: VideoSceneGenerationWorkflowState) -> None:
    _validate_generation_state(state)
    if (
        state.current_stage is not VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS
        or state.status not in {WorkflowStatus.RUNNING, WorkflowStatus.PAUSED_QUOTA}
    ):
        raise ValueError("只有运行中或额度暂停后继续轮询的分镜才能接收原 Operation 结果")


def _validate_review_state(state: VideoSceneGenerationWorkflowState) -> None:
    _validate_generation_state(state)
    if state.current_stage is not VideoSceneGenerationStage.SCENE_VIDEO_REVIEW:
        raise ValueError("只有分镜视频复核阶段才能修改或重试")


def _validate_operation_identity(
    state: VideoSceneGenerationWorkflowState,
    expected: ExternalJobRef,
    actual: ExternalJobRef,
) -> None:
    expected_identity = (expected.job_id, expected.workflow_id, expected.stage, expected.attempt, expected.idempotency_key)
    actual_identity = (actual.job_id, actual.workflow_id, actual.stage, actual.attempt, actual.idempotency_key)
    if actual_identity != expected_identity or actual.workflow_id != state.workflow_id:
        raise ValueError("恢复的 Operation 身份与权威引用不一致")


async def _finalize_operation(
    operation_port: OperationPort,
    state: VideoSceneGenerationWorkflowState,
    expected: ExternalJobRef,
    *,
    target_status: ExternalJobStatus,
    provider_job_id: str | None = None,
    result_hash: str,
) -> VideoSceneOperationTerminalClaim:
    """要求视频 Port 原子 claim 唯一终态；M06 未实现时明确失败关闭。"""

    finalizer = getattr(operation_port, "finalize_scene_operation", None)
    if not callable(finalizer):
        raise ValueError("当前 OperationPort 不支持视频结果原子终态 claim，必须等待 M06 适配后再执行")
    claim = await finalizer(
        expected=expected.model_copy(deep=True),
        target_status=target_status,
        provider_job_id=provider_job_id,
        result_hash=result_hash,
    )
    if not isinstance(claim, VideoSceneOperationTerminalClaim):
        raise OperationConflictError("视频原子终态 Port 返回了不受支持的 claim")
    _validate_operation_identity(state, expected, claim.job)
    if claim.result_hash != result_hash or claim.job.status is not target_status:
        raise OperationConflictError("视频 Operation 终态或结果摘要发生并发漂移")
    if target_status is ExternalJobStatus.SUCCEEDED and claim.job.provider_job_id is None:
        raise OperationConflictError("成功 Operation 必须绑定供应商任务 ID")
    return claim


def _terminal_result_hash(result_type: str, payload: Mapping[str, Any]) -> str:
    identity = {
        "result_type": _required_text(result_type, "Operation 结果类型"),
        "payload": _safe_json_object(payload, field_name="Operation 终态结果"),
    }
    return hashlib.sha256(_canonical_json(identity, field_name="Operation 终态摘要").encode("utf-8")).hexdigest()


def _terminal_claim_payload(
    scene_id: str,
    *,
    stage_version: int,
    claim: VideoSceneOperationTerminalClaim,
) -> dict[str, Any]:
    """把视频专用终态 claim 转成可持久化且可恢复校验的权威载荷。"""

    return {
        "scene_id": _required_text(scene_id, "终态 claim scene_id"),
        "stage_version": _required_int(stage_version, "终态 claim stage_version"),
        "result_hash": _required_text(claim.result_hash, "终态 claim result_hash"),
        "job": claim.job.model_dump(mode="json"),
    }


def _pending_operation(state: VideoSceneGenerationWorkflowState, scene_id: str) -> ExternalJobRef:
    for item in state.pending_operations:
        if _scene_id_from_operation(item) == scene_id:
            return item
    raise ValueError(f"分镜 {scene_id} 没有待完成 Operation")


def _scene_id_from_operation(operation: ExternalJobRef) -> str:
    prefix = "generate_scene_video:"
    if not operation.stage.startswith(prefix):
        raise ValueError("分镜 Operation stage 不受支持")
    return _required_text(operation.stage.removeprefix(prefix), "Operation scene_id")


def _scene_by_id(scenes: Sequence[Mapping[str, Any]], scene_id: str) -> Mapping[str, Any]:
    normalized = _required_text(scene_id, "scene_id")
    for item in scenes:
        if item["scene_id"] == normalized:
            return item
    raise ValueError(f"分镜 {normalized} 不存在")


def _request_by_scene_id(requests: Sequence[Mapping[str, Any]], scene_id: str) -> Mapping[str, Any]:
    for item in requests:
        if item["scene_id"] == scene_id:
            return item
    raise ValueError(f"分镜 {scene_id} 缺少生成请求")


def _endpoint_for_mode(mode: str) -> str:
    endpoints = {
        "text_to_video": "/api/video/text-to-video",
        "image_to_video": "/api/video/image-to-video",
        "two_image_to_video": "/api/video/two-image-to-video",
        "reference_mode_video": "/api/video/reference-mode-video",
        "edit_video": "/api/video/edit-video",
        "extend_video": "/api/video/extend-video",
    }
    return endpoints.get(mode, "/api/video/reference-mode-video")


def _operation_idempotency_key(
    *,
    workflow_id: str,
    stage_version: int,
    scene_id: str,
    attempt: int,
    request_hash: str,
) -> str:
    # 请求摘要由 OperationRequest 单独校验；业务幂等身份不能随漂移请求变化。
    _ = request_hash
    identity = "\0".join((workflow_id, str(stage_version), scene_id, str(attempt)))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"pf:video-scene:{digest}"


def _normalize_capability(value: str) -> str:
    return "".join(
        character
        for character in str(value or "").strip().lower()
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def _safe_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name}必须是对象")
    normalized = json.loads(_canonical_json(value, field_name=field_name))
    return _redact_sensitive_json(normalized)


def _redact_sensitive_json(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = "".join(character for character in key.lower() if character.isalnum())
            if any(marker in normalized_key for marker in ("authorization", "token", "apikey", "secret", "password", "credential")):
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact_sensitive_json(item)
        return result
    if isinstance(value, list):
        return [_redact_sensitive_json(item) for item in value]
    if isinstance(value, str):
        return _sanitize_sensitive_text(value)
    return value


def _sanitize_sensitive_text(value: str) -> str:
    """清除字符串里的凭据和 URL 查询参数，避免安全摘要持久化秘密。"""

    def strip_url_query(match: re.Match[str]) -> str:
        parsed = urlsplit(match.group(0))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    sanitized = _URL_PATTERN.sub(strip_url_query, value)
    sanitized = _SENSITIVE_ASSIGNMENT_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub("Bearer [REDACTED]", sanitized)
    return sanitized


def _is_non_retryable_failure(error: str, raw: Mapping[str, Any]) -> bool:
    status_code = raw.get("status_code")
    if isinstance(status_code, int) and 400 <= status_code < 500 and status_code not in {408, 425, 429}:
        return True
    text = f"{error} {raw}".lower()
    return any(
        marker in text
        for marker in (
            "capability_mismatch",
            "参数验证失败",
            "模型价格配置不存在",
            "validation failed",
            "unsupported ratio",
            "unsupported image quality",
            "does not support",
            "不支持当前生成模式",
        )
    )


def _next_timestamp(state: VideoSceneGenerationWorkflowState, value: datetime | None) -> datetime:
    timestamp = _timestamp(value)
    if timestamp < state.updated_at:
        raise ValueError("Workflow 更新时间不能早于当前状态")
    return timestamp


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


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ValueError("可选文本提供后不能为空")
    return text


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} 必须是整数")
    return value


def _string_value(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    return value


def _required_https_url(value: Any, field_name: str) -> str:
    url = _required_text(value, field_name)
    if value != url:
        raise ValueError(f"{field_name} 必须是无首尾空白的规范值")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError(f"{field_name} 必须是 HTTPS URL")
    return url


def _canonical_json(value: Any, *, field_name: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是可序列化 JSON") from exc


VideoSceneVideoWorkflowService = VideoSceneGenerationWorkflowService
VideoSceneVideoWorkflowState = VideoSceneGenerationWorkflowState
VideoSceneVideoStage = VideoSceneGenerationStage
