"""用上一版结构化摘要和连续新消息构建下一版摘要。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from ..contracts import AgentEventType, ContextBudgetReport, ContextSummary, TurnRecord
from ..persistence.compaction_queue import (
    CompactionLeaseConflictError,
    CompactionQueueRepository,
    ConversationCompactionLease,
)
from .compaction_events import CompactionEventSink
from .profiles import ModelContextProfile, resolve_model_context_profile
from .token_meter import TokenMeter, get_context_budget_policy
from .verification import (
    SummaryVerificationBaseline,
    SummaryVerifier,
    calculate_summary_content_hash,
)


class SummaryBuildValidationError(ValueError):
    """摘要构建输入不满足同会话连续增量约束。"""


class SummaryGenerationError(RuntimeError):
    """摘要 Engine 没有返回可验证的结构化结果。"""


class CompactionValidationError(ValueError):
    """压缩请求或分块不满足冻结预算合同。"""


class CompactionExecutionError(RuntimeError):
    """压缩阶段没有返回单调不增的安全计量结果。"""


class CompactionProgressError(RuntimeError):
    """压缩进度事件没有成功进入持久化 Outbox。"""


COMPACTION_STARTED_MESSAGE = "对话内容较长，正在整理上下文，当前任务和已生成内容不会丢失。"
COMPACTION_COMPLETED_MESSAGE = "上下文整理完成，正在继续处理刚才的请求。"
COMPACTION_FAILED_MESSAGE = "上下文整理暂时未完成，你的输入已保留，系统将继续重试。"


PIXELFLOW_STRUCTURED_SUMMARY_PROMPT = (
    "你是 PixelFlow 结构化上下文摘要器。输入只包含上一版结构化摘要和本版新增消息。\n"
    "请综合两者返回一份完整的新语义快照；允许移除已经解决的问题或替换已变更的决定，\n"
    "但不得编造输入中不存在的 Plan、创作合同、资产、pending action、operation 或凭据。\n"
    "不要输出思维链、Markdown、代码围栏或额外说明，只返回一个 JSON 对象。\n\n"
    "JSON 字段必须严格如下：\n"
    '{{\n  "user_goals": ["用户目标"],\n'
    '  "confirmed_decisions": ["已确认决定"],\n'
    '  "negative_constraints": ["否定约束"],\n'
    '  "workflow_states": {{"workflow_id": "状态摘要"}},\n'
    '  "unresolved_questions": ["未决问题"],\n'
    '  "artifact_evidence_refs": ["稳定 Artifact 引用"]\n}}\n\n'
    "没有内容的数组或对象必须返回空值容器，不得省略字段。\n\n"
    "<messages>\n{messages}\n</messages>"
)


class _CompactionRecord(BaseModel):
    """冻结顶层字段；嵌套值在每个异步或外部边界前深拷贝。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class SummarySourceMessage(_CompactionRecord):
    """只携带摘要所需的原始消息，不接受业务状态 DTO。"""

    conversation_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: JsonValue


class SummarySemanticSnapshot(_CompactionRecord):
    """隔离版本元数据后的结构化摘要语义快照。"""

    user_goals: tuple[str, ...] = ()
    confirmed_decisions: tuple[str, ...] = ()
    negative_constraints: tuple[str, ...] = ()
    workflow_states: dict[str, str] = Field(default_factory=dict)
    unresolved_questions: tuple[str, ...] = ()
    artifact_evidence_refs: tuple[str, ...] = ()


class SummaryBuildRequest(_CompactionRecord):
    """请求只包含上一版摘要和该版尚未覆盖的新消息。"""

    conversation_id: str = Field(min_length=1)
    previous_summary: ContextSummary | None = None
    new_messages: tuple[SummarySourceMessage, ...] = ()
    verification_baseline: SummaryVerificationBaseline

    @model_validator(mode="after")
    def require_verification_conversation(self) -> Self:
        """验证基线必须和本轮增量摘要属于同一 conversation。"""

        if self.verification_baseline.conversation_id != self.conversation_id:
            raise ValueError("verification_baseline 必须属于当前 conversation_id")
        return self


class SummaryGenerationInput(_CompactionRecord):
    """提供给摘要 Engine 的最小增量输入，不包含业务上下文。"""

    conversation_id: str = Field(min_length=1)
    previous_summary: SummarySemanticSnapshot | None = None
    new_messages: tuple[SummarySourceMessage, ...] = ()


class SummaryBuildResult(_CompactionRecord):
    """同时返回结构化摘要和 DeerFlow 口径的源 token 数。"""

    summary: ContextSummary
    source_token_count: int = Field(ge=0)


CompactionAction = Literal[
    "externalize_payloads",
    "incremental_summary",
    "hierarchical_summary",
    "hard_gate_summary",
    "minimal_safe_context",
]
CompactionBatchScope = Literal["messages", "workflow_summaries"]
CompactionStatus = Literal[
    "not_required",
    "target_reached",
    "target_not_reached",
    "minimal_safe_context",
    "paused",
]


class CompactionSegment(_CompactionRecord):
    """只描述可压缩段的稳定标识和估算规模，不携带业务权威对象。"""

    segment_id: str = Field(min_length=1)
    estimated_tokens: int = Field(ge=1, strict=True)


class CompactionBatch(_CompactionRecord):
    """表示一次摘要节点可安全处理的有序输入批次。"""

    scope: CompactionBatchScope
    batch_index: int = Field(ge=1, strict=True)
    batch_count: int = Field(ge=1, strict=True)
    segments: tuple[CompactionSegment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_valid_batch_position(self) -> Self:
        """保证批次位置不会超出同轮分块总数。"""

        if self.batch_index > self.batch_count:
            raise ValueError("batch_index 不能大于 batch_count")
        return self

    @property
    def estimated_tokens(self) -> int:
        """返回批次内所有来源段的累计 token 估算。"""

        return sum(segment.estimated_tokens for segment in self.segments)


class CompactionStageRequest(_CompactionRecord):
    """向具体压缩策略提交一次无业务合同的编排动作。"""

    conversation_id: str = Field(min_length=1)
    action: CompactionAction
    target_input_tokens: int = Field(ge=0, strict=True)
    current_estimated_input_tokens: int = Field(ge=0, strict=True)
    batch: CompactionBatch | None = None

    @model_validator(mode="after")
    def require_action_batch_match(self) -> Self:
        """摘要动作必须携带对应作用域批次，其他动作不得夹带批次。"""

        if self.action == "incremental_summary":
            if self.batch is None or self.batch.scope != "messages":
                raise ValueError("incremental_summary 的 batch 必须是 messages")
        elif self.action == "hierarchical_summary":
            if self.batch is None or self.batch.scope != "workflow_summaries":
                raise ValueError("hierarchical_summary 的 batch 必须是 workflow_summaries")
        elif self.batch is not None:
            raise ValueError(f"{self.action} 的 batch 必须为空")
        return self


class CompactionStageResult(_CompactionRecord):
    """压缩策略完成后必须返回重新组装得到的输入 token 数。"""

    estimated_input_tokens: int = Field(ge=0, strict=True)


class ContextCompactionRequest(_CompactionRecord):
    """只接收预算和可压缩段，业务状态始终留在 Coordinator 之外。"""

    conversation_id: str = Field(min_length=1)
    budget_report: ContextBudgetReport
    incremental_segments: tuple[CompactionSegment, ...] = ()
    workflow_summary_segments: tuple[CompactionSegment, ...] = ()

    @model_validator(mode="after")
    def require_unique_segment_ids(self) -> Self:
        """拒绝重复处理同一消息段或 Workflow 摘要段。"""

        segment_ids = [segment.segment_id for segment in self.incremental_segments + self.workflow_summary_segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("可压缩段 segment_id 不能重复")
        return self


class CompactionAttempt(_CompactionRecord):
    """记录一个成功完成并重新计量的压缩动作。"""

    action: CompactionAction
    estimated_input_tokens_before: int = Field(ge=0, strict=True)
    estimated_input_tokens_after: int = Field(ge=0, strict=True)
    batch: CompactionBatch | None = None


type CompactionProgressObserver = Callable[[CompactionAttempt], Awaitable[None]]


class ContextCompactionResult(_CompactionRecord):
    """向调用方返回是否允许继续模型调用的安全结论。"""

    status: CompactionStatus
    initial_budget_report: ContextBudgetReport
    final_budget_report: ContextBudgetReport
    target_input_tokens: int = Field(ge=0, strict=True)
    attempts: tuple[CompactionAttempt, ...] = ()
    model_invocation_allowed: bool
    pause_reason: Literal["hard_gate_compaction_failed"] | None = None

    @model_validator(mode="after")
    def require_safe_status(self) -> Self:
        """保证暂停与放行标记不会形成相互矛盾的结果。"""

        expected_target = _strict_target_input_tokens(self.initial_budget_report.usable_input_tokens)
        if self.target_input_tokens != expected_target:
            raise ValueError("target_input_tokens 必须严格对应 45% 回落目标")
        if self.status == "paused":
            if self.model_invocation_allowed:
                raise ValueError("压缩暂停时不能允许模型调用")
            if self.pause_reason is None:
                raise ValueError("压缩暂停时必须提供安全原因码")
            return self
        if not self.model_invocation_allowed:
            raise ValueError("非暂停结果必须允许模型调用")
        if self.pause_reason is not None:
            raise ValueError("只有压缩暂停结果可以提供 pause_reason")
        final_tokens = self.final_budget_report.estimated_input_tokens
        if self.status == "target_reached" and final_tokens > self.target_input_tokens:
            raise ValueError("target_reached 必须严格低于 45% 回落目标")
        if self.status in {"target_not_reached", "minimal_safe_context"} and final_tokens <= self.target_input_tokens:
            raise ValueError(f"{self.status} 不能误报已经达到回落目标")
        if self.status == "minimal_safe_context" and final_tokens >= self.final_budget_report.usable_input_tokens:
            raise ValueError("minimal_safe_context 必须低于可用输入上限")
        if self.status == "not_required":
            if self.initial_budget_report.compaction_level != 0:
                raise ValueError("not_required 只允许用于未触发压缩的上下文")
            if final_tokens != self.initial_budget_report.estimated_input_tokens:
                raise ValueError("not_required 不能改变输入 token")
        return self


CompactionRunStatus = Literal[
    "completed",
    "already_running",
    "paused",
]


class ConversationCompactionRunResult(_CompactionRecord):
    """返回压缩互斥状态、Coordinator 结论和下一条已领取输入。"""

    status: CompactionRunStatus
    compaction_result: ContextCompactionResult | None = None
    next_turn: TurnRecord | None = None

    @model_validator(mode="after")
    def require_status_payload_match(self) -> Self:
        """防止等待、暂停和完成结果携带互相矛盾的队列状态。"""

        if self.status == "already_running":
            if self.compaction_result is not None or self.next_turn is not None:
                raise ValueError("already_running 不能携带压缩结果或下一 Turn")
            return self
        if self.compaction_result is None:
            raise ValueError("压缩完成或暂停时必须携带 Coordinator 结果")
        if self.status == "paused":
            if self.compaction_result.status != "paused":
                raise ValueError("paused 必须对应 Coordinator 暂停结果")
            if self.next_turn is not None:
                raise ValueError("压缩暂停时不能领取下一 Turn")
            return self
        if self.compaction_result.status == "paused":
            raise ValueError("completed 不能携带 Coordinator 暂停结果")
        return self


class CompactionStageExecutor(Protocol):
    """把编排动作适配到载荷外置、SummaryBuilder 或层级摘要。"""

    async def execute(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult: ...


class SummaryEngine(Protocol):
    """抽象 DeerFlow token 计量与异步摘要能力。"""

    model_name: str

    def count_tokens(self, source: SummaryGenerationInput) -> int: ...

    async def summarize(
        self,
        source: SummaryGenerationInput,
    ) -> SummarySemanticSnapshot: ...


def _semantic_snapshot(summary: ContextSummary) -> SummarySemanticSnapshot:
    return SummarySemanticSnapshot(
        user_goals=tuple(summary.user_goals),
        confirmed_decisions=tuple(summary.confirmed_decisions),
        negative_constraints=tuple(summary.negative_constraints),
        workflow_states=dict(summary.workflow_states),
        unresolved_questions=tuple(summary.unresolved_questions),
        artifact_evidence_refs=tuple(summary.artifact_evidence_refs),
    )


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_incremental_range(request: SummaryBuildRequest) -> None:
    previous = request.previous_summary
    if previous is not None and previous.conversation_id != request.conversation_id:
        raise SummaryBuildValidationError("上一版摘要不属于当前 conversation_id")
    if not request.new_messages:
        raise SummaryBuildValidationError("增量摘要至少包含一条新消息")

    if any(message.conversation_id != request.conversation_id for message in request.new_messages):
        raise SummaryBuildValidationError("新消息 conversation_id 必须属于当前会话")

    sequences = [message.sequence for message in request.new_messages]
    if any(current <= previous_value for previous_value, current in zip(sequences, sequences[1:], strict=False)):
        raise SummaryBuildValidationError("新消息 sequence 必须严格递增")
    if sequences != list(range(sequences[0], sequences[-1] + 1)):
        raise SummaryBuildValidationError("新消息 sequence 必须连续")

    previous_end = previous.covered_sequence_end if previous is not None else None
    expected_start = 1 if previous_end is None else previous_end + 1
    if sequences[0] != expected_start:
        raise SummaryBuildValidationError(f"新消息必须从 sequence {expected_start} 开始")

    new_message_ids = [message.message_id for message in request.new_messages]
    if len(new_message_ids) != len(set(new_message_ids)):
        raise SummaryBuildValidationError("新消息 message_id 不能重复")
    previous_message_ids = set(previous.covered_message_ids) if previous is not None else set()
    if previous_message_ids.intersection(new_message_ids):
        raise SummaryBuildValidationError("新消息 message_id 不能改写已有覆盖")


def _deerflow_messages(source: SummaryGenerationInput) -> list[AnyMessage]:
    messages: list[AnyMessage] = []
    if source.previous_summary is not None:
        messages.append(
            HumanMessage(
                name="summary",
                content=_canonical_json(
                    {
                        "kind": "previous_structured_summary",
                        "summary": source.previous_summary.model_dump(mode="json"),
                    }
                ),
            )
        )
    for message in source.new_messages:
        content = _canonical_json(
            {
                "kind": "new_message",
                "message_id": message.message_id,
                "sequence": message.sequence,
                "content": message.content,
            }
        )
        if message.role == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return messages


class DeerFlowSummaryEngine:
    """把 DeerFlow middleware 的现有计量和摘要能力适配为结构化 Engine。"""

    def __init__(
        self,
        *,
        model_name: str,
        token_counter: Callable[[list[AnyMessage]], int],
        summary_runner: Callable[[list[AnyMessage]], Awaitable[str]],
    ) -> None:
        normalized_model_name = model_name.strip()
        if not normalized_model_name:
            raise ValueError("model_name 不能为空")
        self.model_name = normalized_model_name
        self._token_counter = token_counter
        self._summary_runner = summary_runner

    @classmethod
    def from_middleware(
        cls,
        middleware: Any,
        *,
        model_name: str,
    ) -> DeerFlowSummaryEngine:
        """复用 DeerFlow 模型与计量器，创建专用结构化摘要实例。"""

        model = getattr(middleware, "model", None)
        token_counter = getattr(middleware, "token_counter", None)
        if model is None or not callable(token_counter):
            raise TypeError("middleware 缺少 DeerFlow 摘要能力")
        trim_tokens = getattr(middleware, "trim_tokens_to_summarize", 4000)

        from deerflow.agents.middlewares.summarization_middleware import (
            DeerFlowSummarizationMiddleware,
        )

        structured_middleware = DeerFlowSummarizationMiddleware(
            model=model,
            trigger=None,
            token_counter=token_counter,
            summary_prompt=PIXELFLOW_STRUCTURED_SUMMARY_PROMPT,
            trim_tokens_to_summarize=trim_tokens,
        )
        return cls(
            model_name=model_name,
            token_counter=structured_middleware.token_counter,
            summary_runner=structured_middleware._acreate_summary,
        )

    def count_tokens(self, source: SummaryGenerationInput) -> int:
        """复用 DeerFlow token counter 计量同一份增量摘要输入。"""

        count = self._token_counter(_deerflow_messages(source))
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise SummaryGenerationError("DeerFlow token counter 返回非法结果")
        return count

    async def summarize(
        self,
        source: SummaryGenerationInput,
    ) -> SummarySemanticSnapshot:
        """复用 DeerFlow 异步摘要调用，并严格解析结构化 JSON。"""

        try:
            raw_summary = await self._summary_runner(_deerflow_messages(source))
            return SummarySemanticSnapshot.model_validate_json(raw_summary)
        except (TypeError, ValueError, ValidationError):
            raise SummaryGenerationError("DeerFlow 未返回合法结构化摘要") from None


class SummaryBuilder:
    """校验增量范围并生成可交给 M04.1 Repository 保存的下一版摘要。"""

    def __init__(
        self,
        *,
        engine: SummaryEngine,
        summary_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        verifier: SummaryVerifier | None = None,
    ) -> None:
        self._engine = engine
        self._summary_id_factory = summary_id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._verifier = verifier or SummaryVerifier()

    async def build(
        self,
        request: SummaryBuildRequest,
    ) -> SummaryBuildResult:
        """冻结输入后只摘要上一版尚未覆盖的连续消息。"""

        frozen_request = SummaryBuildRequest.model_validate(request.model_dump(mode="python"))
        _validate_incremental_range(frozen_request)
        previous = frozen_request.previous_summary
        generation_input = SummaryGenerationInput(
            conversation_id=frozen_request.conversation_id,
            previous_summary=(_semantic_snapshot(previous) if previous is not None else None),
            new_messages=frozen_request.new_messages,
        )

        count_input = SummaryGenerationInput.model_validate(generation_input.model_dump(mode="python"))
        source_token_count = self._engine.count_tokens(count_input)
        if isinstance(source_token_count, bool) or not isinstance(source_token_count, int) or source_token_count < 0:
            raise SummaryGenerationError("摘要 Engine 返回非法 token 数")
        summary_input = SummaryGenerationInput.model_validate(generation_input.model_dump(mode="python"))
        generated = await self._engine.summarize(summary_input)
        semantic = SummarySemanticSnapshot.model_validate(generated.model_dump(mode="python"))

        summary_id = self._summary_id_factory().strip()
        model_name = self._engine.model_name.strip()
        created_at = self._clock()
        if created_at.tzinfo is None:
            raise SummaryBuildValidationError("摘要创建时间必须包含时区")
        created_at = created_at.astimezone(UTC)

        covered_message_ids = list(previous.covered_message_ids) if previous is not None else []
        covered_message_ids.extend(message.message_id for message in frozen_request.new_messages)
        covered_sequence_end = frozen_request.new_messages[-1].sequence
        summary_payload: dict[str, object] = {
            "summary_id": summary_id,
            "conversation_id": frozen_request.conversation_id,
            "version": 1 if previous is None else previous.version + 1,
            "previous_summary_id": (None if previous is None else previous.summary_id),
            "content_hash": "pending-verification",
            "covered_message_ids": covered_message_ids,
            "covered_sequence_start": 1,
            "covered_sequence_end": covered_sequence_end,
            "compression_model": model_name,
            "created_at": created_at,
        }
        summary_payload.update(semantic.model_dump(mode="python"))
        summary_draft = ContextSummary.model_validate(summary_payload)
        summary = summary_draft.model_copy(
            update={"content_hash": calculate_summary_content_hash(summary_draft)},
            deep=True,
        )
        self._verifier.verify(summary, frozen_request.verification_baseline)
        return SummaryBuildResult(
            summary=summary,
            source_token_count=source_token_count,
        )


def _strict_target_input_tokens(usable_input_tokens: int) -> int:
    """计算严格低于 45% 时允许的最大整数 token 数。"""

    return (usable_input_tokens * 45 - 1) // 100


def _build_compaction_batches(
    *,
    scope: CompactionBatchScope,
    segments: tuple[CompactionSegment, ...],
    limit_tokens: int,
) -> tuple[CompactionBatch, ...]:
    """按来源顺序贪心分块，保证每块不超过摘要节点实际预算。"""

    if any(segment.estimated_tokens > limit_tokens for segment in segments):
        raise CompactionValidationError("单段压缩输入超过摘要节点预算，必须先外置或拆分来源")
    grouped: list[tuple[CompactionSegment, ...]] = []
    current: list[CompactionSegment] = []
    current_tokens = 0
    for segment in segments:
        if current and current_tokens + segment.estimated_tokens > limit_tokens:
            grouped.append(tuple(current))
            current = []
            current_tokens = 0
        current.append(segment)
        current_tokens += segment.estimated_tokens
    if current:
        grouped.append(tuple(current))
    batch_count = len(grouped)
    return tuple(
        CompactionBatch(
            scope=scope,
            batch_index=index,
            batch_count=batch_count,
            segments=batch_segments,
        )
        for index, batch_segments in enumerate(grouped, start=1)
    )


class ContextCompactionCoordinator:
    """统一执行四阈值、分块、层级压缩和 92% 硬闸门。"""

    def __init__(
        self,
        *,
        executor: CompactionStageExecutor,
        summary_model_name: str,
        model_profiles: Mapping[str, ModelContextProfile],
        token_meter: TokenMeter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_model_name = summary_model_name.strip()
        if not normalized_model_name:
            raise ValueError("summary_model_name 不能为空")
        frozen_profiles: dict[str, ModelContextProfile] = {}
        for profile_name, profile in model_profiles.items():
            frozen_profile = ModelContextProfile.model_validate(profile.model_dump(mode="python"))
            if profile_name != frozen_profile.model_name:
                raise ValueError("摘要模型档案键必须与 model_name 一致")
            frozen_profiles[profile_name] = frozen_profile
        self._executor = executor
        self._summary_model_name = normalized_model_name
        self._model_profiles = MappingProxyType(frozen_profiles)
        self._token_meter = token_meter or TokenMeter()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def coordinate(
        self,
        request: ContextCompactionRequest,
        *,
        on_progress: CompactionProgressObserver | None = None,
    ) -> ContextCompactionResult:
        """冻结请求并按初始压缩等级累进执行，达到目标后立即停止。"""

        frozen = ContextCompactionRequest.model_validate(request.model_dump(mode="python"))
        initial = self._token_meter.remeasure(
            estimated_input_tokens=frozen.budget_report.estimated_input_tokens,
            baseline=frozen.budget_report,
        )
        if initial.compaction_level != frozen.budget_report.compaction_level:
            raise CompactionValidationError("budget_report.compaction_level 与统一阈值计算不一致")
        target_input_tokens = _strict_target_input_tokens(initial.usable_input_tokens)
        if initial.compaction_level == 0:
            return self._result(
                status="not_required",
                initial=initial,
                current_tokens=initial.estimated_input_tokens,
                target_input_tokens=target_input_tokens,
                attempts=[],
                model_invocation_allowed=True,
            )

        current_tokens = initial.estimated_input_tokens
        attempts: list[CompactionAttempt] = []
        try:
            summary_chunk_limit_tokens = self._summary_chunk_limit_tokens() if initial.compaction_level >= 2 else None
            incremental_batches = (
                _build_compaction_batches(
                    scope="messages",
                    segments=frozen.incremental_segments,
                    limit_tokens=summary_chunk_limit_tokens,
                )
                if summary_chunk_limit_tokens is not None
                else ()
            )
            hierarchical_batches = (
                _build_compaction_batches(
                    scope="workflow_summaries",
                    segments=frozen.workflow_summary_segments,
                    limit_tokens=summary_chunk_limit_tokens,
                )
                if initial.compaction_level >= 3 and summary_chunk_limit_tokens is not None
                else ()
            )
            current_tokens = await self._execute_action(
                frozen,
                action="externalize_payloads",
                batch=None,
                current_tokens=current_tokens,
                target_input_tokens=target_input_tokens,
                attempts=attempts,
                on_progress=on_progress,
            )
            if current_tokens <= target_input_tokens:
                return self._target_result(
                    initial,
                    current_tokens,
                    target_input_tokens,
                    attempts,
                )

            if initial.compaction_level >= 2:
                for batch in incremental_batches:
                    current_tokens = await self._execute_action(
                        frozen,
                        action="incremental_summary",
                        batch=batch,
                        current_tokens=current_tokens,
                        target_input_tokens=target_input_tokens,
                        attempts=attempts,
                        on_progress=on_progress,
                    )
                    if current_tokens <= target_input_tokens:
                        return self._target_result(
                            initial,
                            current_tokens,
                            target_input_tokens,
                            attempts,
                        )

            if initial.compaction_level >= 3:
                for batch in hierarchical_batches:
                    current_tokens = await self._execute_action(
                        frozen,
                        action="hierarchical_summary",
                        batch=batch,
                        current_tokens=current_tokens,
                        target_input_tokens=target_input_tokens,
                        attempts=attempts,
                        on_progress=on_progress,
                    )
                    if current_tokens <= target_input_tokens:
                        return self._target_result(
                            initial,
                            current_tokens,
                            target_input_tokens,
                            attempts,
                        )

            if initial.compaction_level >= 4:
                current_tokens = await self._execute_action(
                    frozen,
                    action="hard_gate_summary",
                    batch=None,
                    current_tokens=current_tokens,
                    target_input_tokens=target_input_tokens,
                    attempts=attempts,
                    on_progress=on_progress,
                )
                if current_tokens <= target_input_tokens:
                    return self._target_result(
                        initial,
                        current_tokens,
                        target_input_tokens,
                        attempts,
                    )
        except CompactionProgressError:
            raise
        except Exception:
            if initial.compaction_level < 4:
                raise
            return await self._minimal_or_paused(
                frozen,
                initial=initial,
                current_tokens=current_tokens,
                target_input_tokens=target_input_tokens,
                attempts=attempts,
                on_progress=on_progress,
            )

        if initial.compaction_level >= 4:
            return await self._minimal_or_paused(
                frozen,
                initial=initial,
                current_tokens=current_tokens,
                target_input_tokens=target_input_tokens,
                attempts=attempts,
                on_progress=on_progress,
            )
        return self._result(
            status="target_not_reached",
            initial=initial,
            current_tokens=current_tokens,
            target_input_tokens=target_input_tokens,
            attempts=attempts,
            model_invocation_allowed=True,
        )

    def _summary_chunk_limit_tokens(self) -> int:
        """从摘要模型档案和 summary 节点策略计算不可伪造的分块上限。"""

        resolution = resolve_model_context_profile(
            self._summary_model_name,
            self._model_profiles,
            now=self._clock(),
        )
        summary_budget = self._token_meter.measure(
            estimated_input_tokens=0,
            profile=resolution.profile,
            policy=get_context_budget_policy("summary"),
        )
        return summary_budget.usable_input_tokens

    async def _execute_action(
        self,
        request: ContextCompactionRequest,
        *,
        action: CompactionAction,
        batch: CompactionBatch | None,
        current_tokens: int,
        target_input_tokens: int,
        attempts: list[CompactionAttempt],
        on_progress: CompactionProgressObserver | None,
    ) -> int:
        raw_result = await self._executor.execute(
            CompactionStageRequest(
                conversation_id=request.conversation_id,
                action=action,
                target_input_tokens=target_input_tokens,
                current_estimated_input_tokens=current_tokens,
                batch=batch,
            )
        )
        try:
            result = CompactionStageResult.model_validate(raw_result)
        except (TypeError, ValueError, ValidationError):
            raise CompactionExecutionError("压缩 Stage 必须返回合法的重新计量结果") from None
        if result.estimated_input_tokens > current_tokens:
            raise CompactionExecutionError("压缩 Stage 不得增加上下文 token")
        attempt = CompactionAttempt(
            action=action,
            estimated_input_tokens_before=current_tokens,
            estimated_input_tokens_after=result.estimated_input_tokens,
            batch=batch,
        )
        attempts.append(attempt)
        if on_progress is not None:
            await on_progress(attempt.model_copy(deep=True))
        return result.estimated_input_tokens

    async def _minimal_or_paused(
        self,
        request: ContextCompactionRequest,
        *,
        initial: ContextBudgetReport,
        current_tokens: int,
        target_input_tokens: int,
        attempts: list[CompactionAttempt],
        on_progress: CompactionProgressObserver | None,
    ) -> ContextCompactionResult:
        before_minimal = current_tokens
        try:
            current_tokens = await self._execute_action(
                request,
                action="minimal_safe_context",
                batch=None,
                current_tokens=current_tokens,
                target_input_tokens=target_input_tokens,
                attempts=attempts,
                on_progress=on_progress,
            )
            if current_tokens >= before_minimal:
                if attempts and attempts[-1].action == "minimal_safe_context":
                    attempts.pop()
                raise CompactionExecutionError("最小安全上下文必须严格降低上下文 token")
            if current_tokens <= target_input_tokens:
                return self._target_result(
                    initial,
                    current_tokens,
                    target_input_tokens,
                    attempts,
                )
            if current_tokens < initial.usable_input_tokens:
                return self._result(
                    status="minimal_safe_context",
                    initial=initial,
                    current_tokens=current_tokens,
                    target_input_tokens=target_input_tokens,
                    attempts=attempts,
                    model_invocation_allowed=True,
                )
        except CompactionProgressError:
            raise
        except Exception:
            current_tokens = before_minimal
        return self._result(
            status="paused",
            initial=initial,
            current_tokens=current_tokens,
            target_input_tokens=target_input_tokens,
            attempts=attempts,
            model_invocation_allowed=False,
            pause_reason="hard_gate_compaction_failed",
        )

    def _target_result(
        self,
        initial: ContextBudgetReport,
        current_tokens: int,
        target_input_tokens: int,
        attempts: list[CompactionAttempt],
    ) -> ContextCompactionResult:
        return self._result(
            status="target_reached",
            initial=initial,
            current_tokens=current_tokens,
            target_input_tokens=target_input_tokens,
            attempts=attempts,
            model_invocation_allowed=True,
        )

    def _result(
        self,
        *,
        status: CompactionStatus,
        initial: ContextBudgetReport,
        current_tokens: int,
        target_input_tokens: int,
        attempts: list[CompactionAttempt],
        model_invocation_allowed: bool,
        pause_reason: Literal["hard_gate_compaction_failed"] | None = None,
    ) -> ContextCompactionResult:
        return ContextCompactionResult(
            status=status,
            initial_budget_report=initial,
            final_budget_report=self._token_meter.remeasure(
                estimated_input_tokens=current_tokens,
                baseline=initial,
            ),
            target_input_tokens=target_input_tokens,
            attempts=tuple(attempts),
            model_invocation_allowed=model_invocation_allowed,
            pause_reason=pause_reason,
        )


class ConversationCompactionRuntime:
    """组合压缩租约、M04.3 Coordinator 和持久化 Turn Inbox。"""

    def __init__(
        self,
        *,
        coordinator: ContextCompactionCoordinator,
        repository: CompactionQueueRepository,
        lease_owner: str,
        lease_ttl: timedelta,
        event_sink: CompactionEventSink,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_owner = lease_owner.strip()
        if not normalized_owner:
            raise ValueError("lease_owner 不能为空")
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl 必须大于零")
        if not isinstance(event_sink, CompactionEventSink):
            raise TypeError("event_sink 必须实现 CompactionEventSink")
        if not event_sink.is_bound_to(repository):
            raise ValueError("event_sink 必须与压缩队列绑定同一个 Repository")
        self._coordinator = coordinator
        self._repository = repository
        self._lease_owner = normalized_owner
        self._lease_ttl = lease_ttl
        self._event_sink = event_sink
        self._clock = clock or (lambda: datetime.now(UTC))

    async def enqueue_turn(
        self,
        user_id: str,
        record: TurnRecord,
    ) -> TurnRecord:
        """由后端根据活跃租约决定 accepted 或 queued，不依赖前端重发。"""

        frozen = TurnRecord.model_validate(record.model_dump(mode="python"))
        return await self._repository.enqueue_turn_for_execution(
            user_id,
            frozen,
            now=self._clock(),
        )

    async def compact(
        self,
        user_id: str,
        request: ContextCompactionRequest,
        *,
        run_id: str,
    ) -> ConversationCompactionRunResult:
        """取得短租约后执行压缩，并按安全结论原子处理队列。"""

        frozen = ContextCompactionRequest.model_validate(request.model_dump(mode="python"))
        normalized_run_id = run_id.strip()
        if not normalized_run_id:
            raise ValueError("run_id 不能为空")
        started_at = self._clock()
        lease = await self._repository.acquire_compaction_lease(
            user_id,
            frozen.conversation_id,
            lease_owner=self._lease_owner,
            now=started_at,
            lease_expires_at=started_at + self._lease_ttl,
        )
        if lease is None:
            return ConversationCompactionRunResult(status="already_running")

        started_event_persisted = False
        progress_step = 0

        async def emit_progress(attempt: CompactionAttempt) -> None:
            nonlocal progress_step
            progress_step += 1
            try:
                await self._append_event(
                    user_id,
                    conversation_id=lease.conversation_id,
                    run_id=normalized_run_id,
                    event_type=AgentEventType.CONTEXT_COMPRESSION_PROGRESSED,
                    payload={
                        "status": "running",
                        "action": attempt.action,
                        "step": progress_step,
                    },
                )
            except Exception:
                raise CompactionProgressError("压缩进度事件持久化失败") from None

        try:
            await self._append_event(
                user_id,
                conversation_id=lease.conversation_id,
                run_id=normalized_run_id,
                event_type=AgentEventType.CONTEXT_COMPRESSION_STARTED,
                payload={
                    "status": "running",
                    "message": COMPACTION_STARTED_MESSAGE,
                },
            )
            started_event_persisted = True
            raw_result = await self._coordinator.coordinate(
                frozen,
                on_progress=emit_progress,
            )
            result = ContextCompactionResult.model_validate(raw_result)
            if result.status == "paused":
                await self._repository.finish_compaction_with_event(
                    user_id,
                    lease.conversation_id,
                    lease_owner=lease.lease_owner,
                    lease_token=lease.lease_token,
                    now=self._clock(),
                    claim_next=False,
                    run_id=normalized_run_id,
                    event_type=AgentEventType.CONTEXT_COMPRESSION_FAILED,
                    payload={
                        "status": "retry_required",
                        "reason_code": "hard_gate_compaction_failed",
                        "message": COMPACTION_FAILED_MESSAGE,
                    },
                )
                return ConversationCompactionRunResult(
                    status="paused",
                    compaction_result=result,
                )

            next_turn, _ = await self._repository.finish_compaction_with_event(
                user_id,
                lease.conversation_id,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                now=self._clock(),
                claim_next=True,
                run_id=normalized_run_id,
                event_type=AgentEventType.CONTEXT_COMPRESSION_COMPLETED,
                payload={
                    "status": "completed",
                    "message": COMPACTION_COMPLETED_MESSAGE,
                },
            )
            return ConversationCompactionRunResult(
                status="completed",
                compaction_result=result,
                next_turn=next_turn,
            )
        except BaseException:
            if started_event_persisted:
                try:
                    await self._repository.finish_compaction_with_event(
                        user_id,
                        lease.conversation_id,
                        lease_owner=lease.lease_owner,
                        lease_token=lease.lease_token,
                        now=self._clock(),
                        claim_next=False,
                        run_id=normalized_run_id,
                        event_type=AgentEventType.CONTEXT_COMPRESSION_FAILED,
                        payload={
                            "status": "retry_required",
                            "reason_code": "compaction_execution_failed",
                            "message": COMPACTION_FAILED_MESSAGE,
                        },
                    )
                except CompactionLeaseConflictError:
                    # 租约已被接管时，陈旧 worker 不得写入任何伪终态。
                    pass
                except Exception:
                    try:
                        await self._mark_failed_compaction(
                            user_id,
                            lease,
                        )
                    except CompactionLeaseConflictError:
                        pass
            else:
                try:
                    await self._mark_failed_compaction(
                        user_id,
                        lease,
                    )
                except CompactionLeaseConflictError:
                    pass
            raise

    async def _append_event(
        self,
        user_id: str,
        *,
        conversation_id: str,
        run_id: str,
        event_type: AgentEventType,
        payload: dict[str, JsonValue],
    ) -> None:
        """通过唯一事件端口先持久化生命周期状态，再继续压缩编排。"""
        await self._event_sink.append(
            user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            occurred_at=self._clock(),
        )

    async def _mark_failed_compaction(
        self,
        user_id: str,
        lease: ConversationCompactionLease,
    ) -> None:
        """异常路径只写恢复标记，绝不消费已经持久化的输入。"""

        await self._repository.finish_compaction(
            user_id,
            lease.conversation_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            now=self._clock(),
            claim_next=False,
        )


__all__ = [
    "CompactionAction",
    "CompactionAttempt",
    "CompactionBatch",
    "CompactionBatchScope",
    "CompactionExecutionError",
    "CompactionProgressError",
    "CompactionProgressObserver",
    "CompactionRunStatus",
    "CompactionSegment",
    "CompactionStageExecutor",
    "CompactionStageRequest",
    "CompactionStageResult",
    "CompactionStatus",
    "CompactionValidationError",
    "ConversationCompactionRunResult",
    "ConversationCompactionRuntime",
    "ContextCompactionCoordinator",
    "ContextCompactionRequest",
    "ContextCompactionResult",
    "DeerFlowSummaryEngine",
    "PIXELFLOW_STRUCTURED_SUMMARY_PROMPT",
    "SummaryBuildRequest",
    "SummaryBuildResult",
    "SummaryBuilder",
    "SummaryBuildValidationError",
    "SummaryEngine",
    "SummaryGenerationError",
    "SummaryGenerationInput",
    "SummarySemanticSnapshot",
    "SummarySourceMessage",
]
