"""按相关性和稳定顺序组装单次模型调用的 ContextEnvelope。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from ..contracts import (
    ContextEnvelope,
    ContextRequest,
    ContextSummary,
    WorkflowRecord,
)
from .profiles import ModelContextProfile, resolve_model_context_profile
from .token_meter import TokenMeter, get_context_budget_policy

logger = logging.getLogger(__name__)

_MEMORY_ITEM_ADAPTER = TypeAdapter(dict[str, JsonValue])
_MEMORY_CATEGORIES = ["preference", "brand", "skill", "experience"]


class ContextVersionConflictError(RuntimeError):
    """请求版本落后于权威上下文版本时拒绝继续组装。"""


class _AssemblyRecord(BaseModel):
    """为 Context Runtime 内部快照提供严格且不可变的字段合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextMessageRecord(_AssemblyRecord):
    """保存消息排序元数据，同时保留原始消息载荷。"""

    conversation_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class WorkflowSummaryRecord(_AssemblyRecord):
    """把结构化摘要与所属 Workflow 建立显式关系。"""

    workflow_id: str = Field(min_length=1)
    summary: ContextSummary


class ArtifactEvidenceRecord(_AssemblyRecord):
    """保存 artifact 引用的会话归属，输出时只暴露稳定引用。"""

    conversation_id: str = Field(min_length=1)
    artifact_ref: str = Field(min_length=1)


class ContextAssemblySnapshot(_AssemblyRecord):
    """数据源为一次组装提供的同用户、同会话权威快照。"""

    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    context_version: int = Field(ge=0)
    active_workflow_id: str | None = Field(default=None, min_length=1)
    workflows: tuple[WorkflowRecord, ...] = ()
    messages: tuple[ContextMessageRecord, ...] = ()
    conversation_summaries: tuple[ContextSummary, ...] = ()
    workflow_summaries: tuple[WorkflowSummaryRecord, ...] = ()
    artifact_evidence: tuple[ArtifactEvidenceRecord, ...] = ()

    @model_validator(mode="after")
    def require_single_conversation_records(self) -> Self:
        """拒绝把其他会话的业务记录混入当前快照。"""

        if any(workflow.conversation_id != self.conversation_id for workflow in self.workflows):
            raise ValueError("workflows 只能属于快照 conversation_id")
        if any(message.conversation_id != self.conversation_id for message in self.messages):
            raise ValueError("messages 只能属于快照 conversation_id")
        if any(summary.conversation_id != self.conversation_id for summary in self.conversation_summaries):
            raise ValueError("conversation_summaries 只能属于快照 conversation_id")
        if any(item.summary.conversation_id != self.conversation_id for item in self.workflow_summaries):
            raise ValueError("workflow_summaries 只能属于快照 conversation_id")
        if any(item.conversation_id != self.conversation_id for item in self.artifact_evidence):
            raise ValueError("artifact_evidence 只能属于快照 conversation_id")

        workflow_ids = [workflow.workflow_id for workflow in self.workflows]
        if len(workflow_ids) != len(set(workflow_ids)):
            raise ValueError("workflows 不得包含重复 workflow_id")
        workflow_id_set = set(workflow_ids)
        if self.active_workflow_id is not None and self.active_workflow_id not in workflow_id_set:
            raise ValueError("active_workflow_id 必须引用当前快照中的 Workflow")
        if any(item.workflow_id not in workflow_id_set for item in self.workflow_summaries):
            raise ValueError("workflow_summaries 必须引用当前快照中的 Workflow")

        message_sequences = [message.sequence for message in self.messages]
        if len(message_sequences) != len(set(message_sequences)):
            raise ValueError("messages 不得包含重复 sequence")
        artifact_refs = [item.artifact_ref for item in self.artifact_evidence]
        if len(artifact_refs) != len(set(artifact_refs)):
            raise ValueError("artifact_evidence 不得包含重复 artifact_ref")
        return self


class ContextSnapshotSource(Protocol):
    """从权威存储读取一次同版本上下文快照。"""

    async def load_context_snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> ContextAssemblySnapshot: ...


class LongTermMemorySearch(Protocol):
    """复用 PowerMemService 的用户隔离搜索接口。"""

    async def search(
        self,
        *,
        user_id: str | None,
        query: str,
        categories: list[str] | None = None,
        source_agent: str | None = None,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> Sequence[Any]: ...


TokenEstimator = Callable[[dict[str, object]], int]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"无法估算 {type(value).__name__} 类型的上下文")


def estimate_context_tokens(payload: dict[str, object]) -> int:
    """以 UTF-8 字节数保守估算未配置 tokenizer 的输入规模。"""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
    return len(serialized.encode("utf-8"))


def _latest_summary(
    summaries: Sequence[ContextSummary],
) -> ContextSummary | None:
    if not summaries:
        return None
    return max(
        summaries,
        key=lambda item: (item.version, item.created_at, item.summary_id),
    )


def _latest_workflow_summaries(
    summaries: Sequence[WorkflowSummaryRecord],
) -> dict[str, ContextSummary]:
    latest: dict[str, ContextSummary] = {}
    for item in summaries:
        current = latest.get(item.workflow_id)
        if current is None or (
            item.summary.version,
            item.summary.created_at,
            item.summary.summary_id,
        ) > (
            current.version,
            current.created_at,
            current.summary_id,
        ):
            latest[item.workflow_id] = item.summary
    return latest


def _deduplicate(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


class ContextAssembler:
    """实现 ContextPort，并把权威快照转换为相关模型输入。"""

    def __init__(
        self,
        *,
        source: ContextSnapshotSource,
        model_name: str,
        model_profiles: Mapping[str, ModelContextProfile],
        budget_node: str,
        memory_search: LongTermMemorySearch | None = None,
        token_estimator: TokenEstimator = estimate_context_tokens,
        clock: Clock = _utc_now,
        recent_message_limit: int = 20,
        memory_limit: int = 5,
        token_meter: TokenMeter | None = None,
    ) -> None:
        if isinstance(recent_message_limit, bool) or not isinstance(recent_message_limit, int) or recent_message_limit <= 0:
            raise ValueError("recent_message_limit 必须是正整数")
        if isinstance(memory_limit, bool) or not isinstance(memory_limit, int) or memory_limit <= 0:
            raise ValueError("memory_limit 必须是正整数")
        if not model_name.strip():
            raise ValueError("model_name 不能为空")

        self._source = source
        self._memory_search = memory_search
        self._model_name = model_name.strip()
        self._model_profiles = dict(model_profiles)
        self._policy = get_context_budget_policy(budget_node)
        self._token_estimator = token_estimator
        self._clock = clock
        self._recent_message_limit = recent_message_limit
        self._memory_limit = memory_limit
        self._token_meter = token_meter or TokenMeter()

    async def assemble(self, request: ContextRequest) -> ContextEnvelope:
        """先完成隔离和版本校验，再检索非关键长期记忆并计算预算。"""

        request = ContextRequest.model_validate(request.model_dump(mode="python")).model_copy(deep=True)
        raw_snapshot = await self._source.load_context_snapshot(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
        )
        snapshot = ContextAssemblySnapshot.model_validate(raw_snapshot).model_copy(deep=True)
        self._validate_owner_and_version(request, snapshot)

        workflow = self._select_workflow(request, snapshot)
        conversation_summary = _latest_summary(snapshot.conversation_summaries)
        workflow_summaries = _latest_workflow_summaries(snapshot.workflow_summaries)
        target_summary = workflow_summaries.get(workflow.workflow_id) if workflow is not None else None
        recent_messages = self._select_recent_messages(
            snapshot,
            conversation_summary,
        )
        artifact_refs = self._select_artifact_refs(
            request,
            snapshot,
            workflow,
            conversation_summary,
            target_summary,
        )
        related_summaries = self._select_related_summaries(
            snapshot,
            workflow,
            workflow_summaries,
        )
        unresolved_question_candidates = list(conversation_summary.unresolved_questions) if conversation_summary is not None else []
        if target_summary is not None:
            unresolved_question_candidates.extend(target_summary.unresolved_questions)
        unresolved_questions = _deduplicate(unresolved_question_candidates)
        memories = await self._search_long_term_memories(request)

        payload: dict[str, object] = {
            "current_input": request.current_input,
            "active_or_target_workflow": (workflow.model_copy(deep=True) if workflow is not None else None),
            "recent_messages": recent_messages,
            "conversation_summary": (conversation_summary.model_copy(deep=True) if conversation_summary is not None else None),
            "related_workflow_summaries": [summary.model_copy(deep=True) for summary in related_summaries],
            "relevant_long_term_memories": memories,
            "artifact_evidence_refs": artifact_refs,
            "unresolved_questions": unresolved_questions,
        }
        estimated_input_tokens = self._token_estimator(deepcopy(payload))
        resolution = resolve_model_context_profile(
            self._model_name,
            self._model_profiles,
            now=self._clock(),
        )
        budget_report = self._token_meter.measure(
            estimated_input_tokens=estimated_input_tokens,
            profile=resolution.profile,
            policy=self._policy,
        )
        payload["budget_report"] = budget_report
        return ContextEnvelope.model_validate(payload)

    @staticmethod
    def _validate_owner_and_version(
        request: ContextRequest,
        snapshot: ContextAssemblySnapshot,
    ) -> None:
        if snapshot.user_id != request.user_id or snapshot.conversation_id != request.conversation_id:
            raise KeyError((request.user_id, request.conversation_id))
        if snapshot.context_version != request.expected_context_version:
            raise ContextVersionConflictError(f"上下文版本冲突：expected={request.expected_context_version} actual={snapshot.context_version}")

    @staticmethod
    def _select_workflow(
        request: ContextRequest,
        snapshot: ContextAssemblySnapshot,
    ) -> WorkflowRecord | None:
        selected_id = request.target_workflow_id if request.target_workflow_id is not None else snapshot.active_workflow_id
        if selected_id is None:
            return None
        for workflow in snapshot.workflows:
            if workflow.workflow_id == selected_id:
                return workflow
        raise KeyError((request.user_id, request.conversation_id, selected_id))

    def _select_recent_messages(
        self,
        snapshot: ContextAssemblySnapshot,
        conversation_summary: ContextSummary | None,
    ) -> list[dict[str, JsonValue]]:
        covered_end = conversation_summary.covered_sequence_end if conversation_summary is not None and conversation_summary.covered_sequence_end is not None else 0
        ordered = sorted(
            (message for message in snapshot.messages if message.sequence > covered_end),
            key=lambda item: item.sequence,
        )
        return [deepcopy(message.payload) for message in ordered[-self._recent_message_limit :]]

    @staticmethod
    def _select_related_summaries(
        snapshot: ContextAssemblySnapshot,
        selected_workflow: WorkflowRecord | None,
        summaries: Mapping[str, ContextSummary],
    ) -> list[ContextSummary]:
        other_workflows = [workflow for workflow in snapshot.workflows if selected_workflow is None or workflow.workflow_id != selected_workflow.workflow_id]
        other_workflows.sort(key=lambda item: item.workflow_id)
        other_workflows.sort(
            key=lambda item: item.updated_at,
            reverse=True,
        )
        return [summaries[workflow.workflow_id] for workflow in other_workflows if workflow.workflow_id in summaries]

    @staticmethod
    def _select_artifact_refs(
        request: ContextRequest,
        snapshot: ContextAssemblySnapshot,
        workflow: WorkflowRecord | None,
        conversation_summary: ContextSummary | None,
        target_summary: ContextSummary | None,
    ) -> list[str]:
        available = {item.artifact_ref for item in snapshot.artifact_evidence}
        for artifact_ref in request.artifact_refs:
            if artifact_ref not in available:
                raise KeyError(
                    (
                        request.user_id,
                        request.conversation_id,
                        artifact_ref,
                    )
                )

        candidates = list(request.artifact_refs)
        if workflow is not None:
            candidates.extend(workflow.latest_artifact_refs)
        if conversation_summary is not None:
            candidates.extend(conversation_summary.artifact_evidence_refs)
        if target_summary is not None:
            candidates.extend(target_summary.artifact_evidence_refs)
        return _deduplicate([artifact_ref for artifact_ref in candidates if artifact_ref in available])

    async def _search_long_term_memories(
        self,
        request: ContextRequest,
    ) -> list[dict[str, JsonValue]]:
        if self._memory_search is None:
            return []
        try:
            items = await self._memory_search.search(
                user_id=request.user_id,
                query=request.current_input,
                categories=list(_MEMORY_CATEGORIES),
                source_agent=None,
                limit=self._memory_limit,
            )
        except Exception as exc:
            logger.warning(
                "Context Runtime 的 PowerMem 检索失败，按 fail-open 继续 exception_type=%s",
                type(exc).__name__,
            )
            return []

        normalized: list[dict[str, JsonValue]] = []
        for item in items:
            try:
                raw_item = item if isinstance(item, Mapping) else item.to_dict()
                normalized.append(_MEMORY_ITEM_ADAPTER.validate_python(deepcopy(dict(raw_item))))
            except Exception as exc:
                logger.warning(
                    "Context Runtime 忽略无效 PowerMem 结果 exception_type=%s",
                    type(exc).__name__,
                )
        return normalized


__all__ = [
    "ArtifactEvidenceRecord",
    "ContextAssembler",
    "ContextAssemblySnapshot",
    "ContextMessageRecord",
    "ContextSnapshotSource",
    "ContextVersionConflictError",
    "LongTermMemorySearch",
    "TokenEstimator",
    "WorkflowSummaryRecord",
    "estimate_context_tokens",
]
