"""持久化 External Job Operation 的首次幂等领取。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from ..contracts import ExternalJobStatus, OperationRequest
from ..persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    OperationRecord,
)
from ..ports import OperationConflictError
from .identity import build_operation_idempotency_key

_REQUEST_HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def _require_scope_text(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} 不能为空")
    if len(normalized) > 64:
        raise ValueError(f"{field} 不能超过 64 个字符")
    return normalized


class OperationCoordinator:
    """在 Repository 唯一约束上提供重复 start 可回读的领域 Service。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        user_id: str,
        conversation_id: str,
        now: Callable[[], datetime] | None = None,
        job_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._user_id = _require_scope_text("user_id", user_id)
        self._conversation_id = _require_scope_text(
            "conversation_id",
            conversation_id,
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory or (lambda: f"operation-{uuid4().hex}")

    async def claim(self, request: OperationRequest) -> OperationRecord:
        """创建或回读同一身份与请求摘要对应的唯一 operation。"""

        normalized = OperationRequest.model_validate(request.model_dump(mode="python"))
        self._validate_request(normalized)
        existing = await self._repository.get_operation_by_idempotency_key(
            self._user_id,
            normalized.idempotency_key,
        )
        if existing is not None:
            return self._match_existing(existing, normalized)

        now = self._now()
        candidate = OperationRecord(
            job_id=self._job_id_factory(),
            provider_job_id=None,
            workflow_id=normalized.workflow_id,
            conversation_id=self._conversation_id,
            stage=normalized.stage,
            stage_version=normalized.stage_version,
            status=ExternalJobStatus.CREATED,
            attempt=normalized.attempt,
            request_hash=normalized.request_hash,
            idempotency_key=normalized.idempotency_key,
            next_poll_at=None,
            lease_owner=None,
            lease_expires_at=None,
            created_at=now,
            updated_at=now,
        )
        try:
            return await self._repository.create_operation(
                self._user_id,
                candidate,
            )
        except AgentRuntimeRecordConflictError:
            winner = await self._repository.get_operation_by_idempotency_key(
                self._user_id,
                normalized.idempotency_key,
            )
            if winner is None:
                raise OperationConflictError("Operation 身份或 job_id 已被其他请求占用") from None
            return self._match_existing(winner, normalized)

    @staticmethod
    def _validate_request(request: OperationRequest) -> None:
        expected_key = build_operation_idempotency_key(
            request.workflow_id,
            request.stage,
            request.stage_version,
            request.attempt,
        )
        if request.idempotency_key != expected_key:
            raise OperationConflictError("Operation 幂等键与 workflow 阶段身份不一致")
        if _REQUEST_HASH_PATTERN.fullmatch(request.request_hash) is None:
            raise OperationConflictError("Operation request_hash 必须是规范 SHA-256")

    def _match_existing(
        self,
        existing: OperationRecord,
        request: OperationRequest,
    ) -> OperationRecord:
        identity_matches = (
            existing.workflow_id == request.workflow_id
            and existing.conversation_id == self._conversation_id
            and existing.stage == request.stage
            and existing.stage_version == request.stage_version
            and existing.attempt == request.attempt
            and existing.request_hash == request.request_hash
            and existing.idempotency_key == request.idempotency_key
        )
        if not identity_matches:
            raise OperationConflictError("Operation 幂等键已被不同身份或请求摘要占用")
        return existing
