"""从权威 Repository 与任务 Store 组装 Supervisor Context 快照。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import JsonValue

from pixelflow.agent_runtime.contracts import ContextSummary
from pixelflow.agent_runtime.persistence import (
    VideoRuntimeRepository,
    VideoRuntimeSafeSnapshot,
)
from pixelflow.tasks import AGENT_RUNTIME_CONTEXT_KEY, PixelFlowTaskStore

from .assembler import (
    ArtifactEvidenceRecord,
    ContextAssemblySnapshot,
    ContextMessageRecord,
    ContextVersionConflictError,
    WorkflowSummaryRecord,
)


class RepositoryContextSourceRepository(VideoRuntimeRepository, Protocol):
    """声明 Context source 额外读取摘要所需的公开 Repository 端口。"""

    async def list_summaries(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[ContextSummary]: ...


class RepositoryContextSnapshotSource:
    """把任务消息与 live Repository 投影合并为严格版本快照。"""

    def __init__(
        self,
        *,
        task_store: PixelFlowTaskStore,
        repository: RepositoryContextSourceRepository,
    ) -> None:
        self._task_store = task_store
        self._repository = repository

    async def load_context_snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str,
        expected_context_version: int,
    ) -> ContextAssemblySnapshot:
        """只返回 owner 与 expected version 同时匹配的权威快照。"""

        owner = _require_text("user_id", user_id)
        conversation_key = _require_text("conversation_id", conversation_id)
        expected = _require_context_version(expected_context_version)
        conversation = await self._task_store.get_conversation(
            conversation_key,
            user_id=owner,
        )
        if conversation is None:
            raise LookupError("对话不存在或不属于当前用户")
        current = _conversation_context_version(conversation.context)
        if current != expected:
            raise ContextVersionConflictError(
                f"上下文版本不一致：expected={expected} current={current}"
            )

        runtime_snapshot = await self._repository.export_safe_snapshot(
            owner,
            conversation_key,
        )
        summaries = await self._repository.list_summaries(
            owner,
            conversation_key,
        )
        task_messages = await self._task_store.list_conversation_messages(
            conversation_key,
            user_id=owner,
        )

        verified_conversation = await self._task_store.get_conversation(
            conversation_key,
            user_id=owner,
        )
        if verified_conversation is None:
            raise ContextVersionConflictError("上下文快照读取期间对话已不可用")
        verified_version = _conversation_context_version(
            verified_conversation.context,
        )
        if (
            verified_version != expected
            or verified_conversation.revision != conversation.revision
        ):
            raise ContextVersionConflictError(
                "上下文快照读取期间版本发生变化"
            )

        return _build_snapshot(
            user_id=owner,
            conversation_id=conversation_key,
            context_version=verified_version,
            runtime_snapshot=runtime_snapshot,
            task_messages=task_messages,
            summaries=summaries,
        )


def _build_snapshot(
    *,
    user_id: str,
    conversation_id: str,
    context_version: int,
    runtime_snapshot: VideoRuntimeSafeSnapshot,
    task_messages: list[object],
    summaries: list[ContextSummary],
) -> ContextAssemblySnapshot:
    workflows = tuple(
        sorted(
            runtime_snapshot.workflows,
            key=lambda item: (item.created_at, item.workflow_id),
        )
    )
    ordered_summaries = tuple(
        sorted(
            summaries,
            key=lambda item: (item.version, item.created_at, item.summary_id),
        )
    )
    workflow_ids = {item.workflow_id for item in workflows}
    workflow_summaries = tuple(
        WorkflowSummaryRecord(workflow_id=workflow_id, summary=summary)
        for summary in ordered_summaries
        for workflow_id in sorted(summary.workflow_states)
        if workflow_id in workflow_ids
    )
    messages = _context_messages(
        conversation_id=conversation_id,
        task_messages=task_messages,
        projection_messages=list(runtime_snapshot.messages),
    )
    artifact_refs: list[str] = []
    for message in messages:
        artifact_refs.extend(_artifact_refs_from_payload(message.payload))
    for workflow in workflows:
        artifact_refs.extend(workflow.latest_artifact_refs)
    for summary in ordered_summaries:
        artifact_refs.extend(summary.artifact_evidence_refs)

    return ContextAssemblySnapshot(
        user_id=user_id,
        conversation_id=conversation_id,
        context_version=context_version,
        active_workflow_id=runtime_snapshot.active_workflow_id,
        workflows=workflows,
        messages=messages,
        conversation_summaries=ordered_summaries,
        workflow_summaries=workflow_summaries,
        artifact_evidence=tuple(
            ArtifactEvidenceRecord(
                conversation_id=conversation_id,
                artifact_ref=artifact_ref,
            )
            for artifact_ref in dict.fromkeys(artifact_refs)
        ),
    )


def _context_messages(
    *,
    conversation_id: str,
    task_messages: list[object],
    projection_messages: list[object],
) -> tuple[ContextMessageRecord, ...]:
    candidates: dict[str, tuple[str, dict[str, JsonValue]]] = {}
    for message in task_messages:
        if getattr(message, "role", None) != "user":
            continue
        message_id = _require_text("message_id", getattr(message, "message_id", None))
        payload: dict[str, JsonValue] = {
            "message_id": message_id,
            "role": "user",
            "content": str(getattr(message, "content", "")),
            "payload": getattr(message, "payload", {}),
        }
        _insert_message_candidate(
            candidates,
            message_id=message_id,
            created_at=str(getattr(message, "created_at", "")),
            payload=payload,
        )
    for message in projection_messages:
        message_id = _require_text("message_id", getattr(message, "message_id", None))
        payload = {
            "message_id": message_id,
            "role": str(getattr(message, "role", "assistant")),
            "content": str(getattr(message, "content", "")),
            "payload": getattr(message, "payload", {}),
        }
        created_at = getattr(message, "created_at", None)
        _insert_message_candidate(
            candidates,
            message_id=message_id,
            created_at=(
                created_at.isoformat()
                if hasattr(created_at, "isoformat")
                else str(created_at or "")
            ),
            payload=payload,
        )
    ordered = sorted(
        candidates.items(),
        key=lambda item: (item[1][0], item[0]),
    )
    return tuple(
        ContextMessageRecord(
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload,
        )
        for sequence, (_message_id, (_created_at, payload)) in enumerate(
            ordered,
            start=1,
        )
    )


def _insert_message_candidate(
    candidates: dict[str, tuple[str, dict[str, JsonValue]]],
    *,
    message_id: str,
    created_at: str,
    payload: dict[str, JsonValue],
) -> None:
    existing = candidates.get(message_id)
    candidate = (created_at, payload)
    if existing is not None and existing != candidate:
        raise ValueError("权威消息 ID 出现冲突")
    candidates[message_id] = candidate


def _artifact_refs_from_payload(value: object) -> tuple[str, ...]:
    """只递归读取显式 Artifact 引用键，并拒绝把 URL 当作引用。"""

    refs: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {
                "artifact_ref",
                "artifactRef",
                "artifact_refs",
                "artifactRefs",
            }:
                candidates = item if isinstance(item, list) else [item]
                refs.extend(
                    candidate.strip()
                    for candidate in candidates
                    if isinstance(candidate, str)
                    and candidate.strip()
                    and "://" not in candidate
                )
            else:
                refs.extend(_artifact_refs_from_payload(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_artifact_refs_from_payload(item))
    return tuple(dict.fromkeys(refs))


def _conversation_context_version(context: object) -> int:
    if not isinstance(context, Mapping):
        raise ContextVersionConflictError("对话缺少 Agent Runtime 上下文")
    runtime = context.get(AGENT_RUNTIME_CONTEXT_KEY)
    if not isinstance(runtime, Mapping):
        raise ContextVersionConflictError("对话缺少 Agent Runtime 上下文")
    return _require_context_version(runtime.get("context_version"))


def _require_context_version(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ContextVersionConflictError("context_version 必须是非负整数")
    return value


def _require_text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return value.strip()


__all__ = [
    "RepositoryContextSnapshotSource",
    "RepositoryContextSourceRepository",
]
