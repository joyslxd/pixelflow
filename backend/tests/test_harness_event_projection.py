"""验证 M2 Sidecar 终态能够安全投影为 Gateway 公共事件。"""

from __future__ import annotations

from pixelflow.agent_control_plane.contracts import AgentEventType
from pixelflow.agent_harness.contracts import HarnessRunEvent
from pixelflow.agent_harness.projector import HarnessRunProjector


def test_cancelled_sidecar_run_is_a_terminal_public_state() -> None:
    """取消事件不得落为未知类型，也不得遗留投影协程继续消费。"""

    source = HarnessRunEvent(
        run_id="hrun_0123456789abcdef0123456789abcdef",
        event_id="hevt_0123456789abcdef0123456789abcdef",
        sequence=3,
        type="run.cancelled",
        occurred_at="2026-08-25T00:00:00Z",
        payload={"status": "cancelled"},
    )

    event_type, payload = HarnessRunProjector._public_event(source)

    assert event_type is AgentEventType.RUN_STATE_CHANGED
    assert payload == {"status": "cancelled"}


def test_public_summary_and_response_delta_are_projected_without_reasoning() -> None:
    """公开摘要和回复增量各自独立投影，reasoning 不能通过事件白名单。"""

    summary = HarnessRunEvent(
        run_id="hrun_0123456789abcdef0123456789abcdef",
        event_id="hevt_1123456789abcdef0123456789abcdef",
        sequence=4,
        type="public_summary.delta",
        occurred_at="2026-08-27T00:00:00Z",
        payload={"delta": "正在完成工具：inspect_scene"},
    )
    response = HarnessRunEvent(
        run_id="hrun_0123456789abcdef0123456789abcdef",
        event_id="hevt_2123456789abcdef0123456789abcdef",
        sequence=5,
        type="response.delta",
        occurred_at="2026-08-27T00:00:01Z",
        payload={"delta": "镜头已检查。"},
    )

    assert HarnessRunProjector._public_event(summary) == (
        AgentEventType.AGENT_THINKING_DELTA,
        {"delta": "正在完成工具：inspect_scene"},
    )
    assert HarnessRunProjector._public_event(response) == (
        AgentEventType.AGENT_RESPONSE_DELTA,
        {"delta": "镜头已检查。"},
    )
