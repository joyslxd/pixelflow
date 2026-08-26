"""验证 F0 公开 Snapshot/Event 合同与投影白名单。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pixelflow.agent_control_plane.contracts import AgentEvent, AgentEventType
from pixelflow.agent_control_plane.public_contracts import AgentSnapshotV1
from pixelflow.agent_harness.projector import HarnessRunProjector
from pixelflow.video.workspace import MemoryVideoAgentRepository, ensure_conversation_video_workspace

_FIXTURE = Path(__file__).parent / "fixtures" / "agent_runtime" / "harness-snapshot-v1.json"


def test_harness_snapshot_fixture_matches_public_contract() -> None:
    """共享 fixture 必须能被冻结的 AgentSnapshotV1 解析。"""

    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    snapshot = AgentSnapshotV1.model_validate(payload["snapshot"])
    assert snapshot.run_id.startswith("hrun_")
    assert snapshot.events[1].type is AgentEventType.AGENT_TOOL_COMPLETED
    assert snapshot.messages[0].role == "user"
    assert snapshot.conversation_id == "conv-f0-1"
    assert snapshot.last_cursor == "cursor-3"


def test_public_event_keeps_canonical_agent_event_type() -> None:
    """浏览器合同使用 AgentEventType 全名，不再缩短为 tool.completed。"""

    event = AgentEvent(
        event_id="hevt_public_1",
        sequence=1,
        cursor="cursor-1",
        conversation_id="conv-1",
        run_id="hrun_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        occurred_at=datetime(2026, 8, 26, tzinfo=UTC),
        type=AgentEventType.AGENT_TOOL_COMPLETED,
        payload={"tool_name": "inspect_video_workspace"},
    )
    public = HarnessRunProjector._to_public_event(event)
    assert public.type is AgentEventType.AGENT_TOOL_COMPLETED
    assert public.conversation_id == "conv-1"
    assert public.cursor == "cursor-1"


@pytest.mark.asyncio
async def test_ensure_conversation_video_workspace_is_idempotent() -> None:
    """同一会话重复打开只回读同一 Workspace，不创建第二份业务状态。"""

    repository = MemoryVideoAgentRepository()
    first = await ensure_conversation_video_workspace(
        repository,
        user_id="user-1",
        conversation_id="conv-ensure",
    )
    second = await ensure_conversation_video_workspace(
        repository,
        user_id="user-1",
        conversation_id="conv-ensure",
    )
    assert first.workspace_id == second.workspace_id
    assert first.revision == second.revision == 1
