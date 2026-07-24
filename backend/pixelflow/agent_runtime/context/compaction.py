"""用上一版结构化摘要和连续新消息构建下一版摘要。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ..contracts import ContextSummary


class SummaryBuildValidationError(ValueError):
    """摘要构建输入不满足同会话连续增量约束。"""


class SummaryGenerationError(RuntimeError):
    """摘要 Engine 没有返回可验证的结构化结果。"""


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


class SummaryGenerationInput(_CompactionRecord):
    """提供给摘要 Engine 的最小增量输入，不包含业务上下文。"""

    conversation_id: str = Field(min_length=1)
    previous_summary: SummarySemanticSnapshot | None = None
    new_messages: tuple[SummarySourceMessage, ...] = ()


class SummaryBuildResult(_CompactionRecord):
    """同时返回结构化摘要和 DeerFlow 口径的源 token 数。"""

    summary: ContextSummary
    source_token_count: int = Field(ge=0)


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


def _summary_content_hash(
    *,
    semantic: SummarySemanticSnapshot,
    covered_message_ids: list[str],
    covered_sequence_end: int,
) -> str:
    payload = {
        "semantic": semantic.model_dump(mode="json"),
        "covered_message_ids": covered_message_ids,
        "covered_sequence_start": 1,
        "covered_sequence_end": covered_sequence_end,
    }
    encoded = _canonical_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


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
    ) -> None:
        self._engine = engine
        self._summary_id_factory = summary_id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))

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
            "content_hash": _summary_content_hash(
                semantic=semantic,
                covered_message_ids=covered_message_ids,
                covered_sequence_end=covered_sequence_end,
            ),
            "covered_message_ids": covered_message_ids,
            "covered_sequence_start": 1,
            "covered_sequence_end": covered_sequence_end,
            "compression_model": model_name,
            "created_at": created_at,
        }
        summary_payload.update(semantic.model_dump(mode="python"))
        summary = ContextSummary.model_validate(summary_payload)
        return SummaryBuildResult(
            summary=summary,
            source_token_count=source_token_count,
        )


__all__ = [
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
