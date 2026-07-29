from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from pixelflow.agent_runtime.contracts import WorkflowKind, WorkflowRecord, WorkflowStatus
from pixelflow.agent_runtime.graph import (
    SupervisorState,
    merge_workflow_records,
    supervisor_namespace,
    workflow_namespace,
)

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def _workflow(
    workflow_id: str,
    *,
    conversation_id: str = "conv-1",
    kind: WorkflowKind = WorkflowKind.IMAGE,
    current_stage: str = "intake",
    stage_version: int = 1,
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        kind=kind,
        status=WorkflowStatus.RUNNING,
        current_stage=current_stage,
        stage_version=stage_version,
        creation_contract_snapshot={},
        pending_external_job=None,
        latest_artifact_refs=[],
        context_version=stage_version,
        created_at=NOW,
        updated_at=NOW,
    )


def test_supervisor_state_reducer_upserts_workflows_in_real_graph() -> None:
    first_image = _workflow("wf-image")
    updated_image = _workflow(
        "wf-image",
        current_stage="planning",
        stage_version=2,
    )
    video = _workflow(
        "wf-video",
        kind=WorkflowKind.VIDEO,
    )

    graph = StateGraph(SupervisorState)
    graph.add_node("register_image", lambda _state: {"workflows": {"wf-image": first_image}})
    graph.add_node(
        "update_workflows",
        lambda _state: {
            "workflows": {
                "wf-image": updated_image,
                "wf-video": video,
            }
        },
    )
    graph.add_edge(START, "register_image")
    graph.add_edge("register_image", "update_workflows")
    graph.add_edge("update_workflows", END)

    result = graph.compile().invoke(
        {
            "conversation_id": "conv-1",
            "user_id": "user-1",
            "context_version": 1,
            "workflows": {},
        }
    )

    assert list(result["workflows"]) == ["wf-image", "wf-video"]
    assert result["workflows"]["wf-image"].current_stage == "planning"
    assert result["workflows"]["wf-image"].stage_version == 2
    assert result["workflows"]["wf-video"].kind == WorkflowKind.VIDEO


def test_workflow_reducer_does_not_mutate_existing_or_update_mappings() -> None:
    existing_record = _workflow("wf-image")
    updated_record = _workflow(
        "wf-image",
        current_stage="planning",
        stage_version=2,
    )
    existing = {"wf-image": existing_record}
    updates = {"wf-image": updated_record}

    merged = merge_workflow_records(existing, updates)

    assert existing["wf-image"].current_stage == "intake"
    assert updates["wf-image"].current_stage == "planning"
    assert merged is not existing
    assert merged["wf-image"] is not existing_record
    assert merged["wf-image"] is not updated_record


def test_workflow_reducer_rejects_mapping_key_mismatch() -> None:
    with pytest.raises(ValueError, match="workflow_id"):
        merge_workflow_records({}, {"wf-map-key": _workflow("wf-record")})


@pytest.mark.parametrize(
    ("replacement", "expected_message"),
    [
        (_workflow("wf-image", conversation_id="conv-2"), "conversation_id"),
        (_workflow("wf-image", kind=WorkflowKind.VIDEO), "kind"),
    ],
)
def test_workflow_reducer_rejects_identity_changes(
    replacement: WorkflowRecord,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        merge_workflow_records(
            {"wf-image": _workflow("wf-image")},
            {"wf-image": replacement},
        )


def test_namespace_builders_isolate_supervisor_workflows_and_conversations() -> None:
    supervisor = supervisor_namespace("conv-1")
    first_workflow = workflow_namespace("conv-1", "wf-image")
    second_workflow = workflow_namespace("conv-1", "wf-video")
    other_conversation = workflow_namespace("conv-2", "wf-image")

    assert supervisor.thread_id == "pf:conversation:conv-1:supervisor:v1"
    assert supervisor.checkpoint_ns == ""
    assert supervisor.as_runnable_config() == {
        "configurable": {
            "thread_id": "pf:conversation:conv-1:supervisor:v1",
            "checkpoint_ns": "",
        }
    }
    assert first_workflow.thread_id == "pf:conversation:conv-1:workflow:wf-image:v1"
    assert first_workflow.checkpoint_ns == ""
    assert len(
        {
            supervisor.thread_id,
            first_workflow.thread_id,
            second_workflow.thread_id,
            other_conversation.thread_id,
        }
    ) == 4


def test_namespace_runnable_config_restores_root_graph_checkpoint() -> None:
    checkpointer = InMemorySaver()
    graph = StateGraph(SupervisorState)
    graph.add_node(
        "advance_context",
        lambda state: {"context_version": state.get("context_version", 0) + 1},
    )
    graph.add_edge(START, "advance_context")
    graph.add_edge("advance_context", END)
    compiled = graph.compile(checkpointer=checkpointer)
    config = supervisor_namespace("conv-restore").as_runnable_config()

    compiled.invoke({"context_version": 1}, config)
    snapshot = compiled.get_state(config)
    resumed = compiled.invoke({}, config)
    workflow_config = workflow_namespace(
        "conv-restore",
        "wf-image",
    ).as_runnable_config()
    compiled.invoke({"context_version": 10}, workflow_config)

    assert snapshot.values["context_version"] == 2
    assert resumed["context_version"] == 3
    assert snapshot.config["configurable"]["thread_id"] == config["configurable"]["thread_id"]
    assert snapshot.config["configurable"]["checkpoint_ns"] == ""
    assert compiled.get_state(config).values["context_version"] == 3
    assert compiled.get_state(workflow_config).values["context_version"] == 11


@pytest.mark.parametrize(
    ("conversation_id", "workflow_id"),
    [
        ("", None),
        ("   ", None),
        ("conv:unsafe", None),
        ("conv-1", ""),
        ("conv-1", "   "),
        ("conv-1", "wf:unsafe"),
    ],
)
def test_namespace_builders_reject_ambiguous_identifiers(
    conversation_id: str,
    workflow_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="标识"):
        if workflow_id is None:
            supervisor_namespace(conversation_id)
        else:
            workflow_namespace(conversation_id, workflow_id)
