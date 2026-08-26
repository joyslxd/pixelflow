"""验证 M2 Run 取消只停止 Harness 模型循环且可从 Event Store 审计。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pixelflow_harness_sidecar.contracts import HarnessRunRequest, RunStatus, TerminationReason
from pixelflow_harness_sidecar.deepseek_engine import DeepSeekEngineResult
from pixelflow_harness_sidecar.event_store import SqliteRunEventStore
from pixelflow_harness_sidecar.run_service import RunService
from pixelflow_harness_sidecar.skill_snapshot import snapshot_skill_root


def _request() -> HarnessRunRequest:
    """构造不含用户正文和凭据的稳定请求。"""

    return HarnessRunRequest.model_validate(
        {
            "protocol_version": "v1",
            "run_request_key": "sha256:m2-cancel-run",
            "request_digest": "sha256:m2-cancel-request",
            "session_id": "pfh_m2_cancel",
            "trigger": {"type": "user_turn", "trigger_id": "m2-cancel-turn"},
            "binding": {
                "conversation_ref": "opaque:m2-conversation",
                "workspace_ref": "opaque:m2-workspace",
                "workspace_revision": 1,
                "context_digest": "sha256:m2-context",
            },
            "model": {
                "profile_name": "m2-test-model",
                "profile_digest": "sha256:m2-model",
                "max_output_tokens": 32,
            },
            "context_budget": {
                "effective_context_k": 896,
                "output_reserve_k": 32,
                "safety_reserve_k": 32,
                "require_verified_model_profile": True,
                "policy_digest": "sha256:m2-budget",
            },
            "limits": {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 90},
            "toolset": {"version": "agent-tools-v1", "manifest_digest": "sha256:m2-manifest"},
            "context": {
                "system_instruction": "执行受控测试。",
                "user_input": "测试取消。",
                "workspace_projection": {},
                "conversation_projection": {},
                "preference_projection": {},
                "brand_profile_projection": {},
                "long_term_memory_projection": [],
            },
        }
    )


class _BlockingEngine:
    """仅在测试目录使用的受控 Engine，用于精确制造取消竞争窗口。"""

    engine_id = "m2-test-engine"
    engine_version = "v1"

    def __init__(self, skill_root: Path) -> None:
        self._snapshot = snapshot_skill_root(skill_root)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def snapshot_skills(self):
        """返回固定测试 Skill 快照。"""

        return self._snapshot

    async def execute(self, *_args):
        """阻塞到测试显式释放，避免以 sleep 猜测执行时序。"""

        self.started.set()
        await self.release.wait()
        return DeepSeekEngineResult(
            final_response="不会在取消后公开",
            finish_reason="completed",
            tool_names=(),
        )


def _engine(tmp_path: Path) -> _BlockingEngine:
    """写入最小有效 Skill，供真实 Sidecar 快照逻辑复用。"""

    skill_file = tmp_path / "skills" / "m2-cancel" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\ndescription: M2 取消合同测试。\n---\n测试正文", encoding="utf-8")
    return _BlockingEngine(tmp_path / "skills")


@pytest.mark.asyncio
async def test_cancel_accepted_run_prevents_later_activation(tmp_path: Path) -> None:
    """取消已接受 Run 后，重复 activate 不得重新启动模型。"""

    engine = _engine(tmp_path)
    service = RunService(SqliteRunEventStore(tmp_path / "runs.sqlite3"), engine)  # type: ignore[arg-type]
    try:
        created = await service.create_run(_request())
        cancelled = await service.cancel_run(created.run_id)
        replay = await service.cancel_run(created.run_id)
        activated = await service.activate_run(created.run_id)

        assert cancelled is not None
        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.termination_reason is TerminationReason.CANCELLED
        assert replay == cancelled
        assert activated == cancelled
        assert not engine.started.is_set()
        events = await service.events_after(created.run_id, 0)
        assert [event.type for event in events] == ["run.accepted", "run.cancelled"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cancel_running_run_persists_one_terminal_event(tmp_path: Path) -> None:
    """执行中的模型循环取消后只写一次取消终态，且不会公开最终回复。"""

    engine = _engine(tmp_path)
    service = RunService(SqliteRunEventStore(tmp_path / "runs.sqlite3"), engine)  # type: ignore[arg-type]
    try:
        created = await service.create_run(_request())
        await service.activate_run(created.run_id)
        await asyncio.wait_for(engine.started.wait(), timeout=1)

        cancelled = await service.cancel_run(created.run_id)
        await asyncio.sleep(0)
        state = await service.get_run(created.run_id)
        events = await service.events_after(created.run_id, 0)

        assert cancelled is not None
        assert state == cancelled
        assert state.status is RunStatus.CANCELLED
        assert [event.type for event in events] == ["run.accepted", "run.started", "run.cancelled"]
        assert all(event.type != "response.completed" for event in events)
    finally:
        await service.aclose()
