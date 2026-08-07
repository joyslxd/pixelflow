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
from pixelflow.video_agent.adapters.scene_operation import (
    M06SceneGenerationOperationPort,
)
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import GenerateScenesTool, VideoToolContext

NOW = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)
AUTHORIZATION = "Bearer scene-operation-test"


class ScriptedSceneJobService:
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
        return {"job_id": "provider-scene-1", "status": "polling"}

    async def status(self, provider_job_id: str):
        self.status_calls.append(provider_job_id)
        return {
            "job_id": provider_job_id,
            "status": "succeeded",
            "result": {
                "variant_id": "scene-3-v2",
                "artifact_ref": "artifact:scene-3-v2",
                "video_url": "https://cdn.example.invalid/scene-3-v2.mp4",
                "completed_at": (NOW + timedelta(seconds=8)).isoformat(),
            },
        }


class RecordingGraphResumer:
    def __init__(self) -> None:
        self.event_ids: list[str] = []

    async def resume_external_job(
        self,
        namespace,
        *,
        user_id: str,
        conversation_id: str,
        completion_event,
        idempotency_key: str,
    ) -> None:
        del namespace, user_id, conversation_id, completion_event
        self.event_ids.append(idempotency_key)


def _context(payload: dict | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        plan_id="plan-scene-1",
        step_id="step-scene-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-scene-1",
            conversation_id="conversation-scene-1",
            payload=payload
            or {
                "scenes": [
                    {
                        "scene_id": "scene-3",
                        "scene_index": 3,
                        "prompt": "稳定展示商品细节",
                        "duration_sec": 5,
                        "asset_refs": ["artifact:product-1"],
                        "variants": [],
                    }
                ],
                "dirty_scene_ids": ["scene-3"],
            },
        ),
    )


@pytest.mark.asyncio
async def test_scene_operation_recovers_variant_without_repeating_start() -> None:
    repository = MemoryAgentRuntimeRepository()
    service = ScriptedSceneJobService()
    adapter = ProviderJobAdapter(service)
    port = M06SceneGenerationOperationPort(
        repository=repository,
        adapter=adapter,
        authorization_provider=lambda context: AUTHORIZATION,
        lease_owner="scene-start-worker",
        clock=lambda: NOW,
        job_id_factory=lambda: "operation-scene-1",
    )
    tool = GenerateScenesTool(operation_port=port)

    started = await tool.execute(
        _context(),
        {"scene_ids": ["scene-3"], "variant_count": 1},
    )

    assert started.pending_operation_job_ids == ("operation-scene-1",)
    assert len(service.start_calls) == 1
    scene_digest = hashlib.sha256(b"scene-3").hexdigest()[:12]
    resumer = RecordingGraphResumer()
    runtime = OperationRecoveryRuntime(
        repository,
        resolver=MappingProviderJobAdapterResolver(
            {f"generate_scene:{scene_digest}:v1": adapter}
        ),
        resumer=resumer,
        worker_id="scene-poll-worker",
        clock=lambda: NOW + timedelta(seconds=3),
    )

    await runtime.run_once()

    replayed = await tool.execute(
        _context({**_context().workspace.payload, **started.workspace_patch}),
        {"scene_ids": ["scene-3"], "variant_count": 1},
    )
    target = replayed.workspace_patch["scenes"][0]
    operation = await repository.get_operation("user-1", "operation-scene-1")

    assert replayed.pending_operation_job_ids == ()
    assert target["variants"][0]["variant_id"] == "scene-3-v2"
    assert target["variants"][0]["artifact_ref"] == "artifact:scene-3-v2"
    assert target["variants"][0]["video_url"].endswith("scene-3-v2.mp4")
    assert replayed.workspace_patch["assets"][-1]["artifact_ref"] == (
        "artifact:scene-3-v2"
    )
    assert target["edit_status"] == "等待版本审核"
    assert len(service.start_calls) == 1
    assert service.status_calls == ["provider-scene-1"]
    assert len(resumer.event_ids) == 1
    assert operation is not None
    assert AUTHORIZATION not in operation.model_dump_json()
