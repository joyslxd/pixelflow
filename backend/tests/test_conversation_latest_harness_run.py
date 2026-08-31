"""验证刷新会话时优先恢复确认、授权等非用户输入触发的 Harness Run。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.gateway.routers.pixelflow_conversations import _conversation_detail
from pixelflow.agent_control_plane.contracts.enums import AgentEventType
from pixelflow.agent_control_plane.contracts.events import AgentEvent
from pixelflow.agent_control_plane.persistence import MemoryAgentRuntimeRepository
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
)


@pytest.mark.asyncio
async def test_conversation_detail_prefers_latest_outbox_run_over_last_user_message() -> None:
    """授权恢复 Run 没有用户消息时，刷新仍须回到该 Run 以显示其公开中断。"""

    store = MemoryPixelFlowTaskStore()
    events = MemoryAgentRuntimeRepository()
    await store.create_conversation(
        PixelFlowConversationRecord(conversation_id="conversation-1", user_id="user-1", title="恢复测试"),
    )
    await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="message-1",
            conversation_id="conversation-1",
            user_id="user-1",
            role="user",
            content="开始生成视频",
            payload={"harness_run_id": "hrun_" + "1" * 32},
        ),
    )
    await events.create_event(
        "user-1",
        AgentEvent(
            event_id="event-1",
            sequence=1,
            cursor="event-1",
            conversation_id="conversation-1",
            run_id="hrun_" + "2" * 32,
            occurred_at=datetime.now(UTC),
            type=AgentEventType.RUN_STATE_CHANGED,
            payload={"status": "suspended_authorization", "interrupt_id": "hint-1"},
        ),
    )

    detail = await _conversation_detail(
        store,
        "conversation-1",
        user_id="user-1",
        runtime_events=events,
    )

    assert detail.latest_harness_run_id == "hrun_" + "2" * 32
