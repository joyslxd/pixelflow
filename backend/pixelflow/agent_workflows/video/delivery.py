"""剪映草稿、历史入口和最终视频下载投影的 Workflow Service。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from pixelflow.agent_runtime.contracts import (
    ExternalJobRef,
    ExternalJobStatus,
    OperationRequest,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.ports import OperationConflictError, OperationPort
from pixelflow.jianying_draft.models import (
    JianyingDraftRequest,
    JianyingDraftResult,
    JianyingDraftScene,
    JianyingDraftStatus,
    compute_storyboard_version_id,
)
from pixelflow.jianying_draft.skill import JianyingDraftSkill

from . import postproduction as postproduction_module
from .postproduction import (
    VideoOperationStartClaim,
    VideoOperationTerminalClaim,
    VideoPostProductionStage,
    VideoPostProductionWorkflowService,
    VideoPostProductionWorkflowState,
)
from .scene_packages import (
    VideoScenePackageAuthoritySnapshot,
    VideoScenePackageWorkflowState,
)
from .video_generation import (
    VideoSceneGenerationWorkflowState,
    VideoSceneOperationTerminalClaim,
)

_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", flags=re.IGNORECASE)
_SENSITIVE_PATTERN = re.compile(
    r"https?://|\b(?:authorization|bearer|token|api[_ -]?key|apikey|secret|password|credential)\b|密钥|鉴权|凭据",
    flags=re.IGNORECASE,
)
_TERMINAL_DRAFT_STATUSES = {
    JianyingDraftStatus.SUCCEEDED,
    JianyingDraftStatus.FAILED,
    JianyingDraftStatus.TIMEOUT,
    JianyingDraftStatus.NOT_CONFIGURED,
}
_PUBLIC_BUSINESS_FAILURE_PREFIXES = (
    "第三方剪映草稿任务创建失败：",
    "第三方剪映草稿任务处理失败：",
)
_RESULT_FIELDS = (
    "status",
    "job_id",
    "provider_task_id",
    "conversation_id",
    "storyboard_version_id",
    "download_url",
    "file_name",
    "expire_at",
    "message",
)
_DEFAULT_JIANYING_TIMEOUT_SECONDS = 30 * 60.0


@dataclass(frozen=True, slots=True)
class VideoDeliveryWorkflowState:
    """保存当前视频、剪映版本历史和下载证据的权威快照。"""

    workflow_id: str
    conversation_id: str
    current_stage: VideoPostProductionStage
    status: WorkflowStatus
    stage_version: int
    context_version: int
    created_at: datetime
    updated_at: datetime
    _postproduction_state: VideoPostProductionWorkflowState = field(repr=False)
    _jianying_draft_records_json: str = field(repr=False)
    _operation_attempts_json: str = field(repr=False)
    _pending_operation: ExternalJobRef | None = field(repr=False)
    _pending_jianying_operation_json: str = field(repr=False)
    _final_video_delivery_json: str = field(repr=False)

    @property
    def postproduction_state(self) -> VideoPostProductionWorkflowState:
        return self._postproduction_state

    @property
    def jianying_draft_records(self) -> dict[str, dict[str, Any]]:
        value = json.loads(self._jianying_draft_records_json)
        return {str(key): dict(item) for key, item in value.items()}

    @property
    def operation_attempts(self) -> dict[str, int]:
        value = json.loads(self._operation_attempts_json)
        return {str(key): int(item) for key, item in value.items()}

    @property
    def pending_operation(self) -> ExternalJobRef | None:
        return self._pending_operation.model_copy(deep=True) if self._pending_operation else None

    @property
    def pending_jianying_request(self) -> dict[str, Any] | None:
        operation = self.pending_jianying_operation
        if operation is None:
            return None
        request = operation.get("request")
        return copy_json(request) if isinstance(request, Mapping) else None

    @property
    def pending_jianying_operation(self) -> dict[str, Any] | None:
        value = json.loads(self._pending_jianying_operation_json)
        return dict(value) if isinstance(value, dict) else None

    @property
    def final_video_delivery(self) -> dict[str, Any] | None:
        value = json.loads(self._final_video_delivery_json)
        return dict(value) if isinstance(value, dict) else None

    @property
    def current_storyboard_version_id(self) -> str:
        return compute_storyboard_version_id(_current_jianying_scenes(self._postproduction_state))

    @property
    def jianying_artifact_refs(self) -> list[str]:
        workflow_key = quote(self.workflow_id, safe="-_.")
        refs: list[str] = []
        for version_id, record in sorted(self.jianying_draft_records.items()):
            digest = hashlib.sha256(_canonical_json(record, field_name="剪映草稿历史 Artifact").encode("utf-8")).hexdigest()
            refs.append(f"artifact:video-jianying:{workflow_key}:{quote(version_id, safe='-_.')}:{digest[:16]}")
        return refs

    @property
    def delivery_artifact_ref(self) -> str | None:
        delivery = self.final_video_delivery
        if delivery is None:
            return None
        workflow_key = quote(self.workflow_id, safe="-_.")
        digest = hashlib.sha256(_canonical_json(delivery, field_name="最终视频下载 Artifact").encode("utf-8")).hexdigest()
        return f"artifact:video-delivery:{workflow_key}:{digest[:16]}"


class VideoDeliveryWorkflowService:
    """类比 Java Application Service，编排剪映草稿和最终交付投影。"""

    def __init__(
        self,
        operation_port: OperationPort | None = None,
        *,
        timeout_seconds: float = _DEFAULT_JIANYING_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("剪映草稿总等待时间必须是大于零的有限秒数")
        self._operation_port = operation_port
        self._timeout_seconds = float(timeout_seconds)

    async def initialize(
        self,
        postproduction_state: VideoPostProductionWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoDeliveryWorkflowState:
        """从已合并且等待人工审核或已人工结束的视频建立交付状态。"""

        port = self._atomic_port(operation_port)
        _validate_ready_postproduction(postproduction_state)
        await _validate_trusted_postproduction(postproduction_state, port)
        timestamp = _timestamp(now) if now is not None else postproduction_state.updated_at
        if timestamp < postproduction_state.updated_at:
            raise ValueError("交付状态更新时间不能早于视频后处理状态")
        state = VideoDeliveryWorkflowState(
            workflow_id=postproduction_state.workflow_id,
            conversation_id=postproduction_state.conversation_id,
            current_stage=postproduction_state.current_stage,
            status=postproduction_state.status,
            stage_version=postproduction_state.stage_version,
            context_version=postproduction_state.context_version,
            created_at=postproduction_state.created_at,
            updated_at=timestamp,
            _postproduction_state=postproduction_state,
            _jianying_draft_records_json="{}",
            _operation_attempts_json="{}",
            _pending_operation=None,
            _pending_jianying_operation_json="null",
            _final_video_delivery_json="null",
        )
        _validate_delivery_state(state)
        return state

    async def synchronize_postproduction(
        self,
        state: VideoDeliveryWorkflowState,
        postproduction_state: VideoPostProductionWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoDeliveryWorkflowState:
        """切换到同一 Workflow 的新视频版本，同时保留旧剪映历史。"""

        port = self._atomic_port(operation_port)
        _validate_delivery_state(state)
        await self._validate_trusted_state(state, port)
        _validate_ready_postproduction(postproduction_state)
        await _validate_trusted_postproduction(postproduction_state, port)
        if state.pending_operation is not None:
            raise ValueError("剪映草稿运行期间不能切换当前视频版本")
        if postproduction_state.workflow_id != state.workflow_id or postproduction_state.conversation_id != state.conversation_id:
            raise ValueError("只能同步同一对话、同一 Workflow 的视频状态")
        previous = state.postproduction_state
        if postproduction_state.stage_version <= previous.stage_version or postproduction_state.context_version <= previous.context_version:
            raise ValueError("新视频状态必须严格晚于当前后处理状态")
        timestamp = _timestamp(now) if now is not None else postproduction_state.updated_at
        if timestamp < postproduction_state.updated_at or timestamp < state.updated_at:
            raise ValueError("同步后处理状态时更新时间不能倒退")
        keep_delivery = state.final_video_delivery if postproduction_state.video_artifact_ref == previous.video_artifact_ref else None
        synchronized = replace(
            state,
            current_stage=postproduction_state.current_stage,
            status=postproduction_state.status,
            stage_version=max(state.stage_version, postproduction_state.stage_version) + 1,
            context_version=max(state.context_version, postproduction_state.context_version) + 1,
            updated_at=timestamp,
            _postproduction_state=postproduction_state,
            _pending_operation=None,
            _pending_jianying_operation_json="null",
            _final_video_delivery_json=_canonical_json(keep_delivery, field_name="最终视频下载证据"),
        )
        _validate_delivery_state(synchronized)
        return synchronized

    def cancel(
        self,
        state: VideoDeliveryWorkflowState,
        *,
        postproduction_service: VideoPostProductionWorkflowService,
        now: datetime | None = None,
    ) -> VideoDeliveryWorkflowState:
        """通过后处理 Service 同步取消交付层，并保留原外部任务事实。"""

        _validate_delivery_state(state, allow_cancelled=True)
        if state.status in {WorkflowStatus.CANCELLED, WorkflowStatus.COMPLETED}:
            raise ValueError("已取消或已完成的视频交付 Workflow 属于终态，不能再次取消")
        timestamp = _cancellation_timestamp(state.updated_at, now)
        cancelled_postproduction = postproduction_service.cancel(
            state.postproduction_state,
            now=timestamp,
        )
        result = replace(
            state,
            status=WorkflowStatus.CANCELLED,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=timestamp,
            _postproduction_state=cancelled_postproduction,
        )
        _validate_delivery_state(result, allow_cancelled=True)
        return result

    def current_jianying_scenes(
        self,
        state: VideoDeliveryWorkflowState,
    ) -> list[JianyingDraftScene]:
        """返回当前版本全部成功分镜的隔离 DTO。"""

        _validate_delivery_state(state)
        return [item.model_copy(deep=True) for item in _current_jianying_scenes(state.postproduction_state)]

    async def start_jianying_draft(
        self,
        state: VideoDeliveryWorkflowState,
        *,
        retry_failed: bool = False,
        project_name: str | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoDeliveryWorkflowState:
        """按当前分镜版本领取唯一剪映 Operation，不直接调用 Provider。"""

        port = self._atomic_port(operation_port)
        _validate_delivery_state(state)
        await self._validate_trusted_state(state, port)
        version_id = state.current_storyboard_version_id
        pending = state.pending_operation
        if pending is not None:
            request = state.pending_jianying_request
            if request and request.get("storyboard_version_id") == version_id:
                return state
            raise ValueError("已有其他剪映草稿任务运行，不能切换版本或重复启动")

        timestamp = _timestamp(now)
        existing = state.jianying_draft_records.get(version_id)
        if existing is not None:
            result = _result_from_record(existing)
            if result.status is JianyingDraftStatus.SUCCEEDED and _is_valid_unexpired_success(result, timestamp):
                return state
            if result.status in {JianyingDraftStatus.FAILED, JianyingDraftStatus.TIMEOUT} and not retry_failed:
                raise ValueError("失败或超时的剪映草稿只有用户显式重试后才能重新生成")
        elif retry_failed:
            raise ValueError("当前分镜版本没有可显式重试的失败草稿")

        scenes = _current_jianying_scenes(state.postproduction_state)
        merged = state.postproduction_state.merged_video or {}
        request = JianyingDraftRequest(
            conversation_id=state.conversation_id,
            storyboard_version_id=version_id,
            scenes=scenes,
            video_task_id=_optional_text(merged.get("task_id")),
            project_name=_project_name(project_name),
        )
        request_payload = request.model_dump(mode="json")
        operation_payload = {
            "request": request_payload,
            "retry_failed": bool(retry_failed),
        }
        attempt = state.operation_attempts.get(version_id, 0) + 1
        stage_version = state.stage_version + 1
        request_hash = _hash_json(operation_payload, field_name="剪映草稿启动请求")
        operation = await port.claim(
            OperationRequest(
                workflow_id=state.workflow_id,
                stage="jianying_draft",
                stage_version=stage_version,
                attempt=attempt,
                request_hash=request_hash,
                idempotency_key=_operation_key(
                    state.workflow_id,
                    stage_version,
                    attempt,
                    operation_payload,
                ),
            )
        )
        started = replace(
            state,
            stage_version=stage_version,
            context_version=state.context_version + 1,
            updated_at=_next_timestamp(state, timestamp),
            _pending_operation=operation,
            _pending_jianying_operation_json=_canonical_json(
                operation_payload,
                field_name="待生成剪映草稿 Operation 请求",
            ),
            _operation_attempts_json=_canonical_json(
                {**state.operation_attempts, version_id: attempt},
                field_name="剪映草稿尝试次数",
            ),
        )
        _validate_delivery_state(started)
        return started

    async def resume_jianying_draft(
        self,
        state: VideoDeliveryWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoDeliveryWorkflowState:
        """刷新时只查询原 Operation；终态从可信 Repository 恢复。"""

        port = self._atomic_port(operation_port)
        _validate_delivery_state(state)
        await self._validate_trusted_state(state, port)
        pending = state.pending_operation
        if pending is None:
            return state
        current = await port.get(pending.job_id)
        if current is None:
            raise ValueError("剪映草稿 Operation 不存在或已过期，不得自动重新启动")
        _validate_pending_operation_identity(state, current)
        refreshed = replace(
            state,
            updated_at=_next_timestamp(state, now) if now is not None else state.updated_at,
            _pending_operation=current,
        )
        if current.status in {
            ExternalJobStatus.SUCCEEDED,
            ExternalJobStatus.FAILED,
            ExternalJobStatus.TIMEOUT,
            ExternalJobStatus.EXPIRED,
        }:
            return await self._restore_terminal_state(refreshed, port, now=now)
        return refreshed

    async def record_jianying_result(
        self,
        state: VideoDeliveryWorkflowState,
        result: JianyingDraftResult,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoDeliveryWorkflowState:
        """把 Skill 终态原子绑定到当前版本和 Agent Operation。"""

        port = self._atomic_port(operation_port)
        _validate_delivery_state(state)
        await self._validate_trusted_state(state, port)
        pending = state.pending_operation
        operation_request = state.pending_jianying_operation
        request = state.pending_jianying_request
        if pending is None or operation_request is None or request is None:
            raise OperationConflictError("剪映草稿终态缺少 pending Operation 或请求快照")
        if result.status not in _TERMINAL_DRAFT_STATUSES:
            raise ValueError("剪映草稿 Skill 必须返回终态，不能把 queued/running 当作完成")
        if result.job_id not in {None, pending.job_id}:
            raise OperationConflictError("剪映草稿结果绑定了其他 Agent job_id")
        if result.conversation_id not in {None, state.conversation_id}:
            raise OperationConflictError("剪映草稿结果绑定了其他对话")
        version_id = str(request["storyboard_version_id"])
        if result.storyboard_version_id not in {None, version_id}:
            raise OperationConflictError("剪映草稿结果绑定了其他分镜版本")
        normalized = _normalize_result(
            result,
            job_id=pending.job_id,
            conversation_id=state.conversation_id,
            storyboard_version_id=version_id,
        )
        provider_job_id = _optional_text(normalized.provider_task_id)
        if normalized.status is JianyingDraftStatus.SUCCEEDED and provider_job_id is None:
            raise OperationConflictError("剪映草稿成功结果必须绑定第三方任务 ID")
        target_status = {
            JianyingDraftStatus.SUCCEEDED: ExternalJobStatus.SUCCEEDED,
            JianyingDraftStatus.TIMEOUT: ExternalJobStatus.TIMEOUT,
            JianyingDraftStatus.FAILED: ExternalJobStatus.FAILED,
            JianyingDraftStatus.NOT_CONFIGURED: ExternalJobStatus.FAILED,
        }[normalized.status]
        result_type = f"jianying_{normalized.status.value}"
        request_hash = _hash_json(operation_request, field_name="剪映草稿终态请求")
        payload = {
            "result": normalized.model_dump(mode="json"),
            "storyboard_version_id": version_id,
            "scene_count": len(request["scenes"]),
            "source_scene_videos_artifact_ref": state.postproduction_state.generation_state.scene_videos_artifact_ref,
            "request_hash": request_hash,
        }
        result_hash = _terminal_hash(result_type, payload)
        current = await port.get(pending.job_id)
        if current is None:
            raise OperationConflictError("剪映草稿 Operation 不存在或已过期")
        _validate_pending_operation_identity(state, current)
        if current.status is ExternalJobStatus.CREATED:
            raise OperationConflictError("剪映草稿 Operation 尚未取得原子启动权，不得写入终态")
        if current.provider_job_id is not None and provider_job_id is not None and current.provider_job_id != provider_job_id:
            raise OperationConflictError("剪映草稿第三方任务 ID 发生并发漂移")
        finalizer = getattr(port, "finalize_video_operation")
        claim = await finalizer(
            expected=current,
            target_status=target_status,
            provider_job_id=provider_job_id,
            result_hash=result_hash,
            result_type=result_type,
            payload=payload,
            stage_version=state.stage_version,
        )
        terminal = _terminal_claim(claim)
        if terminal.result_hash != result_hash or terminal.result_type != result_type or terminal.payload != payload or terminal.stage_version != state.stage_version or terminal.job.status is not target_status:
            raise OperationConflictError("剪映草稿可信终态发生并发漂移")
        if provider_job_id is not None and terminal.job.provider_job_id != provider_job_id:
            raise OperationConflictError("剪映草稿可信终态绑定了其他第三方任务")
        return _apply_terminal_claim(state, terminal, now=now)

    async def generate_jianying_with_skill(
        self,
        state: VideoDeliveryWorkflowState,
        *,
        skill: JianyingDraftSkill,
        retry_failed: bool = False,
        project_name: str | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoDeliveryWorkflowState:
        """用 fake/真实 Skill 执行一次草稿生成，重复调用只恢复可信终态。"""

        port = self._atomic_port(operation_port)
        _validate_delivery_state(state)
        trusted_pending = await self._validate_trusted_state(state, port)
        if trusted_pending is not None and trusted_pending != state.pending_operation:
            state = replace(state, _pending_operation=trusted_pending)
        version_id = state.current_storyboard_version_id
        existing = state.jianying_draft_records.get(version_id)
        timestamp = _timestamp(now)
        if existing is not None:
            existing_result = _result_from_record(existing)
            if _is_valid_unexpired_success(existing_result, timestamp):
                return state
            if existing_result.status in {JianyingDraftStatus.FAILED, JianyingDraftStatus.TIMEOUT} and not retry_failed:
                return await self.start_jianying_draft(
                    state,
                    retry_failed=retry_failed,
                    project_name=project_name,
                    operation_port=port,
                    now=now,
                )
        if trusted_pending is None or trusted_pending.status is ExternalJobStatus.CREATED:
            try:
                capability = await skill.capability()
            except Exception:  # noqa: BLE001 - capability 失败不创建空 Operation，也不泄露异常内容
                return state
            if not capability.available:
                return state
        started = await self.start_jianying_draft(
            state,
            retry_failed=retry_failed,
            project_name=project_name,
            operation_port=port,
            now=now,
        )
        if started.pending_operation is None:
            return started
        start_claim = await self._claim_start(started, port, now=now)
        started = replace(started, _pending_operation=start_claim.job)
        if not start_claim.acquired:
            return await self._restore_terminal_state(started, port, now=now)
        try:
            request = JianyingDraftRequest.model_validate(started.pending_jianying_request)
            async with asyncio.timeout(self._timeout_seconds):
                result = await skill.generate(request)
        except TimeoutError:
            result = JianyingDraftResult(
                status=JianyingDraftStatus.TIMEOUT,
                message="剪映草稿生成超时，请重试。",
            )
        except Exception:  # noqa: BLE001 - Provider 边界只落公开失败，不保存异常内容
            result = JianyingDraftResult(
                status=JianyingDraftStatus.FAILED,
                message="剪映草稿生成失败，请稍后重试。",
            )
        return await self.record_jianying_result(
            started,
            result,
            operation_port=port,
            now=now,
        )

    async def record_jianying_download(
        self,
        state: VideoDeliveryWorkflowState,
        *,
        storyboard_version_id: str,
        download_url: str,
        downloaded_at: datetime,
        operation_port: OperationPort | None = None,
    ) -> VideoDeliveryWorkflowState:
        """记录某个草稿历史入口被点击，但不完成最终视频交付。"""

        port = self._atomic_port(operation_port)
        _validate_delivery_state(state)
        await self._validate_trusted_state(state, port)
        records = state.jianying_draft_records
        record = records.get(storyboard_version_id)
        if record is None:
            raise ValueError("指定剪映草稿历史版本不存在")
        result = _result_from_record(record)
        timestamp = _timestamp(downloaded_at)
        if not _is_valid_unexpired_success(result, timestamp):
            raise ValueError("只有未过期的成功剪映草稿才能下载")
        normalized_url = _https_url(download_url, "剪映草稿下载 URL")
        if normalized_url != str(result.download_url):
            raise ValueError("剪映草稿下载 URL 必须属于所选历史版本")
        if record.get("draftDownloadedAt"):
            if record.get("draftDownloadedUrl") != normalized_url:
                raise OperationConflictError("同一剪映草稿下载证据不能改写 URL")
            return state
        record["draftDownloadedAt"] = timestamp.isoformat()
        record["draftDownloadedUrl"] = normalized_url
        records[storyboard_version_id] = record
        updated = replace(
            state,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=_next_timestamp(state, timestamp),
            _jianying_draft_records_json=_canonical_json(records, field_name="剪映草稿历史"),
        )
        _validate_delivery_state(updated)
        return updated

    async def record_final_video_download(
        self,
        state: VideoDeliveryWorkflowState,
        *,
        download_url: str,
        downloaded_at: datetime,
        operation_port: OperationPort | None = None,
    ) -> VideoDeliveryWorkflowState:
        """只有当前合并成品视频的明确下载才能完成导出交付。"""

        port = self._atomic_port(operation_port)
        _validate_delivery_state(state)
        await self._validate_trusted_state(state, port)
        merged = state.postproduction_state.merged_video or {}
        merged_url = _https_url(merged.get("video_url"), "当前合并成品视频 URL")
        normalized_url = _https_url(download_url, "最终视频下载 URL")
        if normalized_url != merged_url:
            raise ValueError("只有当前合并成品视频下载才能完成导出交付")
        existing = state.final_video_delivery
        if existing is not None:
            if existing.get("deliveryDownloadedUrl") != normalized_url:
                raise OperationConflictError("当前视频交付证据不能继承或改写其他 URL")
            return state
        timestamp = _timestamp(downloaded_at)
        delivery = {
            "video_artifact_ref": state.postproduction_state.video_artifact_ref,
            "deliveryDownloadedAt": timestamp.isoformat(),
            "deliveryDownloadedUrl": normalized_url,
        }
        updated = replace(
            state,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=_next_timestamp(state, timestamp),
            _final_video_delivery_json=_canonical_json(delivery, field_name="最终视频下载证据"),
        )
        _validate_delivery_state(updated)
        return updated

    def to_workflow_record(self, state: VideoDeliveryWorkflowState) -> WorkflowRecord:
        """投影视频、剪映历史、最终下载与 pending Operation。"""

        _validate_delivery_state(state, allow_cancelled=True)
        base = VideoPostProductionWorkflowService().to_workflow_record(state.postproduction_state)
        refs = list(base.latest_artifact_refs)
        refs.extend(state.jianying_artifact_refs)
        if state.delivery_artifact_ref:
            refs.append(state.delivery_artifact_ref)
        return WorkflowRecord(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            kind=WorkflowKind.VIDEO,
            status=state.status,
            current_stage=state.current_stage.value,
            stage_version=state.stage_version,
            creation_contract_snapshot=state.postproduction_state.generation_state.source_scene_package.creation_contract,
            pending_external_job=state.pending_operation,
            latest_artifact_refs=list(dict.fromkeys(refs)),
            context_version=state.context_version,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    def to_artifact_projection(self, state: VideoDeliveryWorkflowState) -> dict[str, Any]:
        """生成与 Web 视频结果卡兼容的稳定 Artifact DTO。"""

        _validate_delivery_state(state, allow_cancelled=True)
        merged = state.postproduction_state.merged_video or {}
        generation = state.postproduction_state.generation_state
        records = {version_id: _public_history_record(record) for version_id, record in state.jianying_draft_records.items()}
        projection: dict[str, Any] = {
            "type": "video_result",
            "videoArtifactRef": state.postproduction_state.video_artifact_ref,
            "storyboardVersionId": state.current_storyboard_version_id,
            "videoScenePackages": _video_scene_packages_projection(
                generation.source_scene_package,
                scene_packages=generation.scene_packages,
            ),
            "mergedVideo": {
                "ok": True,
                "endpoint": merged["endpoint"],
                "merged_video_url": merged["video_url"],
                "task_id": merged["task_id"],
                "scene_videos": copy_json(merged.get("scene_videos") or []),
                "error": None,
                "message": "视频合并完成。",
                "quota_insufficient": False,
                "raw": copy_json(merged.get("raw") or {}),
            },
            "generatedSceneVideos": _generated_scene_videos_projection(generation),
            "videoAccepted": bool(state.postproduction_state.finalized_by_user),
            "jianyingDraftRecords": records,
        }
        current = records.get(state.current_storyboard_version_id)
        if current is not None:
            projection["jianyingDraft"] = current
        if state.pending_operation is not None:
            pending_request = copy_json(state.pending_jianying_request)
            pending_request["retry_failed"] = bool(state.pending_jianying_operation["retry_failed"])
            projection["pendingJianyingDraftJob"] = {
                "job_id": state.pending_operation.job_id,
                "conversation_id": state.conversation_id,
                "storyboard_version_id": state.current_storyboard_version_id,
                "request": pending_request,
            }
        delivery = state.final_video_delivery
        if delivery is not None:
            projection["deliveryDownloadedAt"] = delivery["deliveryDownloadedAt"]
            projection["deliveryDownloadedUrl"] = delivery["deliveryDownloadedUrl"]
        return projection

    def _port(self, explicit: OperationPort | None) -> OperationPort:
        port = explicit or self._operation_port
        if port is None:
            raise ValueError("剪映草稿和视频交付必须提供 OperationPort")
        return port

    def _atomic_port(self, explicit: OperationPort | None) -> OperationPort:
        port = self._port(explicit)
        required_methods = (
            "get_scene_operation_terminal_claim",
            "claim_video_operation_start",
            "mark_video_operation_call_started",
            "finalize_video_operation",
            "get_video_operation_terminal_claim",
        )
        if any(not callable(getattr(port, method, None)) for method in required_methods):
            raise ValueError("剪映草稿必须使用支持两阶段原子启动权和可查询原子终态的 OperationPort")
        return port

    async def _claim_start(
        self,
        state: VideoDeliveryWorkflowState,
        port: OperationPort,
        *,
        now: datetime | None,
    ) -> VideoOperationStartClaim:
        pending = state.pending_operation
        if pending is None:
            raise OperationConflictError("剪映草稿外部调用缺少 pending Operation")
        current = await port.get(pending.job_id)
        if current is None:
            raise OperationConflictError("剪映草稿 Operation 不存在或已过期")
        _validate_pending_operation_identity(state, current)
        owner = "video-delivery-service"
        timestamp = _timestamp(now)
        starter = getattr(port, "claim_video_operation_start")
        claim = await starter(
            expected=current,
            owner=owner,
            now=timestamp,
            lease_seconds=30,
        )
        if not isinstance(claim, VideoOperationStartClaim):
            raise OperationConflictError("剪映草稿原子启动 Port 返回了不受支持的 claim")
        _validate_pending_operation_identity(state, claim.job)
        if claim.acquired:
            if claim.job.status is not ExternalJobStatus.CREATED or claim.job.provider_job_id is not None or claim.job.lease_owner != owner or claim.job.lease_expires_at is None or claim.job.lease_expires_at <= timestamp:
                raise OperationConflictError("剪映草稿启动权必须绑定未过期的外调前租约")
            marker = getattr(port, "mark_video_operation_call_started")
            started = await marker(expected=claim.job, owner=owner, now=timestamp)
            _validate_pending_operation_identity(state, started)
            if started.status is not ExternalJobStatus.POLLING or started.provider_job_id is not None:
                raise OperationConflictError("剪映草稿外调标记必须进入未绑定第三方任务的 polling 状态")
            return VideoOperationStartClaim(job=started, acquired=True)
        return claim

    async def _restore_terminal_state(
        self,
        state: VideoDeliveryWorkflowState,
        port: OperationPort,
        *,
        now: datetime | None,
    ) -> VideoDeliveryWorkflowState:
        pending = state.pending_operation
        if pending is None:
            return state
        getter = getattr(port, "get_video_operation_terminal_claim")
        claim = await getter(job_id=pending.job_id)
        if claim is None:
            if pending.status in {
                ExternalJobStatus.SUCCEEDED,
                ExternalJobStatus.FAILED,
                ExternalJobStatus.TIMEOUT,
                ExternalJobStatus.EXPIRED,
            }:
                raise OperationConflictError("剪映草稿 Operation 已终结但可信 Repository 缺少业务终态")
            return state
        terminal = _terminal_claim(claim)
        _validate_pending_operation_identity(state, terminal.job)
        return _apply_terminal_claim(state, terminal, now=now)

    async def _validate_trusted_state(
        self,
        state: VideoDeliveryWorkflowState,
        port: OperationPort,
    ) -> ExternalJobRef | None:
        await _validate_trusted_postproduction(state.postproduction_state, port)
        getter = getattr(port, "get_video_operation_terminal_claim")
        for record in state.jianying_draft_records.values():
            checkpoint = _terminal_claim(record.get("terminal_claim"))
            trusted_value = await getter(job_id=checkpoint.job.job_id)
            if trusted_value is None:
                raise OperationConflictError("剪映草稿 checkpoint 终态在可信 Repository 中不存在")
            trusted = _terminal_claim(trusted_value)
            if trusted != checkpoint:
                raise OperationConflictError("剪映草稿 checkpoint 终态与可信 Repository 不一致")
        pending = state.pending_operation
        if pending is not None:
            trusted_pending = await port.get(pending.job_id)
            if trusted_pending is None:
                raise OperationConflictError("剪映草稿 pending Operation 在可信 Repository 中不存在")
            _validate_pending_operation_identity(state, trusted_pending)
            return trusted_pending
        return None


class VideoWebArtifactAdapter:
    """把三类视频权威状态适配为 Web 已冻结的 ChatArtifact DTO。"""

    def __init__(self, delivery_service: VideoDeliveryWorkflowService) -> None:
        self._delivery_service = delivery_service

    def project(
        self,
        state: (
            VideoScenePackageWorkflowState
            | VideoSceneGenerationWorkflowState
            | VideoDeliveryWorkflowState
        ),
    ) -> dict[str, Any]:
        """由单一入口生成 Web 可直接消费的场景或成片卡片。"""

        if isinstance(state, VideoScenePackageWorkflowState):
            projection = {
                "type": "video_scene_packages",
                "title": "视频分镜与场景素材",
                "description": "请审核分镜与场景素材，确认后生成分镜视频。",
                "actionLabel": "确认分镜",
                "videoScenePackages": _video_scene_packages_projection(
                    state.scene_package
                ),
            }
        elif isinstance(state, VideoSceneGenerationWorkflowState):
            projection = {
                "type": "video_scene_packages",
                "title": "视频分镜生成结果",
                "description": "请确认分镜视频，或修改后仅重生成受影响镜头。",
                "actionLabel": "确认分镜视频",
                "videoScenePackages": _video_scene_packages_projection(
                    state.source_scene_package,
                    scene_packages=state.scene_packages,
                ),
                "generatedSceneVideos": _generated_scene_videos_projection(state),
                "videoScenePackageEditedSceneIds": copy_json(
                    state.edited_scene_ids
                ),
            }
        elif isinstance(state, VideoDeliveryWorkflowState):
            projection = self._delivery_service.to_artifact_projection(state)
            projection.update(
                {
                    "title": "视频成片",
                    "description": (
                        "视频已由用户确认，可下载最终成片或生成剪映草稿。"
                        if state.postproduction_state.finalized_by_user
                        else "请确认最终成片，也可先生成剪映草稿或提出修改意见。"
                    ),
                    "actionLabel": "下载视频",
                }
            )
        else:
            raise TypeError("Web 视频 Artifact 不支持当前权威状态")
        return copy_json(projection)


def _video_scene_packages_projection(
    source: VideoScenePackageAuthoritySnapshot,
    *,
    scene_packages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成与 PrepareScenePackagesResponse 一一对应的稳定字段。"""

    return {
        "ok": True,
        "message": "视频分镜与场景素材已准备完成。",
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": source.target_duration_ms,
        "global_assets": copy_json(source.global_assets),
        "scene_packages": copy_json(
            source.scene_packages if scene_packages is None else scene_packages
        ),
        "creation_contract": copy_json(source.creation_contract),
    }


def _generated_scene_videos_projection(
    state: VideoSceneGenerationWorkflowState,
) -> dict[str, Any]:
    """生成与 GenerateSceneVideosResponse 一一对应的稳定字段。"""

    scene_videos = state.scene_videos
    failed_scenes = state.failed_scenes
    endpoints = sorted(
        {
            endpoint
            for item in scene_videos
            if isinstance((endpoint := item.get("endpoint")), str) and endpoint
        }
    )
    endpoint = (
        endpoints[0]
        if len(endpoints) == 1
        else "/api/video/mixed"
        if endpoints
        else "/api/video/reference-mode-video"
    )
    quota_insufficient = any(
        item.get("quota_insufficient") is True for item in failed_scenes
    )
    if quota_insufficient:
        message = "场景视频生成额度不足，请恢复额度后重试。"
    elif failed_scenes:
        message = "部分场景视频生成失败，请查看 failed_scenes。"
    else:
        message = "场景视频生成完成。"
    return {
        "ok": not failed_scenes,
        "endpoint": endpoint,
        "scene_videos": copy_json(scene_videos),
        "failed_scenes": copy_json(failed_scenes),
        "message": message,
        "quota_insufficient": quota_insufficient,
    }


def _validate_ready_postproduction(
    state: VideoPostProductionWorkflowState,
    *,
    allow_cancelled: bool = False,
) -> None:
    postproduction_module._validate_postproduction_state(state)
    allowed = {
        VideoPostProductionStage.VIDEO_REVIEW: WorkflowStatus.AWAITING_USER,
        VideoPostProductionStage.COMPLETED: WorkflowStatus.COMPLETED,
    }
    cancelled_review = (
        allow_cancelled
        and state.current_stage is VideoPostProductionStage.VIDEO_REVIEW
        and state.status is WorkflowStatus.CANCELLED
    )
    if allowed.get(state.current_stage) is not state.status and not cancelled_review:
        raise ValueError("剪映草稿只允许使用等待人工审核或已人工结束的合并视频")
    if state.pending_operation is not None or state.merged_video is None:
        raise ValueError("视频后处理仍有 pending Operation 或缺少合并结果")
    generation = state.generation_state
    expected_ids = {item["scene_id"] for item in generation.scene_packages}
    actual_ids = {item["scene_id"] for item in generation.scene_videos}
    if generation.pending_operations or generation.failed_scenes or generation.dirty_scene_ids or expected_ids != actual_ids or len(actual_ids) != len(generation.scene_packages):
        raise ValueError("剪映草稿只允许使用当前版本全部成功且未修改的分镜视频")


async def _validate_trusted_postproduction(
    state: VideoPostProductionWorkflowState,
    port: OperationPort,
) -> None:
    scene_getter = getattr(port, "get_scene_operation_terminal_claim")
    for entry in state.generation_state.terminal_claims:
        checkpoint = _scene_terminal_claim(entry)
        trusted_value = await scene_getter(job_id=checkpoint.job.job_id)
        if trusted_value is None or _scene_terminal_claim(trusted_value) != checkpoint:
            raise OperationConflictError("分镜终态与可信 Repository 不一致")
    getter = getattr(port, "get_video_operation_terminal_claim")
    for entry in state.terminal_claims:
        checkpoint = VideoOperationTerminalClaim(
            job=ExternalJobRef.model_validate(entry["job"]),
            result_hash=str(entry["result_hash"]),
            result_type=str(entry["result_type"]),
            payload=copy_json(entry["payload"]),
            stage_version=int(entry["stage_version"]),
        )
        trusted_value = await getter(job_id=checkpoint.job.job_id)
        if trusted_value is None or _terminal_claim(trusted_value) != checkpoint:
            raise OperationConflictError("视频后处理 checkpoint 与可信 Repository 不一致")


def _validate_delivery_state(
    state: VideoDeliveryWorkflowState,
    *,
    allow_cancelled: bool = False,
) -> None:
    _validate_ready_postproduction(
        state.postproduction_state,
        allow_cancelled=allow_cancelled,
    )
    if state.status is WorkflowStatus.CANCELLED and not allow_cancelled:
        raise ValueError("已取消的视频交付 Workflow 属于终态，不能继续推进")
    if state.workflow_id != state.postproduction_state.workflow_id:
        raise ValueError("交付状态 workflow_id 与视频后处理不一致")
    if state.conversation_id != state.postproduction_state.conversation_id:
        raise ValueError("交付状态 conversation_id 与视频后处理不一致")
    if state.current_stage is not state.postproduction_state.current_stage:
        raise ValueError("交付状态 current_stage 与视频后处理不一致")
    if state.status is not state.postproduction_state.status:
        raise ValueError("交付状态 status 与视频后处理不一致")
    if state.stage_version < state.postproduction_state.stage_version:
        raise ValueError("交付状态 stage_version 不能早于视频后处理")
    if state.context_version < state.postproduction_state.context_version:
        raise ValueError("交付状态 context_version 不能早于视频后处理")

    records = state.jianying_draft_records
    attempts = state.operation_attempts
    for version_id, record in records.items():
        result = _result_from_record(record)
        if result.storyboard_version_id != version_id:
            raise ValueError("剪映草稿历史键与结果版本不一致")
        if result.conversation_id != state.conversation_id:
            raise ValueError("剪映草稿历史绑定了其他对话")
        claim = _terminal_claim(record.get("terminal_claim"))
        if claim.job.workflow_id != state.workflow_id or claim.job.stage != "jianying_draft":
            raise ValueError("剪映草稿历史 Operation 不属于当前 Workflow")
        if result.job_id != claim.job.job_id:
            raise ValueError("剪映草稿历史 job_id 与可信终态不一致")
        payload = claim.payload
        if payload.get("result") != result.model_dump(mode="json"):
            raise ValueError("剪映草稿历史结果与终态载荷不一致")
        if payload.get("storyboard_version_id") != version_id:
            raise ValueError("剪映草稿终态版本不一致")
        if record.get("scene_count") != payload.get("scene_count"):
            raise ValueError("剪映草稿历史分镜数量与终态不一致")
        if record.get("source_scene_videos_artifact_ref") != payload.get("source_scene_videos_artifact_ref"):
            raise ValueError("剪映草稿历史来源 Artifact 与终态不一致")
        if claim.result_hash != _terminal_hash(claim.result_type, claim.payload):
            raise ValueError("剪映草稿历史终态摘要不一致")
        request_hash = payload.get("request_hash")
        if not isinstance(request_hash, str) or not request_hash:
            raise ValueError("剪映草稿历史终态缺少启动请求摘要")
        expected_key = _operation_key_from_hash(
            state.workflow_id,
            claim.stage_version,
            claim.job.attempt,
            request_hash,
        )
        if claim.job.idempotency_key != expected_key:
            raise ValueError("剪映草稿历史 Operation 与启动请求摘要不一致")
        recorded_attempt = attempts.get(version_id)
        if recorded_attempt is None or claim.job.attempt > recorded_attempt:
            raise ValueError("剪映草稿历史尝试次数与 Operation 不一致")
        expected_type = f"jianying_{result.status.value}"
        if claim.result_type != expected_type:
            raise ValueError("剪映草稿历史终态类型不一致")
        expected_status = {
            JianyingDraftStatus.SUCCEEDED: ExternalJobStatus.SUCCEEDED,
            JianyingDraftStatus.FAILED: ExternalJobStatus.FAILED,
            JianyingDraftStatus.TIMEOUT: ExternalJobStatus.TIMEOUT,
            JianyingDraftStatus.NOT_CONFIGURED: ExternalJobStatus.FAILED,
        }.get(result.status)
        if expected_status is None or claim.job.status is not expected_status:
            raise ValueError("剪映草稿历史 Operation 状态不一致")
        if result.status is JianyingDraftStatus.SUCCEEDED and claim.job.provider_job_id != result.provider_task_id:
            raise ValueError("剪映草稿成功历史的第三方任务 ID 不一致")
        downloaded_at = record.get("draftDownloadedAt")
        downloaded_url = record.get("draftDownloadedUrl")
        if bool(downloaded_at) != bool(downloaded_url):
            raise ValueError("剪映草稿下载证据必须同时包含时间和 URL")
        if downloaded_at:
            _timestamp(datetime.fromisoformat(str(downloaded_at)))
            if _https_url(downloaded_url, "剪映草稿历史下载 URL") != str(result.download_url):
                raise ValueError("剪映草稿历史下载 URL 与成功结果不一致")

    pending = state.pending_operation
    operation_request = state.pending_jianying_operation
    request = state.pending_jianying_request
    if (pending is None) != (operation_request is None):
        raise ValueError("剪映草稿 pending Operation 与请求快照必须同时存在")
    if pending is not None:
        if request is None:
            raise ValueError("剪映草稿 pending Operation 缺少稳定请求 DTO")
        _validate_pending_operation_identity(state, pending)
        version_id = str(request["storyboard_version_id"])
        if version_id != state.current_storyboard_version_id:
            raise ValueError("剪映草稿 pending 请求必须属于当前分镜版本")
        if state.operation_attempts.get(version_id) != pending.attempt:
            raise ValueError("剪映草稿 pending 尝试次数与 Operation 不一致")
        JianyingDraftRequest.model_validate(request)
        retry_failed = operation_request.get("retry_failed")
        if not isinstance(retry_failed, bool):
            raise ValueError("剪映草稿 pending 请求缺少显式重试标志")
        operation_stage_version = (
            state.stage_version - 1
            if state.status is WorkflowStatus.CANCELLED
            else state.stage_version
        )
        expected_key = _operation_key(
            state.workflow_id,
            operation_stage_version,
            pending.attempt,
            operation_request,
        )
        if pending.idempotency_key != expected_key:
            raise ValueError("剪映草稿 pending Operation 与请求摘要不一致")

    expected_attempt_versions = set(records)
    if pending is not None:
        expected_attempt_versions.add(state.current_storyboard_version_id)
    if set(attempts) != expected_attempt_versions or any(isinstance(attempt, bool) or attempt < 1 for attempt in attempts.values()):
        raise ValueError("剪映草稿尝试次数必须与历史和 pending 版本一一对应")
    pending_version = str(request["storyboard_version_id"]) if pending is not None else None
    for version_id, record in records.items():
        if version_id == pending_version:
            continue
        claim = _terminal_claim(record.get("terminal_claim"))
        if attempts[version_id] != claim.job.attempt:
            raise ValueError("剪映草稿已完成版本的尝试次数必须等于最新终态 Operation")

    delivery = state.final_video_delivery
    if delivery is not None:
        if delivery.get("video_artifact_ref") != state.postproduction_state.video_artifact_ref:
            raise ValueError("最终视频下载证据不能继承旧视频 Artifact")
        if delivery.get("deliveryDownloadedUrl") != state.postproduction_state.merged_video["video_url"]:
            raise ValueError("最终视频下载证据必须属于当前合并成品")
        _timestamp(datetime.fromisoformat(str(delivery.get("deliveryDownloadedAt"))))


def _current_jianying_scenes(
    state: VideoPostProductionWorkflowState,
) -> list[JianyingDraftScene]:
    _validate_ready_postproduction(state, allow_cancelled=True)
    return [
        JianyingDraftScene(
            scene_id=item["scene_id"],
            scene_index=item["scene_index"],
            task_id=item["task_id"],
            video_url=item["video_url"],
        )
        for item in sorted(state.generation_state.scene_videos, key=lambda item: item["scene_index"])
    ]


def _validate_pending_operation_identity(
    state: VideoDeliveryWorkflowState,
    operation: ExternalJobRef,
) -> None:
    pending = state.pending_operation
    request = state.pending_jianying_request
    if pending is None or request is None:
        raise OperationConflictError("剪映草稿状态缺少 pending Operation 或请求")
    identity = (
        operation.job_id,
        operation.workflow_id,
        operation.stage,
        operation.attempt,
        operation.idempotency_key,
    )
    expected_identity = (
        pending.job_id,
        state.workflow_id,
        "jianying_draft",
        pending.attempt,
        pending.idempotency_key,
    )
    if identity != expected_identity:
        raise OperationConflictError("剪映草稿 Operation 身份与当前请求不一致")


def _apply_terminal_claim(
    state: VideoDeliveryWorkflowState,
    claim: VideoOperationTerminalClaim,
    *,
    now: datetime | None,
) -> VideoDeliveryWorkflowState:
    request = state.pending_jianying_request
    if request is None or state.pending_operation is None:
        raise OperationConflictError("剪映草稿终态缺少 pending 请求")
    if claim.stage_version != state.stage_version:
        raise OperationConflictError("剪映草稿终态阶段版本不一致")
    _validate_pending_operation_identity(state, claim.job)
    payload = copy_json(claim.payload)
    if claim.result_hash != _terminal_hash(claim.result_type, payload):
        raise OperationConflictError("剪映草稿终态摘要不一致")
    result_payload = payload.get("result")
    if not isinstance(result_payload, Mapping):
        raise OperationConflictError("剪映草稿终态缺少稳定结果 DTO")
    result = JianyingDraftResult.model_validate(result_payload)
    version_id = str(request["storyboard_version_id"])
    operation_request = state.pending_jianying_operation
    if operation_request is None:
        raise OperationConflictError("剪映草稿终态缺少完整 Operation 请求")
    expected_request_hash = _hash_json(operation_request, field_name="剪映草稿终态请求")
    if payload.get("request_hash") != expected_request_hash:
        raise OperationConflictError("剪映草稿终态与启动请求摘要不一致")
    if result.storyboard_version_id != version_id or result.conversation_id != state.conversation_id:
        raise OperationConflictError("剪映草稿终态目标与当前请求不一致")
    if result.job_id != claim.job.job_id:
        raise OperationConflictError("剪映草稿终态 job_id 与 Operation 不一致")
    if payload.get("storyboard_version_id") != version_id:
        raise OperationConflictError("剪映草稿终态版本摘要不一致")
    if payload.get("scene_count") != len(request["scenes"]):
        raise OperationConflictError("剪映草稿终态分镜数量与启动请求不一致")
    if payload.get("source_scene_videos_artifact_ref") != state.postproduction_state.generation_state.scene_videos_artifact_ref:
        raise OperationConflictError("剪映草稿终态来源 Artifact 与当前分镜不一致")
    record = result.model_dump(mode="json")
    record.update(
        scene_count=payload.get("scene_count"),
        source_scene_videos_artifact_ref=payload.get("source_scene_videos_artifact_ref"),
        terminal_claim=_terminal_claim_payload(claim),
    )
    records = state.jianying_draft_records
    records[version_id] = record
    updated = replace(
        state,
        stage_version=state.stage_version + 1,
        context_version=state.context_version + 1,
        updated_at=_next_timestamp(state, now),
        _jianying_draft_records_json=_canonical_json(records, field_name="剪映草稿历史"),
        _pending_operation=None,
        _pending_jianying_operation_json="null",
    )
    _validate_delivery_state(updated)
    return updated


def _normalize_result(
    result: JianyingDraftResult,
    *,
    job_id: str,
    conversation_id: str,
    storyboard_version_id: str,
) -> JianyingDraftResult:
    status = result.status
    if status is JianyingDraftStatus.SUCCEEDED:
        message = "剪映草稿已生成"
    elif status is JianyingDraftStatus.TIMEOUT:
        message = "剪映草稿生成超时，请重试。"
    elif status is JianyingDraftStatus.NOT_CONFIGURED:
        message = "剪映草稿服务待接入"
    else:
        message = _safe_failure_message(result.message)
    payload = result.model_dump(mode="python")
    if status is not JianyingDraftStatus.SUCCEEDED:
        payload.update(
            provider_task_id=None,
            download_url=None,
            file_name=None,
            expire_at=None,
        )
    payload.update(
        job_id=job_id,
        conversation_id=conversation_id,
        storyboard_version_id=storyboard_version_id,
        message=message,
        file_name=_file_name(result.file_name) if status is JianyingDraftStatus.SUCCEEDED else None,
        provider_task_id=(_optional_text(result.provider_task_id) if status is JianyingDraftStatus.SUCCEEDED else None),
    )
    return JianyingDraftResult.model_validate(payload)


def _result_from_record(record: Mapping[str, Any]) -> JianyingDraftResult:
    payload = {field_name: record.get(field_name) for field_name in _RESULT_FIELDS}
    return JianyingDraftResult.model_validate(payload)


def _public_history_record(record: Mapping[str, Any]) -> dict[str, Any]:
    public = {field_name: copy_json(record.get(field_name)) for field_name in _RESULT_FIELDS}
    public["scene_count"] = record.get("scene_count")
    if record.get("draftDownloadedAt"):
        public["draftDownloadedAt"] = record["draftDownloadedAt"]
        public["draftDownloadedUrl"] = record["draftDownloadedUrl"]
    return public


def _terminal_claim(value: Any) -> VideoOperationTerminalClaim:
    if isinstance(value, VideoOperationTerminalClaim):
        return VideoOperationTerminalClaim(
            job=value.job.model_copy(deep=True),
            result_hash=value.result_hash,
            result_type=value.result_type,
            payload=copy_json(value.payload),
            stage_version=value.stage_version,
        )
    job = getattr(value, "job", None)
    result_hash = getattr(value, "result_hash", None)
    result_type = getattr(value, "result_type", None)
    payload = getattr(value, "payload", None)
    stage_version = getattr(value, "stage_version", None)
    if isinstance(value, Mapping):
        job = value.get("job")
        result_hash = value.get("result_hash")
        result_type = value.get("result_type")
        payload = value.get("payload")
        stage_version = value.get("stage_version")
    if not isinstance(result_hash, str) or not isinstance(result_type, str) or not isinstance(payload, Mapping) or not isinstance(stage_version, int):
        raise OperationConflictError("剪映草稿原子终态 Port 返回了不受支持的结果")
    return VideoOperationTerminalClaim(
        job=job if isinstance(job, ExternalJobRef) else ExternalJobRef.model_validate(job),
        result_hash=result_hash,
        result_type=result_type,
        payload=copy_json(payload),
        stage_version=stage_version,
    )


def _scene_terminal_claim(value: Any) -> VideoSceneOperationTerminalClaim:
    if isinstance(value, VideoSceneOperationTerminalClaim):
        return VideoSceneOperationTerminalClaim(
            job=value.job.model_copy(deep=True),
            result_hash=value.result_hash,
        )
    job = getattr(value, "job", None)
    result_hash = getattr(value, "result_hash", None)
    if isinstance(value, Mapping):
        job = value.get("job")
        result_hash = value.get("result_hash")
    if not isinstance(result_hash, str):
        raise OperationConflictError("分镜原子终态 Port 返回了不受支持的结果")
    return VideoSceneOperationTerminalClaim(
        job=job if isinstance(job, ExternalJobRef) else ExternalJobRef.model_validate(job),
        result_hash=result_hash,
    )


def _terminal_claim_payload(claim: VideoOperationTerminalClaim) -> dict[str, Any]:
    return {
        "job": claim.job.model_dump(mode="json"),
        "result_hash": claim.result_hash,
        "result_type": claim.result_type,
        "payload": copy_json(claim.payload),
        "stage_version": claim.stage_version,
    }


def _terminal_hash(result_type: str, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(
            {"result_type": result_type, "payload": payload},
            field_name="剪映草稿终态",
        ).encode("utf-8")
    ).hexdigest()


def _operation_key(
    workflow_id: str,
    stage_version: int,
    attempt: int,
    payload: Mapping[str, Any],
) -> str:
    digest = _hash_json(payload, field_name="剪映草稿 Operation 请求")
    return _operation_key_from_hash(workflow_id, stage_version, attempt, digest)


def _operation_key_from_hash(
    workflow_id: str,
    stage_version: int,
    attempt: int,
    request_hash: str,
) -> str:
    return f"video:{workflow_id}:jianying_draft:v{stage_version}:a{attempt}:{request_hash}"


def _is_valid_unexpired_success(result: JianyingDraftResult, now: datetime) -> bool:
    if result.status is not JianyingDraftStatus.SUCCEEDED or result.download_url is None:
        return False
    if result.expire_at is None:
        return True
    expire_at = _timestamp(result.expire_at)
    return now < expire_at


def _canonical_json(value: Any, *, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是可持久化 JSON") from exc


def _hash_json(value: Any, *, field_name: str) -> str:
    return hashlib.sha256(_canonical_json(value, field_name=field_name).encode("utf-8")).hexdigest()


def copy_json(value: Any) -> Any:
    return json.loads(_canonical_json(value, field_name="交付投影"))


def _https_url(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("https://"):
        raise ValueError(f"{field_name}必须使用 HTTPS")
    if text != value:
        raise ValueError(f"{field_name}必须使用无首尾空白的规范 URL")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _project_name(value: str | None) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    text = _URL_PATTERN.sub("[链接已隐藏]", text)
    if _SENSITIVE_PATTERN.search(text) or any(character in text for character in "\r\n\t"):
        return "PixelFlow 视频草稿"
    return text[:120]


def _file_name(value: str | None) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if any(character in text for character in "/\\\r\n\t") or _SENSITIVE_PATTERN.search(text):
        return "jianying-draft.zip"
    return text[:160]


def _safe_failure_message(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 220 or any(character in candidate for character in "\r\n\t") or not candidate.startswith(_PUBLIC_BUSINESS_FAILURE_PREFIXES) or _SENSITIVE_PATTERN.search(candidate):
        return "剪映草稿生成失败，请稍后重试。"
    return candidate


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return timestamp


def _next_timestamp(
    state: VideoDeliveryWorkflowState,
    value: datetime | None,
) -> datetime:
    timestamp = _timestamp(value)
    if timestamp < state.updated_at:
        raise ValueError("Workflow 更新时间不能倒退")
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
