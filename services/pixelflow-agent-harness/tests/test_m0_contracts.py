"""验证 M0 Sidecar 稳定合同不依赖真实 Harness 或 Provider。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pixelflow_harness_sidecar.contracts import HarnessRunRequest
from pixelflow_harness_sidecar.engine import FakeAgentEngine
from pixelflow_harness_sidecar.fake_tools import InspectVideoWorkspaceInput, inspect_video_workspace
from pixelflow_harness_sidecar.skill_snapshot import snapshot_skill_root


def _request(*, request_digest: str = "sha256:request") -> HarnessRunRequest:
    """构造不含敏感数据的最小合法 Run 请求。"""

    return HarnessRunRequest.model_validate(
        {
            "protocol_version": "v1",
            "run_request_key": "sha256:stable-run",
            "request_digest": request_digest,
            "session_id": "pfh_m0",
            "trigger": {"type": "user_turn", "trigger_id": "turn-m0"},
            "binding": {
                "conversation_ref": "opaque:conversation",
                "workspace_ref": "opaque:workspace",
                "workspace_revision": 0,
                "context_digest": "sha256:context",
            },
            "model": {
                "profile_name": "fake-model",
                "profile_digest": "sha256:model",
                "max_output_tokens": 32,
            },
            "context_budget": {
                "effective_context_k": 896,
                "output_reserve_k": 32,
                "safety_reserve_k": 32,
                "require_verified_model_profile": True,
                "policy_digest": "sha256:budget",
            },
            "limits": {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 600},
            "toolset": {"version": "agent-tools-v1", "manifest_digest": "sha256:manifest"},
            "context": {
                "system_instruction": "安全指令",
                "user_input": "测试输入",
                "workspace_projection": {},
                "conversation_projection": {},
                "preference_projection": {},
                "brand_profile_projection": {},
                "long_term_memory_projection": [],
            },
        }
    )


@pytest.mark.asyncio
async def test_fake_engine_reuses_same_identity_and_rejects_digest_drift() -> None:
    """同一稳定 Run 身份只能回读或报告请求摘要冲突。"""

    engine = FakeAgentEngine()
    first = await engine.create_run(_request())
    second = await engine.create_run(_request())

    assert first.run_id == second.run_id
    with pytest.raises(ValueError, match="请求摘要不一致"):
        await engine.create_run(_request(request_digest="sha256:changed"))


@pytest.mark.asyncio
async def test_fake_engine_replays_events_after_sequence() -> None:
    """事件序列可以按游标断点读取且不重复返回旧事件。"""

    engine = FakeAgentEngine()
    handle = await engine.create_run(_request())

    events = [event async for event in engine.stream_events(handle.run_id, after_sequence=0)]
    replay = [event async for event in engine.stream_events(handle.run_id, after_sequence=1)]

    assert [event.sequence for event in events] == [1]
    assert replay == []


def test_skill_snapshot_isolated_from_later_file_change(tmp_path: Path) -> None:
    """运行中的快照保持首次读取正文，新快照才可见后续修改。"""

    skill_file = tmp_path / "video-generation" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("---\ndescription: 初始规则\n---\n初始正文", encoding="utf-8")

    first = snapshot_skill_root(tmp_path)
    skill_file.write_text("---\ndescription: 新规则\n---\n新正文", encoding="utf-8")
    second = snapshot_skill_root(tmp_path)

    assert first.load("video-generation").body.endswith("初始正文")
    assert second.load("video-generation").body.endswith("新正文")
    assert first.catalog_digest != second.catalog_digest


def test_skill_snapshot_rejects_invalid_name_and_missing_description(tmp_path: Path) -> None:
    """非法目录或不完整 frontmatter 必须失败关闭。"""

    invalid_file = tmp_path / "Video_Generation" / "SKILL.md"
    invalid_file.parent.mkdir()
    invalid_file.write_text("---\ndescription: 测试\n---\n正文", encoding="utf-8")
    with pytest.raises(ValueError, match="kebab-case"):
        snapshot_skill_root(tmp_path)


def test_fake_tool_is_strict_and_returns_safe_observation() -> None:
    """只读 Fake Tool 不接受隐藏字段，也不返回敏感信息。"""

    result = inspect_video_workspace(InspectVideoWorkspaceInput(workspace_ref="opaque:workspace"))

    assert result.code == "workspace_inspected"
    assert result.workspace_revision == 0
    with pytest.raises(ValidationError):
        InspectVideoWorkspaceInput.model_validate(
            {"workspace_ref": "opaque:workspace", "authorization": "forbidden"}
        )


def test_sidecar_compose_gateway_endpoint_is_limited_to_fixed_service_name(monkeypatch) -> None:
    """Docker Compose 内网只允许固定 Gateway 服务名，任意明文地址仍不能通过。"""

    from pixelflow_harness_sidecar.config import SidecarSettings

    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_BASE_URL", "http://gateway:8001")
    settings = SidecarSettings.from_env()
    assert settings._tool_broker_url_is_safe
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_BASE_URL", "http://untrusted-host:8001")
    assert not SidecarSettings.from_env()._tool_broker_url_is_safe


def test_sidecar_rejects_limit_profile_or_digest_drift(monkeypatch) -> None:
    """Sidecar 只能接受与本地配置逐字段、逐摘要一致的 Gateway Run limits。"""

    from pixelflow_harness_sidecar.config import SidecarSettings
    from pixelflow_harness_sidecar.contracts import RunLimits

    profiles = {
        "video_interactive_v1": {
            "deadline_seconds": 180,
            "max_model_steps": 12,
            "max_business_tools": 6,
            "max_billable_batch_starts": 1,
        }
    }
    monkeypatch.setenv("PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES", json.dumps(profiles))
    expected = {"profile": "video_interactive_v1", **profiles["video_interactive_v1"]}
    digest = "sha256:" + hashlib.sha256(
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    settings = SidecarSettings.from_env()
    settings.validate_run_limits(RunLimits(digest=digest, **expected))
    with pytest.raises(ValueError, match="限制"):
        settings.validate_run_limits(RunLimits(digest=digest, **{**expected, "max_business_tools": 5}))
