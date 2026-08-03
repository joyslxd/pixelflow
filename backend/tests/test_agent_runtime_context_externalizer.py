"""Agent Runtime 大型 tool/artifact 上下文外置测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from pixelflow.agent_runtime.config import ContextBudgetConfig
from pixelflow.agent_runtime.context import ContextBudgetPolicyProvider
from pixelflow.agent_runtime.context.assembler import (
    ContextAssembler,
    ContextAssemblySnapshot,
    ContextMessageRecord,
)
from pixelflow.agent_runtime.context.profiles import ModelContextProfile
from pixelflow.agent_runtime.contracts import (
    ContextRequest,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)

NOW = datetime(2026, 7, 24, 8, 45, tzinfo=UTC)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _workflow() -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id="wf-video",
        conversation_id="conv-1",
        kind=WorkflowKind.VIDEO,
        status=WorkflowStatus.RUNNING,
        current_stage="generate_scenes",
        stage_version=3,
        creation_contract_snapshot={
            "creation_contract": {
                "video_duration_sec": 30,
                "negative_prompt": "不要修改商品文字",
            },
            "scene_blueprints": [{"scene_id": "scene-1", "duration_sec": 10}],
            "asset_manifest": {"characters": ["角色A"]},
            "pending_action": {"type": "confirm_scene_packages"},
            "operations": [{"operation_id": "op-1", "status": "polling"}],
        },
        latest_artifact_refs=["artifact:storyboard-1"],
        context_version=7,
        created_at=NOW,
        updated_at=NOW,
    )


def _payload() -> dict[str, object]:
    return {
        "current_input": "继续处理当前视频，商品文字不要改",
        "active_or_target_workflow": _workflow(),
        "recent_messages": [
            {
                "message_id": "msg-tool-1",
                "role": "tool",
                "tool_call_id": "call-1",
                "context_snippet": "工具已找到 128 个候选",
                "content": "逐行结果：" + ("A" * 4_000),
            },
            {
                "message_id": "msg-artifact-1",
                "role": "assistant",
                "artifact": {
                    "artifact_ref": "artifact:storyboard-1",
                    "title": "竞品视频拆解",
                    "status": "ready",
                    "content": "完整分镜：" + ("B" * 4_000),
                },
            },
            {
                "message_id": "msg-tool-small",
                "role": "assistant",
                "tool_result": {"status": "ok", "count": 2},
            },
        ],
        "conversation_summary": None,
        "related_workflow_summaries": [],
        "relevant_long_term_memories": [],
        "artifact_evidence_refs": ["artifact:storyboard-1"],
        "unresolved_questions": [],
    }


class _PayloadStore:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.records: list[Any] = []
        self.error = error

    async def save_context_payload(self, record: Any) -> str:
        if self.error is not None:
            raise self.error
        self.records.append(record)
        return f"context-payload:{record.source_kind}:{record.source_ref}"


class _OversizedReferenceStore(_PayloadStore):
    async def save_context_payload(self, record: Any) -> str:
        self.records.append(record)
        return "context-payload:" + ("R" * 5_000)


@pytest.mark.asyncio
async def test_externalizer_preserves_authoritative_fields_and_reduces_prompt_size() -> None:
    from pixelflow.agent_runtime.context.externalizer import (
        ContextPayloadExternalizer,
        estimate_prompt_bytes,
    )

    payload = _payload()
    original_contract = payload["active_or_target_workflow"].creation_contract_snapshot
    original_contract_hash = _canonical_hash(original_contract)
    original_prompt_bytes = estimate_prompt_bytes(payload)
    store = _PayloadStore()
    externalizer = ContextPayloadExternalizer(
        store=store,
        externalize_min_bytes=1_000,
        snippet_max_chars=80,
    )

    result = await externalizer.externalize(
        user_id="user-1",
        conversation_id="conv-1",
        payload=payload,
    )

    assert result.payload["current_input"] == payload["current_input"]
    assert result.payload["active_or_target_workflow"] == payload["active_or_target_workflow"]
    assert _canonical_hash(result.payload["active_or_target_workflow"].creation_contract_snapshot) == original_contract_hash
    assert payload["recent_messages"][0]["content"].endswith("A" * 4_000)
    assert payload["recent_messages"][1]["artifact"]["content"].endswith("B" * 4_000)
    assert len(store.records) == 2
    assert store.records[0].storage_identity == (
        "user-1",
        "conv-1",
        "tool",
        "call-1",
        store.records[0].content_hash,
    )
    assert store.records[0].payload == "逐行结果：" + ("A" * 4_000)
    assert store.records[1].payload["content"] == "完整分镜：" + ("B" * 4_000)

    recent_messages = result.payload["recent_messages"]
    tool_placeholder = recent_messages[0]["content"]
    artifact_placeholder = recent_messages[1]["artifact"]
    assert tool_placeholder == {
        "context_externalized": True,
        "external_ref": "context-payload:tool:call-1",
        "content_hash": store.records[0].content_hash,
        "original_bytes": store.records[0].original_bytes,
        "snippet": "工具已找到 128 个候选",
    }
    assert artifact_placeholder == {
        "context_externalized": True,
        "external_ref": "context-payload:artifact:artifact:storyboard-1",
        "content_hash": store.records[1].content_hash,
        "original_bytes": store.records[1].original_bytes,
        "snippet": '{"artifact_ref":"artifact:storyboard-1","status":"ready","title":"竞品视频拆解"}',
    }
    assert recent_messages[2]["tool_result"] == {"status": "ok", "count": 2}
    assert result.prompt_bytes < original_prompt_bytes
    assert result.prompt_bytes == estimate_prompt_bytes(result.payload)
    assert [item.source_kind for item in result.externalized] == ["tool", "artifact"]


@pytest.mark.asyncio
async def test_externalizer_extracts_bounded_safe_head_and_tail_for_plain_tool_text() -> None:
    from pixelflow.agent_runtime.context.externalizer import ContextPayloadExternalizer

    payload = _payload()
    tool_message = payload["recent_messages"][0]
    del tool_message["context_snippet"]
    tool_message["content"] = "开头结论 Authorization: Bearer secret-token https://provider.example/result?token=query-secret\n" + ("中间明细" * 1_000) + "\n末尾证据：候选3=30元"
    externalizer = ContextPayloadExternalizer(
        store=_PayloadStore(),
        externalize_min_bytes=1_000,
        snippet_max_chars=120,
    )

    result = await externalizer.externalize(
        user_id="user-1",
        conversation_id="conv-1",
        payload=payload,
    )

    snippet = result.payload["recent_messages"][0]["content"]["snippet"]
    assert snippet.startswith("开头结论")
    assert snippet.endswith("末尾证据：候选3=30元")
    assert "secret-token" not in snippet
    assert "query-secret" not in snippet
    assert "已隐藏" in snippet
    assert len(snippet) <= 120


@pytest.mark.asyncio
async def test_externalizer_redacts_quoted_json_credentials_from_snippet() -> None:
    from pixelflow.agent_runtime.context.externalizer import ContextPayloadExternalizer

    payload = _payload()
    payload["recent_messages"][0]["context_snippet"] = '调用结果 {"api_key":"secret-value","token":"session-value","status":"ok"}'
    externalizer = ContextPayloadExternalizer(
        store=_PayloadStore(),
        externalize_min_bytes=1_000,
    )

    result = await externalizer.externalize(
        user_id="user-1",
        conversation_id="conv-1",
        payload=payload,
    )

    snippet = result.payload["recent_messages"][0]["content"]["snippet"]
    assert "secret-value" not in snippet
    assert "session-value" not in snippet
    assert snippet.count("[已隐藏凭据]") == 2


@pytest.mark.asyncio
async def test_externalizer_rejects_reference_that_cannot_reduce_prompt() -> None:
    from pixelflow.agent_runtime.context.externalizer import ContextPayloadExternalizer

    payload = _payload()
    externalizer = ContextPayloadExternalizer(
        store=_OversizedReferenceStore(),
        externalize_min_bytes=1_000,
    )

    with pytest.raises(ValueError, match="external_ref|降低"):
        await externalizer.externalize(
            user_id="user-1",
            conversation_id="conv-1",
            payload=payload,
        )

    assert payload["recent_messages"][0]["content"].endswith("A" * 4_000)


@pytest.mark.asyncio
async def test_externalizer_is_idempotent_and_does_not_mutate_store_records() -> None:
    from pixelflow.agent_runtime.context.externalizer import ContextPayloadExternalizer

    store = _PayloadStore()
    externalizer = ContextPayloadExternalizer(
        store=store,
        externalize_min_bytes=1_000,
    )
    first = await externalizer.externalize(
        user_id="user-1",
        conversation_id="conv-1",
        payload=_payload(),
    )
    stored_tool_payload = store.records[0].payload

    first.payload["recent_messages"][0]["context_snippet"] = "调用方后续修改"
    second = await externalizer.externalize(
        user_id="user-1",
        conversation_id="conv-1",
        payload=first.payload,
    )

    assert len(store.records) == 2
    assert second.externalized == ()
    assert stored_tool_payload == "逐行结果：" + ("A" * 4_000)


@pytest.mark.asyncio
async def test_externalizer_propagates_store_failure_without_changing_input() -> None:
    from pixelflow.agent_runtime.context.externalizer import ContextPayloadExternalizer

    payload = _payload()
    payload_hash = _canonical_hash(payload["active_or_target_workflow"].model_dump(mode="json"))
    externalizer = ContextPayloadExternalizer(
        store=_PayloadStore(error=RuntimeError("存储暂不可用")),
        externalize_min_bytes=1_000,
    )

    with pytest.raises(RuntimeError, match="存储暂不可用"):
        await externalizer.externalize(
            user_id="user-1",
            conversation_id="conv-1",
            payload=payload,
        )

    assert _canonical_hash(payload["active_or_target_workflow"].model_dump(mode="json")) == payload_hash
    assert payload["recent_messages"][0]["content"].endswith("A" * 4_000)


class _SnapshotSource:
    def __init__(self, snapshot: ContextAssemblySnapshot) -> None:
        self.snapshot = snapshot

    async def load_context_snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str,
        expected_context_version: int,
    ) -> ContextAssemblySnapshot:
        assert expected_context_version == self.snapshot.context_version
        return self.snapshot


@pytest.mark.asyncio
async def test_assembler_externalizes_level_one_payload_then_remeasures_budget() -> None:
    from pixelflow.agent_runtime.context.externalizer import ContextPayloadExternalizer

    workflow = _workflow()
    snapshot = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=7,
        active_workflow_id=workflow.workflow_id,
        workflows=[workflow],
        messages=[
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=1,
                payload={
                    "message_id": "msg-tool-1",
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "context_snippet": "工具已完成检索",
                    "content": "原始工具输出：" + ("A" * 4_000),
                },
            )
        ],
    )
    store = _PayloadStore()
    estimates: list[dict[str, object]] = []

    def estimate(value: dict[str, object]) -> int:
        estimates.append(value)
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        return 60_000 if "原始工具输出" in serialized else 10_000

    assembler = ContextAssembler(
        source=_SnapshotSource(snapshot),
        model_name="unverified-model",
        model_profiles={
            "unverified-model": ModelContextProfile(
                model_name="unverified-model",
                max_context_tokens=512 * 1024,
                max_output_tokens=64 * 1024,
                tokenizer_strategy="provider_usage",
            )
        },
        budget_node="supervisor",
        token_estimator=estimate,
        clock=lambda: NOW,
        externalizer=ContextPayloadExternalizer(
            store=store,
            externalize_min_bytes=1_000,
        ),
        budget_policy_provider=ContextBudgetPolicyProvider(
            ContextBudgetConfig(require_verified_model_profile=False),
        ),
    )

    envelope = await assembler.assemble(
        ContextRequest(
            conversation_id="conv-1",
            user_id="user-1",
            current_input="继续当前视频",
            target_workflow_id="wf-video",
            artifact_refs=[],
            expected_context_version=7,
        )
    )

    assert len(estimates) == 2
    assert len(store.records) == 1
    assert envelope.current_input == "继续当前视频"
    assert envelope.active_or_target_workflow == workflow
    assert envelope.recent_messages[0]["content"]["context_externalized"] is True
    assert envelope.budget_report.estimated_input_tokens == 10_000
    assert envelope.budget_report.compaction_level == 0
