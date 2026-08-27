"""实现 Harness 到 PixelFlow 视频 Capability Tool 的受控 Broker。"""

from __future__ import annotations

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.registry import VideoToolRegistry
from pixelflow.video.services.tool_executor import VideoToolExecutor

from .catalog import runtime_video_tool_registry
from .contracts import ToolCallRequest, ToolCallResponse
from .manifest import manifest
from .repository import (
    AgentToolBindingConflictError,
    SQLAgentToolRepository,
    ToolCallClaimState,
)

_OBSERVATION_MAX_BYTES = 8_192
_OBSERVATION_FORBIDDEN = ("authorization", "credential", "secret", "token", "password", "api_key", "provider")


class AgentToolBroker:
    """按 Run binding 调用真实领域 Tool；不信任 Sidecar 传入 owner 或工作区。"""

    def __init__(
        self,
        repository: SQLAgentToolRepository,
        video_repository: object,
        *,
        video_tools: VideoToolRegistry | None = None,
    ) -> None:
        self._repository = repository
        self._video_repository = video_repository
        self._video_tools = video_tools or runtime_video_tool_registry(
            plan_repository=video_repository,
        )
        self._executor = VideoToolExecutor(
            repository=video_repository,  # type: ignore[arg-type]
            registry=self._video_tools,
        )

    async def call(
        self,
        request: ToolCallRequest,
        *,
        idempotency_key: str,
    ) -> ToolCallResponse:
        """校验 binding、manifest、revision 和 DTO 后执行冻结领域 Tool。"""

        binding = await self._repository.get_run_binding(request.run_id)
        if binding is None:
            return self._rejected("当前 Run 未在 Gateway 注册")
        tool_call_key = self._repository.tool_call_key(
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
        )
        if idempotency_key != tool_call_key:
            raise AgentToolBindingConflictError("Tool Call Idempotency-Key 与稳定身份不一致")
        if (
            request.session_id != binding.session_id
            or request.context_digest != binding.context_digest
            or request.toolset_version != binding.toolset_version
        ):
            return self._rejected("Tool Call 与冻结 Run binding 不一致")
        current_manifest = manifest()
        if binding.tool_manifest_digest != current_manifest.digest:
            return self._rejected("Tool Manifest 与冻结 Run 不一致")
        if request.expected_workspace_revision != binding.workspace_revision:
            return self._rejected("工作区 revision 与冻结 Run 不一致")
        digest = self._repository.request_digest(
            binding=binding,
            tool_name=request.tool_name,
            arguments=request.arguments,
            expected_revision=request.expected_workspace_revision,
        )
        claim = await self._repository.claim_tool_call(
            tool_call_key=tool_call_key,
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
            request_digest=digest,
        )
        if claim.state is ToolCallClaimState.COMPLETED:
            assert claim.response is not None
            return ToolCallResponse.model_validate(claim.response)
        if claim.state is ToolCallClaimState.EXECUTING:
            return ToolCallResponse(
                protocol_version="v1",
                status="failed",
                public_summary="该 Tool 调用正在执行，请使用同一调用标识重试",
                model_observation={"code": "tool_call_in_progress"},
            )
        tool = self._video_tools.resolve(request.tool_name)
        if tool is None:
            return self._rejected("当前 Run 未授权该 Tool")
        if tool.spec.confirmation_required or tool.spec.cost_level.value not in {"none"}:
            return self._rejected("该 Tool 必须经确认或 Operation 边界调用")
        workspace = await self._video_repository.get_workspace(
            binding.user_id,
            binding.workspace_id,
        )
        if workspace is None or workspace.conversation_id != binding.conversation_id:
            return self._rejected("工作区不存在或不属于当前 Run")
        if workspace.revision != request.expected_workspace_revision:
            return self._rejected("工作区 revision 已变化")
        try:
            result = await self._executor.execute_tool_call(
                context=VideoToolContext(user_id=binding.user_id, workspace=workspace),
                tool_name=request.tool_name,
                arguments=request.arguments,
            )
            current_workspace = await self._video_repository.get_workspace(
                binding.user_id,
                binding.workspace_id,
            )
            workspace_revision = (
                current_workspace.revision if current_workspace is not None else workspace.revision
            )
            observation = _safe_model_observation(
                result.model_observation,
                allowed_keys=tool.spec.model_observation_keys,
            )
            response = ToolCallResponse(
                protocol_version="v1",
                status="completed",
                public_summary=result.public_summary,
                model_observation={
                    "code": "video_tool_completed",
                    "tool_name": result.tool_name,
                    "workspace_revision": workspace_revision,
                    "artifact_refs": list(result.artifact_refs),
                    **observation,
                },
            )
        except Exception:  # noqa: BLE001 - Tool 失败必须写入同一幂等结果，禁止再次执行业务副作用。
            response = ToolCallResponse(
                protocol_version="v1",
                status="failed",
                public_summary="该 Tool 调用未完成，请基于当前工作区继续",
                model_observation={"code": "tool_call_failed"},
            )
        persisted = await self._repository.complete_tool_call(
            tool_call_key=tool_call_key,
            request_digest=digest,
            response=response.model_dump(mode="json"),
        )
        return ToolCallResponse.model_validate(persisted)

    @staticmethod
    def _rejected(summary: str) -> ToolCallResponse:
        """返回固定安全拒绝，不回显数据库、Provider 或异常细节。"""

        return ToolCallResponse(
            protocol_version="v1",
            status="rejected",
            public_summary=summary,
            model_observation={"code": "tool_call_rejected"},
        )


__all__ = ["AgentToolBindingConflictError", "AgentToolBroker"]


def _safe_model_observation(
    value: object,
    *,
    allowed_keys: tuple[str, ...],
) -> dict[str, object]:
    """强制 Tool Response DTO 的字段白名单预算，禁止模型看到敏感或无限结果。"""

    import json

    if not isinstance(value, dict):
        raise ValueError("Tool observation 必须是对象")
    unexpected = set(value) - set(allowed_keys)
    if unexpected:
        raise ValueError("Tool observation 包含未声明字段")
    for key in value:
        normalized = str(key).casefold()
        if any(fragment in normalized for fragment in _OBSERVATION_FORBIDDEN):
            raise ValueError("Tool observation 包含敏感字段")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > _OBSERVATION_MAX_BYTES:
        raise ValueError("Tool observation 超过预算")
    return dict(value)
