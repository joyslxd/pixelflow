"""P0-4.2 frontend_v2 → video_agent_v2 历史升级。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.tasks.store import MemoryPixelFlowTaskStore, PixelFlowConversationRecord
from pixelflow.video_agent.legacy_upgrade import (
    FrontendV2LegacyUpgrader,
    _workspace_id_for,
    map_frontend_v2_payload,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


def test_map_frontend_v2_payload_collects_scene_packages_and_dirty_scenes() -> None:
    payload = map_frontend_v2_payload(
        conversation_id="conv-1",
        context={
            "videoScenePackages": {"scenes": [{"scene_id": "s1"}]},
            "videoScenePackageEditedSceneIds": ["s1"],
            "artifact_refs": ["artifact:script-1"],
        },
        messages=[
            SimpleNamespace(
                artifact={
                    "type": "video_scene_packages",
                    "mergedVideo": {"url": "https://example.com/a.mp4"},
                    "artifact_refs": ["artifact:scene-1"],
                }
            )
        ],
    )
    assert payload["scene_packages"]["scenes"][0]["scene_id"] == "s1"
    assert payload["dirty_scene_ids"] == ["s1"]
    assert "artifact:script-1" in payload["artifact_refs"]
    assert "artifact:scene-1" in payload["artifact_refs"]
    assert payload["merged_video"]["url"].endswith("a.mp4")


@pytest.mark.asyncio
async def test_legacy_upgrade_is_idempotent_and_switches_mode() -> None:
    now = datetime(2026, 8, 12, 14, tzinfo=UTC)
    store = MemoryPixelFlowTaskStore()
    repo = MemoryVideoAgentRepository()
    conversation = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conv-legacy-1",
            user_id="user-1",
            title="旧项目",
            orchestration_mode="frontend_v2",
            orchestration_version=1,
            revision=1,
            context={
                "agent_runtime": {
                    "mode": "primary",
                    "enabled_intents": ["video"],
                    "primary_execution_ready": False,
                    "context_version": 0,
                },
                "__agent_runtime": {
                    "mode": "primary",
                    "enabled_intents": ["video"],
                    "primary_execution_ready": False,
                    "context_version": 0,
                },
                "scriptContent": "# 旧脚本\n镜头一",
                "artifact_refs": ["artifact:old-1"],
            },
        )
    )

    upgrader = FrontendV2LegacyUpgrader(task_store=store, video_repository=repo)
    first = await upgrader.upgrade_if_needed(
        user_id="user-1",
        conversation=conversation,
        now=now,
    )
    assert first.upgraded is True
    assert first.orchestration_mode == "video_agent_v2"
    assert first.workspace.payload["script"]["content"].startswith("# 旧脚本")

    refreshed = await store.get_conversation(
        conversation.conversation_id,
        user_id="user-1",
    )
    assert refreshed is not None
    assert refreshed.orchestration_mode == "video_agent_v2"
    runtime = refreshed.context["__agent_runtime"]
    assert runtime["primary_execution_ready"] is True
    assert "video" in runtime["enabled_intents"]
    assert runtime["legacy_upgraded_from"] == "frontend_v2"
    second = await upgrader.upgrade_if_needed(
        user_id="user-1",
        conversation=refreshed,
        now=now,
    )
    assert second.upgraded is False
    assert second.workspace.workspace_id == first.workspace.workspace_id


@pytest.mark.asyncio
async def test_legacy_upgrade_rolls_back_workspace_when_mode_switch_fails() -> None:
    now = datetime(2026, 8, 12, 14, tzinfo=UTC)
    store = MemoryPixelFlowTaskStore()
    repo = MemoryVideoAgentRepository()
    conversation = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conv-legacy-fail",
            user_id="user-1",
            title="失败升级",
            orchestration_mode="frontend_v2",
            revision=1,
            context={"scriptContent": "x"},
        )
    )
    store.update_conversation = AsyncMock(return_value=None)  # type: ignore[method-assign]
    upgrader = FrontendV2LegacyUpgrader(task_store=store, video_repository=repo)
    with pytest.raises(AgentRuntimeRecordConflictError):
        await upgrader.upgrade_if_needed(
            user_id="user-1",
            conversation=conversation,
            now=now,
        )
    leftover = await repo.get_workspace(
        "user-1",
        _workspace_id_for(conversation.conversation_id),
    )
    assert leftover is None
    still = await store.get_conversation(
        conversation.conversation_id,
        user_id="user-1",
    )
    assert still is not None
    assert still.orchestration_mode == "frontend_v2"


@pytest.mark.asyncio
async def test_legacy_upgrade_rejects_unknown_mode() -> None:
    now = datetime(2026, 8, 12, 14, tzinfo=UTC)
    store = MemoryPixelFlowTaskStore()
    repo = MemoryVideoAgentRepository()
    upgrader = FrontendV2LegacyUpgrader(task_store=store, video_repository=repo)
    with pytest.raises(AgentRuntimeRecordConflictError):
        await upgrader.upgrade_if_needed(
            user_id="user-1",
            conversation=SimpleNamespace(
                conversation_id="c1",
                orchestration_mode="supervisor_v1",
                revision=1,
                context={},
            ),
            now=now,
        )


@pytest.mark.asyncio
async def test_legacy_upgrade_sql_is_same_transaction() -> None:
    """Workspace 与 orchestration_mode 必须同一 SQL 事务提交。"""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base
    from pixelflow.tasks.store import SQLPixelFlowTaskStore
    from pixelflow.video_agent.workspace.repository import SQLVideoAgentRepository

    now = datetime(2026, 8, 12, 14, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        store = SQLPixelFlowTaskStore(session_factory)
        repo = SQLVideoAgentRepository(session_factory)
        conversation = await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="conv-sql-upgrade",
                user_id="user-1",
                title="SQL升级",
                orchestration_mode="frontend_v2",
                orchestration_version=1,
                revision=1,
                context={
                    "scriptContent": "# SQL 脚本",
                    "__agent_runtime": {
                        "mode": "primary",
                        "enabled_intents": ["video"],
                        "primary_execution_ready": False,
                        "context_version": 0,
                    },
                },
            )
        )
        upgrader = FrontendV2LegacyUpgrader(task_store=store, video_repository=repo)
        result = await upgrader.upgrade_if_needed(
            user_id="user-1",
            conversation=conversation,
            now=now,
        )
        assert result.upgraded is True
        refreshed = await store.get_conversation(
            "conv-sql-upgrade",
            user_id="user-1",
        )
        assert refreshed is not None
        assert refreshed.orchestration_mode == "video_agent_v2"
        runtime = refreshed.context.get("__agent_runtime") or {}
        assert runtime.get("legacy_upgraded_from") == "frontend_v2"
        assert runtime.get("primary_execution_ready") is True
        workspace = await repo.get_workspace(
            "user-1",
            _workspace_id_for("conv-sql-upgrade"),
        )
        assert workspace is not None
        assert workspace.payload.get("script", {}).get("content") == "# SQL 脚本"
    finally:
        await engine.dispose()
