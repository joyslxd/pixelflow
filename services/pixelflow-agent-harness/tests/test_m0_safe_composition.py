"""验证 M0 安全 Composition 不声明被禁止的通用能力。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _m0_tool_manifest() -> dict[str, object]:
    """提供只用于 Runtime 启动测试的最小冻结 Manifest，不实际调用 Broker。"""

    return {
        "protocol_version": "v1",
        "version": "agent-tools-v1",
        "digest": "sha256:" + "0" * 64,
        "tools": [],
    }


def test_m0_safe_composition_excludes_forbidden_capabilities() -> None:
    """Composition 只能包含 M0 允许的官方插件和显式安全开关。"""

    config = (
        Path(__file__).parents[1] / "engines/deepseek/cordis/m0-safe.cordis.yml"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "dsh-bash-local",
        "dsh-subprocess-local",
        "dsh-tool-fs",
        "dsh-tool-web",
        "dsh-mcp",
        "dsh-tool-subagent",
        "dsh-tool-todo",
    ):
        assert forbidden not in config
    assert "toolBash: false" in config
    assert "toolJobs: false" in config
    assert "goals: false" in config
    assert "includeDefaultRoots: false" in config


def test_capability_plugin_does_not_require_untracked_node_modules_at_runtime() -> None:
    """Capability Plugin 必须仅使用官方 Runtime 已注入的 Tool Registry。"""

    plugin = (
        Path(__file__).parents[1]
        / "engines/deepseek/packages/dsh-plugin-capability-tools/dist/index.js"
    ).read_text(encoding="utf-8")

    assert 'from "@deepseek-ai/dsh-tools"' not in plugin
    assert "ctx.tools.register({" in plugin


@pytest.mark.m0_runtime
def test_fixed_python_sdk_starts_safe_jsonrpc_runtime(tmp_path: Path) -> None:
    """固定 SDK 必须能以不含 Bash 的 Composition 启动 JSON-RPC Runtime。"""

    deepseek_harness = pytest.importorskip("deepseek_harness")
    root = tmp_path / "agent-home"
    root.mkdir()
    skill_file = root / "skills" / "m0-probe-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: m0-probe-skill\ndescription: M0 隔离 Runtime 探针\n---\nM0 隔离 Skill 正文",
        encoding="utf-8",
    )
    host_skill_file = root / "host-skill" / "SKILL.md"
    host_skill_file.parent.mkdir()
    host_skill_file.write_text(
        "---\nname: host-skill\ndescription: 不应发现的宿主 Skill\n---\n宿主正文",
        encoding="utf-8",
    )
    cordis = Path(__file__).parents[1] / "engines/deepseek/cordis/m0-safe.cordis.yml"
    client = deepseek_harness.HarnessClient(
        deepseek_harness.HarnessConfig(
            cwd=str(root),
            env={
                "DSH_HOME": str(root),
                "DSH_AGENTS_HOME": str(root / "agents-home"),
                "DSH_SESSION_ROOT": str(root / "sessions"),
                "DSH_CORDIS_CONFIG": str(cordis),
                "PIXELFLOW_HARNESS_TOOLSET_VERSION": "agent-tools-v1",
                "PIXELFLOW_HARNESS_TOOL_MANIFEST_JSON": json.dumps(_m0_tool_manifest()),
            },
            request_timeout_seconds=15,
        )
    )
    try:
        client.start()
        initialized = client.initialize(
            cwd=str(root),
            provider="deepseek-official",
            model="deepseek-v4-flash",
            max_tokens=32,
        )
    finally:
        client.close()

    assert initialized.serverInfo is not None
    assert initialized.serverInfo.name == "deepseek-harness-sdk-runtime"


@pytest.mark.m0_real
def test_fixed_sdk_performs_real_ark_deepseek_turn(tmp_path: Path) -> None:
    """经官方安全 Composition 执行真实 Ark DeepSeek 最小模型 Turn。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_M0") != "1":
        pytest.skip("未显式开启会消耗测试 token 的真实 M0 用例")
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("缺少仅用于测试的 DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL")
    if not base_url:
        pytest.skip("缺少 Ark OpenAI 兼容端点 DEEPSEEK_BASE_URL")

    deepseek_harness = pytest.importorskip("deepseek_harness")
    root = tmp_path / "agent-home"
    skill_file = root / "skills" / "m0-probe-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: m0-probe-skill\ndescription: 仅用于验证 Harness 隔离 Skill 发现。\n---\nM0 隔离 Skill 正文",
        encoding="utf-8",
    )
    cordis = Path(__file__).parents[1] / "engines/deepseek/cordis/m0-safe.cordis.yml"
    harness = deepseek_harness.DeepSeekHarness(
        deepseek_harness.DeepSeekHarnessConfig(
            provider="deepseek-official",
            model=os.environ.get("PIXELFLOW_M0_DEEPSEEK_MODEL", "deepseek-v4-pro-ga-260813"),
            max_tokens=64,
            cwd=str(root),
            session_root=str(root / "sessions"),
            cordis=str(cordis),
            env={
                "DSH_HOME": str(root),
                "DSH_AGENTS_HOME": str(root / "agents-home"),
                "DSH_SESSION_ROOT": str(root / "sessions"),
                "DEEPSEEK_API_KEY": api_key,
                "DEEPSEEK_BASE_URL": base_url,
                "PIXELFLOW_HARNESS_TOOLSET_VERSION": "agent-tools-v1",
                "PIXELFLOW_HARNESS_TOOL_MANIFEST_JSON": json.dumps(_m0_tool_manifest()),
            },
            request_timeout_seconds=90,
        )
    )
    try:
        result = harness.run("请用不超过六个汉字说明你已连通。", session_id="m0-real-ark")
    finally:
        harness.close()

    assert result.finish_reason == "completed"
    assert result.final_response.strip()
    event_sequences = [event.get("seq") for event in result.events]
    assert event_sequences
    assert all(isinstance(sequence, int) for sequence in event_sequences)
    assert event_sequences == sorted(event_sequences)
    assert len(event_sequences) == len(set(event_sequences))
