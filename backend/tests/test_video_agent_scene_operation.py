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
                "creation_contract": {
                    "video_model": "seedance-2.0",
                    "video_ratio": "9:16",
                    "video_size": "720p",
                    "video_sound": "on",
                    "video_duration_sec": 30,
                },
                "scenes": [
                    {
                        "scene_id": "scene-3",
                        "scene_index": 3,
                        "prompt": "稳定展示商品细节",
                        "duration_ms": 5000,
                        "shot_description": {
                            "text": "0-5秒 商品特写",
                            "mentions": [
                                {
                                    "asset_id": "prop-1",
                                    "image_url": "https://cdn.example.invalid/prop-1.png",
                                }
                            ],
                        },
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
    provider_request = service.start_calls[0]["request"]
    assert provider_request["model"] == "seedance-2.0"
    assert provider_request["ratio"] == "9:16"
    assert provider_request["size"] == "720p"
    assert provider_request["sound"] == "on"
    assert provider_request["duration"] == 5
    assert provider_request["generation_mode"] == "reference_mode_video"
    assert provider_request["image_urls"] == [
        "https://cdn.example.invalid/prop-1.png"
    ]
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
    assert target["edit_status"] == "重新生成完成"
    assert target["approved_variant_id"] == "scene-3-v2"
    assert replayed.workspace_patch["dirty_scene_ids"] == []
    assert len(service.start_calls) == 1
    assert service.status_calls == ["provider-scene-1"]
    assert len(resumer.event_ids) == 1
    assert operation is not None
    assert AUTHORIZATION not in operation.model_dump_json()


@pytest.mark.asyncio
async def test_scene_operation_requires_creation_contract_params() -> None:
    repository = MemoryAgentRuntimeRepository()
    service = ScriptedSceneJobService()
    port = M06SceneGenerationOperationPort(
        repository=repository,
        adapter=ProviderJobAdapter(service),
        authorization_provider=lambda context: AUTHORIZATION,
        lease_owner="scene-start-worker",
        clock=lambda: NOW,
        job_id_factory=lambda: "operation-scene-missing-contract",
    )
    tool = GenerateScenesTool(operation_port=port)

    with pytest.raises(Exception, match="创作合同"):
        await tool.execute(
            _context(
                {
                    "scenes": [
                        {
                            "scene_id": "scene-3",
                            "prompt": "缺少合同参数",
                            "duration_ms": 5000,
                            "variants": [],
                        }
                    ],
                    "dirty_scene_ids": ["scene-3"],
                }
            ),
            {"scene_ids": ["scene-3"], "variant_count": 1},
        )

    assert service.start_calls == []


@pytest.mark.asyncio
async def test_scene_operation_fills_image_urls_from_global_assets() -> None:
    """mentions 缺 image_url 时，须从 global_assets 按 reference_asset_ids 补齐。"""

    repository = MemoryAgentRuntimeRepository()
    service = ScriptedSceneJobService()
    port = M06SceneGenerationOperationPort(
        repository=repository,
        adapter=ProviderJobAdapter(service),
        authorization_provider=lambda context: AUTHORIZATION,
        lease_owner="scene-start-worker",
        clock=lambda: NOW,
        job_id_factory=lambda: "operation-scene-global-assets",
    )
    tool = GenerateScenesTool(operation_port=port)
    await tool.execute(
        _context(
            {
                "creation_contract": {
                    "video_model": "seedance-2.0",
                    "video_ratio": "9:16",
                    "video_size": "720p",
                    "video_sound": "on",
                },
                "global_assets": {
                    "characters": [
                        {
                            "asset_id": "character-1",
                            "name": "安然",
                            "images": ["https://cdn.example.invalid/anran.png"],
                        }
                    ],
                    "scenes": [],
                    "props": [],
                },
                "scenes": [
                    {
                        "scene_id": "scene-3",
                        "scene_index": 3,
                        "prompt": "故事线：旧脏字段。镜头描述：应被忽略",
                        "duration_ms": 5000,
                        "reference_asset_ids": ["character-1"],
                        "shot_description": {
                            "text": "0-5秒: 画面：@character-1盯着手机。",
                            "mentions": [{"asset_id": "character-1", "name": "安然"}],
                        },
                        "variants": [],
                    }
                ],
                "dirty_scene_ids": ["scene-3"],
            }
        ),
        {"scene_ids": ["scene-3"], "variant_count": 1},
    )
    request = service.start_calls[0]["request"]
    # Provider 提示词用正式名；image_urls 仍按 asset_id 从 global_assets 绑定。
    assert request["prompt"] == "0-5秒: 画面：@安然盯着手机。"
    assert request["image_urls"] == ["https://cdn.example.invalid/anran.png"]
    assert request["generation_mode"] == "reference_mode_video"


@pytest.mark.asyncio
async def test_scene_operation_rewrites_multiple_asset_ids_to_names() -> None:
    """多角色/道具/场景的 @asset_id 在 Provider prompt 中一并换成正式名。"""

    repository = MemoryAgentRuntimeRepository()
    service = ScriptedSceneJobService()
    port = M06SceneGenerationOperationPort(
        repository=repository,
        adapter=ProviderJobAdapter(service),
        authorization_provider=lambda context: AUTHORIZATION,
        lease_owner="scene-start-worker",
        clock=lambda: NOW,
        job_id_factory=lambda: "operation-scene-name-rewrite",
    )
    tool = GenerateScenesTool(operation_port=port)
    await tool.execute(
        _context(
            {
                "creation_contract": {
                    "video_model": "seedance-2.0",
                    "video_ratio": "9:16",
                    "video_size": "720p",
                    "video_sound": "on",
                },
                "global_assets": {
                    "characters": [
                        {
                            "asset_id": "character-1",
                            "name": "Yann",
                            "images": ["https://cdn.example.invalid/yann.png"],
                        },
                        {
                            "asset_id": "character-2",
                            "name": "安然",
                            "images": ["https://cdn.example.invalid/anran.png"],
                        },
                    ],
                    "scenes": [
                        {
                            "asset_id": "scene-8451cb6d9d",
                            "name": "海岛临时剪辑房",
                            "images": ["https://cdn.example.invalid/room.png"],
                        }
                    ],
                    "props": [
                        {
                            "asset_id": "prop-2",
                            "name": "录音笔",
                            "images": ["https://cdn.example.invalid/recorder.png"],
                        }
                    ],
                },
                "scenes": [
                    {
                        "scene_id": "scene-7",
                        "scene_index": 7,
                        "duration_ms": 10000,
                        "reference_asset_ids": [
                            "scene-8451cb6d9d",
                            "character-2",
                            "prop-2",
                            "character-1",
                        ],
                        "shot_description": {
                            "text": (
                                "0-10秒: 地点:@scene-8451cb6d9d。"
                                "形象参考@character-2握住道具:@prop-2，"
                                "放进形象参考@character-1手中"
                            ),
                            "mentions": [
                                {"asset_id": "scene-8451cb6d9d", "name": "海岛临时剪辑房"},
                                {"asset_id": "character-2", "name": "安然"},
                                {"asset_id": "prop-2", "name": "录音笔"},
                                {"asset_id": "character-1", "name": "Yann"},
                            ],
                        },
                        "variants": [],
                    }
                ],
                "dirty_scene_ids": ["scene-7"],
            }
        ),
        {"scene_ids": ["scene-7"], "variant_count": 1},
    )
    request = service.start_calls[0]["request"]
    assert request["prompt"] == (
        "0-10秒: 地点:@海岛临时剪辑房。"
        "形象参考@安然握住道具:@录音笔，"
        "放进形象参考@Yann手中"
    )
    assert request["image_urls"] == [
        "https://cdn.example.invalid/room.png",
        "https://cdn.example.invalid/anran.png",
        "https://cdn.example.invalid/recorder.png",
        "https://cdn.example.invalid/yann.png",
    ]
