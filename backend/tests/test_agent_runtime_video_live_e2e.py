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
from pixelflow.agent_runtime.conversation_router import ConversationRouteService
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.tasks import MemoryPixelFlowTaskStore
from tests._router_auth_helpers import make_authed_test_app

NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-4000-8000-000000000214")
AUTHORIZATION = "Bearer task14-local-fake-credential"
QUOTA_V1_AUTHORIZATION = "Bearer quota-resume-e2e-marker"
STALE_REVISION_AUTHORIZATION = "Bearer stale-revision-marker"
QUOTA_V2_AUTHORIZATION = "Bearer quota-resume-v2-marker"
FLOW_AUTHORIZATIONS = (
    AUTHORIZATION,
    QUOTA_V1_AUTHORIZATION,
    STALE_REVISION_AUTHORIZATION,
    QUOTA_V2_AUTHORIZATION,
)
AUTHORIZATION_TOKENS = tuple(
    authorization.partition(" ")[2]
    for authorization in FLOW_AUTHORIZATIONS
)
SENSITIVE_AUTHORIZATION_MARKERS = (
    *FLOW_AUTHORIZATIONS,
    *AUTHORIZATION_TOKENS,
)
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
        self.idempotency_key_by_job: dict[str, str] = {}
        self.status_scripts: dict[str, list[object]] = {}

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
        self.idempotency_key_by_job[job_id] = idempotency_key
        return {"job_id": job_id, "status": "running", "result": {"progress": 0}}

    async def status(self, provider_job_id: str) -> object:
        self.status_calls += 1
        script = self.status_scripts.get(provider_job_id)
        if script:
            outcome = script.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, Mapping):
                return dict(outcome)
            if outcome == "quota_paused":
                return {"job_id": provider_job_id, "status": "quota_paused"}
            if outcome != "succeeded":
                raise AssertionError(f"未知本地 Provider 脚本结果：{outcome}")
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

    def script_status(self, provider_job_id: str, *outcomes: object) -> None:
        """为已真实 start 的 Provider job 安排后续 status 结果。"""

        assert provider_job_id in self.requests_by_job
        assert outcomes
        self.status_scripts[provider_job_id] = list(outcomes)

    def start_count_for_provider_job(self, provider_job_id: str) -> int:
        """按稳定幂等键统计目标 Provider job 的真实 start 次数。"""

        idempotency_key = self.idempotency_key_by_job[provider_job_id]
        return self.idempotency_keys.count(idempotency_key)


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
    clock = _Clock()
    repository = MemoryVideoRuntimeRepository(
        task_store=task_store,
        completion_clock=clock.now,
    )
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
            conversation_router=ConversationRouteService(),
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


async def _wait_for_turn_completed(
    client: httpx.AsyncClient,
    conversation_id: str,
    run_id: str,
) -> dict[str, Any]:
    """通过公开 run/job 等待原 Turn 提交，避免误把 notify 入队前当成空闲。"""

    latest: dict[str, Any] | None = None
    for _index in range(500):
        response = await client.get(
            f"/agent/conversations/{conversation_id}/turns/jobs/{run_id}"
        )
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] == "completed":
            return latest
        if latest["status"] == "failed":
            raise AssertionError(f"等待原 Turn 完成时进入 failed：{latest}")
        await asyncio.sleep(0.01)
    raise AssertionError(f"限定时间内原 Turn 未完成：{latest}")


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


async def _respond_to_authorization(
    client: httpx.AsyncClient,
    conversation_id: str,
    snapshot: dict[str, Any],
    *,
    sequence: int,
    authorization: str,
    explicit_action: dict[str, Any] | None = None,
) -> httpx.Response:
    """原样提交公开中断冻结的授权动作，避免测试自行重建权威补丁。"""

    interrupt = snapshot["interrupt"]
    assert interrupt is not None
    authorization_action = (
        interrupt["payload"]["authorization_action"]
        if explicit_action is None
        else explicit_action
    )
    return await client.post(
        f"/agent/conversations/{conversation_id}/interrupts/"
        f"{interrupt['interrupt_id']}/responses",
        headers={"Authorization": authorization},
        json={
            "client_response_id": str(UUID(int=sequence)),
            "value": {
                "content": "额度已恢复，继续原任务",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "explicit_action": authorization_action,
            },
        },
    )


async def _advance_external_jobs(
    live_runtime: PixelFlowAgentLiveRuntime,
    clock: _Clock,
) -> None:
    """只从公开 M06 worker port 投递 fake 完成事件。"""

    assert live_runtime.operation_recovery is not None
    clock.advance()
    await live_runtime.operation_recovery.run_once()


async def _start_scene_generation(
    client: httpx.AsyncClient,
    live_runtime: PixelFlowAgentLiveRuntime,
    *,
    client_input_id: str,
    response_sequence_base: int,
    title: str,
) -> tuple[str, str, dict[str, Any]]:
    """通过公开 Turn 与四次人工响应推进到真实分镜 Operation。"""

    created = await client.post(
        "/agent/conversations",
        json={"title": title},
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]
    started = await client.post(
        f"/agent/conversations/{conversation_id}/turns/start",
        headers={"Authorization": AUTHORIZATION},
        json={
            "client_input_id": client_input_id,
            "content": "使用商品参考图制作一条 30 秒竖屏新品视频",
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
        sequence=response_sequence_base + 1,
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
        sequence=response_sequence_base + 2,
        action="continue_workflow",
        patch={"direction_id": direction_id},
        content="选择第一条创意方向",
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
        sequence=response_sequence_base + 3,
        action="continue_workflow",
        patch={},
        content="同意创作方案",
    )
    packages = await _wait_for_interrupt(
        client,
        live_runtime,
        conversation_id,
        kind="video_scene_package_review",
        run_id=run_id,
    )
    await _respond(
        client,
        conversation_id,
        packages,
        sequence=response_sequence_base + 4,
        action="continue_workflow",
        patch={},
        content="确认分镜和素材",
    )
    generating = await _wait_for_snapshot(
        client,
        conversation_id,
        lambda value: value["workflows"]
        and value["workflows"][0]["current_stage"]
        == "generate_scene_videos",
        live_runtime=live_runtime,
    )
    return conversation_id, run_id, generating


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


class _PublicProjectionReplay:
    """从上一公开 cursor 逐段消费 SSE，并重建下一份权威 Snapshot。"""

    _PROJECTED_FIELDS = (
        "run",
        "workflows",
        "messages",
        "interrupt",
        "context_version",
        "resume",
    )

    def __init__(
        self,
        *,
        app: FastAPI,
        conversation_id: str,
        initial_snapshot: dict[str, Any],
    ) -> None:
        self._app = app
        self._conversation_id = conversation_id
        self._projection = {
            key: copy.deepcopy(initial_snapshot[key])
            for key in self._PROJECTED_FIELDS
        }
        self.checkpoints: list[str] = []
        self.credential_boundaries: set[str] = set()
        self.verify_no_credentials(
            "initial_snapshot",
            initial_snapshot,
        )

    async def verify(
        self,
        label: str,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """验证 sequence/cursor 连续，并对事件投影后的全部公开字段做等值比较。"""

        previous_resume = self._projection["resume"]
        target_resume = snapshot["resume"]
        assert isinstance(previous_resume, dict)
        assert isinstance(target_resume, dict)
        previous_cursor = previous_resume["cursor"]
        target_cursor = target_resume["cursor"]
        assert isinstance(previous_cursor, str)
        assert isinstance(target_cursor, str)
        assert target_resume["sequence"] > previous_resume["sequence"]
        events = await _read_sse_until_cursor(
            self._app,
            self._conversation_id,
            after_cursor=previous_cursor,
            target_cursor=target_cursor,
        )
        expected_sequences = list(
            range(previous_resume["sequence"] + 1, target_resume["sequence"] + 1)
        )
        assert [event["sequence"] for event in events] == expected_sequences
        self.verify_no_credentials(f"{label}_snapshot", snapshot)
        self.verify_no_credentials(f"{label}_sse", events)
        for event in events:
            self._apply(event)
        for field in self._PROJECTED_FIELDS:
            assert self._projection[field] == snapshot[field], (
                f"{label} 的 SSE 重建字段不一致：{field}"
            )
        self.checkpoints.append(label)
        return events

    def verify_no_credentials(self, label: str, document: object) -> None:
        """扫描一个真实边界，并登记该边界已覆盖全部授权 marker。"""

        self._assert_no_credential(label, document)
        self.credential_boundaries.add(label)

    def _apply(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        payload = event["payload"]
        if event_type == "workflow.progressed":
            workflow = copy.deepcopy(payload["workflow"])
            workflows = {
                item["workflow_id"]: item
                for item in self._projection["workflows"]
            }
            workflows[workflow["workflow_id"]] = workflow
            self._projection["workflows"] = list(workflows.values())
        elif event_type == "message.upserted":
            message = copy.deepcopy(payload["message"])
            messages = {
                item["message_id"]: item
                for item in self._projection["messages"]
            }
            messages[message["message_id"]] = message
            self._projection["messages"] = sorted(
                messages.values(),
                key=lambda item: (
                    str(item.get("created_at", "")),
                    str(item.get("message_id", "")),
                ),
            )
        elif event_type == "interrupt.opened":
            interrupt = payload["interrupt"]
            self._projection["interrupt"] = {
                key: copy.deepcopy(interrupt.get(key))
                for key in (
                    "interrupt_id",
                    "conversation_id",
                    "workflow_id",
                    "turn_id",
                    "kind",
                    "reason_code",
                    "payload",
                    "opened_at",
                )
            }
        elif event_type == "interrupt.closed":
            current = self._projection["interrupt"]
            if (
                isinstance(current, dict)
                and current.get("interrupt_id") == payload["interrupt_id"]
            ):
                self._projection["interrupt"] = None
        elif event_type == "interrupt.responded":
            self._projection["context_version"] += 1
        elif event_type in {"input.state_changed", "run.state_changed"}:
            run_id = payload.get("run_id", payload.get("turn_id", event["run_id"]))
            status = {
                "waiting_user": "waiting_user",
                "failed": "failed",
                "completed": "completed",
            }.get(payload["status"], "running")
            run = self._projection["run"]
            assert isinstance(run, dict)
            if run.get("runId") != run_id:
                run["updatedAt"] = event["occurred_at"]
                self._projection["context_version"] += 1
            run["runId"] = run_id
            run["status"] = status
        self._projection["resume"] = {
            "cursor": event["cursor"],
            "sequence": event["sequence"],
        }

    @staticmethod
    def _assert_no_credential(label: str, document: object) -> None:
        serialized = json.dumps(
            document,
            ensure_ascii=False,
            default=str,
        ).lower()
        for marker in SENSITIVE_AUTHORIZATION_MARKERS:
            assert marker.lower() not in serialized, label
        assert "secret_only" not in serialized, label


@pytest.mark.parametrize(
    "marker",
    (*FLOW_AUTHORIZATIONS, *AUTHORIZATION_TOKENS),
)
def test_public_projection_guard_rejects_every_flow_authorization(marker: str) -> None:
    """公开投影守卫必须识别本流程实际使用的每一个授权值。"""

    with pytest.raises(AssertionError):
        _PublicProjectionReplay._assert_no_credential(
            "injected-authorization",
            {"authorization": marker},
        )


@pytest.mark.asyncio
async def test_video_live_public_entry_opens_intake_with_complete_attachment() -> None:
    """真实 Controller 必须冻结 live owner，并把首轮附件完整投影到 Snapshot。"""

    async with _live_client() as (client, live_runtime, providers, _clock, _app):
        created = await client.post(
            "/agent/conversations",
            json={"title": "Task 14 视频链路"},
        )
        assert created.status_code == 200
        conversation = created.json()
        assert conversation["orchestration_mode"] == "frontend_v2"
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
        assert started.json()["orchestration_mode"] == "supervisor_v1"
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
async def test_video_live_flow_from_zero_to_delivery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """从零经公开人工中断与 M06 worker 完成 QA 修改和最终交付。"""

    async with _live_client() as (client, live_runtime, providers, clock, app):
        assert live_runtime.operation_recovery is not None
        # 测试按步骤手动驱动同一个生产 Worker，避免后台扫描与断言并发争抢租约。
        await live_runtime.operation_recovery.aclose()
        created = await client.post(
            "/agent/conversations",
            json={"title": "Task 14 完整视频链路"},
        )
        assert created.status_code == 200
        conversation = created.json()
        conversation_id = conversation["conversation_id"]
        assert conversation["orchestration_mode"] == "frontend_v2"

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
        assert started.json()["orchestration_mode"] == "supervisor_v1"
        run_id = started.json()["run_id"]
        intake = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_intake_form",
            run_id=run_id,
        )
        projection_replay = _PublicProjectionReplay(
            app=app,
            conversation_id=conversation_id,
            initial_snapshot=intake,
        )
        repository = live_runtime.repository
        assert repository is not None
        owner = str(USER_ID)
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
        await projection_replay.verify("response-1", directions)
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
        await projection_replay.verify("response-2", plan)
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
        await projection_replay.verify("response-3", scene_packages)
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
        await projection_replay.verify("response-4", generating)
        first_scene_starts = providers[0].start_calls
        assert first_scene_starts > 0
        assert generating["interrupt"] is None

        pending_job = generating["workflows"][0]["pending_external_job"]
        assert pending_job is not None
        scene_job_id = pending_job["job_id"]
        original_provider_job_id = pending_job["provider_job_id"]
        original_attempt = pending_job["attempt"]
        assert isinstance(original_provider_job_id, str)
        providers[0].script_status(
            original_provider_job_id,
            "quota_paused",
            "quota_paused",
            "succeeded",
        )

        await _advance_external_jobs(live_runtime, clock)
        assert live_runtime.operation_recovery is not None
        await live_runtime.operation_recovery.run_once()
        quota_paused = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="authorization_required",
            run_id=run_id,
        )
        await projection_replay.verify("worker-scene-quota-v1", quota_paused)
        quota_interrupt_v1 = await repository.get_interrupt(
            owner,
            quota_paused["interrupt"]["interrupt_id"],
        )
        assert quota_interrupt_v1 is not None
        assert live_runtime.graph_runtime is not None
        quota_v1_pause_checkpoint = (
            await live_runtime.graph_runtime.graph.aget_state(
                {
                    "configurable": {
                        "thread_id": quota_interrupt_v1.thread_id,
                        "checkpoint_ns": "",
                    }
                }
            )
        )
        projection_replay.verify_no_credentials(
            "quota_v1_pause_graph_checkpoint",
            {
                "values": quota_v1_pause_checkpoint.values,
                "interrupts": [
                    item.value for item in quota_v1_pause_checkpoint.interrupts
                ],
            },
        )
        authorization_action = quota_paused["interrupt"]["payload"][
            "authorization_action"
        ]
        assert authorization_action["patch"] == {
            "job_id": scene_job_id,
            "quota_pause_revision": 1,
        }
        starts_before_resume = providers[0].start_calls
        resumed = await _respond_to_authorization(
            client,
            conversation_id,
            quota_paused,
            sequence=101,
            authorization=QUOTA_V1_AUTHORIZATION,
        )
        assert resumed.status_code == 200, resumed.text
        resumed_turn = await _wait_for_turn_completed(
            client,
            conversation_id,
            run_id,
        )
        assert resumed_turn["turn_id"] == run_id
        resumed_snapshot = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["interrupt"] is None
            and value["workflows"]
            and value["workflows"][0]["status"] == "running",
            live_runtime=live_runtime,
        )
        await projection_replay.verify("response-scene-quota-v1", resumed_snapshot)
        quota_v1_resume_checkpoint = (
            await live_runtime.graph_runtime.graph.aget_state(
                {
                    "configurable": {
                        "thread_id": quota_interrupt_v1.thread_id,
                        "checkpoint_ns": "",
                    }
                }
            )
        )
        assert quota_v1_resume_checkpoint.interrupts == ()
        projection_replay.verify_no_credentials(
            "quota_v1_resume_graph_checkpoint",
            {
                "values": quota_v1_resume_checkpoint.values,
                "interrupts": [
                    item.value for item in quota_v1_resume_checkpoint.interrupts
                ],
            },
        )
        resumed_job = resumed_snapshot["workflows"][0]["pending_external_job"]
        assert resumed_job is not None
        assert resumed_job["job_id"] == scene_job_id
        assert resumed_job["provider_job_id"] == original_provider_job_id
        assert resumed_job["attempt"] == original_attempt
        assert providers[0].start_calls == starts_before_resume
        assert providers[0].start_count_for_provider_job(original_provider_job_id) == 1

        await _advance_external_jobs(live_runtime, clock)
        await live_runtime.operation_recovery.run_once()
        quota_paused_again = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="authorization_required",
            run_id=run_id,
        )
        await projection_replay.verify(
            "worker-scene-quota-v2",
            quota_paused_again,
        )
        quota_interrupt_v2 = await repository.get_interrupt(
            owner,
            quota_paused_again["interrupt"]["interrupt_id"],
        )
        assert quota_interrupt_v2 is not None
        quota_v2_pause_checkpoint = (
            await live_runtime.graph_runtime.graph.aget_state(
                {
                    "configurable": {
                        "thread_id": quota_interrupt_v2.thread_id,
                        "checkpoint_ns": "",
                    }
                }
            )
        )
        projection_replay.verify_no_credentials(
            "quota_v2_pause_graph_checkpoint",
            {
                "values": quota_v2_pause_checkpoint.values,
                "interrupts": [
                    item.value for item in quota_v2_pause_checkpoint.interrupts
                ],
            },
        )
        authorization_action_v2 = quota_paused_again["interrupt"]["payload"][
            "authorization_action"
        ]
        assert authorization_action_v2["patch"] == {
            "job_id": scene_job_id,
            "quota_pause_revision": 2,
        }
        stale_action = copy.deepcopy(authorization_action_v2)
        stale_action["patch"]["quota_pause_revision"] = 1
        stale = await _respond_to_authorization(
            client,
            conversation_id,
            quota_paused_again,
            sequence=102,
            authorization=STALE_REVISION_AUTHORIZATION,
            explicit_action=stale_action,
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["reason_code"] == "video_quota_resume_stale"

        resumed_again = await _respond_to_authorization(
            client,
            conversation_id,
            quota_paused_again,
            sequence=103,
            authorization=QUOTA_V2_AUTHORIZATION,
        )
        assert resumed_again.status_code == 200, resumed_again.text
        resumed_turn_again = await _wait_for_turn_completed(
            client,
            conversation_id,
            run_id,
        )
        assert resumed_turn_again["turn_id"] == run_id
        resumed_snapshot_again = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["interrupt"] is None
            and value["workflows"]
            and value["workflows"][0]["status"] == "running",
            live_runtime=live_runtime,
        )
        await projection_replay.verify(
            "response-scene-quota-v2",
            resumed_snapshot_again,
        )
        quota_v2_resume_checkpoint = (
            await live_runtime.graph_runtime.graph.aget_state(
                {
                    "configurable": {
                        "thread_id": quota_interrupt_v2.thread_id,
                        "checkpoint_ns": "",
                    }
                }
            )
        )
        assert quota_v2_resume_checkpoint.interrupts == ()
        projection_replay.verify_no_credentials(
            "quota_v2_resume_graph_checkpoint",
            {
                "values": quota_v2_resume_checkpoint.values,
                "interrupts": [
                    item.value for item in quota_v2_resume_checkpoint.interrupts
                ],
            },
        )
        resumed_job_again = resumed_snapshot_again["workflows"][0][
            "pending_external_job"
        ]
        assert resumed_job_again is not None
        assert resumed_job_again["job_id"] == scene_job_id
        assert resumed_job_again["provider_job_id"] == original_provider_job_id
        assert resumed_job_again["attempt"] == original_attempt
        assert providers[0].start_calls == starts_before_resume
        assert providers[0].start_count_for_provider_job(original_provider_job_id) == 1

        await _advance_external_jobs(live_runtime, clock)
        scene_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_scene_video_review",
            run_id=run_id,
        )
        await projection_replay.verify("worker-scene-v1", scene_review)
        await _respond(
            client,
            conversation_id,
            scene_review,
            sequence=5,
            action="continue_workflow",
            patch={},
            content="确认分镜视频并合并",
        )
        merging = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"] == "merge_video",
        )
        await projection_replay.verify("response-5", merging)
        assert providers[1].start_calls == 1
        await _advance_external_jobs(live_runtime, clock)
        video_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_result_review",
            run_id=run_id,
        )
        await projection_replay.verify("worker-merge-v1", video_review)
        await _respond(
            client,
            conversation_id,
            video_review,
            sequence=6,
            action="modify_workflow",
            patch={"user_feedback": "请检查第二镜商品露出"},
            content="请先执行视频质检",
        )
        quality_running = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"] == "quality_review",
        )
        await projection_replay.verify("response-6", quality_running)
        assert providers[2].start_calls == 1
        await _advance_external_jobs(live_runtime, clock)
        quality_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_result_review",
            run_id=run_id,
        )
        await projection_replay.verify(
            "worker-quality-review",
            quality_review,
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
        revised_generating = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"]
            == "generate_scene_videos",
        )
        await projection_replay.verify("response-7", revised_generating)
        assert providers[0].start_calls == first_scene_starts + 1
        await _advance_external_jobs(live_runtime, clock)
        revised_scene_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_scene_video_review",
            run_id=run_id,
        )
        await projection_replay.verify(
            "worker-scene-v2",
            revised_scene_review,
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
        revised_merging = await _wait_for_snapshot(
            client,
            conversation_id,
            lambda value: value["workflows"]
            and value["workflows"][0]["current_stage"] == "merge_video",
        )
        await projection_replay.verify("response-8", revised_merging)
        assert providers[1].start_calls == 2
        await _advance_external_jobs(live_runtime, clock)
        revised_video_review = await _wait_for_interrupt(
            client,
            live_runtime,
            conversation_id,
            kind="video_result_review",
            run_id=run_id,
        )
        await projection_replay.verify(
            "worker-merge-v2",
            revised_video_review,
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
        await projection_replay.verify("response-9", completed)
        completed_workflow = completed["workflows"][0]
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
        sse_events = await projection_replay.verify(
            "download",
            final_snapshot,
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
        assert sse_events
        assert sse_events[-1]["cursor"] == final_snapshot["resume"]["cursor"]
        assert sse_events[-1]["sequence"] == final_snapshot["resume"]["sequence"]
        assert projection_replay.checkpoints == [
            "response-1",
            "response-2",
            "response-3",
            "response-4",
            "worker-scene-quota-v1",
            "response-scene-quota-v1",
            "worker-scene-quota-v2",
            "response-scene-quota-v2",
            "worker-scene-v1",
            "response-5",
            "worker-merge-v1",
            "response-6",
            "worker-quality-review",
            "response-7",
            "worker-scene-v2",
            "response-8",
            "worker-merge-v2",
            "response-9",
            "download",
        ]
        assert [
            label
            for label in projection_replay.checkpoints
            if label
            in {
                "response-4",
                "worker-scene-quota-v1",
                "response-scene-quota-v1",
                "worker-scene-v1",
            }
        ] == [
            "response-4",
            "worker-scene-quota-v1",
            "response-scene-quota-v1",
            "worker-scene-v1",
        ]
        turns = await repository.list_turns(owner, conversation_id)
        operations = await repository.list_operations(owner, conversation_id)
        events = await repository.list_events(owner, conversation_id)
        projection_messages = await repository.list_projection_messages(
            owner,
            conversation_id,
        )
        projection_replay.verify_no_credentials(
            "repository_turns",
            [turn.model_dump(mode="json") for turn in turns],
        )
        projection_replay.verify_no_credentials(
            "repository_operations",
            [operation.model_dump(mode="json") for operation in operations],
        )
        projection_replay.verify_no_credentials(
            "quota_completion_projection_events",
            [event.model_dump(mode="json") for event in events],
        )
        projection_replay.verify_no_credentials(
            "projection_messages",
            [message.model_dump(mode="json") for message in projection_messages],
        )
        projection_replay.verify_no_credentials(
            "safety_logs",
            [record.getMessage() for record in caplog.records],
        )
        assert {
            "repository_turns",
            "repository_operations",
            "quota_completion_projection_events",
            "quota_v1_pause_graph_checkpoint",
            "quota_v1_resume_graph_checkpoint",
            "quota_v2_pause_graph_checkpoint",
            "quota_v2_resume_graph_checkpoint",
            "worker-scene-quota-v1_snapshot",
            "response-scene-quota-v1_snapshot",
            "worker-scene-quota-v2_snapshot",
            "response-scene-quota-v2_snapshot",
            "download_snapshot",
            "worker-scene-quota-v1_sse",
            "response-scene-quota-v1_sse",
            "worker-scene-quota-v2_sse",
            "response-scene-quota-v2_sse",
            "projection_messages",
            "safety_logs",
        }.issubset(projection_replay.credential_boundaries)

    workflow = final_snapshot["workflows"][0]
    assert workflow["current_stage"] == "completed"
    assert workflow["status"] == "completed"
    assert providers[0].start_calls == 4
    assert providers[1].start_calls == 2
    assert providers[2].start_calls == 1
    assert providers[3].start_calls == 0
