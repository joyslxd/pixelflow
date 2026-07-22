"""供并行 Workflow 模块使用的确定性内存 Port 实现。"""

from collections.abc import Mapping

from .contracts import (
    ContextEnvelope,
    ContextRequest,
    ExternalJobRef,
    ExternalJobStatus,
    OperationRequest,
)
from .ports import OperationConflictError


class FakeOperationPort:
    """在内存中模拟幂等 claim，并返回隔离副本。"""

    def __init__(self) -> None:
        self._jobs_by_id: dict[str, ExternalJobRef] = {}
        self._job_ids_by_idempotency_key: dict[str, str] = {}
        self._requests_by_idempotency_key: dict[str, OperationRequest] = {}

    async def claim(self, request: OperationRequest) -> ExternalJobRef:
        existing_job_id = self._job_ids_by_idempotency_key.get(request.idempotency_key)
        if existing_job_id is not None:
            if self._requests_by_idempotency_key[request.idempotency_key] != request:
                raise OperationConflictError(f"idempotency_key {request.idempotency_key!r} belongs to another operation request")
            return self._jobs_by_id[existing_job_id].model_copy(deep=True)

        job_id = f"fake-job-{len(self._jobs_by_id) + 1:04d}"
        job = ExternalJobRef(
            job_id=job_id,
            provider_job_id=None,
            workflow_id=request.workflow_id,
            stage=request.stage,
            status=ExternalJobStatus.CREATED,
            attempt=request.attempt,
            idempotency_key=request.idempotency_key,
            next_poll_at=None,
            lease_owner=None,
            lease_expires_at=None,
        )
        self._jobs_by_id[job_id] = job
        self._job_ids_by_idempotency_key[request.idempotency_key] = job_id
        self._requests_by_idempotency_key[request.idempotency_key] = request.model_copy(deep=True)
        return job.model_copy(deep=True)

    async def get(self, job_id: str) -> ExternalJobRef | None:
        job = self._jobs_by_id.get(job_id)
        return None if job is None else job.model_copy(deep=True)

    async def save(self, job: ExternalJobRef) -> ExternalJobRef:
        normalized = ExternalJobRef.model_validate(job.model_dump(mode="python"))
        existing = self._jobs_by_id.get(normalized.job_id)
        if existing is None:
            raise KeyError(normalized.job_id)
        existing_identity = (existing.workflow_id, existing.stage, existing.attempt, existing.idempotency_key)
        normalized_identity = (normalized.workflow_id, normalized.stage, normalized.attempt, normalized.idempotency_key)
        if existing_identity != normalized_identity:
            raise OperationConflictError("claimed job identity cannot change after claim")
        self._jobs_by_id[normalized.job_id] = normalized
        return normalized.model_copy(deep=True)


class FakeContextPort:
    """按 conversation 提供模板，并把当前原始输入原样写回副本。"""

    def __init__(self, envelopes: Mapping[tuple[str, str], ContextEnvelope]) -> None:
        self._envelopes: dict[tuple[str, str], ContextEnvelope] = {}
        for owner_key, envelope in envelopes.items():
            self._validate_template(owner_key[1], envelope)
            self._envelopes[owner_key] = envelope.model_copy(deep=True)

    @staticmethod
    def _validate_template(conversation_id: str, envelope: ContextEnvelope) -> None:
        """拒绝把其他对话的 Workflow 或摘要装进当前所有者模板。"""

        workflow = envelope.active_or_target_workflow
        if workflow is not None and workflow.conversation_id != conversation_id:
            raise ValueError("active_or_target_workflow.conversation_id must match owner conversation_id")
        summary = envelope.conversation_summary
        if summary is not None and summary.conversation_id != conversation_id:
            raise ValueError("conversation_summary.conversation_id must match owner conversation_id")
        if any(item.conversation_id != conversation_id for item in envelope.related_workflow_summaries):
            raise ValueError("related_workflow_summaries conversation_id must match owner conversation_id")

    async def assemble(self, request: ContextRequest) -> ContextEnvelope:
        owner_key = (request.user_id, request.conversation_id)
        template = self._envelopes.get(owner_key)
        if template is None:
            raise KeyError(owner_key)
        workflow = template.active_or_target_workflow
        if request.target_workflow_id is not None and (workflow is None or workflow.workflow_id != request.target_workflow_id):
            raise KeyError((request.user_id, request.conversation_id, request.target_workflow_id))
        payload = template.model_dump(mode="python")
        payload["current_input"] = request.current_input
        return ContextEnvelope.model_validate(payload)
