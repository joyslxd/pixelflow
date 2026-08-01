"""从权威 Repository 与任务 Store 组装 Supervisor Context 快照。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import JsonValue

from pixelflow.agent_runtime.contracts import AgentEventType, ContextSummary
from pixelflow.agent_runtime.persistence import (
    VideoRuntimeContextSnapshot,
    VideoRuntimeContextSnapshotConflictError,
    VideoRuntimeRepository,
    VideoRuntimeSafeSnapshot,
    turn_registration_context_read_scope,
)
from pixelflow.tasks import PixelFlowTaskStore

from .assembler import (
    ArtifactEvidenceRecord,
    ContextAssemblySnapshot,
    ContextMessageRecord,
    ContextVersionConflictError,
    WorkflowSummaryRecord,
)


class RepositoryContextSourceRepository(VideoRuntimeRepository, Protocol):
    """声明 Context source 所需的单一公开 Repository 端口。"""


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
        async with turn_registration_context_read_scope(owner, conversation_key):
            try:
                authoritative = (
                    await self._repository.read_versioned_context_snapshot(
                        owner,
                        conversation_key,
                        expected_context_version=expected,
                    )
                )
            except VideoRuntimeContextSnapshotConflictError:
                raise ContextVersionConflictError("上下文版本不一致") from None

        task_messages, summaries = _select_expected_version_records(authoritative)

        return _build_snapshot(
            user_id=owner,
            conversation_id=conversation_key,
            context_version=expected,
            runtime_snapshot=authoritative.runtime,
            task_messages=task_messages,
            summaries=summaries,
        )


def _select_expected_version_records(
    snapshot: VideoRuntimeContextSnapshot,
) -> tuple[list[object], list[ContextSummary]]:
    """按登记事件顺序选取 expected 版本之前的已提交用户输入。"""

    task_user_messages = [
        item for item in snapshot.task_messages if getattr(item, "role", None) == "user"
    ]
    user_events = _registered_user_events(snapshot)
    if not user_events:
        if snapshot.expected_context_version != snapshot.current_context_version:
            raise ContextVersionConflictError("上下文版本不一致")
        visible_messages = task_user_messages
    else:
        event_message_ids = [item[0] for item in user_events]
        task_message_ids = [
            _require_text("message_id", getattr(item, "message_id", None))
            for item in task_user_messages
        ]
        if (
            len(event_message_ids) != snapshot.current_context_version
            or len(set(event_message_ids)) != len(event_message_ids)
            or set(event_message_ids) != set(task_message_ids)
        ):
            raise ContextVersionConflictError("上下文登记事件不完整")
        _validate_interrupt_response_versions(snapshot, user_events)
        visible_ids = set(
            event_message_ids[: snapshot.expected_context_version]
        )
        visible_messages = [
            item
            for item in task_user_messages
            if getattr(item, "message_id", None) in visible_ids
        ]

    available_message_ids = {
        _require_text("message_id", getattr(item, "message_id", None))
        for item in visible_messages
    }
    available_message_ids.update(
        _require_text("message_id", getattr(item, "message_id", None))
        for item in snapshot.runtime.messages
    )
    workflow_ids = {item.workflow_id for item in snapshot.runtime.workflows}
    summaries = [
        item
        for item in snapshot.summaries
        if set(item.covered_message_ids).issubset(available_message_ids)
        and set(item.workflow_states).issubset(workflow_ids)
    ]
    return list(visible_messages), summaries


def _registered_user_events(
    snapshot: VideoRuntimeContextSnapshot,
) -> list[tuple[str, Mapping[str, object]]]:
    """提取 MESSAGE_UPSERTED 中已登记的用户消息及其公开 payload。"""

    registered: list[tuple[str, Mapping[str, object]]] = []
    for event in snapshot.events:
        if event.type is not AgentEventType.MESSAGE_UPSERTED:
            continue
        message = event.payload.get("message")
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        message_id = _require_text("message_id", message.get("message_id"))
        payload = message.get("payload")
        registered.append(
            (message_id, payload if isinstance(payload, Mapping) else {})
        )
    return registered


def _validate_interrupt_response_versions(
    snapshot: VideoRuntimeContextSnapshot,
    user_events: list[tuple[str, Mapping[str, object]]],
) -> None:
    """确认每次人工响应保存的 pre-input 版本与登记顺序一致。"""

    for interrupt in snapshot.runtime.interrupts:
        if interrupt.response is None or interrupt.response_id is None:
            continue
        pre_input = interrupt.response.get("pre_input_context_version")
        if type(pre_input) is not int or pre_input < 0:
            raise ContextVersionConflictError("interrupt 响应缺少合法快照身份")
        matches = [
            index
            for index, (_message_id, payload) in enumerate(user_events)
            if payload.get("interrupt_id") == interrupt.interrupt_id
            and payload.get("client_message_id") == str(interrupt.response_id)
        ]
        if matches != [pre_input]:
            raise ContextVersionConflictError("interrupt 响应快照身份不一致")


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
            "payload": _serialized_message_payload(message),
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
            "payload": _serialized_message_payload(message),
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


def _serialized_message_payload(message: object) -> dict[str, JsonValue]:
    """只在单条冻结消息的消费边界导出普通 JSON payload。"""

    serializer = getattr(message, "model_dump", None)
    if not callable(serializer):
        raise ValueError("权威消息缺少稳定 JSON serializer")
    document = serializer(mode="json")
    if not isinstance(document, dict):
        raise ValueError("权威消息序列化结果必须是 JSON 对象")
    payload = document.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("权威消息 payload 必须是 JSON 对象")
    return payload


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
