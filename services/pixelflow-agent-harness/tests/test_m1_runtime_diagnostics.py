"""验证 Harness Runtime 故障只投影安全诊断字段。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pixelflow_harness_sidecar.contracts import HarnessRunRequest
from pixelflow_harness_sidecar.deepseek_engine import (
    HarnessExecutionDiagnostic,
    HarnessExecutionError,
)
from pixelflow_harness_sidecar.event_store import SqliteRunEventStore
from pixelflow_harness_sidecar.run_service import RunService
from pixelflow_harness_sidecar.skill_snapshot import snapshot_skill_root


def _request() -> HarnessRunRequest:
    """构造不包含真实用户内容的固定 Run 请求。"""

    return HarnessRunRequest.model_validate(
        {
            "protocol_version": "v1",
            "run_request_key": "sha256:m1-runtime-diagnostic-run",
            "request_digest": "sha256:m1-runtime-diagnostic-request",
            "session_id": "pfh_m1_runtime_diagnostic",
            "trigger": {"type": "user_turn", "trigger_id": "m1-runtime-diagnostic-turn"},
            "binding": {
                "conversation_ref": "opaque:m1-runtime-diagnostic-conversation",
                "workspace_ref": "opaque:m1-runtime-diagnostic-workspace",
                "workspace_revision": 1,
                "context_digest": "sha256:m1-runtime-diagnostic-context",
            },
            "model": {
                "profile_name": "m1-runtime-diagnostic-model",
                "profile_digest": "sha256:m1-runtime-diagnostic-model",
                "max_output_tokens": 32,
            },
            "context_budget": {
                "effective_context_k": 896,
                "output_reserve_k": 32,
                "safety_reserve_k": 32,
                "require_verified_model_profile": True,
                "policy_digest": "sha256:m1-runtime-diagnostic-budget",
            },
            "limits": {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 90},
            "toolset": {"version": "agent-tools-v1", "manifest_digest": "sha256:m1-runtime-diagnostic-tools"},
            "context": {
                "system_instruction": "执行受控测试。",
                "user_input": "验证安全失败投影。",
                "workspace_projection": {},
                "conversation_projection": {},
                "preference_projection": {},
                "brand_profile_projection": {},
                "long_term_memory_projection": [],
            },
        }
    )


class _TimeoutEngine:
    """构造模型执行超时，不依赖真实模型或 Runtime 子进程。"""

    engine_id = "m1-runtime-diagnostic-engine"
    engine_version = "v1"

    def __init__(self, skill_root: Path) -> None:
        skill_file = skill_root / "m1-diagnostic" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\ndescription: M1 安全诊断测试。\n---\n测试正文", encoding="utf-8")
        self._snapshot = snapshot_skill_root(skill_root)

    def snapshot_skills(self):
        """返回固定 Skill 快照。"""

        return self._snapshot

    async def execute(self, *_args):
        """抛出不包含底层错误正文的结构化超时。"""

        await asyncio.sleep(0)
        raise HarnessExecutionError(
            HarnessExecutionDiagnostic(
                exception_type="TimeoutError",
                timeout_phase="model_execution",
            )
        )


@pytest.mark.asyncio
async def test_runtime_timeout_only_projects_safe_diagnostic_fields(tmp_path: Path) -> None:
    """SSE 失败事件必须保留类型与阶段，同时不包含底层异常文本。"""

    service = RunService(
        SqliteRunEventStore(tmp_path / "runs.sqlite3"),
        _TimeoutEngine(tmp_path / "skills"),
    )
    try:
        created = await service.create_run(_request())
        await service.activate_run(created.run_id)
        for _ in range(20):
            state = await service.get_run(created.run_id)
            if state is not None and state.status.value == "failed":
                break
            await asyncio.sleep(0)
        events = await service.events_after(created.run_id, 0)

        assert events[-1].type == "run.failed"
        assert events[-1].payload == {
            "code": "engine_execution_failed",
            "exception_type": "TimeoutError",
            "timeout_phase": "model_execution",
        }
        assert "底层" not in str(events[-1].payload)
    finally:
        await service.aclose()
