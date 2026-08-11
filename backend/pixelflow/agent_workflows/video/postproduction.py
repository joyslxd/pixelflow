"""视频合并、QAAgent 质检和人工结束的 Workflow Service。

DEPRECATED (V2.1 批次 E): 未被 Gateway / video_agent 生产路径引用；见包级说明。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from pixelflow.agent_runtime.contracts import (
    ExternalJobRef,
    ExternalJobStatus,
    OperationRequest,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.ports import OperationConflictError, OperationPort
from pixelflow.skills.base import (
    GenerationResult,
    VideoQualityReviewResult,
    is_quota_insufficient,
    quota_resume_message,
)

from .video_generation import (
    VideoSceneGenerationStage,
    VideoSceneGenerationWorkflowService,
    VideoSceneGenerationWorkflowState,
)

_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", flags=re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\bbearer\s+[^\s,;]+", flags=re.IGNORECASE)
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"\b(?:authorization|token|api[_-]?key|apikey|secret|password|credential)\b\s*(?:[:=]\s*)?[^\s,;]+",
    flags=re.IGNORECASE,
)


class VideoPostProductionStage(StrEnum):
    """合并、质检、人工确认和结束阶段。"""

    MERGE_VIDEO = "merge_video"
    QUALITY_REVIEW = "quality_review"
    VIDEO_REVIEW = "video_review"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class VideoQualityReviewWorkflowResult:
    """把第三方 QC 结果收敛成可持久化的稳定 DTO。"""

    ok: bool
    passed: bool = False
    summary_markdown: str = ""
    quality_report_markdown: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    affected_scene_ids: list[str] = field(default_factory=list)
    revision_prompt: str = ""
    task_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_skill(cls, result: VideoQualityReviewResult) -> VideoQualityReviewWorkflowResult:
        raw = _safe_json_object(result.raw, field_name="视频质检原始结果")
        passed_value = raw.get("passed")
        passed = bool(passed_value) if isinstance(passed_value, bool) else bool(result.ok and not result.issues)
        return cls(
            ok=bool(result.ok),
            passed=passed,
            summary_markdown=_safe_text(result.summary_markdown),
            quality_report_markdown=_safe_text(result.quality_report_markdown),
            issues=_safe_json_list(result.issues, field_name="视频质检 issues"),
            affected_scene_ids=_string_list(result.affected_scene_ids, field_name="视频质检 affected_scene_ids"),
            revision_prompt=_sanitize_text(result.revision_prompt or ""),
            task_id=_optional_text(result.task_id),
            error=_sanitize_text(result.error) if result.error else None,
            raw=raw,
        )


@dataclass(frozen=True, slots=True)
class VideoOperationStartClaim:
    """标识当前调用方是否取得唯一的供应商启动权。"""

    job: ExternalJobRef
    acquired: bool


@dataclass(frozen=True, slots=True)
class VideoOperationTerminalClaim:
    """记录可信 Repository 中的视频终态、结果类型和安全载荷。"""

    job: ExternalJobRef
    result_hash: str
    result_type: str
    payload: dict[str, Any]
    stage_version: int


class VideoPostProductionAtomicOperationPort(OperationPort, Protocol):
    """要求 Operation Port 原子管理外调启动权与可查询终态。"""

    async def claim_video_operation_start(
        self,
        *,
        expected: ExternalJobRef,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> VideoOperationStartClaim: ...

    async def mark_video_operation_call_started(
        self,
        *,
        expected: ExternalJobRef,
        owner: str,
        now: datetime,
    ) -> ExternalJobRef: ...

    async def finalize_video_operation(
        self,
        *,
        expected: ExternalJobRef,
        target_status: ExternalJobStatus,
        provider_job_id: str | None,
        result_hash: str,
        result_type: str,
        payload: Mapping[str, Any],
        stage_version: int,
    ) -> VideoOperationTerminalClaim: ...

    async def get_video_operation_terminal_claim(
        self,
        *,
        job_id: str,
    ) -> VideoOperationTerminalClaim | None: ...


class VideoMergeSkillPort(Protocol):
    """视频合并 Client 的最小依赖。"""

    async def merge_videos(self, **kwargs: Any) -> GenerationResult: ...


class VideoQualityReviewSkillPort(Protocol):
    """QAAgent QC Client 的最小依赖。"""

    async def review_video_quality(self, **kwargs: Any) -> VideoQualityReviewResult: ...


@dataclass(frozen=True, slots=True)
class VideoPostProductionWorkflowState:
    """保存合并、QC 和人工结束所需的权威快照。"""

    workflow_id: str
    conversation_id: str
    current_stage: VideoPostProductionStage
    status: WorkflowStatus
    stage_version: int
    context_version: int
    created_at: datetime
    updated_at: datetime
    _generation_state: VideoSceneGenerationWorkflowState = field(repr=False)
    _merge_request_json: str = field(repr=False)
    _merged_video_json: str = field(repr=False)
    _merge_error_json: str = field(repr=False)
    _quality_review_json: str = field(repr=False)
    _quality_feedback_json: str = field(repr=False)
    _pending_operation: ExternalJobRef | None = field(repr=False)
    _terminal_result_hash: str | None = field(repr=False)
    _terminal_claims_json: str = field(repr=False)
    _operation_attempts_json: str = field(repr=False)
    finalized_by_user: bool = False

    @property
    def generation_state(self) -> VideoSceneGenerationWorkflowState:
        return self._generation_state

    @property
    def merge_request(self) -> dict[str, Any]:
        return json.loads(self._merge_request_json)

    @property
    def merged_video(self) -> dict[str, Any] | None:
        value = json.loads(self._merged_video_json)
        return value if isinstance(value, dict) else None

    @property
    def merge_error(self) -> dict[str, Any] | None:
        value = json.loads(self._merge_error_json)
        return value if isinstance(value, dict) else None

    @property
    def quality_review(self) -> dict[str, Any] | None:
        value = json.loads(self._quality_review_json)
        return value if isinstance(value, dict) else None

    @property
    def quality_feedback(self) -> str | None:
        value = json.loads(self._quality_feedback_json)
        return value if isinstance(value, str) else None

    @property
    def pending_operation(self) -> ExternalJobRef | None:
        return self._pending_operation.model_copy(deep=True) if self._pending_operation else None

    @property
    def operation_attempts(self) -> dict[str, int]:
        value = json.loads(self._operation_attempts_json)
        return {str(key): int(item) for key, item in value.items()}

    @property
    def terminal_claims(self) -> list[dict[str, Any]]:
        value = json.loads(self._terminal_claims_json)
        return [dict(item) for item in value]

    @property
    def quality_artifact_ref(self) -> str | None:
        if self.quality_review is None:
            return None
        digest = hashlib.sha256(
            _canonical_json(
                {
                    "workflow_id": self.workflow_id,
                    "merged_video": self.merged_video,
                    "quality_review": self.quality_review,
                },
                field_name="视频质检 Artifact",
            ).encode("utf-8")
        ).hexdigest()
        return f"artifact:video-quality-review:{self.workflow_id}:{digest[:16]}"

    @property
    def video_artifact_ref(self) -> str | None:
        merged = self.merged_video
        if merged is None:
            return None
        digest = hashlib.sha256(_canonical_json(merged, field_name="合并视频 Artifact").encode("utf-8")).hexdigest()
        return f"artifact:video-merged:{self.workflow_id}:{digest[:16]}"


class VideoPostProductionWorkflowService:
    """类比 Java Application Service，编排合并、QC 和人工确认。"""

    def __init__(self, operation_port: OperationPort | None = None) -> None:
        self._operation_port = operation_port

    async def start_merge(
        self,
        generation_state: VideoSceneGenerationWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """只为当前完整成功版本领取一次合并 Operation。"""

        _validate_generation_ready(generation_state)
        timestamp = _timestamp(now)
        if timestamp < generation_state.updated_at:
            raise ValueError("Workflow 更新时间不能早于分镜生成结果")
        request = _build_merge_request(generation_state)
        port = self._port(operation_port)
        attempt = 1
        operation = await port.claim(
            OperationRequest(
                workflow_id=generation_state.workflow_id,
                stage=VideoPostProductionStage.MERGE_VIDEO.value,
                stage_version=generation_state.stage_version + 1,
                attempt=attempt,
                request_hash=_hash_json(request, field_name="合并视频请求"),
                idempotency_key=_operation_key(
                    generation_state.workflow_id,
                    VideoPostProductionStage.MERGE_VIDEO,
                    generation_state.stage_version + 1,
                    attempt,
                    request,
                ),
            )
        )
        return _new_state(
            generation_state,
            current_stage=VideoPostProductionStage.MERGE_VIDEO,
            status=WorkflowStatus.RUNNING,
            stage_version=generation_state.stage_version + 1,
            context_version=generation_state.context_version + 1,
            updated_at=timestamp,
            merge_request=request,
            pending_operation=operation,
            operation_attempts={"merge": attempt},
        )

    async def resume(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """刷新只查询原 Operation，不重新领取或启动供应商任务。"""

        _validate_postproduction_state(state)
        if state.status is WorkflowStatus.CANCELLED:
            raise ValueError("已取消的视频后处理 Workflow 属于终态，不能恢复 Operation")
        if state.pending_operation is None:
            return state
        port = self._port(operation_port)
        existing = await port.get(state.pending_operation.job_id)
        if existing is None:
            raise ValueError("视频合并或质检 Operation 不存在或已过期，不得自动重新启动")
        _validate_operation_identity(state, existing)
        if existing.status in {
            ExternalJobStatus.SUCCEEDED,
            ExternalJobStatus.FAILED,
            ExternalJobStatus.TIMEOUT,
            ExternalJobStatus.EXPIRED,
        }:
            return await self._restore_terminal_state(state, self._atomic_port(port), now=now)
        timestamp = _timestamp(now) if now is not None else state.updated_at
        if timestamp < state.updated_at:
            raise ValueError("Workflow 更新时间不能早于当前状态")
        return replace(state, updated_at=timestamp, _pending_operation=existing)

    def cancel(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """取消后处理，同时保留合并、质检及 pending Operation 权威事实。"""

        _validate_postproduction_state(state)
        if state.status in {WorkflowStatus.CANCELLED, WorkflowStatus.COMPLETED}:
            raise ValueError("已取消或已完成的视频后处理 Workflow 属于终态，不能再次取消")
        result = replace(
            state,
            status=WorkflowStatus.CANCELLED,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=_cancellation_timestamp(state.updated_at, now),
        )
        _validate_postproduction_state(result)
        return result

    async def record_merge_success(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        merged_video_url: str,
        provider_job_id: str | None,
        raw: Mapping[str, Any] | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """原子绑定合并结果，并把流程交给视频人工审核阶段。"""

        _validate_stage(state, VideoPostProductionStage.MERGE_VIDEO)
        url = _https_url(merged_video_url, "合并视频 URL")
        request = state.merge_request
        resolved_provider_id = _optional_text(provider_job_id)
        if resolved_provider_id is None and len(request["video_urls"]) == 1:
            resolved_provider_id = f"passthrough:{_hash_text(url)[:16]}"
        if resolved_provider_id is None:
            raise OperationConflictError("合并成功必须绑定供应商任务 ID")
        payload = {
            "video_url": url,
            "task_id": resolved_provider_id,
            "endpoint": "/api/video/merge",
            "scene_videos": request["scene_videos"],
            "raw": _safe_json_object(raw or {}, field_name="合并视频原始结果"),
        }
        claim = await self._finalize(
            state,
            target_status=ExternalJobStatus.SUCCEEDED,
            provider_job_id=resolved_provider_id,
            result_hash=_terminal_hash("merge_succeeded", payload),
            result_type="merge_succeeded",
            payload=payload,
            stage_version=state.stage_version,
            operation_port=operation_port,
        )
        return _apply_terminal_claim(state, claim, now=now)

    async def record_merge_failure(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        error: str,
        attempts: int,
        retryable: bool = True,
        quota_insufficient: bool = False,
        raw: Mapping[str, Any] | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """保留失败原因，额度不足时暂停而不是继续启动后续调用。"""

        _validate_stage(state, VideoPostProductionStage.MERGE_VIDEO)
        if attempts < 1:
            raise ValueError("合并 attempts 必须为正整数")
        normalized_raw = _safe_json_object(raw or {}, field_name="合并失败原始结果")
        quota = bool(quota_insufficient or is_quota_insufficient(error) or is_quota_insufficient(normalized_raw))
        if quota:
            retryable = True
        safe_error = _sanitize_text(error)
        failure = {
            "error": safe_error,
            "attempts": attempts,
            "retryable": bool(retryable),
            "quota_insufficient": quota,
            "message": _sanitize_text(quota_resume_message(safe_error)) if quota else safe_error,
            "raw": normalized_raw,
        }
        result_hash = _terminal_hash("merge_failed", failure)
        claim = await self._finalize(
            state,
            target_status=ExternalJobStatus.FAILED,
            provider_job_id=None,
            result_hash=result_hash,
            result_type="merge_failed",
            payload=failure,
            stage_version=state.stage_version,
            operation_port=operation_port,
        )
        return _apply_terminal_claim(state, claim, now=now)

    async def retry_merge(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """仅在用户明确重试时领取新的合并 Operation。"""

        _validate_stage(state, VideoPostProductionStage.MERGE_VIDEO, allow_status={WorkflowStatus.PAUSED_QUOTA, WorkflowStatus.FAILED})
        await self._validate_trusted_terminal_claims(state, self._atomic_port(operation_port))
        if not state.merge_error or state.merge_error.get("retryable") is not True:
            raise ValueError("当前合并失败不可直接重试")
        request = state.merge_request
        attempt = state.operation_attempts.get("merge", 1) + 1
        port = self._port(operation_port)
        operation = await port.claim(
            OperationRequest(
                workflow_id=state.workflow_id,
                stage=VideoPostProductionStage.MERGE_VIDEO.value,
                stage_version=state.stage_version + 1,
                attempt=attempt,
                request_hash=_hash_json(request, field_name="重试合并请求"),
                idempotency_key=_operation_key(state.workflow_id, VideoPostProductionStage.MERGE_VIDEO, state.stage_version + 1, attempt, request),
            )
        )
        return replace(
            state,
            status=WorkflowStatus.RUNNING,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=_next_timestamp(state, now),
            _pending_operation=operation,
            _operation_attempts_json=_canonical_json({"merge": attempt}, field_name="合并重试次数"),
        )

    async def start_quality_review(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        user_feedback: str | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """针对当前合并版本领取一次 QAAgent QC Operation。"""

        _validate_postproduction_state(state)
        port = self._atomic_port(operation_port)
        await self._validate_trusted_terminal_claims(state, port)
        allowed_start = state.current_stage is VideoPostProductionStage.VIDEO_REVIEW and state.status is WorkflowStatus.AWAITING_USER
        allowed_retry = state.current_stage is VideoPostProductionStage.QUALITY_REVIEW and state.status in {
            WorkflowStatus.PAUSED_QUOTA,
            WorkflowStatus.FAILED,
        }
        if not allowed_start and not allowed_retry:
            raise ValueError("只有视频人工审核或可重试的质检失败才能启动 QAAgent QC")
        if state.merged_video is None:
            raise ValueError("没有合并视频不能开始质检")
        if allowed_start:
            normalized_feedback = _sanitize_text(_optional_text(user_feedback) or "")
            if not normalized_feedback:
                raise ValueError("提出视频修改时必须提供用户意见")
        else:
            normalized_feedback = state.quality_feedback or ""
            if not normalized_feedback:
                raise ValueError("质检重试必须保留首次用户意见")
            if user_feedback is not None and _sanitize_text(_optional_text(user_feedback) or "") != normalized_feedback:
                raise OperationConflictError("质检重试不得改写首次用户意见")
        staged = replace(
            state,
            _quality_feedback_json=_canonical_json(normalized_feedback, field_name="视频质检用户意见"),
        )
        payload = _build_quality_request(staged)
        attempt = state.operation_attempts.get("quality", 0) + 1
        operation = await port.claim(
            OperationRequest(
                workflow_id=state.workflow_id,
                stage=VideoPostProductionStage.QUALITY_REVIEW.value,
                stage_version=state.stage_version + 1,
                attempt=attempt,
                request_hash=_hash_json(payload, field_name="视频质检请求"),
                idempotency_key=_operation_key(state.workflow_id, VideoPostProductionStage.QUALITY_REVIEW, state.stage_version + 1, attempt, payload),
            )
        )
        return replace(
            staged,
            current_stage=VideoPostProductionStage.QUALITY_REVIEW,
            status=WorkflowStatus.RUNNING,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=_next_timestamp(state, now),
            _pending_operation=operation,
            _operation_attempts_json=_canonical_json({**state.operation_attempts, "quality": attempt}, field_name="质检尝试次数"),
        )

    async def record_quality_success(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        result: VideoQualityReviewWorkflowResult | VideoQualityReviewResult,
        provider_job_id: str | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """保存唯一 QC 结论；QC 未通过时仍等待用户提出修改意见。"""

        _validate_stage(state, VideoPostProductionStage.QUALITY_REVIEW)
        normalized = VideoQualityReviewWorkflowResult.from_skill(result) if isinstance(result, VideoQualityReviewResult) else result
        if not normalized.ok:
            raise ValueError("QC 失败必须通过 record_quality_failure 写入")
        scene_ids = {item["scene_id"] for item in state.generation_state.scene_packages}
        affected = _unique_strings(normalized.affected_scene_ids)
        if set(affected).difference(scene_ids):
            raise ValueError("QC affected_scene_ids 必须属于当前分镜")
        payload = {
            "ok": True,
            "passed": bool(normalized.passed),
            "summary_markdown": _sanitize_text(normalized.summary_markdown),
            "quality_report_markdown": _sanitize_text(normalized.quality_report_markdown),
            "issues": _safe_json_list(normalized.issues, field_name="视频质检 issues"),
            "affected_scene_ids": affected,
            "revision_prompt": _sanitize_text(normalized.revision_prompt),
            "task_id": _optional_text(provider_job_id or normalized.task_id),
            "raw": _safe_json_object(normalized.raw, field_name="视频质检结果"),
        }
        resolved_provider_id = payload["task_id"]
        if not resolved_provider_id:
            raise OperationConflictError("QC 成功必须绑定供应商任务 ID")
        result_hash = _terminal_hash("quality_succeeded", payload)
        claim = await self._finalize(
            state,
            target_status=ExternalJobStatus.SUCCEEDED,
            provider_job_id=resolved_provider_id,
            result_hash=result_hash,
            result_type="quality_succeeded",
            payload=payload,
            stage_version=state.stage_version,
            operation_port=operation_port,
        )
        return _apply_terminal_claim(state, claim, now=now)

    async def record_quality_failure(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        error: str,
        attempts: int,
        retryable: bool = True,
        quota_insufficient: bool = False,
        raw: Mapping[str, Any] | None = None,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """QC 失败保留安全摘要；额度不足时暂停当前质检阶段。"""

        _validate_stage(state, VideoPostProductionStage.QUALITY_REVIEW)
        if attempts < 1:
            raise ValueError("质检 attempts 必须为正整数")
        normalized_raw = _safe_json_object(raw or {}, field_name="视频质检失败原始结果")
        quota = bool(quota_insufficient or is_quota_insufficient(error) or is_quota_insufficient(normalized_raw))
        safe_error = _sanitize_text(error)
        failure = {
            "ok": False,
            "error": safe_error,
            "attempts": attempts,
            "retryable": bool(retryable or quota),
            "quota_insufficient": quota,
            "message": _sanitize_text(quota_resume_message(safe_error)) if quota else safe_error,
            "raw": normalized_raw,
        }
        result_hash = _terminal_hash("quality_failed", failure)
        claim = await self._finalize(
            state,
            target_status=ExternalJobStatus.FAILED,
            provider_job_id=None,
            result_hash=result_hash,
            result_type="quality_failed",
            payload=failure,
            stage_version=state.stage_version,
            operation_port=operation_port,
        )
        return _apply_terminal_claim(state, claim, now=now)

    async def retry_quality_review(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """仅在用户明确重试时重新领取质检 Operation。"""

        _validate_stage(state, VideoPostProductionStage.QUALITY_REVIEW, allow_status={WorkflowStatus.PAUSED_QUOTA, WorkflowStatus.FAILED})
        await self._validate_trusted_terminal_claims(state, self._atomic_port(operation_port))
        error = state.quality_review or {}
        if error.get("retryable") is not True:
            raise ValueError("当前质检失败不可直接重试")
        return await self.start_quality_review(state, operation_port=operation_port, now=now)

    async def apply_user_revision(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        scene_patches: Mapping[str, Mapping[str, Any]],
        generation_service: VideoSceneGenerationWorkflowService,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoSceneGenerationWorkflowState:
        """把用户意见转换为单镜授权修改，并交回 M11.3 只重生 dirty 分镜。"""

        _validate_postproduction_state(state)
        await self._validate_trusted_terminal_claims(state, self._atomic_port(operation_port))
        allowed_statuses = {
            VideoPostProductionStage.VIDEO_REVIEW: {WorkflowStatus.AWAITING_USER},
            VideoPostProductionStage.QUALITY_REVIEW: {WorkflowStatus.FAILED, WorkflowStatus.PAUSED_QUOTA},
        }
        if state.current_stage not in allowed_statuses or state.status not in allowed_statuses[state.current_stage]:
            raise ValueError("只有视频人工审核或质检失败后才能按用户意见修改分镜")
        if state.current_stage is VideoPostProductionStage.VIDEO_REVIEW and state.quality_review is None:
            raise ValueError("提出修改后必须先完成 QAAgent QC，不能直接绕过质检")
        if not isinstance(scene_patches, Mapping) or not scene_patches:
            raise ValueError("人工修改必须至少指定一个分镜")
        allowed = {item["scene_id"] for item in state.generation_state.scene_packages}
        unknown = set(scene_patches).difference(allowed)
        if unknown:
            raise ValueError("用户修改只能针对当前版本已有分镜")
        generation_state = replace(
            state.generation_state,
            stage_version=state.stage_version,
            context_version=state.context_version,
            updated_at=state.updated_at,
        )
        for scene_id, patch in scene_patches.items():
            generation_state = generation_service.modify_scene(generation_state, scene_id=scene_id, patch=patch, now=now)
        return await generation_service.regenerate_modified_scenes(generation_state, operation_port=operation_port, now=now)

    async def finish(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """只接受用户明确确认，不根据超时自动结束视频。"""

        _validate_postproduction_state(state)
        await self._validate_trusted_terminal_claims(state, self._atomic_port(operation_port))
        if state.current_stage is not VideoPostProductionStage.VIDEO_REVIEW:
            raise ValueError("视频必须回到人工审核，并由用户人工确认后才能结束")
        if state.status is not WorkflowStatus.AWAITING_USER or state.merged_video is None:
            raise ValueError("视频必须保留合并结果并由人工确认后才能结束")
        timestamp = _next_timestamp(state, now)
        return replace(
            state,
            current_stage=VideoPostProductionStage.COMPLETED,
            status=WorkflowStatus.COMPLETED,
            stage_version=state.stage_version + 1,
            context_version=state.context_version + 1,
            updated_at=timestamp,
            finalized_by_user=True,
        )

    def to_workflow_record(self, state: VideoPostProductionWorkflowState) -> WorkflowRecord:
        """投影稳定的 WorkflowRecord 和视频/质检 Artifact 引用。"""

        _validate_postproduction_state(state)
        refs = [state.generation_state.source_scene_package_artifact_ref, state.generation_state.scene_videos_artifact_ref]
        if state.video_artifact_ref:
            refs.append(state.video_artifact_ref)
        if state.quality_artifact_ref:
            refs.append(state.quality_artifact_ref)
        return WorkflowRecord(
            workflow_id=state.workflow_id,
            conversation_id=state.conversation_id,
            kind=WorkflowKind.VIDEO,
            status=state.status,
            current_stage=state.current_stage.value,
            stage_version=state.stage_version,
            creation_contract_snapshot=state.generation_state.source_scene_package.creation_contract,
            pending_external_job=state.pending_operation,
            latest_artifact_refs=refs,
            context_version=state.context_version,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    async def merge_with_skill(
        self,
        generation_state: VideoSceneGenerationWorkflowState,
        *,
        skill: VideoMergeSkillPort,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """领取合并 Operation 后调用 Client，异常结果统一回写为可恢复状态。"""

        port = self._atomic_port(operation_port)
        state = await self.start_merge(generation_state, operation_port=port, now=now)
        start_claim = await self._claim_start(state, port, now=now)
        state = replace(state, _pending_operation=start_claim.job)
        if not start_claim.acquired:
            return await self._restore_terminal_state(state, port, now=now)
        if len(state.merge_request["video_urls"]) == 1:
            return await self.record_merge_success(
                state,
                merged_video_url=state.merge_request["video_urls"][0],
                provider_job_id=None,
                operation_port=port,
                now=now,
            )
        try:
            result = await skill.merge_videos(
                video_urls=state.merge_request["video_urls"],
                duration=state.merge_request["duration"],
                size=state.merge_request["size"],
                model=state.merge_request["model"],
            )
        except Exception as exc:  # noqa: BLE001 - 统一把 Client 异常落为可轮询状态
            return await self.record_merge_failure(state, error=str(exc), attempts=1, raw={}, operation_port=port, now=now)
        if result.ok and result.url:
            return await self.record_merge_success(
                state,
                merged_video_url=result.url,
                provider_job_id=result.task_id,
                raw=result.raw,
                operation_port=port,
                now=now,
            )
        return await self.record_merge_failure(
            state,
            error=result.error or "视频合并失败",
            attempts=1,
            quota_insufficient=is_quota_insufficient(result.raw) or is_quota_insufficient(result.error),
            raw=result.raw,
            operation_port=port,
            now=now,
        )

    async def quality_review_with_skill(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        user_feedback: str,
        skill: VideoQualityReviewSkillPort,
        operation_port: OperationPort | None = None,
        now: datetime | None = None,
    ) -> VideoPostProductionWorkflowState:
        """调用唯一的 QAAgent QC Client，不执行本地二次质检。"""

        port = self._atomic_port(operation_port)
        state = await self.start_quality_review(
            state,
            user_feedback=user_feedback,
            operation_port=port,
            now=now,
        )
        start_claim = await self._claim_start(state, port, now=now)
        state = replace(state, _pending_operation=start_claim.job)
        if not start_claim.acquired:
            return await self._restore_terminal_state(state, port, now=now)
        try:
            result = await skill.review_video_quality(**_build_quality_request(state))
        except Exception as exc:  # noqa: BLE001 - 统一把 Client 异常落为可轮询状态
            return await self.record_quality_failure(state, error=str(exc), attempts=1, raw={}, operation_port=port, now=now)
        normalized = VideoQualityReviewWorkflowResult.from_skill(result)
        if normalized.ok:
            return await self.record_quality_success(state, result=normalized, operation_port=port, now=now)
        return await self.record_quality_failure(
            state,
            error=normalized.error or "视频质检失败",
            attempts=1,
            quota_insufficient=is_quota_insufficient(normalized.raw) or is_quota_insufficient(normalized.error),
            raw=normalized.raw,
            operation_port=port,
            now=now,
        )

    def _atomic_port(self, explicit: OperationPort | None) -> OperationPort:
        port = self._port(explicit)
        required_methods = (
            "claim_video_operation_start",
            "mark_video_operation_call_started",
            "finalize_video_operation",
            "get_video_operation_terminal_claim",
        )
        if any(not callable(getattr(port, method, None)) for method in required_methods):
            raise ValueError("视频外部调用必须使用支持两阶段原子启动权和可查询原子终态的 OperationPort")
        return port

    async def _claim_start(
        self,
        state: VideoPostProductionWorkflowState,
        port: OperationPort,
        *,
        now: datetime | None,
    ) -> VideoOperationStartClaim:
        pending = state.pending_operation
        if pending is None:
            raise OperationConflictError("视频外部调用缺少 pending Operation")
        current = await port.get(pending.job_id)
        if current is None:
            raise OperationConflictError("视频 Operation 不存在或已过期")
        _validate_operation_identity(state, current)
        starter = getattr(port, "claim_video_operation_start")
        owner = "video-postproduction-service"
        timestamp = _timestamp(now)
        claim = await starter(expected=current, owner=owner, now=timestamp, lease_seconds=30)
        if not isinstance(claim, VideoOperationStartClaim):
            raise OperationConflictError("视频原子启动 Port 返回了不受支持的 claim")
        _validate_operation_identity(state, claim.job)
        if claim.acquired:
            if (
                claim.job.status is not ExternalJobStatus.CREATED
                or claim.job.provider_job_id is not None
                or claim.job.lease_owner != owner
                or claim.job.lease_expires_at is None
                or claim.job.lease_expires_at <= timestamp
            ):
                raise OperationConflictError("视频启动权必须原子绑定未过期的外调前租约")
            marker = getattr(port, "mark_video_operation_call_started")
            started = await marker(expected=claim.job, owner=owner, now=timestamp)
            _validate_operation_identity(state, started)
            if started.status is not ExternalJobStatus.POLLING or started.provider_job_id is not None:
                raise OperationConflictError("视频外调开始标记必须原子进入未绑定供应商任务的 polling 状态")
            return VideoOperationStartClaim(job=started, acquired=True)
        return claim

    def _port(self, explicit: OperationPort | None) -> OperationPort:
        port = explicit or self._operation_port
        if port is None:
            raise ValueError("视频合并和质检必须提供 OperationPort")
        return port

    async def _finalize(
        self,
        state: VideoPostProductionWorkflowState,
        *,
        target_status: ExternalJobStatus,
        provider_job_id: str | None,
        result_hash: str,
        result_type: str,
        payload: Mapping[str, Any],
        stage_version: int,
        operation_port: OperationPort | None,
    ) -> VideoOperationTerminalClaim:
        pending = state.pending_operation
        if pending is None:
            raise OperationConflictError("视频阶段缺少 pending Operation")
        port = self._port(operation_port)
        finalizer = getattr(port, "finalize_video_operation", None)
        if not callable(finalizer):
            raise ValueError("视频合并和质检必须使用支持原子终态绑定的 OperationPort")
        current = await port.get(pending.job_id)
        if current is None:
            raise OperationConflictError("视频 Operation 不存在或已过期")
        _validate_operation_identity(state, current)
        await self._validate_trusted_terminal_claims(state, self._atomic_port(port))
        if current.status is ExternalJobStatus.CREATED:
            raise OperationConflictError("视频 Operation 尚未取得原子启动权，不得写入供应商终态")
        if provider_job_id is not None and current.provider_job_id is not None and current.provider_job_id != provider_job_id:
            raise OperationConflictError("视频 Operation 的供应商任务 ID 发生并发漂移")
        saved = await finalizer(
            expected=current,
            target_status=target_status,
            provider_job_id=provider_job_id,
            result_hash=result_hash,
            result_type=result_type,
            payload=payload,
            stage_version=stage_version,
        )
        if isinstance(saved, VideoOperationTerminalClaim):
            claim = saved
        elif isinstance(saved, ExternalJobRef):
            claim = VideoOperationTerminalClaim(
                job=saved,
                result_hash=result_hash,
                result_type=result_type,
                payload=_safe_json_object(payload, field_name="视频终态载荷"),
                stage_version=stage_version,
            )
        else:
            candidate_job = getattr(saved, "job", None)
            candidate_hash = getattr(saved, "result_hash", None)
            candidate_type = getattr(saved, "result_type", None)
            candidate_payload = getattr(saved, "payload", None)
            candidate_stage_version = getattr(saved, "stage_version", None)
            if (
                not isinstance(candidate_job, ExternalJobRef)
                or not isinstance(candidate_hash, str)
                or not isinstance(candidate_type, str)
                or not isinstance(candidate_payload, Mapping)
                or not isinstance(candidate_stage_version, int)
            ):
                raise OperationConflictError("视频 Operation 原子终态 Port 返回了不受支持的结果")
            claim = VideoOperationTerminalClaim(
                job=candidate_job,
                result_hash=candidate_hash,
                result_type=candidate_type,
                payload=_safe_json_object(candidate_payload, field_name="视频终态载荷"),
                stage_version=candidate_stage_version,
            )
        _validate_operation_identity(state, claim.job)
        if claim.result_hash != result_hash or claim.job.status is not target_status:
            raise OperationConflictError("视频 Operation 终态或结果摘要发生并发漂移")
        if claim.result_type != result_type or claim.payload != _safe_json_object(payload, field_name="视频终态载荷"):
            raise OperationConflictError("视频 Operation 终态业务载荷发生并发漂移")
        if claim.stage_version != stage_version:
            raise OperationConflictError("视频 Operation 终态阶段版本发生并发漂移")
        if target_status is ExternalJobStatus.SUCCEEDED and not claim.job.provider_job_id:
            raise OperationConflictError("成功 Operation 必须绑定供应商任务 ID")
        if provider_job_id is not None and claim.job.provider_job_id != provider_job_id:
            raise OperationConflictError("视频 Operation 的供应商任务 ID 与终态结果不一致")
        return claim

    async def _restore_terminal_state(
        self,
        state: VideoPostProductionWorkflowState,
        port: OperationPort,
        *,
        now: datetime | None,
    ) -> VideoPostProductionWorkflowState:
        pending = state.pending_operation
        if pending is None or pending.status not in {ExternalJobStatus.SUCCEEDED, ExternalJobStatus.FAILED}:
            return state
        getter = getattr(port, "get_video_operation_terminal_claim")
        claim = await getter(job_id=pending.job_id)
        if not isinstance(claim, VideoOperationTerminalClaim):
            raise OperationConflictError("视频 Operation 已终结但可信 Repository 缺少业务终态")
        _validate_operation_identity(state, claim.job)
        if claim.job != pending:
            raise OperationConflictError("视频 Operation 终态与当前 pending 引用不一致")
        return _apply_terminal_claim(state, claim, now=now)

    async def _validate_trusted_terminal_claims(
        self,
        state: VideoPostProductionWorkflowState,
        port: OperationPort,
    ) -> None:
        getter = getattr(port, "get_video_operation_terminal_claim")
        for entry in state.terminal_claims:
            checkpoint_job = ExternalJobRef.model_validate(entry["job"])
            trusted = await getter(job_id=checkpoint_job.job_id)
            if not isinstance(trusted, VideoOperationTerminalClaim):
                raise OperationConflictError("视频 checkpoint 终态在可信 Repository 中不存在")
            expected_payload = _safe_json_object(entry["payload"], field_name="视频终态载荷")
            if (
                trusted.job != checkpoint_job
                or trusted.result_hash != entry["result_hash"]
                or trusted.result_type != entry["result_type"]
                or trusted.payload != expected_payload
                or trusted.stage_version != entry["stage_version"]
            ):
                raise OperationConflictError("视频 checkpoint 终态与可信 Repository 不一致")


def _new_state(
    generation_state: VideoSceneGenerationWorkflowState,
    *,
    current_stage: VideoPostProductionStage,
    status: WorkflowStatus,
    stage_version: int,
    context_version: int,
    updated_at: datetime,
    merge_request: Mapping[str, Any],
    pending_operation: ExternalJobRef | None,
    operation_attempts: Mapping[str, int],
) -> VideoPostProductionWorkflowState:
    state = VideoPostProductionWorkflowState(
        workflow_id=generation_state.workflow_id,
        conversation_id=generation_state.conversation_id,
        current_stage=current_stage,
        status=status,
        stage_version=stage_version,
        context_version=context_version,
        created_at=generation_state.created_at,
        updated_at=updated_at,
        _generation_state=generation_state,
        _merge_request_json=_canonical_json(merge_request, field_name="合并视频请求"),
        _merged_video_json="null",
        _merge_error_json="null",
        _quality_review_json="null",
        _quality_feedback_json="null",
        _pending_operation=pending_operation,
        _terminal_result_hash=None,
        _terminal_claims_json="[]",
        _operation_attempts_json=_canonical_json(operation_attempts, field_name="视频 Operation 尝试次数"),
    )
    _validate_postproduction_state(state)
    return state


def _apply_terminal_claim(
    state: VideoPostProductionWorkflowState,
    claim: VideoOperationTerminalClaim,
    *,
    now: datetime | None,
) -> VideoPostProductionWorkflowState:
    """只用可信 Repository 返回的业务终态推进 checkpoint。"""

    result_type = claim.result_type
    expected_stage = {
        "merge_succeeded": VideoPostProductionStage.MERGE_VIDEO,
        "merge_failed": VideoPostProductionStage.MERGE_VIDEO,
        "quality_succeeded": VideoPostProductionStage.QUALITY_REVIEW,
        "quality_failed": VideoPostProductionStage.QUALITY_REVIEW,
    }.get(result_type)
    if expected_stage is None or state.current_stage is not expected_stage:
        raise OperationConflictError("可信视频终态类型与当前阶段不一致")
    if claim.stage_version != state.stage_version:
        raise OperationConflictError("可信视频终态与当前阶段版本不一致")
    expected_status = ExternalJobStatus.SUCCEEDED if result_type.endswith("succeeded") else ExternalJobStatus.FAILED
    if claim.job.status is not expected_status:
        raise OperationConflictError("可信视频终态的 Operation 状态不一致")
    _validate_operation_identity(state, claim.job)
    payload = _safe_json_object(claim.payload, field_name="可信视频终态载荷")
    if claim.result_hash != _terminal_hash(result_type, payload):
        raise OperationConflictError("可信视频终态的结果摘要不一致")
    updates: dict[str, Any] = {
        "stage_version": state.stage_version + 1,
        "context_version": state.context_version + 1,
        "updated_at": _next_timestamp(state, now),
        "_pending_operation": None,
        "_terminal_result_hash": claim.result_hash,
        "_terminal_claims_json": _append_terminal_claim(state, result_type, payload, claim),
    }
    if result_type == "merge_succeeded":
        updates.update(
            current_stage=VideoPostProductionStage.VIDEO_REVIEW,
            status=WorkflowStatus.AWAITING_USER,
            _merged_video_json=_canonical_json(payload, field_name="合并视频结果"),
            _merge_error_json="null",
        )
    elif result_type == "merge_failed":
        updates.update(
            status=WorkflowStatus.PAUSED_QUOTA if payload.get("quota_insufficient") is True else WorkflowStatus.FAILED,
            _merge_error_json=_canonical_json(payload, field_name="合并失败结果"),
        )
    elif result_type == "quality_succeeded":
        updates.update(
            current_stage=VideoPostProductionStage.VIDEO_REVIEW,
            status=WorkflowStatus.AWAITING_USER,
            _quality_review_json=_canonical_json(payload, field_name="视频质检结果"),
        )
    else:
        updates.update(
            status=WorkflowStatus.PAUSED_QUOTA if payload.get("quota_insufficient") is True else WorkflowStatus.FAILED,
            _quality_review_json=_canonical_json(payload, field_name="视频质检失败结果"),
        )
    restored = replace(state, **updates)
    _validate_postproduction_state(restored)
    return restored


def _append_terminal_claim(
    state: VideoPostProductionWorkflowState,
    result_type: str,
    payload: Mapping[str, Any],
    claim: VideoOperationTerminalClaim,
) -> str:
    entries = state.terminal_claims
    entries.append(
        {
            "result_type": result_type,
            "result_hash": claim.result_hash,
            "stage_version": claim.stage_version,
            "job": claim.job.model_dump(mode="json"),
            "payload": json.loads(_canonical_json(payload, field_name="视频终态载荷")),
        }
    )
    return _canonical_json(entries, field_name="视频终态 claim")


def _validate_generation_ready(state: VideoSceneGenerationWorkflowState) -> None:
    VideoSceneGenerationWorkflowService().to_workflow_record(state)
    if state.current_stage is not VideoSceneGenerationStage.SCENE_VIDEO_REVIEW or state.status is not WorkflowStatus.AWAITING_USER:
        raise ValueError("只有等待人工确认的完整分镜结果才能合并")
    if state.pending_operations or state.failed_scenes or state.dirty_scene_ids:
        raise ValueError("全部分镜必须成功且没有待重生或运行中的 Operation")
    if len(state.scene_videos) != len(state.scene_packages) or not state.scene_videos:
        raise ValueError("全部分镜成功后才能合并")
    for video in state.scene_videos:
        _https_url(video.get("video_url"), "分镜视频 URL")


def _validate_postproduction_state(state: VideoPostProductionWorkflowState) -> None:
    VideoSceneGenerationWorkflowService().to_workflow_record(state.generation_state)
    if (
        state.generation_state.current_stage
        is not VideoSceneGenerationStage.SCENE_VIDEO_REVIEW
        or state.generation_state.status is not WorkflowStatus.AWAITING_USER
    ):
        raise ValueError("视频后处理来源必须是未取消且等待人工确认的完整分镜结果")
    if state.workflow_id != state.generation_state.workflow_id or state.conversation_id != state.generation_state.conversation_id:
        raise ValueError("视频后处理状态必须属于同一 Workflow 和对话")
    if state.stage_version <= state.generation_state.stage_version or state.context_version <= state.generation_state.context_version:
        raise ValueError("视频后处理版本必须严格晚于来源分镜生成状态")
    expected_merge_request = _build_merge_request(state.generation_state)
    if _canonical_json(state.merge_request, field_name="当前合并请求") != _canonical_json(expected_merge_request, field_name="权威合并请求"):
        raise ValueError("合并请求必须从当前权威分镜结果机械重建")
    _validate_postproduction_payloads(state)
    _validate_terminal_claims(state)
    if state.pending_operation is not None:
        _validate_operation_identity(state, state.pending_operation)
        expected_attempt = state.operation_attempts.get("merge" if state.current_stage is VideoPostProductionStage.MERGE_VIDEO else "quality")
        if expected_attempt != state.pending_operation.attempt:
            raise ValueError("pending Operation attempt 必须匹配当前阶段尝试次数")
        operation_stage_version = (
            state.stage_version - 1
            if state.status is WorkflowStatus.CANCELLED
            else state.stage_version
        )
        expected_key = _operation_key(
            state.workflow_id,
            state.current_stage,
            operation_stage_version,
            state.pending_operation.attempt,
            state.merge_request if state.current_stage is VideoPostProductionStage.MERGE_VIDEO else _build_quality_request(state),
        )
        if state.pending_operation.idempotency_key != expected_key:
            raise ValueError("pending Operation 幂等键与当前权威阶段不一致")
    if state.current_stage in {VideoPostProductionStage.MERGE_VIDEO, VideoPostProductionStage.QUALITY_REVIEW}:
        if state.status is WorkflowStatus.RUNNING and state.pending_operation is None:
            raise ValueError("运行中的视频阶段必须保留 pending Operation")
        if state.status not in {
            WorkflowStatus.RUNNING,
            WorkflowStatus.CANCELLED,
        } and state.pending_operation is not None:
            raise ValueError("非运行视频阶段不得保留 pending Operation")
    elif state.pending_operation is not None:
        raise ValueError("人工审核或完成阶段不得保留 pending Operation")
    if state.current_stage is VideoPostProductionStage.MERGE_VIDEO and state.status not in {
        WorkflowStatus.RUNNING,
        WorkflowStatus.PAUSED_QUOTA,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    }:
        raise ValueError("合并阶段状态不合法")
    if state.current_stage is VideoPostProductionStage.QUALITY_REVIEW:
        if state.merged_video is None:
            raise ValueError("质检阶段必须保留合并视频")
        if state.status not in {
            WorkflowStatus.RUNNING,
            WorkflowStatus.PAUSED_QUOTA,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
            raise ValueError("质检阶段状态不合法")
    if state.current_stage is VideoPostProductionStage.VIDEO_REVIEW:
        if state.status not in {
            WorkflowStatus.AWAITING_USER,
            WorkflowStatus.PAUSED_QUOTA,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
            raise ValueError("视频人工审核阶段状态不合法")
        if state.merged_video is None:
            raise ValueError("视频人工审核阶段必须保留合并结果")
    if state.current_stage is VideoPostProductionStage.COMPLETED:
        if state.status is not WorkflowStatus.COMPLETED or not state.finalized_by_user:
            raise ValueError("视频结束必须由用户明确确认")


def _validate_postproduction_payloads(state: VideoPostProductionWorkflowState) -> None:
    merged = state.merged_video
    if merged is not None:
        if set(merged) != {"video_url", "task_id", "endpoint", "scene_videos", "raw"}:
            raise ValueError("合并视频结果字段不完整或包含额外字段")
        if _https_url(merged.get("video_url"), "合并视频 URL") != merged["video_url"]:
            raise ValueError("合并视频 URL 必须是已移除查询参数的规范 HTTPS URL")
        _optional_text(merged.get("task_id"))
        if merged.get("endpoint") != "/api/video/merge" or merged.get("scene_videos") != state.merge_request["scene_videos"]:
            raise ValueError("合并视频结果必须绑定当前权威分镜顺序和接口")
        if _safe_json_object(merged.get("raw"), field_name="合并视频原始结果") != merged["raw"]:
            raise ValueError("合并视频原始结果必须完成敏感信息清洗")
    merge_error = state.merge_error
    if merge_error is not None and not isinstance(merge_error, dict):
        raise ValueError("合并失败结果必须是对象")
    review = state.quality_review
    if review is not None and not isinstance(review, dict):
        raise ValueError("视频质检结果必须是对象")
    feedback = state.quality_feedback
    if feedback is not None:
        if not feedback or _sanitize_text(feedback) != feedback:
            raise ValueError("视频质检用户意见必须非空且完成敏感信息清洗")
    if state.current_stage is VideoPostProductionStage.QUALITY_REVIEW and not feedback:
        raise ValueError("视频质检阶段必须保留首次用户意见")


def _validate_terminal_claims(state: VideoPostProductionWorkflowState) -> None:
    entries = state.terminal_claims
    seen_jobs: set[str] = set()
    normalized: list[tuple[str, dict[str, Any], str]] = []
    previous_stage_version = state.generation_state.stage_version
    attempts_by_stage: dict[VideoPostProductionStage, list[int]] = {
        VideoPostProductionStage.MERGE_VIDEO: [],
        VideoPostProductionStage.QUALITY_REVIEW: [],
    }
    stage_by_result = {
        "merge_succeeded": VideoPostProductionStage.MERGE_VIDEO,
        "merge_failed": VideoPostProductionStage.MERGE_VIDEO,
        "quality_succeeded": VideoPostProductionStage.QUALITY_REVIEW,
        "quality_failed": VideoPostProductionStage.QUALITY_REVIEW,
    }
    status_by_result = {
        "merge_succeeded": ExternalJobStatus.SUCCEEDED,
        "merge_failed": ExternalJobStatus.FAILED,
        "quality_succeeded": ExternalJobStatus.SUCCEEDED,
        "quality_failed": ExternalJobStatus.FAILED,
    }
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"result_type", "result_hash", "stage_version", "job", "payload"}:
            raise ValueError("视频终态 claim 字段不完整或包含额外字段")
        result_type = _optional_text(entry.get("result_type"))
        if result_type not in stage_by_result:
            raise ValueError("视频终态 claim 类型不受支持")
        result_hash = _optional_text(entry.get("result_hash"))
        stage_version = entry.get("stage_version")
        if not isinstance(stage_version, int) or isinstance(stage_version, bool) or stage_version < 1:
            raise ValueError("视频终态 claim stage_version 必须为正整数")
        if stage_version <= previous_stage_version:
            raise ValueError("视频终态 claim 的阶段版本必须严格递增")
        previous_stage_version = stage_version
        job = ExternalJobRef.model_validate(entry.get("job"))
        payload = _safe_json_object(entry.get("payload"), field_name="视频终态载荷")
        if job.job_id in seen_jobs:
            raise ValueError("同一个视频 Operation 不得重复写入终态 claim")
        seen_jobs.add(job.job_id)
        expected_stage = stage_by_result[result_type]
        if job.workflow_id != state.workflow_id or job.stage != expected_stage.value or job.status is not status_by_result[result_type]:
            raise ValueError("视频终态 claim 的 Workflow、阶段或状态不一致")
        if result_type.endswith("succeeded") and not job.provider_job_id:
            raise ValueError("视频成功终态 claim 必须绑定供应商任务 ID")
        authority_request = (
            state.merge_request
            if expected_stage is VideoPostProductionStage.MERGE_VIDEO
            else _build_quality_request(state)
        )
        if job.idempotency_key != _operation_key(
            state.workflow_id,
            expected_stage,
            stage_version,
            job.attempt,
            authority_request,
        ):
            raise ValueError("视频终态 claim 幂等键与权威阶段版本不一致")
        if result_hash != _terminal_hash(result_type, payload):
            raise ValueError("视频终态 claim 的结果摘要不一致")
        attempts_by_stage[expected_stage].append(job.attempt)
        normalized.append((result_type, payload, result_hash))
    for stage, attempts in attempts_by_stage.items():
        if attempts != list(range(1, len(attempts) + 1)):
            raise ValueError("视频终态 claim 的 attempt 必须按阶段连续递增")
        pending_increment = int(state.pending_operation is not None and state.current_stage is stage)
        expected_attempt = len(attempts) + pending_increment
        key = "merge" if stage is VideoPostProductionStage.MERGE_VIDEO else "quality"
        actual_attempt = state.operation_attempts.get(key, 0)
        if actual_attempt != expected_attempt:
            raise ValueError("视频 Operation 尝试次数与权威终态历史不一致")
    if normalized and state.pending_operation is None:
        terminal_offset = (
            2
            if state.current_stage is VideoPostProductionStage.COMPLETED
            or state.status is WorkflowStatus.CANCELLED
            else 1
        )
        if state.stage_version != previous_stage_version + terminal_offset:
            raise ValueError("视频状态版本与最新权威终态阶段不一致")
    if state._terminal_result_hash != (normalized[-1][2] if normalized else None):
        raise ValueError("视频状态必须绑定最新终态 claim 的结果摘要")
    merge_entries = [item for item in normalized if item[0].startswith("merge_")]
    if state.merged_video is not None:
        if not merge_entries or merge_entries[-1][0] != "merge_succeeded" or merge_entries[-1][1] != state.merged_video:
            raise ValueError("合并视频必须绑定成功的权威终态 claim")
    elif state.merge_error is not None:
        if not merge_entries or merge_entries[-1][0] != "merge_failed" or merge_entries[-1][1] != state.merge_error:
            raise ValueError("合并失败必须绑定失败的权威终态 claim")
    elif merge_entries:
        raise ValueError("视频状态不得丢弃已有 merge 终态事实")
    if state.current_stage in {VideoPostProductionStage.QUALITY_REVIEW, VideoPostProductionStage.VIDEO_REVIEW, VideoPostProductionStage.COMPLETED} and state.merged_video is None:
        raise ValueError("合并后阶段必须保留权威合并视频")
    quality_entries = [item for item in normalized if item[0].startswith("quality_")]
    if state.quality_review is not None:
        expected_type = "quality_succeeded" if state.quality_review.get("ok") is True else "quality_failed"
        if not quality_entries or quality_entries[-1][0] != expected_type or quality_entries[-1][1] != state.quality_review:
            raise ValueError("视频质检结果必须绑定对应的权威终态 claim")
    elif quality_entries:
        raise ValueError("视频状态不得丢弃已有质检终态事实")


def _validate_stage(state: VideoPostProductionWorkflowState, stage: VideoPostProductionStage, *, allow_status: set[WorkflowStatus] | None = None) -> None:
    _validate_postproduction_state(state)
    if state.current_stage is not stage:
        raise ValueError(f"当前阶段不是 {stage.value}")
    if allow_status is None and state.status is not WorkflowStatus.RUNNING:
        raise ValueError("当前阶段必须处于运行状态")
    if allow_status is not None and state.status not in allow_status:
        raise ValueError("当前阶段状态不允许该操作")


def _build_merge_request(state: VideoSceneGenerationWorkflowState) -> dict[str, Any]:
    ordered = sorted(state.scene_videos, key=lambda item: item["scene_index"])
    return {
        "video_urls": [_https_url(item["video_url"], "分镜视频 URL") for item in ordered],
        "scene_videos": [
            {"scene_id": item["scene_id"], "scene_index": item["scene_index"], "video_url": item["video_url"]}
            for item in ordered
        ],
        "duration": int(state.source_scene_package.target_duration_ms // 1000),
        "size": state.source_scene_package.creation_contract.get("video_size") or "1080p",
        "model": state.source_scene_package.creation_contract.get("video_model"),
    }


def _build_quality_request(state: VideoPostProductionWorkflowState) -> dict[str, Any]:
    merged = state.merged_video
    if merged is None:
        raise ValueError("质检请求缺少合并视频")
    source = state.generation_state
    contract = source.source_scene_package.creation_contract
    return {
        "merged_video_url": merged["video_url"],
        "scene_videos": [
            {"scene_id": item["scene_id"], "scene_index": item["scene_index"], "video_url": item["video_url"]}
            for item in sorted(source.scene_videos, key=lambda item: item["scene_index"])
        ],
        "scene_packages": source.scene_packages,
        "brief": {
            "creation_contract": contract,
            "original_scene_packages": source.source_scene_package.scene_packages,
            "expected_duration_sec": source.source_scene_package.target_duration_ms // 1000,
        },
        "materials": [{"url": url} for url in source.source_scene_package.material_image_urls],
        "user_feedback": state.quality_feedback,
        "ratio": contract.get("video_ratio"),
        "size": contract.get("video_size"),
    }


def _validate_operation_identity(state: VideoPostProductionWorkflowState, operation: ExternalJobRef) -> None:
    pending = state.pending_operation
    if pending is not None:
        expected_identity = (
            pending.job_id,
            pending.workflow_id,
            pending.stage,
            pending.attempt,
            pending.idempotency_key,
        )
        actual_identity = (
            operation.job_id,
            operation.workflow_id,
            operation.stage,
            operation.attempt,
            operation.idempotency_key,
        )
        if actual_identity != expected_identity:
            raise OperationConflictError("视频 Operation 身份不一致")
    if operation.workflow_id != state.workflow_id or operation.stage != state.current_stage.value:
        raise OperationConflictError("视频 Operation 不属于当前 Workflow")


def _terminal_hash(kind: str, payload: Mapping[str, Any]) -> str:
    return _hash_text(f"{kind}:{_canonical_json(payload, field_name='视频终态结果')}")


def _operation_key(
    workflow_id: str,
    stage: VideoPostProductionStage,
    stage_version: int,
    attempt: int,
    payload: Mapping[str, Any],
) -> str:
    request_hash = _hash_json(payload, field_name="视频 Operation 权威请求")
    identity = "|".join((workflow_id, stage.value, str(stage_version), str(attempt), request_hash))
    return f"pf:video-post:{_hash_text(identity)}"


def _safe_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必须是对象")
    return _redact(json.loads(_canonical_json(value, field_name=field_name)))


def _safe_json_list(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field_name} 必须是对象数组")
    return [_safe_json_object(item, field_name=field_name) for item in value]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = "".join(char for char in key_text.lower() if char.isalnum())
            if any(marker in normalized for marker in ("authorization", "token", "apikey", "secret", "password", "credential")):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    text = str(value)

    def strip_url_query(match: re.Match[str]) -> str:
        parsed = urlsplit(match.group(0))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    text = _URL_PATTERN.sub(strip_url_query, text)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    return _SENSITIVE_TEXT_PATTERN.sub("[REDACTED]", text)


def _canonical_json(value: Any, *, field_name: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是可序列化 JSON") from exc


def _hash_json(value: Any, *, field_name: str) -> str:
    return _hash_text(_canonical_json(value, field_name=field_name))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _https_url(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} 必须是规范 HTTPS URL")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError(f"{field_name} 必须是 HTTPS URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _safe_text(value: Any) -> str:
    return _sanitize_text(value) if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("文本值不能为空")
    return value.strip()


def _string_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field_name} 必须是非空字符串数组")
    return [item.strip() for item in value]


def _unique_strings(value: Sequence[str]) -> list[str]:
    result: list[str] = []
    for item in value:
        if item not in result:
            result.append(item)
    return result


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Workflow 时间必须包含时区")
    return timestamp


def _next_timestamp(state: VideoPostProductionWorkflowState, value: datetime | None) -> datetime:
    timestamp = _timestamp(value)
    if timestamp < state.updated_at:
        raise ValueError("Workflow 更新时间不能早于当前状态")
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
