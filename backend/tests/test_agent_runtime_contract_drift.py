"""验证浏览器 TypeScript 与 Python 权威运行时合同的字段不漂移。"""

from __future__ import annotations

import json
from pathlib import Path

from app.gateway.routers.pixelflow_conversations import HarnessTurnStartRequest
from pixelflow.agent_control_plane.contracts import (
    InterruptResponseRequest,
    WorkspaceCommandRequest,
)
from pixelflow.agent_control_plane.public_contracts import AgentSnapshotV1, PublicAgentEventV1


def _typescript_contract() -> str:
    """读取唯一浏览器公开类型文件，不执行前端构建或 Sidecar 代码。"""

    return (Path(__file__).parents[2] / "web/src/api/contracts.ts").read_text(encoding="utf-8")


def _harness_fixture() -> dict[str, object]:
    """读取唯一公开 Harness 跨端 fixture，禁止混入已删除 Runtime 的 DTO。"""

    path = Path(__file__).parent / "fixtures/agent_harness/contracts-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _require_fields(source: str, type_name: str, fields: set[str]) -> None:
    """确认 TypeScript 合同包含 Python DTO 的稳定字段名。"""

    marker = f"export type {type_name}"
    start = source.index(marker)
    block = source[start : source.index("};", start)]
    for field in fields:
        assert (
            f"{field}:" in block or f"{field}?:" in block
        ), f"{type_name} 缺少字段 {field}"


def test_harness_contract_fixture_matches_python_public_dtos() -> None:
    """冻结的 Turn、Interrupt、Workspace、Snapshot 与 Event 必须能被 Python 权威 DTO 接受。"""

    fixture = _harness_fixture()
    assert set(fixture) == {
        "schema_version",
        "turn_start",
        "interrupt_response",
        "workspace_command",
        "event",
        "snapshot",
    }
    assert fixture["schema_version"] == 1
    HarnessTurnStartRequest.model_validate(fixture["turn_start"])
    InterruptResponseRequest.model_validate(fixture["interrupt_response"])
    WorkspaceCommandRequest.model_validate(fixture["workspace_command"])
    PublicAgentEventV1.model_validate(fixture["event"])
    AgentSnapshotV1.model_validate(fixture["snapshot"])


def test_harness_contract_fields_do_not_drift_between_python_and_typescript() -> None:
    """浏览器类型必须覆盖当前公开 Harness DTO，旧 Runtime 合同不再参与门禁。"""

    source = _typescript_contract()
    _require_fields(
        source,
        "TurnStartV1",
        {"client_input_id", "workspace_id", "expected_workspace_revision", "content"},
    )
    _require_fields(source, "InterruptResponseV1", {"client_response_id", "value"})
    _require_fields(
        source,
        "WorkspaceCommandV1",
        set(WorkspaceCommandRequest.model_fields),
    )
    _require_fields(source, "PublicAgentEventV1", set(PublicAgentEventV1.model_fields))
    _require_fields(source, "AgentSnapshotV1", set(AgentSnapshotV1.model_fields))
    _require_fields(source, "TurnStartV1", set(HarnessTurnStartRequest.model_fields))
    _require_fields(source, "InterruptResponseV1", {"client_response_id", "value"})
    assert {"client_response_id", "value"}.issubset(InterruptResponseRequest.model_fields)
