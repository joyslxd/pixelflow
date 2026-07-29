"""验证统一 Agent Runtime 图装配、注册 ID 与生命周期边界。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import interrupt

from app.gateway.pixelflow_agent_runtime import (
    make_pixelflow_agent_graph_runtime,
)
from pixelflow import make_pixelflow_graph
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    AGENT_RUNTIME_GRAPH_ID,
    FakeWorkflowRegistry,
    WorkflowCommand,
    make_agent_runtime_graph,
    resume_graph_from_interrupt,
    supervisor_namespace,
)
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
    ActionClassificationTarget,
    DecisionValidationRequest,
    DeterministicResolution,
    DeterministicResolutionStatus,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _workflow(
    *,
    conversation_id: str,
    current_stage: str = "plan_review",
    stage_version: int = 1,
) -> WorkflowRecord:
    """构造图装配测试所需的最小 Workflow 投影。"""

    return WorkflowRecord(
        workflow_id="wf-image",
        conversation_id=conversation_id,
        kind=WorkflowKind.IMAGE,
        status=WorkflowStatus.AWAITING_USER,
        current_stage=current_stage,
        stage_version=stage_version,
        creation_contract_snapshot={},
        pending_external_job=None,
        latest_artifact_refs=[],
        context_version=stage_version,
        created_at=NOW,
        updated_at=NOW,
    )


def _decision(conversation_id: str) -> ActionDecision:
    """构造一条显式定位既有 Workflow 的继续命令。"""

    return ActionDecision(
        action=AgentAction.CONTINUE_WORKFLOW,
        intent=AgentIntent.IMAGE,
        target_workflow_id="wf-image",
        target_stage="plan_review",
        target_artifact_ref=None,
        confidence=1,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="explicit_target",
        idempotency_key=f"decision:turn-{conversation_id}",
    )


def _state(conversation_id: str, *, current_input: str = "继续") -> dict:
    """生成可直接交给 Supervisor 图的测试状态。"""

    workflow = _workflow(conversation_id=conversation_id)
    decision = _decision(conversation_id)
    candidate = ActionClassificationCandidate(
        workflow_id=workflow.workflow_id,
        intent=AgentIntent.IMAGE,
        status=workflow.status,
        current_stage=workflow.current_stage,
        stage_version=workflow.stage_version,
        context_version=workflow.context_version,
        allowed_actions=(AgentAction.CONTINUE_WORKFLOW,),
        targets=(
            ActionClassificationTarget(
                target_stage=workflow.current_stage,
            ),
        ),
    )
    classification_request = ActionClassificationRequest(
        turn_id=f"turn-{conversation_id}",
        content=current_input,
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.IMAGE,
            target_workflow_id=workflow.workflow_id,
            target_stage=workflow.current_stage,
            reason_code="explicit_target",
            candidate_workflow_ids=(workflow.workflow_id,),
        ),
        candidates=(candidate,),
    )
    return {
        "conversation_id": conversation_id,
        "user_id": f"user-{conversation_id}",
        "turn_id": f"turn-{conversation_id}",
        "run_id": f"run-{conversation_id}",
        "current_input": current_input,
        "context_version": 1,
        "workflows": {workflow.workflow_id: workflow},
        "active_workflow_id": workflow.workflow_id,
        "decision": decision,
        "decision_validation_request": DecisionValidationRequest(
            decision=decision,
            classification_request=classification_request,
            current_candidates=(candidate,),
            expected_context_version=1,
            current_context_version=1,
        ),
    }


class _InterruptingHandler:
    """用真实 LangGraph interrupt 模拟 Workflow 人工确认。"""

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        response = interrupt(
            {
                "type": "plan_review",
                "workflow_id": command.workflow_id,
            }
        )
        assert response == {"approved": True}
        assert command.workflow is not None
        return command.workflow.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "current_stage": "generation",
                "stage_version": command.workflow.stage_version + 1,
                "context_version": command.workflow.context_version + 1,
            },
            deep=True,
        )


class _ImmediateHandler:
    """立即返回新投影，用于验证多会话 checkpoint 隔离。"""

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        assert command.workflow is not None
        return command.workflow.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "current_stage": "generation",
                "stage_version": command.workflow.stage_version + 1,
                "context_version": command.workflow.context_version + 1,
            },
            deep=True,
        )


def _registry(handler: object) -> FakeWorkflowRegistry:
    """把单个测试处理器注册到图片 Workflow。"""

    return FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})


@pytest.mark.asyncio
async def test_composed_graph_resumes_original_memory_interrupt_after_rebuild() -> None:
    """图对象重建后仍通过共享 Memory checkpointer 恢复原中断。"""

    checkpointer = InMemorySaver()
    namespace = supervisor_namespace("conv-memory")
    first_graph = make_agent_runtime_graph(
        registry=_registry(_InterruptingHandler()),
        checkpointer=checkpointer,
    )

    await first_graph.ainvoke(_state("conv-memory"), namespace.as_runnable_config())
    interrupted = await first_graph.aget_state(namespace.as_runnable_config())
    original_interrupt_id = interrupted.interrupts[0].id

    restarted_graph = make_agent_runtime_graph(
        registry=_registry(_InterruptingHandler()),
        checkpointer=checkpointer,
    )
    result = await resume_graph_from_interrupt(
        restarted_graph,
        namespace,
        interrupt_id=original_interrupt_id,
        response={"approved": True},
    )

    assert result["workflows"]["wf-image"].current_stage == "generation"
    assert result["workflows"]["wf-image"].status == WorkflowStatus.RUNNING
    completed = await restarted_graph.aget_state(namespace.as_runnable_config())
    assert completed.interrupts == ()
    assert completed.next == ()


@pytest.mark.asyncio
async def test_composed_graph_resumes_original_sqlite_interrupt_after_reopen(
    tmp_path: Path,
) -> None:
    """关闭并重开 SQLite 后仍从同一 Supervisor 中断继续。"""

    database_path = tmp_path / "m02-composition.db"
    namespace = supervisor_namespace("conv-sqlite")

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as first_checkpointer:
        await first_checkpointer.setup()
        first_graph = make_agent_runtime_graph(
            registry=_registry(_InterruptingHandler()),
            checkpointer=first_checkpointer,
        )
        await first_graph.ainvoke(
            _state("conv-sqlite"),
            namespace.as_runnable_config(),
        )
        interrupted = await first_graph.aget_state(namespace.as_runnable_config())
        original_interrupt_id = interrupted.interrupts[0].id

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as restarted_checkpointer:
        await restarted_checkpointer.setup()
        restarted_graph = make_agent_runtime_graph(
            registry=_registry(_InterruptingHandler()),
            checkpointer=restarted_checkpointer,
        )
        result = await resume_graph_from_interrupt(
            restarted_graph,
            namespace,
            interrupt_id=original_interrupt_id,
            response={"approved": True},
        )

    assert result["workflows"]["wf-image"].current_stage == "generation"
    assert result["workflows"]["wf-image"].stage_version == 2


@pytest.mark.asyncio
async def test_composed_graph_keeps_same_workflow_id_isolated_by_conversation() -> None:
    """不同 conversation 的同名 Workflow 不共享 Supervisor 状态。"""

    checkpointer = InMemorySaver()
    graph = make_agent_runtime_graph(
        registry=_registry(_ImmediateHandler()),
        checkpointer=checkpointer,
    )
    first_namespace = supervisor_namespace("conv-first")
    second_namespace = supervisor_namespace("conv-second")

    await graph.ainvoke(
        _state("conv-first", current_input="第一条"),
        first_namespace.as_runnable_config(),
    )
    await graph.ainvoke(
        _state("conv-second", current_input="第二条"),
        second_namespace.as_runnable_config(),
    )

    first = await graph.aget_state(first_namespace.as_runnable_config())
    second = await graph.aget_state(second_namespace.as_runnable_config())
    assert first.values["conversation_id"] == "conv-first"
    assert first.values["current_input"] == "第一条"
    assert first.values["workflows"]["wf-image"].conversation_id == "conv-first"
    assert second.values["conversation_id"] == "conv-second"
    assert second.values["current_input"] == "第二条"
    assert second.values["workflows"]["wf-image"].conversation_id == "conv-second"


@pytest.mark.asyncio
async def test_gateway_graph_runtime_reuses_shared_checkpointer_and_cleans_state() -> None:
    """Gateway 只借用共享 checkpointer，并在退出时移除图运行时引用。"""

    app = FastAPI()
    checkpointer = InMemorySaver()

    async with make_pixelflow_agent_graph_runtime(
        app,
        checkpointer=checkpointer,
    ) as runtime:
        assert app.state.pixelflow_agent_graph_runtime is runtime
        assert runtime.graph_id == AGENT_RUNTIME_GRAPH_ID
        assert runtime.graph.checkpointer is checkpointer
        assert runtime.closed is False

    assert runtime.closed is True
    assert not hasattr(app.state, "pixelflow_agent_graph_runtime")
    assert [item async for item in checkpointer.alist({"configurable": {"thread_id": "still-open"}})] == []


def test_langgraph_registry_adds_new_graph_without_replacing_old_graph() -> None:
    """工具注册表新增独立图 ID，同时保留旧 PixelFlow 图入口。"""

    config = json.loads((BACKEND_ROOT / "langgraph.json").read_text(encoding="utf-8"))

    assert AGENT_RUNTIME_GRAPH_ID == "pixelflow_agent_runtime"
    assert config["graphs"]["pixelflow"] == "pixelflow:make_pixelflow_graph"
    assert config["graphs"]["lead_agent"] == "deerflow.agents:make_lead_agent"
    assert config["graphs"][AGENT_RUNTIME_GRAPH_ID] == ("pixelflow.agent_runtime.graph:make_agent_runtime_graph")
    assert make_pixelflow_graph().get_graph().nodes
