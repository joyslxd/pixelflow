"""R2 视频 live Handler 真实公共入口与 fake Provider 端到端门禁。"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import JsonValue
from test_agent_video_live_handler import VIDEO_FORM, _FakeCapabilities

from app.gateway.auth.models import User
from app.gateway.pixelflow_agent_live_capabilities import (
    GatewayVideoLiveCapabilities,
    PowerMemVideoLivePort,
)
from app.gateway.pixelflow_agent_live_providers import (
    make_video_live_provider_adapters,
)
from app.gateway.pixelflow_agent_runtime import (
    PixelFlowAgentLiveRuntime,
    make_pixelflow_agent_live_runtime,
)
from app.gateway.routers import pixelflow_conversations
from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.context import ModelContextProfile
from pixelflow.agent_runtime.persistence import MemoryVideoRuntimeRepository
from pixelflow.agent_runtime.service import AgentRuntimeService
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.tasks import MemoryPixelFlowTaskStore
from tests._router_auth_helpers import make_authed_test_app

NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-4000-8000-000000000214")
AUTHORIZATION = "Bearer task14-local-fake-credential"
MATERIAL = {
    "type": "image",
    "url": "https://materials.example.com/task14-product.png",
    "name": "Task14 商品参考图.png",
    "reference": "material:task14:product:v1",
}
LIVE_VIDEO_FORM = copy.deepcopy(VIDEO_FORM)
LIVE_VIDEO_FORM["video_model_capabilities"]["durations_sec"] = [
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    12,
    14,
    15,
]


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int = 3) -> None:
        self.value += timedelta(seconds=seconds)


class _DecisionModel:
    async def ainvoke(self, _messages: Any) -> object:
        raise AssertionError("结构化首轮动作不应调用分类模型")


class _AnswerPort:
    async def answer(self, _context: Any) -> str:
        raise AssertionError("视频主流程不应进入只读回答")


class _MemoryService:
    async def search(self, **_kwargs: Any) -> list[Any]:
        return []

    async def record(self, **_kwargs: Any) -> bool:
        return True


class _LiveCapabilities(_FakeCapabilities):
    """让本地 fake Plan 满足场景包阶段要求的具体资产合同。"""

    async def generate_initial_plan(self, **kwargs: Any):
        result = await super().generate_initial_plan(**kwargs)
        blueprints = copy.deepcopy(result.scene_blueprints)
        manifest = copy.deepcopy(result.asset_manifest)
        replacements = {
            "目标用户": "都市健康管理师林岚",
            "真实使用场景": "晨间公寓健康监测区",
        }
        for blueprint in blueprints:
            requirements = blueprint["asset_requirements"]
            for collection in ("characters", "scenes", "props"):
                requirements[collection] = [
                    replacements.get(name, name)
                    for name in requirements[collection]
                ]
            for source, target in replacements.items():
                for field_name in ("shot_description", "storyline", "narration"):
                    blueprint[field_name] = blueprint[field_name].replace(
                        source,
                        target,
                    )
        for collection in ("characters", "scenes", "props"):
            for item in manifest[collection]:
                old_name = item["name"]
                new_name = replacements.get(old_name, old_name)
                item["name"] = new_name
                for field_name in (
                    "description",
                    "three_view_prompt",
                    "image_prompt",
                ):
                    if field_name in item:
                        item[field_name] = item[field_name].replace(
                            old_name,
                            new_name,
                        )
        normalized_manifest = normalize_asset_manifest(manifest, blueprints)
        history = copy.deepcopy(result.plan_history)
        history[-1]["scene_blueprints"] = copy.deepcopy(blueprints)
        history[-1]["asset_manifest"] = copy.deepcopy(normalized_manifest)
        return replace(
            result,
            scene_blueprints=blueprints,
            asset_manifest=normalized_manifest,
            plan_history=history,
        )


class _ScriptedProvider:
    """按 stage 返回可安全完成的本地结果，并记录每个稳定 start。"""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.start_calls = 0
        self.status_calls = 0
        self.requests_by_job: dict[str, dict[str, JsonValue]] = {}
        self.idempotency_keys: list[str] = []

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        assert authorization == AUTHORIZATION
        assert idempotency_key.startswith("operation:v1:sha256:")
        self.start_calls += 1
        job_id = f"task14-{self.stage}-{self.start_calls}"
        self.requests_by_job[job_id] = dict(request)
        self.idempotency_keys.append(idempotency_key)
        return {"job_id": job_id, "status": "running", "result": {"progress": 0}}

    async def status(self, provider_job_id: str) -> object:
        self.status_calls += 1
        request = self.requests_by_job[provider_job_id]
        if self.stage == "generate_scene_video":
            scene_id = str(request["scene_id"])
            result: dict[str, JsonValue] = {
                "video_url": f"https://videos.example.com/{scene_id}-a{self.start_calls}.mp4",
                "raw": {},
            }
        elif self.stage == "merge_video":
            result = {
                "video_url": f"https://videos.example.com/merged-{self.start_calls}.mp4",
                "raw": {},
            }
        elif self.stage == "quality_review":
            scene_videos = request["scene_videos"]
            assert isinstance(scene_videos, list) and scene_videos
            affected = scene_videos[min(1, len(scene_videos) - 1)]
            assert isinstance(affected, dict)
            result = {
                "passed": False,
                "summary_markdown": "一处分镜需要调整。",
                "quality_report_markdown": "商品露出需要加强。",
                "issues": [
                    {
                        "scene_id": str(affected["scene_id"]),
                        "message": "商品露出不足",
                    }
                ],
                "affected_scene_ids": [str(affected["scene_id"])],
                "revision_prompt": "增强商品露出",
                "raw": {},
            }
        else:
            raise AssertionError("本 happy path 不应创建剪映草稿")
        return {"job_id": provider_job_id, "status": "succeeded", "result": result}


def _user() -> User:
    return User(
        email="task14@example.com",
        password_hash="x",
        system_role="user",
        id=USER_ID,
    )


def _config() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        mode="primary",
        enabled_intents=("video",),
        new_conversation_rollout_percent=100,
        context_compaction_enabled=True,
    )


def _profiles() -> dict[str, ModelContextProfile]:
    return {
        "deepseek-v4-pro": ModelContextProfile(
            model_name="deepseek-v4-pro",
            max_context_tokens=1_000_000,
            max_output_tokens=32 * 1024,
            tokenizer_strategy="task14_local_fake",
            verified_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            source="Task 14 本地 fake 档案",
        )
    }


@asynccontextmanager
async def _live_client() -> AsyncIterator[
    tuple[
        httpx.AsyncClient,
        PixelFlowAgentLiveRuntime,
        tuple[_ScriptedProvider, ...],
        _Clock,
        FastAPI,
    ]
]:
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryVideoRuntimeRepository(task_store=task_store)
    app: FastAPI = make_authed_test_app(user_factory=_user)
    memory_service = _MemoryService()
    memory_port = PowerMemVideoLivePort(memory_service)
    capabilities = GatewayVideoLiveCapabilities(
        capabilities=_LiveCapabilities(),  # type: ignore[arg-type]
        decision_model=_DecisionModel(),
        answer_port=_AnswerPort(),
        memory_port=memory_port,
        reason_code=None,
    )
    providers = tuple(
        _ScriptedProvider(stage)
        for stage in (
            "generate_scene_video",
            "merge_video",
            "quality_review",
            "jianying_draft",
        )
    )
    provider_adapters = make_video_live_provider_adapters(
        generate_scene_video=providers[0],
        merge_video=providers[1],
        quality_review=providers[2],
        jianying_draft=providers[3],
    )
    clock = _Clock()
    async with make_pixelflow_agent_live_runtime(
        app,
        config=_config(),
        repository=repository,
        task_store=task_store,
        checkpointer=InMemorySaver(),
        capabilities=capabilities,
        providers=provider_adapters,
        model_name="deepseek-v4-pro",
        model_profiles=_profiles(),
        memory_search=memory_service,
        clock=clock,
    ) as live_runtime:
        assert live_runtime.executor is not None
        app.state.pixelflow_task_store = task_store
        app.state.pixelflow_agent_runtime_service = AgentRuntimeService(
            config=_config(),
            repository=repository,
            task_store=task_store,
            turn_executor=live_runtime.executor,
            video_repository=repository,
            primary_execution_intents=live_runtime.primary_execution_intents,
            clock=clock.now,
        )
        app.include_router(pixelflow_conversations.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://task14.local",
        ) as client:
            yield client, live_runtime, providers, clock, app


async def _wait_for_interrupt(
    client: httpx.AsyncClient,
    live_runtime: object,
    conversation_id: str,
    *,
    kind: str,
    run_id: str,
) -> dict[str, Any]:
    latest_snapshot: dict[str, Any] | None = None
    for _index in range(500):
        response = await client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot"
        )
        assert response.status_code == 200
        latest_snapshot = response.json()
        interrupt = latest_snapshot["interrupt"]
        if interrupt is not None and interrupt["kind"] == kind:
            return latest_snapshot
        await asyncio.sleep(0.01)
    run_response = await client.get(
        f"/agent/conversations/{conversation_id}/turns/jobs/{run_id}"
    )
    snapshot_summary = {
        "run": latest_snapshot["run"] if latest_snapshot is not None else None,
        "interrupt": (
            latest_snapshot["interrupt"] if latest_snapshot is not None else None
        ),
        "workflowProgress": (
            latest_snapshot.get("workflowProgress")
            if latest_snapshot is not None
            else None
        ),
    }
    raise AssertionError(
        f"限定时间内未出现 {kind} interrupt；"
        f"run={run_response.json()} runtime_status={live_runtime.status_snapshot()} "
        f"snapshot={snapshot_summary}"
    )


async def _wait_for_snapshot(
    client: httpx.AsyncClient,
    conversation_id: str,
    predicate: Any,
    *,
    live_runtime: PixelFlowAgentLiveRuntime | None = None,
) -> dict[str, Any]:
    """只轮询公开 Snapshot，避免测试越过 Controller 读取权威状态。"""

    latest: dict[str, Any] | None = None
    for _index in range(500):
        response = await client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot"
        )
        assert response.status_code == 200
        latest = response.json()
        if predicate(latest):
            return latest
        if latest["run"]["status"] == "failed":
            raise AssertionError(
                "等待 Snapshot 目标状态时 Turn 已进入 failed："
                f"run={latest['run']} workflows={latest['workflows']} "
                f"runtime_status={None if live_runtime is None else live_runtime.status_snapshot()}"
            )
        await asyncio.sleep(0.01)
    raise AssertionError(f"限定时间内 Snapshot 未达到目标状态：{latest}")


async def _respond(
    client: httpx.AsyncClient,
    conversation_id: str,
    snapshot: dict[str, Any],
    *,
    sequence: int,
    action: str,
    patch: dict[str, JsonValue],
    content: str,
) -> dict[str, Any]:
    """通过公开响应 endpoint 恢复同一原 Turn。"""

    interrupt = snapshot["interrupt"]
    assert interrupt is not None
    payload = interrupt["payload"]
    explicit_action = {
        "action": action,
        "intent": "video",
        "workflow_id": interrupt["workflow_id"],
        "stage": payload.get("stage"),
        "artifact_ref": payload.get("artifact_ref"),
        "patch": patch,
    }
    response = await client.post(
        f"/agent/conversations/{conversation_id}/interrupts/"
        f"{interrupt['interrupt_id']}/responses",
        headers={"Authorization": AUTHORIZATION},
        json={
            "client_response_id": str(UUID(int=sequence)),
            "value": {
                "content": content,
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "explicit_action": explicit_action,
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _advance_external_jobs(
    live_runtime: PixelFlowAgentLiveRuntime,
    clock: _Clock,
) -> None:
    """只从公开 M06 worker port 投递 fake 完成事件。"""

    assert live_runtime.operation_recovery is not None
    clock.advance()
    await live_runtime.operation_recovery.run_once()


def _latest_artifact(snapshot: dict[str, Any], artifact_type: str) -> dict[str, Any]:
    for message in snapshot["messages"]:
        artifact = message.get("payload", {}).get("artifact")
        if isinstance(artifact, dict) and artifact.get("type") == artifact_type:
            return artifact
    raise AssertionError(f"Snapshot 中不存在 {artifact_type} Artifact")


async def _read_sse_until_cursor(
    app: FastAPI,
    conversation_id: str,
    *,
    after_cursor: str,
    target_cursor: str,
) -> list[dict[str, Any]]:
    """只经公开 SSE endpoint 读取已提交事件，命中 Snapshot 游标后主动断开。"""

    events: list[dict[str, Any]] = []
    disconnected = asyncio.Event()
    request_sent = False
    response_status: int | None = None
    response_buffer = ""

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_status, response_buffer
        if message["type"] == "http.response.start":
            response_status = int(message["status"])
            return
        if message["type"] != "http.response.body":
            return
        response_buffer += bytes(message.get("body", b"")).decode("utf-8")
        while "\n\n" in response_buffer:
            block, response_buffer = response_buffer.split("\n\n", 1)
            data_line = next(
                (line for line in block.splitlines() if line.startswith("data: ")),
                None,
            )
            if data_line is None:
                continue
            event = json.loads(data_line.removeprefix("data: "))
            events.append(event)
            if event["cursor"] == target_cursor:
                disconnected.set()

    query_string = str(httpx.QueryParams({"cursor": after_cursor})).encode("ascii")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/agent/conversations/{conversation_id}/agent-events",
        "raw_path": f"/agent/conversations/{conversation_id}/agent-events".encode(),
        "query_string": query_string,
        "headers": [(b"host", b"task14.local")],
        "client": ("127.0.0.1", 50000),
        "server": ("task14.local", 80),
        "root_path": "",
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    assert response_status == 200
    return events


@pytest.mark.asyncio
async def test_video_live_public_entry_opens_intake_with_complete_attachment() -> None:
    """真实 Controller 必须冻结 live owner，并把首轮附件完整投影到 Snapshot。"""

    async with _live_client() as (client, live_runtime, providers, _clock, _app):
        created = await client.post(
            "/agent/conversations",
            json={"title": "Task 14 视频链路", "initial_intent": "video"},
        )
        assert created.status_code == 200
        conversation = created.json()
        assert conversation["orchestration_mode"] == "supervisor_v1"
        assert live_runtime.ready is True

        started = await client.post(
            f"/agent/conversations/{conversation['conversation_id']}/turns/start",
            headers={"Authorization": AUTHORIZATION},
            json={
                "client_input_id": "11111111-1111-4111-8111-111111111214",
                "content": "用商品参考图制作一条 30 秒竖屏新品视频",
                "materials": [MATERIAL],
                "expected_context_version": 0,
                "explicit_action": {
                    "action": "start_workflow",
                    "intent": "video",
                    "workflow_id": None,
                    "stage": None,
                    "artifact_ref": None,
                    "patch": {},
                },
            },
        )
        assert started.status_code == 200
        snapshot = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation["conversation_id"],
            kind="video_intake_form",
            run_id=started.json()["run_id"],
        )

    assert snapshot["interrupt"]["payload"]["materials"] == [MATERIAL]
    assert snapshot["interrupt"]["payload"]["core_message"] == (
        "用商品参考图制作一条 30 秒竖屏新品视频"
    )
    assert all(provider.start_calls == 0 for provider in providers)
    assert all(provider.status_calls == 0 for provider in providers)


@pytest.mark.asyncio
async def test_video_live_flow_from_zero_to_delivery() -> None:
    """从零经公开人工中断与 M06 worker 完成 QA 修改和最终交付。"""

    async with _live_client() as (client, live_runtime, providers, clock, app):
        created = await client.post(
            "/agent/conversations",
            json={"title": "Task 14 完整视频链路", "initial_intent": "video"},
        )
        assert created.status_code == 200
        conversation = created.json()
        conversation_id = conversation["conversation_id"]
        assert conversation["orchestration_mode"] == "supervisor_v1"

        started = await client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            headers={"Authorization": AUTHORIZATION},
            json={
                "client_input_id": "22222222-2222-4222-8222-222222222214",
                "content": "用商品参考图制作一条 30 秒竖屏新品视频",
                "materials": [MATERIAL],
                "expected_context_version": 0,
                "explicit_action": {
                    "action": "start_workflow",
                    "intent": "video",
                    "workflow_id": None,
                    "stage": None,
                    "artifact_ref": None,
                    "patch": {},
                },
            },
        )
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        intake = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_intake_form",
            run_id=run_id,
        )
        await _respond(
            client,
            conversation_id,
            intake,
            sequence=1,
            action="continue_workflow",
            patch={"form_values": LIVE_VIDEO_FORM},
            content="确认视频需求",
        )
        directions = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_direction_review",
            run_id=run_id,
        )
        direction_id = directions["interrupt"]["payload"]["directions"][0][
            "direction_id"
        ]
        await _respond(
            client,
            conversation_id,
            directions,
            sequence=2,
            action="continue_workflow",
            patch={"direction_id": direction_id},
            content="选择第一个创意方向",
        )
        plan = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_plan_review",
            run_id=run_id,
        )
        await _respond(
            client,
            conversation_id,
            plan,
            sequence=3,
            action="continue_workflow",
            patch={},
            content="同意创作方案",
        )
        scene_packages = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_scene_package_review",
            run_id=run_id,
        )
        await _respond(
            client,
            conversation_id,
            scene_packages,
            sequence=4,
            action="continue_workflow",
            patch={},
            content="确认分镜和素材",
        )
        generating = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"] == "generate_scene_videos",
        )
        first_scene_starts = providers[0].start_calls
        assert first_scene_starts > 0
        assert generating["interrupt"] is None

        await _advance_external_jobs(live_runtime, clock)
        scene_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_scene_video_review",
            run_id=run_id,
        )
        await _respond(
            client,
            conversation_id,
            scene_review,
            sequence=5,
            action="continue_workflow",
            patch={},
            content="确认分镜视频并合并",
        )
        await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"] == "merge_video",
        )
        assert providers[1].start_calls == 1
        await _advance_external_jobs(live_runtime, clock)
        video_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_result_review",
            run_id=run_id,
        )
        await _respond(
            client,
            conversation_id,
            video_review,
            sequence=6,
            action="modify_workflow",
            patch={"user_feedback": "请检查第二镜商品露出"},
            content="请先执行视频质检",
        )
        await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"] == "quality_review",
        )
        assert providers[2].start_calls == 1
        await _advance_external_jobs(live_runtime, clock)
        quality_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_result_review",
            run_id=run_id,
        )
        quality_artifact = _latest_artifact(
            quality_review,
            "video_quality_review",
        )
        quality_result = quality_artifact["videoQualityReview"]
        affected_scene_id = quality_result["affected_scene_ids"][0]
        await _respond(
            client,
            conversation_id,
            quality_review,
            sequence=7,
            action="modify_workflow",
            patch={
                "scene_patches": {
                    affected_scene_id: {"narration": "强化商品功能旁白"}
                }
            },
            content="只重生成质检命中的分镜",
        )
        await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"]
            == "generate_scene_videos",
        )
        assert providers[0].start_calls == first_scene_starts + 1
        await _advance_external_jobs(live_runtime, clock)
        revised_scene_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_scene_video_review",
            run_id=run_id,
        )
        await _respond(
            client,
            conversation_id,
            revised_scene_review,
            sequence=8,
            action="continue_workflow",
            patch={},
            content="确认修改后的分镜并重新合并",
        )
        await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"] == "merge_video",
        )
        assert providers[1].start_calls == 2
        await _advance_external_jobs(live_runtime, clock)
        revised_video_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_result_review",
            run_id=run_id,
        )
        await _respond(
            client,
            conversation_id,
            revised_video_review,
            sequence=9,
            action="continue_workflow",
            patch={},
            content="无意见，结束",
        )
        completed = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["status"] == "completed",
        )
        completed_workflow = completed["workflows"][0]
        completed_cursor = completed["resume"]["cursor"]
        delivery_artifact = next(
            artifact
            for artifact in (
                message.get("payload", {}).get("artifact")
                for message in completed["messages"]
            )
            if isinstance(artifact, dict)
            and artifact.get("type") == "video_result"
            and artifact.get("videoAccepted") is True
        )
        current_merged_url = delivery_artifact["mergedVideo"][
            "merged_video_url"
        ]
        delivery_ref = completed_workflow["latest_artifact_refs"][-1]
        download_request = {
            "client_input_id": "22222222-2222-4222-8222-222222222215",
            "content": "记录当前最终视频下载",
            "materials": [],
            "expected_context_version": completed["context_version"],
            "explicit_action": {
                "action": "continue_workflow",
                "intent": "video",
                "workflow_id": completed_workflow["workflow_id"],
                "stage": completed_workflow["current_stage"],
                "artifact_ref": delivery_ref,
                "patch": {"delivery_download_url": current_merged_url},
            },
        }
        downloaded = await client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            headers={"Authorization": AUTHORIZATION},
            json=download_request,
        )
        assert downloaded.status_code == 200
        download_run_id = downloaded.json()["run_id"]
        final_snapshot = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: any(
                isinstance(artifact, dict)
                and artifact.get("deliveryDownloadedUrl")
                == current_merged_url
                for artifact in (
                    message.get("payload", {}).get("artifact")
                    for message in value["messages"]
                )
            ),
            live_runtime=live_runtime,
        )
        final_artifact = next(
            artifact
            for artifact in (
                message.get("payload", {}).get("artifact")
                for message in final_snapshot["messages"]
            )
            if isinstance(artifact, dict)
            and artifact.get("deliveryDownloadedUrl") == current_merged_url
        )
        assert final_artifact["deliveryDownloadedUrl"] == current_merged_url
        assert final_artifact["mergedVideo"]["merged_video_url"] == current_merged_url
        assert delivery_artifact.get("deliveryDownloadedUrl") is None
        starts_before_replay = tuple(provider.start_calls for provider in providers)
        replayed = await client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            headers={"Authorization": AUTHORIZATION},
            json=download_request,
        )
        assert replayed.status_code == 200
        assert replayed.json()["run_id"] == download_run_id
        for _index in range(3):
            refreshed = await client.get(
                f"/agent/conversations/{conversation_id}/agent-snapshot"
            )
            assert refreshed.status_code == 200
        assert tuple(provider.start_calls for provider in providers) == starts_before_replay
        sse_events = await _read_sse_until_cursor(
            app,
            conversation_id,
            after_cursor=completed_cursor,
            target_cursor=final_snapshot["resume"]["cursor"],
        )
        assert sse_events
        assert sse_events[-1]["cursor"] == final_snapshot["resume"]["cursor"]
        assert sse_events[-1]["sequence"] == final_snapshot["resume"]["sequence"]

    workflow = final_snapshot["workflows"][0]
    assert workflow["current_stage"] == "completed"
    assert workflow["status"] == "completed"
    assert providers[0].start_calls == 4
    assert providers[1].start_calls == 2
    assert providers[2].start_calls == 1
    assert providers[3].start_calls == 0
