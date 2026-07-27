from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from pixelflow.agent_runtime.contracts import (
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    SupervisorState,
    resume_graph_from_interrupt,
    supervisor_namespace,
    workflow_namespace,
    workflow_projection_command,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _workflow(
    workflow_id: str,
    *,
    conversation_id: str = "conv-1",
    kind: WorkflowKind = WorkflowKind.IMAGE,
    current_stage: str = "review",
    stage_version: int = 1,
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        kind=kind,
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


class _ReviewState(TypedDict, total=False):
    workflow: WorkflowRecord
    responses: list[dict[str, Any]]


def _build_review_graph(checkpointer: Any) -> Any:
    def wait_for_review(state: _ReviewState) -> dict[str, Any]:
        response = interrupt(
            {
                "type": "plan_review",
                "workflow_id": state["workflow"].workflow_id,
            }
        )
        current = state["workflow"]
        approved = current.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "current_stage": "generation",
                "stage_version": current.stage_version + 1,
                "context_version": current.context_version + 1,
            },
            deep=True,
        )
        return {
            "workflow": approved,
            "responses": [response],
        }

    graph = StateGraph(_ReviewState)
    graph.add_node("wait_for_review", wait_for_review)
    graph.add_edge(START, "wait_for_review")
    graph.add_edge("wait_for_review", END)
    return graph.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_resume_uses_original_interrupt_after_graph_rebuild() -> None:
    checkpointer = InMemorySaver()
    namespace = workflow_namespace("conv-1", "wf-image")
    first_process_graph = _build_review_graph(checkpointer)

    await first_process_graph.ainvoke(
        {"workflow": _workflow("wf-image"), "responses": []},
        namespace.as_runnable_config(),
    )
    interrupted = await first_process_graph.aget_state(
        namespace.as_runnable_config()
    )
    assert len(interrupted.interrupts) == 1
    original_interrupt = interrupted.interrupts[0]

    restarted_graph = _build_review_graph(checkpointer)
    result = await resume_graph_from_interrupt(
        restarted_graph,
        namespace,
        interrupt_id=original_interrupt.id,
        response={"approved": True},
    )

    assert result["workflow"].current_stage == "generation"
    assert result["workflow"].status == WorkflowStatus.RUNNING
    assert result["responses"] == [{"approved": True}]
    completed = await restarted_graph.aget_state(namespace.as_runnable_config())
    assert completed.interrupts == ()
    assert completed.next == ()


@pytest.mark.asyncio
async def test_resume_reopens_sqlite_and_uses_original_interrupt(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "m02-interrupts.db"
    namespace = workflow_namespace("conv-sqlite", "wf-image")

    async with AsyncSqliteSaver.from_conn_string(
        str(database_path)
    ) as first_checkpointer:
        await first_checkpointer.setup()
        first_process_graph = _build_review_graph(first_checkpointer)
        await first_process_graph.ainvoke(
            {
                "workflow": _workflow(
                    "wf-image",
                    conversation_id="conv-sqlite",
                ),
                "responses": [],
            },
            namespace.as_runnable_config(),
        )
        interrupted = await first_process_graph.aget_state(
            namespace.as_runnable_config()
        )
        original_interrupt_id = interrupted.interrupts[0].id

    async with AsyncSqliteSaver.from_conn_string(
        str(database_path)
    ) as restarted_checkpointer:
        await restarted_checkpointer.setup()
        restarted_graph = _build_review_graph(restarted_checkpointer)
        result = await resume_graph_from_interrupt(
            restarted_graph,
            namespace,
            interrupt_id=original_interrupt_id,
            response={"approved": True},
        )
        completed = await restarted_graph.aget_state(
            namespace.as_runnable_config()
        )

    assert result["workflow"].current_stage == "generation"
    assert result["responses"] == [{"approved": True}]
    assert completed.interrupts == ()
    assert completed.next == ()


@pytest.mark.asyncio
async def test_resume_rejects_wrong_interrupt_without_advancing_checkpoint() -> None:
    checkpointer = InMemorySaver()
    namespace = workflow_namespace("conv-1", "wf-image")
    graph = _build_review_graph(checkpointer)
    await graph.ainvoke(
        {"workflow": _workflow("wf-image"), "responses": []},
        namespace.as_runnable_config(),
    )
    before = await graph.aget_state(namespace.as_runnable_config())

    with pytest.raises(LookupError, match="interrupt"):
        await resume_graph_from_interrupt(
            graph,
            namespace,
            interrupt_id="interrupt-does-not-exist",
            response={"approved": True},
        )

    after = await graph.aget_state(namespace.as_runnable_config())
    assert after.config == before.config
    assert after.interrupts == before.interrupts
    assert after.values == before.values


@pytest.mark.asyncio
async def test_resume_keeps_other_conversation_interrupt_isolated() -> None:
    checkpointer = InMemorySaver()
    graph = _build_review_graph(checkpointer)
    first_namespace = workflow_namespace("conv-1", "wf-image")
    second_namespace = workflow_namespace("conv-2", "wf-image")

    await graph.ainvoke(
        {"workflow": _workflow("wf-image"), "responses": []},
        first_namespace.as_runnable_config(),
    )
    await graph.ainvoke(
        {
            "workflow": _workflow("wf-image", conversation_id="conv-2"),
            "responses": [],
        },
        second_namespace.as_runnable_config(),
    )
    first_state = await graph.aget_state(first_namespace.as_runnable_config())
    second_before = await graph.aget_state(second_namespace.as_runnable_config())

    await resume_graph_from_interrupt(
        graph,
        first_namespace,
        interrupt_id=first_state.interrupts[0].id,
        response={"approved": True},
    )

    second_after = await graph.aget_state(second_namespace.as_runnable_config())
    assert second_after.config == second_before.config
    assert second_after.interrupts == second_before.interrupts
    assert second_after.values == second_before.values


def test_projection_command_deep_copies_workflow_update() -> None:
    projected = _workflow(
        "wf-image",
        current_stage="generation",
        stage_version=2,
    ).model_copy(
        update={"latest_artifact_refs": ["artifact://image-1"]},
        deep=True,
    )

    command = workflow_projection_command(
        projected,
        conversation_id="conv-1",
        goto="observe",
    )
    projected.latest_artifact_refs.append("artifact://later")

    assert isinstance(command, Command)
    assert command.goto == "observe"
    assert command.update["workflows"]["wf-image"].latest_artifact_refs == [
        "artifact://image-1"
    ]


def test_projection_command_rejects_foreign_conversation() -> None:
    foreign = _workflow("wf-image", conversation_id="conv-2")

    with pytest.raises(ValueError, match="conversation_id"):
        workflow_projection_command(
            foreign,
            conversation_id="conv-1",
        )


def test_projection_command_updates_reducer_before_next_node() -> None:
    existing = _workflow("wf-image")
    other = _workflow(
        "wf-video",
        kind=WorkflowKind.VIDEO,
        current_stage="intake",
    )
    projected = _workflow(
        "wf-image",
        current_stage="generation",
        stage_version=2,
    )

    def project_workflow(_state: SupervisorState) -> Command:
        return workflow_projection_command(
            projected,
            conversation_id=_state["conversation_id"],
            goto="observe",
        )

    def observe_projection(state: SupervisorState) -> dict[str, str]:
        current = state["workflows"]["wf-image"]
        return {"current_input": current.current_stage}

    graph = StateGraph(SupervisorState)
    graph.add_node(
        "project_workflow",
        project_workflow,
        destinations=("observe",),
    )
    graph.add_node("observe", observe_projection)
    graph.add_edge(START, "project_workflow")
    graph.add_edge("observe", END)

    result = graph.compile().invoke(
        {
            "conversation_id": "conv-1",
            "active_workflow_id": "wf-video",
            "workflows": {
                existing.workflow_id: existing,
                other.workflow_id: other,
            },
        },
        supervisor_namespace("conv-1").as_runnable_config(),
    )

    assert result["current_input"] == "generation"
    assert result["active_workflow_id"] == "wf-video"
    assert result["workflows"]["wf-image"].current_stage == "generation"
    assert result["workflows"]["wf-video"] == other
