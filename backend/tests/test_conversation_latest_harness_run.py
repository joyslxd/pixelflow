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
    assert detail.latest_harness_run_is_user_turn is False


@pytest.mark.asyncio
async def test_conversation_detail_marks_latest_user_turn_for_safe_recovery() -> None:
    """只有绑定同一用户消息的失败 Run 才能由前端展示通用恢复入口。"""

    store = MemoryPixelFlowTaskStore()
    events = MemoryAgentRuntimeRepository()
    run_id = "hrun_" + "3" * 32
    await store.create_conversation(
        PixelFlowConversationRecord(conversation_id="conversation-2", user_id="user-2", title="恢复测试"),
    )
    await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="message-2",
            conversation_id="conversation-2",
            user_id="user-2",
            role="user",
            content="继续处理",
            payload={"harness_run_id": run_id},
        ),
    )
    await events.create_event(
        "user-2",
        AgentEvent(
            event_id="event-2",
            sequence=1,
            cursor="event-2",
            conversation_id="conversation-2",
            run_id=run_id,
            occurred_at=datetime.now(UTC),
            type=AgentEventType.RUN_STATE_CHANGED,
            payload={"status": "failed", "code": "harness_run_recovery_required"},
        ),
    )

    detail = await _conversation_detail(store, "conversation-2", user_id="user-2", runtime_events=events)

    assert detail.latest_harness_run_is_user_turn is True
