"""生成 Gateway 与 Harness 共用的 Capability Tool Manifest。"""

import hashlib
import json
from typing import Any

from .catalog import runtime_video_tool_registry
from .contracts import ToolManifestResponse

MANIFEST_VERSION = "agent-tools-v1"


def manifest() -> ToolManifestResponse:
    """返回当前可运行 Tool 的完整可审计 Manifest。"""

    tools: list[dict[str, Any]] = [
        {
            "name": spec.name,
            "description": spec.description,
            "parameters_schema": spec.input_schema,
            "cost_level": spec.cost_level.value,
            "confirmation_required": spec.confirmation_required,
            "idempotency_mode": spec.idempotency_mode.value,
            "recovery_mode": spec.recovery_mode.value,
            "workspace_mutation_roots": list(spec.workspace_mutations),
        }
        for spec in runtime_video_tool_registry().specs()
    ]
    digest = "sha256:" + hashlib.sha256(
        json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    return ToolManifestResponse(protocol_version="v1", version=MANIFEST_VERSION, digest=digest, tools=tools)
