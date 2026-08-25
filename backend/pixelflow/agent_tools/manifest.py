"""生成 Gateway 与 Harness 共用的 Capability Tool Manifest。"""

import hashlib
import json
from typing import Any

from .contracts import ToolManifestResponse
from .video import (
    InspectSceneTool,
    InspectVideoWorkspaceTool,
    PatchSceneTool,
    ReplaceSceneAssetTool,
    VideoToolRegistry,
)

MANIFEST_VERSION = "agent-tools-v1"


def runtime_video_tool_registry() -> VideoToolRegistry:
    """构造可由 Harness 直接调用的非计费视频 Tool 集合。

    仅注册不需要用户 Authorization、不会调用外部 Provider 的领域能力。付费生成、
    素材删除等需要显式确认的能力仍由后续 Operation Tool 单独接入，不能借由
    Sidecar 直接绕过确认边界。
    """

    return VideoToolRegistry(
        (
            InspectVideoWorkspaceTool(),
            InspectSceneTool(),
            PatchSceneTool(),
            ReplaceSceneAssetTool(),
        )
    )


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
