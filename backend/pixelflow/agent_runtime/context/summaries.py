"""验证结构化摘要的版本链、消息覆盖范围和 Artifact 证据。"""

from __future__ import annotations

from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import ContextSummary
from ..persistence import AgentRuntimeRepository


class SummaryVersionConflictError(ValueError):
    """摘要版本、前驱或累计覆盖范围不连续。"""


class SummaryEvidenceValidationError(ValueError):
    """摘要声明的消息或 Artifact 证据无法由权威快照证明。"""


class _SummaryRecord(BaseModel):
    """为摘要证据提供不可变且拒绝额外字段的内部合同。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class SummaryMessageEvidence(_SummaryRecord):
    """用稳定消息 ID 和会话内 sequence 标识一条原始消息。"""

    message_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)


class SummaryEvidenceSnapshot(_SummaryRecord):
    """保存一次同用户、同会话的消息和 Artifact 证据快照。"""

    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    messages: tuple[SummaryMessageEvidence, ...] = ()
    artifact_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_unique_evidence(self) -> Self:
        """拒绝重复或不可定位的消息和 Artifact 证据。"""

        message_ids = [message.message_id for message in self.messages]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("messages 不能包含重复 message_id")
        sequences = [message.sequence for message in self.messages]
        if len(sequences) != len(set(sequences)):
            raise ValueError("messages 不能包含重复 sequence")
        normalized_artifacts = [artifact_ref.strip() for artifact_ref in self.artifact_refs]
        if any(not artifact_ref for artifact_ref in normalized_artifacts):
            raise ValueError("artifact_refs 不能包含空引用")
        if len(normalized_artifacts) != len(set(normalized_artifacts)):
            raise ValueError("artifact_refs 不能重复")
        if tuple(normalized_artifacts) != self.artifact_refs:
            object.__setattr__(self, "artifact_refs", tuple(normalized_artifacts))
        return self


class SummaryEvidenceSource(Protocol):
    """从权威消息和 Artifact Store 读取同版本证据快照。"""

    async def load_summary_evidence(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> SummaryEvidenceSnapshot: ...


def _clone_summary(summary: ContextSummary) -> ContextSummary:
    return ContextSummary.model_validate(summary.model_dump(mode="python"))


def _validate_coverage_progress(
    previous: ContextSummary,
    current: ContextSummary,
) -> None:
    previous_start = previous.covered_sequence_start
    previous_end = previous.covered_sequence_end
    if previous_start is None:
        return
    current_start = current.covered_sequence_start
    current_end = current.covered_sequence_end
    if current_start is None or current_end is None:
        raise SummaryVersionConflictError("消息覆盖范围不能回退")
    if current_start != previous_start or current_end < previous_end:
        raise SummaryVersionConflictError("消息覆盖范围不能回退")
    previous_ids = previous.covered_message_ids
    if current.covered_message_ids[: len(previous_ids)] != previous_ids:
        raise SummaryVersionConflictError("新摘要不能改写已有消息覆盖前缀")


def _validate_next_version(
    previous: ContextSummary | None,
    current: ContextSummary,
) -> None:
    if previous is None:
        if current.version != 1:
            raise SummaryVersionConflictError("第一条摘要必须是版本 1")
        return
    if current.version != previous.version + 1:
        raise SummaryVersionConflictError("摘要版本必须连续")
    if current.previous_summary_id != previous.summary_id:
        raise SummaryVersionConflictError("previous_summary_id 必须指向最新摘要")
    _validate_coverage_progress(previous, current)


def _validate_existing_chain(summaries: list[ContextSummary]) -> None:
    previous: ContextSummary | None = None
    for summary in summaries:
        _validate_next_version(previous, summary)
        previous = summary


def _validate_summary_evidence(
    summary: ContextSummary,
    snapshot: SummaryEvidenceSnapshot,
) -> None:
    start = summary.covered_sequence_start
    end = summary.covered_sequence_end
    if start is None or end is None:
        expected_message_ids: list[str] = []
    else:
        covered_messages = sorted(
            (message for message in snapshot.messages if start <= message.sequence <= end),
            key=lambda message: message.sequence,
        )
        expected_sequences = list(range(start, end + 1))
        if [message.sequence for message in covered_messages] != expected_sequences:
            raise SummaryEvidenceValidationError("消息证据必须按 sequence 完整匹配覆盖范围")
        expected_message_ids = [message.message_id for message in covered_messages]
    if summary.covered_message_ids != expected_message_ids:
        raise SummaryEvidenceValidationError("消息证据必须按 sequence 完整匹配覆盖范围")

    available_artifacts = set(snapshot.artifact_refs)
    if any(artifact_ref not in available_artifacts for artifact_ref in summary.artifact_evidence_refs):
        raise SummaryEvidenceValidationError("Artifact 证据不存在")


class StructuredSummaryRepository:
    """在 M01 Repository 写入前验证结构化摘要的证据和版本链。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        evidence_source: SummaryEvidenceSource,
    ) -> None:
        self._repository = repository
        self._evidence_source = evidence_source

    async def save(
        self,
        user_id: str,
        summary: ContextSummary,
    ) -> ContextSummary:
        """保存经同用户、同会话证据证明的下一版摘要。"""

        owner = user_id.strip()
        frozen_summary = _clone_summary(summary)
        existing = await self._repository.list_summaries(
            owner,
            frozen_summary.conversation_id,
        )
        _validate_existing_chain(existing)
        _validate_next_version(existing[-1] if existing else None, frozen_summary)

        snapshot = await self._evidence_source.load_summary_evidence(
            user_id=owner,
            conversation_id=frozen_summary.conversation_id,
        )
        frozen_snapshot = SummaryEvidenceSnapshot.model_validate(snapshot.model_dump(mode="python"))
        if frozen_snapshot.user_id != owner or frozen_snapshot.conversation_id != frozen_summary.conversation_id:
            raise SummaryEvidenceValidationError("证据快照不属于当前用户和会话")
        _validate_summary_evidence(frozen_summary, frozen_snapshot)
        return await self._repository.create_summary(owner, frozen_summary)

    async def get(
        self,
        user_id: str,
        summary_id: str,
    ) -> ContextSummary | None:
        """按所有者读取单条摘要，不暴露其他用户的记录。"""

        return await self._repository.get_summary(user_id, summary_id)

    async def list(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[ContextSummary]:
        """按版本顺序读取同会话摘要链。"""

        return await self._repository.list_summaries(
            user_id,
            conversation_id,
        )


__all__ = [
    "StructuredSummaryRepository",
    "SummaryEvidenceSnapshot",
    "SummaryEvidenceSource",
    "SummaryEvidenceValidationError",
    "SummaryMessageEvidence",
    "SummaryVersionConflictError",
]
