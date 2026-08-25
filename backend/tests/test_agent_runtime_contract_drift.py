"""验证浏览器 TypeScript 与 Python 权威运行时合同的字段不漂移。"""

from __future__ import annotations

from pathlib import Path

from pixelflow.agent_control_plane.contracts import (
    InterruptResponseRequest,
    TurnStartRequest,
    WorkspaceCommandRequest,
)
from pixelflow.agent_control_plane.public_contracts import AgentSnapshotV1, PublicAgentEventV1


def _typescript_contract() -> str:
    """读取唯一浏览器公开类型文件，不执行前端构建或 Sidecar 代码。"""

    return (Path(__file__).parents[2] / "web/src/api/contracts.ts").read_text(encoding="utf-8")


def _require_fields(source: str, type_name: str, fields: set[str]) -> None:
    """确认 TypeScript 合同包含 Python DTO 的稳定字段名。"""

    marker = f"export type {type_name}"
    start = source.index(marker)
    block = source[start : source.index("};", start)]
    for field in fields:
        assert f"{field}:" in block, f"{type_name} 缺少字段 {field}"


def test_runtime_contract_fields_do_not_drift_between_python_and_typescript() -> None:
    """Turn、Interrupt、Workspace、Snapshot 与 Event 必须以同一字段集合对外发布。"""

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
    assert {"client_input_id", "content"}.issubset(TurnStartRequest.model_fields)
    assert {"client_response_id", "value"}.issubset(InterruptResponseRequest.model_fields)
