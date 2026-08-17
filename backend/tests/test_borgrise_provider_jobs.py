from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from pixelflow.agent_runtime.jobs import (
    MappingProviderJobAdapterResolver,
    OperationRecoveryRuntime,
    ProviderJobAdapter,
    ProviderJobOutcome,
)
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.skills.borgrise.provider_jobs import (
    ContentAppTaskContractError,
    make_merge_video_job_service,
    make_quality_review_job_service,
    make_reference_analysis_job_service,
    make_scene_video_job_service,
)
from pixelflow.video_agent.adapters.reference_operation import (
    M06ReferenceAnalysisOperationPort,
)
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import VideoToolContext
from pixelflow.video_agent.tools.reference import AnalyzeReferenceVideoTool

AUTHORIZATION = "Bearer transient-user-token"
STATUS_KEY = "internal-status-key"
NOW = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)


class _RecordingGraphResumer:
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


def _reference_context(payload: dict | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-provider-e2e",
        plan_id="plan-provider-e2e",
        step_id="step-provider-e2e",
        workspace=VideoWorkspace(
            workspace_id="workspace-provider-e2e",
            conversation_id="conversation-provider-e2e",
            payload=payload
            or {
                "assets": [
                    {
                        "artifact_ref": "artifact:reference-provider-e2e",
                        "media_type": "video",
                        "url": "https://example.invalid/reference.mp4?signature=temporary",
                    }
                ]
            },
        ),
    )


@pytest.mark.asyncio
async def test_reference_job_start_and_restart_status_use_separate_credentials() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/creative/decompose_video_to_storyboard"
            assert request.headers["Authorization"] == AUTHORIZATION
            assert request.headers["Idempotency-Key"].startswith("operation:v1:")
            assert json.loads(request.content) == {
                "video_url": "https://example.invalid/reference.mp4?signature=temporary"
            }
            return httpx.Response(
                200,
                json={"data": {"taskId": "provider-reference-1", "status": "PENDING"}},
            )
        assert request.method == "GET"
        assert request.url.path == "/api/task/provider-reference-1/status"
        assert "Authorization" not in request.headers
        assert request.headers["X-PixelFlow-Task-Status-Key"] == STATUS_KEY
        return httpx.Response(
            200,
            json={
                "data": {
                    "taskId": "provider-reference-1",
                    "status": "COMPLETED",
                    "result": {
                        "data": {
                            "shots": [
                                {
                                    "description": "安全镜头",
                                    "duration": 3,
                                    "provider_secret": "不能进入结果",
                                }
                            ]
                        }
                    },
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as first_client:
        first_service = make_reference_analysis_job_service(
            client=first_client,
            base_url="https://content-app.invalid/api",
            status_headers_provider=lambda: {
                "X-PixelFlow-Task-Status-Key": STATUS_KEY,
            },
        )
        started = await ProviderJobAdapter(first_service).start(
            {
                "video_url": "https://example.invalid/reference.mp4?signature=temporary"
            },
            authorization=AUTHORIZATION,
            idempotency_key="operation:v1:reference-test",
        )

    assert started.outcome is ProviderJobOutcome.POLLING
    assert AUTHORIZATION not in started.model_dump_json()

    # 模拟进程重建：新Service只使用provider job ID和独立状态通道。
    async with httpx.AsyncClient(transport=transport) as restarted_client:
        restarted_service = make_reference_analysis_job_service(
            client=restarted_client,
            base_url="https://content-app.invalid/api",
            status_headers_provider=lambda: {
                "X-PixelFlow-Task-Status-Key": STATUS_KEY,
            },
        )
        completed = await ProviderJobAdapter(restarted_service).status(
            "provider-reference-1"
        )

    assert completed.outcome is ProviderJobOutcome.SUCCEEDED
    assert completed.result["storyboard"][0]["description"] == "安全镜头"
    assert "provider_secret" not in completed.model_dump_json()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_status_fails_closed_without_server_status_channel() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("未配置状态通道时不应发送HTTP请求")
        )
    ) as client:
        service = make_reference_analysis_job_service(
            client=client,
            base_url="https://content-app.invalid/api",
            status_headers_provider=lambda: {},
        )

        with pytest.raises(ContentAppTaskContractError, match="状态查询通道未配置"):
            await service.status("provider-reference-1")


@pytest.mark.asyncio
async def test_status_rejects_persisted_user_authorization_header() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("用户Authorization不得进入恢复查询")
        )
    ) as client:
        service = make_reference_analysis_job_service(
            client=client,
            base_url="https://content-app.invalid/api",
            status_headers_provider=lambda: {"Authorization": AUTHORIZATION},
        )

        with pytest.raises(ContentAppTaskContractError, match="Authorization"):
            await service.status("provider-reference-1")


@pytest.mark.asyncio
async def test_service_authorization_status_maps_business_not_found_to_expired() -> None:
    seen_authorization = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_authorization
        seen_authorization = request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json={"success": False, "data": None, "message": "任务不存在"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = make_reference_analysis_job_service(
            client=client,
            base_url="https://content-app.invalid/api",
            status_headers_provider=lambda: {"Authorization": "Bearer service-test"},
            status_auth_mode="service_authorization",
        )

        snapshot = await ProviderJobAdapter(service).status("provider-reference-missing")

    assert seen_authorization == "Bearer service-test"
    assert snapshot.outcome is ProviderJobOutcome.EXPIRED
    assert "任务不存在" not in snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_recreated_provider_service_resumes_m06_without_repeating_start() -> None:
    start_count = 0
    status_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal start_count, status_count
        if request.method == "POST":
            start_count += 1
            return httpx.Response(
                200,
                json={"data": {"taskId": "provider-e2e-1", "status": "PENDING"}},
            )
        status_count += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "taskId": "provider-e2e-1",
                    "status": "COMPLETED",
                    "result": {
                        "shots": [{"description": "恢复后的安全镜头", "duration": 4}]
                    },
                }
            },
        )

    repository = MemoryAgentRuntimeRepository()
    transport = httpx.MockTransport(handler)

    def status_headers() -> dict[str, str]:
        return {"X-PixelFlow-Task-Status-Key": STATUS_KEY}

    async with httpx.AsyncClient(transport=transport) as start_client:
        start_adapter = ProviderJobAdapter(
            make_reference_analysis_job_service(
                client=start_client,
                base_url="https://content-app.invalid/api",
                status_headers_provider=status_headers,
            )
        )
        start_tool = AnalyzeReferenceVideoTool(
            operation_port=M06ReferenceAnalysisOperationPort(
                repository=repository,
                adapter=start_adapter,
                authorization_provider=lambda context: AUTHORIZATION,
                lease_owner="provider-e2e-start",
                clock=lambda: NOW,
                job_id_factory=lambda: "operation-provider-e2e",
            )
        )
        started = await start_tool.execute(
            _reference_context(),
            {"reference_asset_ref": "artifact:reference-provider-e2e"},
        )

    assert started.pending_operation_job_ids == ("operation-provider-e2e",)
    async with httpx.AsyncClient(transport=transport) as restarted_client:
        restarted_adapter = ProviderJobAdapter(
            make_reference_analysis_job_service(
                client=restarted_client,
                base_url="https://content-app.invalid/api",
                status_headers_provider=status_headers,
            )
        )
        stage_digest = hashlib.sha256(
            b"artifact:reference-provider-e2e"
        ).hexdigest()[:16]
        resumer = _RecordingGraphResumer()
        await OperationRecoveryRuntime(
            repository,
            resolver=MappingProviderJobAdapterResolver(
                {f"analyze_reference:{stage_digest}": restarted_adapter}
            ),
            resumer=resumer,
            worker_id="provider-e2e-recovery",
            clock=lambda: NOW + timedelta(seconds=3),
        ).run_once()
        replay_tool = AnalyzeReferenceVideoTool(
            operation_port=M06ReferenceAnalysisOperationPort(
                repository=repository,
                adapter=restarted_adapter,
                authorization_provider=lambda context: AUTHORIZATION,
                lease_owner="provider-e2e-replay",
                clock=lambda: NOW + timedelta(seconds=4),
            )
        )
        replayed = await replay_tool.execute(
            _reference_context(
                {**_reference_context().workspace.payload, **started.workspace_patch}
            ),
            {"reference_asset_ref": "artifact:reference-provider-e2e"},
        )

    assert replayed.pending_operation_job_ids == ()
    assert replayed.workspace_patch["scenes"][0]["description"] == (
        "恢复后的安全镜头"
    )
    assert start_count == 1
    assert status_count == 1
    assert len(resumer.event_ids) == 1


@pytest.mark.asyncio
async def test_scene_video_service_maps_text_generation_to_safe_artifact() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/video/text-to-video"
            assert request.headers["Authorization"] == AUTHORIZATION
            assert request.headers["modelType"] == "seedance-2.0"
            assert json.loads(request.content) == {
                "prompt": "稳定展示商品",
                "model": "seedance-2.0",
                "ratio": "9:16",
                "size": "720p",
                "duration": 5,
                "videoCount": 1,
                "sound": "on",
            }
            return httpx.Response(
                200,
                json={"data": {"taskId": "provider-scene-1", "status": "PENDING"}},
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "taskId": "provider-scene-1",
                    "status": "COMPLETED",
                    "result": {
                        "video_url": "https://cdn.example.invalid/provider-scene-1.mp4"
                    },
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = make_scene_video_job_service(
            client=client,
            base_url="https://content-app.invalid/api",
            status_headers_provider=lambda: {
                "Authorization": "Bearer scene-status-service"
            },
            status_auth_mode="service_authorization",
        )
        adapter = ProviderJobAdapter(service)
        started = await adapter.start(
            {
                "generation_mode": "text_to_video",
                "prompt": "稳定展示商品",
                "model": "seedance-2.0",
                "ratio": "9:16",
                "size": "720p",
                "duration_sec": 5,
                "sound": "on",
            },
            authorization=AUTHORIZATION,
            idempotency_key="operation:v1:scene-service-test",
        )
        completed = await adapter.status("provider-scene-1")

    assert started.outcome is ProviderJobOutcome.POLLING
    assert completed.outcome is ProviderJobOutcome.SUCCEEDED
    assert completed.result["artifact_ref"].startswith("artifact:provider-video:")
    assert completed.result["video_url"].endswith("provider-scene-1.mp4")
    assert AUTHORIZATION not in completed.model_dump_json()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_quality_review_service_projects_only_workflow_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/creative/video_quality_review"
            body = json.loads(request.content)
            assert body["merged_video_url"].endswith("merged.mp4")
            assert body["scene_videos"][0]["scene_id"] == "scene-1"
            return httpx.Response(
                200,
                json={"data": {"taskId": "provider-quality-1", "status": "PENDING"}},
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "taskId": "provider-quality-1",
                    "status": "COMPLETED",
                    "result": {
                        "passed": False,
                        "summaryMarkdown": "发现一处商品遮挡",
                        "qualityReportMarkdown": "建议重做第一镜",
                        "issues": [{"scene_id": "scene-1", "type": "occlusion"}],
                        "affectedSceneIds": ["scene-1"],
                        "revisionPrompt": "保持构图并减少遮挡",
                        "provider_secret": "不能进入工作流",
                    },
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ProviderJobAdapter(
            make_quality_review_job_service(
                client=client,
                base_url="https://content-app.invalid/api",
                status_headers_provider=lambda: {
                    "Authorization": "Bearer quality-status-service"
                },
                status_auth_mode="service_authorization",
            )
        )
        started = await adapter.start(
            {
                "merged_video_url": "https://cdn.example.invalid/merged.mp4",
                "scene_videos": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 0,
                        "video_url": "https://cdn.example.invalid/scene-1.mp4",
                    }
                ],
                "scene_packages": [],
                "brief": {"expected_duration_sec": 5},
                "materials": [],
                "user_feedback": "修正商品遮挡",
                "ratio": "9:16",
                "size": "720p",
            },
            authorization=AUTHORIZATION,
            idempotency_key="operation:v1:quality-service-test",
        )
        completed = await adapter.status("provider-quality-1")

    assert started.outcome is ProviderJobOutcome.POLLING
    assert completed.outcome is ProviderJobOutcome.SUCCEEDED
    assert completed.result["passed"] is False
    assert completed.result["affected_scene_ids"] == ("scene-1",)
    assert "raw" not in completed.result
    assert "provider_secret" not in completed.model_dump_json()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_merge_service_returns_safe_synchronous_terminal() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/video/merge"
        assert request.headers["Authorization"] == AUTHORIZATION
        assert request.headers["Idempotency-Key"] == "operation:v1:merge-test"
        assert json.loads(request.content) == {
            "videoUrls": [
                "https://cdn.example.invalid/scene-1.mp4",
                "https://cdn.example.invalid/scene-2.mp4",
            ]
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "video_url": "https://cdn.example.invalid/merged.mp4",
                    "provider_secret": "不能进入结果",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ProviderJobAdapter(
            make_merge_video_job_service(
                client=client,
                base_url="https://content-app.invalid/api",
                request_timeout_seconds=30,
            )
        )
        completed = await adapter.start(
            {
                "video_urls": [
                    "https://cdn.example.invalid/scene-1.mp4",
                    "https://cdn.example.invalid/scene-2.mp4",
                ],
                "scene_videos": [],
                "duration": 10,
                "size": "720p",
                "model": "seedance-2.0",
            },
            authorization=AUTHORIZATION,
            idempotency_key="operation:v1:merge-test",
        )

    assert completed.outcome is ProviderJobOutcome.SUCCEEDED
    assert completed.provider_job_id.startswith("sync-merge-")
    assert completed.result["video_url"].endswith("merged.mp4")
    assert "provider_secret" not in completed.model_dump_json()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_merge_service_maps_transport_disconnect_to_timeout() -> None:
    """网关掐断长连接时不得抛 ProviderJobCallError，应落 timeout 终态。"""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.RemoteProtocolError("server disconnected without sending a response")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        completed = await ProviderJobAdapter(
            make_merge_video_job_service(
                client=client,
                base_url="https://content-app.invalid/api",
            )
        ).start(
            {
                "video_urls": [
                    "https://cdn.example.invalid/scene-1.mp4",
                    "https://cdn.example.invalid/scene-2.mp4",
                ],
                "scene_videos": [],
                "duration": 10,
                "size": "720p",
                "model": "seedance-2.0",
            },
            authorization=AUTHORIZATION,
            idempotency_key="operation:v1:merge-transport-test",
        )

    assert completed.outcome is ProviderJobOutcome.TIMEOUT
    assert completed.provider_job_id.startswith("sync-merge-")


@pytest.mark.asyncio
async def test_single_scene_merge_finishes_without_provider_call() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("单镜头合并不得调用content-app")
        )
    ) as client:
        completed = await ProviderJobAdapter(
            make_merge_video_job_service(
                client=client,
                base_url="https://content-app.invalid/api",
            )
        ).start(
            {
                "video_urls": ["https://cdn.example.invalid/scene-1.mp4"],
                "scene_videos": [],
                "duration": 5,
                "size": "720p",
                "model": "seedance-2.0",
            },
            authorization=AUTHORIZATION,
            idempotency_key="operation:v1:single-merge-test",
        )

    assert completed.outcome is ProviderJobOutcome.SUCCEEDED
    assert completed.result["video_url"].endswith("scene-1.mp4")
