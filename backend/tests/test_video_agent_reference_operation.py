from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from pixelflow.agent_runtime.jobs import (
    MappingProviderJobAdapterResolver,
    OperationRecoveryRuntime,
    ProviderJobAdapter,
)
from pixelflow.agent_runtime.persistence.repositories import (
    MemoryAgentRuntimeRepository,
)
from pixelflow.video_agent.adapters.reference_operation import (
    M06ReferenceAnalysisOperationPort,
)
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import VideoToolContext
from pixelflow.video_agent.tools.reference import AnalyzeReferenceVideoTool

NOW = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
AUTHORIZATION = "Bearer reference-operation-test"


class ScriptedReferenceJobService:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.status_calls: list[str] = []

    async def start(
        self,
        request,
        *,
        authorization: str,
        idempotency_key: str,
    ):
        self.start_calls.append(
            {
                "request": dict(request),
                "authorization": authorization,
                "idempotency_key": idempotency_key,
            }
        )
        return {"job_id": "provider-reference-1", "status": "polling"}

    async def status(self, provider_job_id: str):
        self.status_calls.append(provider_job_id)
        return {
            "job_id": provider_job_id,
            "status": "succeeded",
            "result": {
                "storyboard": [
                    {
                        "description": "商品使用前的痛点",
                        "duration": 3,
                        "shot_type": "近景",
                    },
                    {
                        "description": "商品解决问题",
                        "duration": 5,
                        "shot_type": "特写",
                    },
                ]
            },
        }


class RecordingGraphResumer:
    def __init__(self) -> None:
        self.event_ids: list[str] = []

    async def resume_external_job(
        self,
        namespace,
        *,
        completion_event,
        idempotency_key: str,
    ) -> None:
        del namespace, completion_event
        self.event_ids.append(idempotency_key)


def _context(payload: dict | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        plan_id="plan-reference-1",
        step_id="step-reference-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-reference-1",
            conversation_id="conversation-reference-1",
            payload=payload
            or {
                "assets": [
                    {
                        "artifact_ref": "artifact:reference-1",
                        "media_type": "video",
                        "url": "https://example.invalid/reference.mp4?signature=sensitive",
                    }
                ]
            },
        ),
    )


@pytest.mark.asyncio
async def test_reference_operation_recovers_same_job_without_repeating_start() -> None:
    repository = MemoryAgentRuntimeRepository()
    service = ScriptedReferenceJobService()
    adapter = ProviderJobAdapter(service)
    authorization_calls = 0

    def authorization_provider(context: VideoToolContext) -> str:
        nonlocal authorization_calls
        assert context.user_id == "user-1"
        authorization_calls += 1
        return AUTHORIZATION

    port = M06ReferenceAnalysisOperationPort(
        repository=repository,
        adapter=adapter,
        authorization_provider=authorization_provider,
        lease_owner="reference-start-worker",
        clock=lambda: NOW,
        job_id_factory=lambda: "operation-reference-1",
    )
    tool = AnalyzeReferenceVideoTool(operation_port=port)

    started = await tool.execute(
        _context(),
        {"reference_asset_ref": "artifact:reference-1"},
    )

    assert started.pending_operation_job_ids == ("operation-reference-1",)
    assert len(service.start_calls) == 1
    stage_digest = hashlib.sha256(b"artifact:reference-1").hexdigest()[:16]
    resumer = RecordingGraphResumer()
    runtime = OperationRecoveryRuntime(
        repository,
        resolver=MappingProviderJobAdapterResolver(
            {f"analyze_reference:{stage_digest}": adapter}
        ),
        resumer=resumer,
        worker_id="reference-poll-worker",
        clock=lambda: NOW + timedelta(seconds=3),
    )

    await runtime.run_once()

    replay_payload = {
        **_context().workspace.payload,
        **started.workspace_patch,
    }
    replayed = await tool.execute(
        _context(replay_payload),
        {"reference_asset_ref": "artifact:reference-1"},
    )
    operation = await repository.get_operation("user-1", "operation-reference-1")

    assert replayed.pending_operation_job_ids == ()
    assert replayed.workspace_patch["reference_videos"][0]["status"] == "done"
    assert len(replayed.workspace_patch["scenes"]) == 2
    assert len(service.start_calls) == 1
    assert service.status_calls == ["provider-reference-1"]
    assert len(resumer.event_ids) == 1
    assert authorization_calls == 2
    assert operation is not None
    assert AUTHORIZATION not in operation.model_dump_json()
    assert "sensitive" not in operation.model_dump_json()
