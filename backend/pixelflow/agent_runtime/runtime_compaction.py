"""把 M03 预算、M04 摘要/Coordinator/租约接入 R1 真实 Turn 路径。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel

from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    PixelFlowTaskStore,
    SQLPixelFlowTaskStore,
)

from .context import (
    CompactionSegment,
    CompactionStageRequest,
    CompactionStageResult,
    ContextCompactionCoordinator,
    ContextCompactionRequest,
    ConversationCompactionRunResult,
    ConversationCompactionRuntime,
    DeerFlowSummaryEngine,
    ModelContextProfile,
    RepositoryCompactionEventOutbox,
    StructuredSummaryRepository,
    SummaryBuilder,
    SummaryBuildRequest,
    SummaryEvidenceSnapshot,
    SummaryMessageEvidence,
    SummarySourceMessage,
    SummaryVerificationBaseline,
    SummaryVerifier,
    TokenMeter,
    calculate_summary_content_hash,
    estimate_context_tokens,
    get_context_budget_policy,
    parse_model_context_profiles,
    resolve_model_context_profile,
)
from .context.externalizer import ContextPayloadExternalizer
from .contracts import ContextSummary
from .persistence import (
    CompactionQueueRepository,
    MemoryContextPayloadStore,
    SQLContextPayloadStore,
)

_CURRENT_MESSAGE_ID: ContextVar[str | None] = ContextVar(
    "pixelflow_agent_runtime_current_message_id",
    default=None,
)
_EXTERNALIZED_MESSAGES: ContextVar[dict[str, dict[str, object]] | None] = ContextVar(
    "pixelflow_agent_runtime_externalized_messages",
    default=None,
)
_CLAUSE_SPLIT_PATTERN = re.compile(r"(?<=[。！？!?；;])\s*|[\r\n]+")
_NEGATIVE_CONSTRAINT_PATTERN = re.compile(
    r"不要|不得|禁止|不允许|不能|避免|拒绝|切勿|别",
)
_USER_GOAL_PATTERN = re.compile(
    r"请|帮我|需要|想要|生成|制作|创建|分析|编辑|修改|重试|继续|重新",
)
_CONFIRMED_DECISION_PATTERN = re.compile(
    r"已确认|确认采用|决定|选择|同意",
)
_BUSINESS_FACT_PATTERN = re.compile(
    r"商品|产品|品牌|行业|主色|颜色|材质|时长|比例|画幅|模型|"
    r"数量|用途|风格|尺寸|分辨率|文案|声音|音效|语言|受众|平台",
)
_QUESTION_PATTERN = re.compile(r"[?？]$|是否|怎么|如何|哪")
_STABLE_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?=[A-Za-z0-9_-]{3,128}(?![A-Za-z0-9_-]))"
    r"(?=[A-Za-z0-9_-]*\d)"
    r"(?=[A-Za-z0-9_-]*[-_])"
    r"[A-Za-z0-9][A-Za-z0-9_-]*",
)
_PAYLOAD_IDENTIFIER_KEYS = {
    "artifact_id",
    "artifactId",
    "artifact_ref",
    "artifactRef",
    "artifact_refs",
    "artifactRefs",
    "job_id",
    "jobId",
    "task_id",
    "taskId",
    "workflow_id",
    "workflowId",
    "scene_id",
    "sceneId",
    "operation_id",
    "operationId",
}
_RECOVERY_ONLY_CONTEXT_KEYS = {
    "pendingImageRevision",
    "pendingPlanRevision",
    "pendingPlanRevisionRequest",
    "pendingPptOutlineRevision",
    "pendingVideoRevision",
    "pending_image_revision",
    "pending_plan_revision",
    "pending_plan_revision_request",
    "pending_ppt_outline_revision",
    "pending_video_revision",
}


class AgentContextCompactor(Protocol):
    """描述 Turn 登记后自动评估并压缩同一 conversation 的能力。"""

    async def maybe_compact(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        current_message_id: str,
    ) -> ConversationCompactionRunResult | None: ...

    async def retry_compaction(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        current_message_id: str,
    ) -> ConversationCompactionRunResult: ...


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"无法估算 {type(value).__name__} 类型的上下文")


def _message_tokens(message) -> int:
    payload = {
        "role": message.role,
        "content": message.content,
        "payload": message.payload,
    }
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_value,
    )
    return max(1, len(rendered.encode("utf-8")))


def _message_context_payload(message) -> dict[str, object]:
    """把消息 payload 展开到模型消息层，保护稳定元字段不被业务键覆盖。"""

    payload = deepcopy(message.payload)
    payload.update(
        {
            "message_id": message.message_id,
            "role": message.role,
            "content": message.content,
        },
    )
    return payload


def _workflow_summary_payload(workflow) -> dict[str, object]:
    """提供可分层汇总的最小 Workflow 投影，不修改权威合同。"""

    return {
        "workflow_id": workflow.workflow_id,
        "kind": workflow.kind.value,
        "status": workflow.status.value,
        "current_stage": workflow.current_stage,
        "stage_version": workflow.stage_version,
        "context_version": workflow.context_version,
        "latest_artifact_refs": list(workflow.latest_artifact_refs),
    }


def _workflow_state_marker(workflow) -> str:
    """把状态和版本冻结成可判断“是否已经汇总”的稳定证据。"""

    return f"{workflow.current_stage}|stage_version={workflow.stage_version}|context_version={workflow.context_version}"


def _workflow_is_covered(workflow, summary) -> bool:
    if summary is None:
        return False
    return summary.workflow_states.get(workflow.workflow_id) == _workflow_state_marker(workflow) and set(workflow.latest_artifact_refs).issubset(
        summary.artifact_evidence_refs,
    )


def _workflow_summary_tokens(workflow) -> int:
    return estimate_context_tokens(
        {"workflow_summary": _workflow_summary_payload(workflow)},
    )


def _summary_prompt_payload(summary) -> dict[str, object] | None:
    """模型只读取摘要语义，版本链/hash/覆盖证据继续留在权威 Store。"""

    if summary is None:
        return None
    payload: dict[str, object] = {}
    for key, value in (
        ("user_goals", list(summary.user_goals)),
        (
            "confirmed_decisions",
            list(summary.confirmed_decisions),
        ),
        (
            "negative_constraints",
            list(summary.negative_constraints),
        ),
        ("workflow_states", dict(summary.workflow_states)),
        (
            "unresolved_questions",
            list(summary.unresolved_questions),
        ),
        (
            "artifact_evidence_refs",
            list(summary.artifact_evidence_refs),
        ),
    ):
        if value:
            payload[key] = value
    return payload


def _stage_message_tokens(message) -> int:
    externalized = _EXTERNALIZED_MESSAGES.get()
    if externalized is not None:
        payload = externalized.get(message.message_id)
        if payload is not None:
            return estimate_context_tokens({"message": payload})
    return _message_tokens(message)


def _business_context(context: dict) -> dict:
    """保留业务事实，但不把旧前端的完整修订恢复快照重复放进模型输入。"""

    return {
        key: deepcopy(value)
        for key, value in context.items()
        if key != AGENT_RUNTIME_CONTEXT_KEY
        and key not in _RECOVERY_ONLY_CONTEXT_KEYS
    }


def _ordered_unique(values) -> tuple[str, ...]:
    """按首次出现顺序去重，避免验证基线被重复事实稀释。"""

    return tuple(dict.fromkeys(value for value in values if value))


def _message_clauses(content: str) -> tuple[str, ...]:
    """只按明确句界切分，验证时继续使用用户原句而不是模糊改写。"""

    return _ordered_unique(part.strip() for part in _CLAUSE_SPLIT_PATTERN.split(content) if part.strip())


def _artifact_refs_from_payload(value) -> tuple[str, ...]:
    """只抽取稳定 Artifact 引用，不把完整 URL 或任意业务字段写入摘要。"""

    refs: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "artifact_ref",
                "artifactRef",
                "artifact_refs",
                "artifactRefs",
            }:
                candidates = item if isinstance(item, list) else [item]
                refs.extend(candidate.strip() for candidate in candidates if isinstance(candidate, str) and candidate.strip() and "://" not in candidate)
            else:
                refs.extend(_artifact_refs_from_payload(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_artifact_refs_from_payload(item))
    return _ordered_unique(refs)


def _identifiers_from_payload(value) -> tuple[str, ...]:
    """只读取白名单 ID 字段，拒绝 URL、凭据和任意大字符串。"""

    identifiers: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _PAYLOAD_IDENTIFIER_KEYS:
                candidates = item if isinstance(item, list) else [item]
                identifiers.extend(candidate.strip() for candidate in candidates if isinstance(candidate, str) and candidate.strip() and "://" not in candidate and len(candidate.strip()) <= 256)
            else:
                identifiers.extend(_identifiers_from_payload(item))
    elif isinstance(value, list):
        for item in value:
            identifiers.extend(_identifiers_from_payload(item))
    return _ordered_unique(identifiers)


def _summary_verification_baseline(
    *,
    conversation_id: str,
    previous,
    messages,
    workflows,
) -> SummaryVerificationBaseline:
    """从上一摘要和本批原文提取必须精确保留的可验证事实。"""

    user_goals = [] if previous is None else list(previous.user_goals)
    confirmed = [] if previous is None else list(previous.confirmed_decisions)
    negative = [] if previous is None else list(previous.negative_constraints)
    unresolved = [] if previous is None else list(previous.unresolved_questions)
    artifact_refs = [] if previous is None else list(previous.artifact_evidence_refs)
    identifiers: list[str] = []
    for message in messages:
        clauses = _message_clauses(message.content)
        if message.role == "user":
            negative.extend(clause for clause in clauses if _NEGATIVE_CONSTRAINT_PATTERN.search(clause))
            user_goals.extend(clause for clause in clauses if _USER_GOAL_PATTERN.search(clause) and not _NEGATIVE_CONSTRAINT_PATTERN.search(clause))
            unresolved.extend(clause for clause in clauses if _QUESTION_PATTERN.search(clause))
            confirmed.extend(clause for clause in clauses if _BUSINESS_FACT_PATTERN.search(clause) and not _QUESTION_PATTERN.search(clause) and not _NEGATIVE_CONSTRAINT_PATTERN.search(clause))
        else:
            confirmed.extend(clause for clause in clauses if _CONFIRMED_DECISION_PATTERN.search(clause))
        identifiers.extend(_STABLE_IDENTIFIER_PATTERN.findall(message.content))
        identifiers.extend(
            _identifiers_from_payload(message.payload),
        )
        artifact_refs.extend(
            _artifact_refs_from_payload(message.payload),
        )
    workflow_states = {} if previous is None else dict(previous.workflow_states)
    for workflow in workflows:
        workflow_states[workflow.workflow_id] = _workflow_state_marker(workflow)
        artifact_refs.extend(workflow.latest_artifact_refs)
        identifiers.append(workflow.workflow_id)
    identifiers.extend(artifact_refs)
    return SummaryVerificationBaseline(
        conversation_id=conversation_id,
        required_user_goals=_ordered_unique(user_goals),
        required_confirmed_decisions=_ordered_unique(confirmed),
        required_negative_constraints=_ordered_unique(negative),
        required_workflow_states=workflow_states,
        required_unresolved_questions=_ordered_unique(unresolved),
        required_artifact_evidence_refs=_ordered_unique(artifact_refs),
        required_identifiers=_ordered_unique(identifiers),
    )


def _verification_hints(
    baseline: SummaryVerificationBaseline,
) -> dict[str, object]:
    """把精确验证要求显式交给摘要模型，输出缺失时仍由 Verifier 拒绝。"""

    return {
        "user_goals": list(baseline.required_user_goals),
        "confirmed_decisions": list(
            baseline.required_confirmed_decisions,
        ),
        "negative_constraints": list(
            baseline.required_negative_constraints,
        ),
        "workflow_states": baseline.required_workflow_states,
        "unresolved_questions": list(
            baseline.required_unresolved_questions,
        ),
        "artifact_evidence_refs": list(
            baseline.required_artifact_evidence_refs,
        ),
        "stable_identifiers": list(baseline.required_identifiers),
    }


class ContextBudgetGuard:
    """从权威消息、业务 context 和最新摘要生成唯一预算报告。"""

    def __init__(
        self,
        *,
        task_store: PixelFlowTaskStore,
        repository: CompactionQueueRepository,
        model_name: str,
        model_profiles: Mapping[str, ModelContextProfile],
        clock=None,
    ) -> None:
        normalized_model_name = model_name.strip()
        if not normalized_model_name:
            raise ValueError("Context Budget Guard 的 model_name 不能为空")
        self._task_store = task_store
        self._repository = repository
        self._model_name = normalized_model_name
        self._model_profiles = dict(model_profiles)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_meter = TokenMeter()

    async def build_request(
        self,
        *,
        user_id: str,
        conversation_id: str,
        current_message_id: str,
    ) -> ContextCompactionRequest:
        """当前输入永不进入可压缩段，旧消息只按稳定 ID 提交给 M04。"""

        conversation = await self._task_store.get_conversation(
            conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        messages = [
            message
            for message in await self._task_store.list_conversation_messages(
                conversation_id,
                user_id=user_id,
            )
            if message.role
            in {
                "user",
                "assistant",
                "tool",
                "tool_result",
            }
        ]
        summaries = await self._repository.list_summaries(
            user_id,
            conversation_id,
        )
        latest_summary = summaries[-1] if summaries else None
        workflows = await self._repository.list_workflows(
            user_id,
            conversation_id,
        )
        uncovered_workflows = [workflow for workflow in workflows if not _workflow_is_covered(workflow, latest_summary)]
        covered_ids = set(latest_summary.covered_message_ids) if latest_summary is not None else set()
        uncovered = [message for message in messages if message.message_id not in covered_ids]
        current_message = next(
            (message for message in uncovered if message.message_id == current_message_id),
            None,
        )
        if current_message is None:
            raise LookupError("Current conversation message not found")
        compressible = [message for message in uncovered if message.message_id != current_message_id]
        payload = {
            "current_input": ({} if current_message is None else _message_context_payload(current_message)),
            "recent_messages": [_message_context_payload(message) for message in compressible],
            "conversation_summary": _summary_prompt_payload(
                latest_summary,
            ),
            "related_workflow_summaries": [_workflow_summary_payload(workflow) for workflow in uncovered_workflows],
            "business_context": _business_context(conversation.context),
        }
        estimated_input_tokens = estimate_context_tokens(payload)
        resolution = resolve_model_context_profile(
            self._model_name,
            self._model_profiles,
            now=self._clock(),
        )
        budget_report = self._token_meter.measure(
            estimated_input_tokens=estimated_input_tokens,
            profile=resolution.profile,
            policy=get_context_budget_policy("supervisor"),
        )
        return ContextCompactionRequest(
            conversation_id=conversation_id,
            budget_report=budget_report,
            incremental_segments=tuple(
                CompactionSegment(
                    segment_id=message.message_id,
                    estimated_tokens=_message_tokens(message),
                )
                for message in compressible
                if message.role in {"user", "assistant"}
            ),
            workflow_summary_segments=tuple(
                CompactionSegment(
                    segment_id=workflow.workflow_id,
                    estimated_tokens=_workflow_summary_tokens(workflow),
                )
                for workflow in workflows
                if not _workflow_is_covered(
                    workflow,
                    latest_summary,
                )
            ),
        )


class _TaskStoreSummaryEvidenceSource:
    """为 StructuredSummaryRepository 提供同一时点的消息证据。"""

    def __init__(
        self,
        *,
        task_store: PixelFlowTaskStore,
        repository: CompactionQueueRepository,
    ) -> None:
        self._task_store = task_store
        self._repository = repository

    async def load_summary_evidence(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> SummaryEvidenceSnapshot:
        messages = [
            message
            for message in await self._task_store.list_conversation_messages(
                conversation_id,
                user_id=user_id,
            )
            if message.role in {"user", "assistant"}
        ]
        workflows = await self._repository.list_workflows(
            user_id,
            conversation_id,
        )
        artifact_refs = tuple(
            dict.fromkeys(
                (
                    artifact_ref
                    for message in messages
                    for artifact_ref in _artifact_refs_from_payload(
                        message.payload,
                    )
                ),
            )
            | dict.fromkeys(artifact_ref for workflow in workflows for artifact_ref in workflow.latest_artifact_refs),
        )
        return SummaryEvidenceSnapshot(
            user_id=user_id,
            conversation_id=conversation_id,
            messages=tuple(
                SummaryMessageEvidence(
                    message_id=message.message_id,
                    sequence=sequence,
                )
                for sequence, message in enumerate(messages, start=1)
            ),
            artifact_refs=artifact_refs,
        )


class RuntimeCompactionStageExecutor:
    """把 Coordinator 摘要动作适配到 M04 SummaryBuilder 与权威 Store。"""

    def __init__(
        self,
        *,
        task_store: PixelFlowTaskStore,
        repository: CompactionQueueRepository,
        summary_builder: SummaryBuilder,
        externalizer: ContextPayloadExternalizer | None = None,
    ) -> None:
        self._task_store = task_store
        self._repository = repository
        self._summary_builder = summary_builder
        self._externalizer = externalizer or ContextPayloadExternalizer(
            store=MemoryContextPayloadStore(),
        )
        self._summary_repository = StructuredSummaryRepository(
            repository=repository,
            evidence_source=_TaskStoreSummaryEvidenceSource(
                task_store=task_store,
                repository=repository,
            ),
        )

    async def execute(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult:
        """只摘要旧消息；业务 context、当前输入和原始消息始终留在权威 Store。"""

        if request.action == "externalize_payloads":
            return await self._externalize_payloads(request)
        if request.action == "hierarchical_summary":
            return await self._hierarchical_summary(request)
        if request.action in {
            "incremental_summary",
            "hard_gate_summary",
        }:
            return await self._summarize_messages(request)
        if request.action == "minimal_safe_context":
            return await self._minimal_safe_context(request)
        raise ValueError(f"未知压缩动作：{request.action}")

    async def _conversation_owner(self, conversation_id: str) -> str:
        conversation = await self._task_store.get_conversation(
            conversation_id,
            user_id=None,
        )
        if conversation is None or not conversation.user_id:
            raise LookupError("Conversation not found")
        return conversation.user_id

    async def _effective_context_payload(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> dict[str, object]:
        """按最新摘要、外置副本和 Workflow 覆盖证据重建整份有效输入。"""

        conversation = await self._task_store.get_conversation(
            conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        messages = [
            message
            for message in await self._task_store.list_conversation_messages(
                conversation_id,
                user_id=user_id,
            )
            if message.role in {"user", "assistant", "tool", "tool_result"}
        ]
        summaries = await self._repository.list_summaries(
            user_id,
            conversation_id,
        )
        latest_summary = summaries[-1] if summaries else None
        covered_ids = set(latest_summary.covered_message_ids) if latest_summary is not None else set()
        workflows = await self._repository.list_workflows(
            user_id,
            conversation_id,
        )
        current_message_id = _CURRENT_MESSAGE_ID.get()
        current_message = next(
            (message for message in messages if message.message_id == current_message_id),
            None,
        )
        externalized = _EXTERNALIZED_MESSAGES.get() or {}
        recent_messages = []
        for message in messages:
            if message.message_id == current_message_id or message.message_id in covered_ids:
                continue
            recent_messages.append(
                deepcopy(externalized[message.message_id]) if message.message_id in externalized else _message_context_payload(message),
            )
        return {
            "current_input": ({} if current_message is None else _message_context_payload(current_message)),
            "recent_messages": recent_messages,
            "conversation_summary": _summary_prompt_payload(
                latest_summary,
            ),
            "related_workflow_summaries": [
                _workflow_summary_payload(workflow)
                for workflow in workflows
                if not _workflow_is_covered(
                    workflow,
                    latest_summary,
                )
            ],
            "business_context": _business_context(
                conversation.context,
            ),
        }

    async def _externalize_payloads(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult:
        """真实调用 M03 externalizer，完整载荷落库后再按 Prompt 副本重计量。"""

        user_id = await self._conversation_owner(request.conversation_id)
        payload = await self._effective_context_payload(
            user_id=user_id,
            conversation_id=request.conversation_id,
        )
        result = await self._externalizer.externalize(
            user_id=user_id,
            conversation_id=request.conversation_id,
            payload=payload,
        )
        _EXTERNALIZED_MESSAGES.set(
            {str(message["message_id"]): message for message in result.payload["recent_messages"] if isinstance(message, dict) and isinstance(message.get("message_id"), str)},
        )
        remeasured = estimate_context_tokens(result.payload)
        return CompactionStageResult(
            estimated_input_tokens=min(
                request.current_estimated_input_tokens,
                remeasured,
            ),
        )

    async def _hierarchical_summary(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult:
        """把批次 Workflow 状态和证据引用确定性汇总到会话级摘要。"""

        if request.batch is None:
            raise ValueError("hierarchical_summary 缺少 Workflow 批次")
        user_id = await self._conversation_owner(request.conversation_id)
        workflows = await self._repository.list_workflows(
            user_id,
            request.conversation_id,
        )
        allowed_ids = {segment.segment_id for segment in request.batch.segments}
        selected = tuple(workflow for workflow in workflows if workflow.workflow_id in allowed_ids)
        if not selected:
            return CompactionStageResult(
                estimated_input_tokens=request.current_estimated_input_tokens,
            )
        summaries = await self._repository.list_summaries(
            user_id,
            request.conversation_id,
        )
        previous = summaries[-1] if summaries else None
        workflow_states = {} if previous is None else dict(previous.workflow_states)
        artifact_refs = [] if previous is None else list(previous.artifact_evidence_refs)
        for workflow in selected:
            workflow_states[workflow.workflow_id] = _workflow_state_marker(workflow)
            artifact_refs.extend(workflow.latest_artifact_refs)
        summary_draft = ContextSummary(
            summary_id=uuid4().hex,
            conversation_id=request.conversation_id,
            version=1 if previous is None else previous.version + 1,
            previous_summary_id=(None if previous is None else previous.summary_id),
            content_hash="pending-verification",
            user_goals=([] if previous is None else list(previous.user_goals)),
            confirmed_decisions=([] if previous is None else list(previous.confirmed_decisions)),
            negative_constraints=([] if previous is None else list(previous.negative_constraints)),
            workflow_states=workflow_states,
            unresolved_questions=([] if previous is None else list(previous.unresolved_questions)),
            artifact_evidence_refs=list(
                dict.fromkeys(artifact_refs),
            ),
            covered_message_ids=([] if previous is None else list(previous.covered_message_ids)),
            covered_sequence_start=(None if previous is None else previous.covered_sequence_start),
            covered_sequence_end=(None if previous is None else previous.covered_sequence_end),
            compression_model=("deterministic-workflow-hierarchy-v1"),
            created_at=datetime.now(UTC),
        )
        summary = summary_draft.model_copy(
            update={
                "content_hash": calculate_summary_content_hash(
                    summary_draft,
                ),
            },
            deep=True,
        )
        baseline = _summary_verification_baseline(
            conversation_id=request.conversation_id,
            previous=previous,
            messages=[],
            workflows=selected,
        )
        SummaryVerifier().verify(summary, baseline)
        current_payload = await self._effective_context_payload(
            user_id=user_id,
            conversation_id=request.conversation_id,
        )
        current_tokens = estimate_context_tokens(current_payload)
        candidate_payload = deepcopy(current_payload)
        candidate_payload["conversation_summary"] = _summary_prompt_payload(summary)
        candidate_payload["related_workflow_summaries"] = [item for item in candidate_payload["related_workflow_summaries"] if isinstance(item, dict) and item.get("workflow_id") not in allowed_ids]
        candidate_tokens = estimate_context_tokens(candidate_payload)
        if candidate_tokens >= current_tokens:
            return CompactionStageResult(
                estimated_input_tokens=(request.current_estimated_input_tokens),
            )
        await self._summary_repository.save(user_id, summary)
        reduced_tokens = request.current_estimated_input_tokens - (current_tokens - candidate_tokens)
        return CompactionStageResult(
            estimated_input_tokens=max(0, reduced_tokens),
        )

    async def _summarize_messages(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult:
        user_id = await self._conversation_owner(request.conversation_id)
        messages = [
            message
            for message in await self._task_store.list_conversation_messages(
                request.conversation_id,
                user_id=user_id,
            )
            if message.role in {"user", "assistant"}
        ]
        summaries = await self._repository.list_summaries(
            user_id,
            request.conversation_id,
        )
        previous = summaries[-1] if summaries else None
        covered_end = previous.covered_sequence_end if previous is not None and previous.covered_sequence_end is not None else 0
        current_message_id = _CURRENT_MESSAGE_ID.get()
        current_sequence = next(
            (sequence for sequence, message in enumerate(messages, start=1) if message.message_id == current_message_id),
            len(messages) + 1,
        )
        allowed_ids = {segment.segment_id for segment in request.batch.segments} if request.batch is not None else {message.message_id for sequence, message in enumerate(messages, start=1) if covered_end < sequence < current_sequence}
        source_records = tuple((sequence, message) for sequence, message in enumerate(messages, start=1) if covered_end < sequence < current_sequence and message.message_id in allowed_ids)
        if not source_records:
            return CompactionStageResult(
                estimated_input_tokens=request.current_estimated_input_tokens,
            )
        workflows = await self._repository.list_workflows(
            user_id,
            request.conversation_id,
        )
        verification_baseline = _summary_verification_baseline(
            conversation_id=request.conversation_id,
            previous=previous,
            messages=[message for _, message in source_records],
            workflows=workflows,
        )
        hints = _verification_hints(verification_baseline)
        externalized_messages = _EXTERNALIZED_MESSAGES.get() or {}
        source_messages: list[SummarySourceMessage] = []
        for index, (sequence, message) in enumerate(source_records):
            externalized = externalized_messages.get(
                message.message_id,
            )
            content = (
                deepcopy(externalized)
                if externalized is not None
                else {
                    "content": message.content,
                    "payload": deepcopy(message.payload),
                }
            )
            content["verification_requirements"] = hints if index == 0 else {}
            source_messages.append(
                SummarySourceMessage(
                    conversation_id=request.conversation_id,
                    message_id=message.message_id,
                    sequence=sequence,
                    role=message.role,
                    content=content,
                ),
            )
        frozen_source_messages = tuple(source_messages)
        build_result = await self._summary_builder.build(
            SummaryBuildRequest(
                conversation_id=request.conversation_id,
                previous_summary=previous,
                new_messages=frozen_source_messages,
                verification_baseline=verification_baseline,
            ),
        )
        saved = await self._summary_repository.save(
            user_id,
            build_result.summary,
        )
        summary_tokens = estimate_context_tokens(
            {
                "summary": saved.model_dump(mode="json"),
            },
        )
        replaced_tokens = sum(_stage_message_tokens(messages[item.sequence - 1]) for item in frozen_source_messages)
        remeasured = request.current_estimated_input_tokens - replaced_tokens + summary_tokens
        return CompactionStageResult(
            estimated_input_tokens=max(0, remeasured),
        )

    async def _minimal_safe_context(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult:
        user_id = await self._conversation_owner(request.conversation_id)
        conversation = await self._task_store.get_conversation(
            request.conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        messages = [
            message
            for message in await self._task_store.list_conversation_messages(
                request.conversation_id,
                user_id=user_id,
            )
            if message.role in {"user", "assistant"}
        ]
        summaries = await self._repository.list_summaries(
            user_id,
            request.conversation_id,
        )
        current_message_id = _CURRENT_MESSAGE_ID.get()
        current_message = next(
            (message for message in messages if message.message_id == current_message_id),
            None,
        )
        minimal_tokens = estimate_context_tokens(
            {
                "current_input": ({} if current_message is None else _message_context_payload(current_message)),
                "conversation_summary": _summary_prompt_payload(
                    None if not summaries else summaries[-1],
                ),
                "business_context": _business_context(
                    conversation.context,
                ),
            },
        )
        return CompactionStageResult(
            estimated_input_tokens=min(
                request.current_estimated_input_tokens,
                minimal_tokens,
            ),
        )


class AutomaticConversationCompactor:
    """达到阈值时同步取得租约，让并发输入由后端直接进入 queued。"""

    def __init__(
        self,
        *,
        budget_guard: ContextBudgetGuard,
        runtime: ConversationCompactionRuntime,
    ) -> None:
        self._budget_guard = budget_guard
        self._runtime = runtime

    async def maybe_compact(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        current_message_id: str,
    ) -> ConversationCompactionRunResult | None:
        request = await self._budget_guard.build_request(
            user_id=user_id,
            conversation_id=conversation_id,
            current_message_id=current_message_id,
        )
        if request.budget_report.compaction_level == 0:
            return None
        context_token = _CURRENT_MESSAGE_ID.set(current_message_id)
        externalized_token = _EXTERNALIZED_MESSAGES.set(None)
        try:
            return await self._runtime.compact(
                user_id,
                request,
                run_id=run_id,
            )
        finally:
            _EXTERNALIZED_MESSAGES.reset(externalized_token)
            _CURRENT_MESSAGE_ID.reset(context_token)

    async def retry_compaction(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        current_message_id: str,
    ) -> ConversationCompactionRunResult:
        """接管 retry_required 时即使重新计量已低于阈值，也要释放旧租约并领取队首。"""

        request = await self._budget_guard.build_request(
            user_id=user_id,
            conversation_id=conversation_id,
            current_message_id=current_message_id,
        )
        context_token = _CURRENT_MESSAGE_ID.set(current_message_id)
        externalized_token = _EXTERNALIZED_MESSAGES.set(None)
        try:
            return await self._runtime.compact(
                user_id,
                request,
                run_id=run_id,
            )
        finally:
            _EXTERNALIZED_MESSAGES.reset(externalized_token)
            _CURRENT_MESSAGE_ID.reset(context_token)


def build_agent_context_compactor(
    *,
    task_store: PixelFlowTaskStore,
    repository: CompactionQueueRepository,
    app_config,
) -> AutomaticConversationCompactor:
    """按启动快照装配真实摘要模型、四阈值 Coordinator 和租约 Runtime。"""

    from deerflow.agents.middlewares.summarization_middleware import (
        DeerFlowSummarizationMiddleware,
    )
    from deerflow.models.factory import create_chat_model

    if not app_config.models:
        raise ValueError("启用 Context 压缩时至少需要一个模型配置")
    summary_model_name = app_config.summarization.model_name or app_config.models[0].name
    model = create_chat_model(
        name=summary_model_name,
        thinking_enabled=False,
        app_config=app_config,
        attach_tracing=False,
    ).with_config(tags=["pixelflow:context_compaction"])
    middleware = DeerFlowSummarizationMiddleware(
        model=model,
        trigger=None,
    )
    summary_engine = DeerFlowSummaryEngine.from_middleware(
        middleware,
        model_name=summary_model_name,
    )
    summary_builder = SummaryBuilder(engine=summary_engine)
    model_profiles = parse_model_context_profiles(app_config.models)
    payload_store = SQLContextPayloadStore(task_store.session_factory) if isinstance(task_store, SQLPixelFlowTaskStore) else MemoryContextPayloadStore()
    executor = RuntimeCompactionStageExecutor(
        task_store=task_store,
        repository=repository,
        summary_builder=summary_builder,
        externalizer=ContextPayloadExternalizer(
            store=payload_store,
        ),
    )
    coordinator = ContextCompactionCoordinator(
        executor=executor,
        summary_model_name=summary_model_name,
        model_profiles=model_profiles,
    )
    runtime = ConversationCompactionRuntime(
        coordinator=coordinator,
        repository=repository,
        lease_owner=f"gateway-r1-{uuid4().hex}",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(
            repository=repository,
        ),
    )
    return AutomaticConversationCompactor(
        budget_guard=ContextBudgetGuard(
            task_store=task_store,
            repository=repository,
            model_name=summary_model_name,
            model_profiles=model_profiles,
        ),
        runtime=runtime,
    )


__all__ = [
    "AgentContextCompactor",
    "AutomaticConversationCompactor",
    "ContextBudgetGuard",
    "RuntimeCompactionStageExecutor",
    "build_agent_context_compactor",
]
