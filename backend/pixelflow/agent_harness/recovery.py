"""从 Gateway 权威消息和 Workspace 创建新的 run_recovery，不续跑旧 Harness Session。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pixelflow.agent_tools.repository import HarnessRecoveryRecord, SQLAgentToolRepository
from pixelflow.tasks import PixelFlowTaskStore

from .contracts import HarnessRunRequest
from .limits import LimitProfileResolver
from .port import AgentHarnessPort


@dataclass(frozen=True, slots=True)
class HarnessRecoveryResult:
    """表示恢复已创建、已存在或因副作用边界不明而转人工核对。"""

    status: str
    recovery_event_id: str
    recovery_run_id: str | None


class HarnessRecoveryService:
    """类似 Application Service：只根据权威记录重建新 Run，不读取旧 Session/JSONL。"""

    def __init__(
        self,
        *,
        binding_repository: SQLAgentToolRepository,
        task_store: PixelFlowTaskStore,
        video_repository: object,
    ) -> None:
        self._bindings = binding_repository
        self._task_store = task_store
        self._video_repository = video_repository

    async def recover(
        self,
        *,
        bridge: AgentHarnessPort,
        user_id: str,
        conversation_id: str,
        original_run_id: str,
    ) -> HarnessRecoveryResult:
        """先写唯一恢复事件；无法证明安全输入时失败关闭到人工核对。"""

        binding = await bridge.get_owned_binding(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=original_run_id,
        )
        recovery = await self._bindings.get_or_create_recovery_event(original_run_id)
        if recovery.status == "created":
            return self._result(recovery)
        if recovery.status == "manual_review":
            return self._result(recovery)
        if await self._bindings.has_tool_calls(original_run_id):
            return self._result(
                await self._bindings.mark_recovery_manual_review(original_run_id),
            )
        message = await self._source_message(
            user_id=user_id,
            conversation_id=conversation_id,
            original_run_id=original_run_id,
        )
        workspace = await self._video_repository.get_workspace(user_id, binding.workspace_id)
        if message is None or workspace is None or workspace.conversation_id != conversation_id:
            return self._result(
                await self._bindings.mark_recovery_manual_review(original_run_id),
            )
        limits = LimitProfileResolver().resolve("run_recovery")
        request = HarnessRunRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            workspace_id=workspace.workspace_id,
            workspace_revision=workspace.revision,
            trigger_id=recovery.recovery_event_id,
            trigger_type="run_recovery",
            user_input=message.content,
            system_instruction=(
                "你是 PixelFlow 视频 Agent。这是一次在旧 Harness Run 中断后的安全恢复。"
                "只能依据当前权威工作区与已加载 Skill 作答；不得假设旧 Session 仍可用。"
            ),
            context_digest=_digest(
                {
                    "recovery_event_id": recovery.recovery_event_id,
                    "parent_run_id": original_run_id,
                    "workspace_id": workspace.workspace_id,
                    "workspace_revision": workspace.revision,
                },
            ),
            model_profile_digest=_digest({"profile": "deepseek-v4-pro"}),
            context_budget_digest=_digest(
                {"effective_context_k": 896, "output_reserve_k": 32, "safety_reserve_k": 32},
            ),
            run_limits_digest=limits.digest,
            limit_profile=limits.profile,
            max_model_steps=limits.max_model_steps,
            max_business_tools=limits.max_business_tools,
            max_billable_batch_starts=limits.max_billable_batch_starts,
            deadline_seconds=limits.deadline_seconds,
            # 用途：恢复时仍需让 Agent 完成完整规划/Tool 调度；影响：不再因 192 token
            # 预算再次提前结束，计费上限仍由 run_recovery_v1 限制为零。
            max_output_tokens=32_768,
        )
        run = await bridge.create_and_bind(request)
        bound = await self._bindings.bind_recovery_run(
            original_run_id=original_run_id,
            recovery_run_id=run.run_id,
        )
        return self._result(bound)

    async def _source_message(
        self,
        *,
        user_id: str,
        conversation_id: str,
        original_run_id: str,
    ):
        messages = await self._task_store.list_conversation_messages(
            conversation_id,
            user_id=user_id,
        )
        for message in reversed(messages):
            if message.role == "user" and message.payload.get("harness_run_id") == original_run_id:
                return message
        return None

    @staticmethod
    def _result(record: HarnessRecoveryRecord) -> HarnessRecoveryResult:
        return HarnessRecoveryResult(
            status=record.status,
            recovery_event_id=record.recovery_event_id,
            recovery_run_id=record.recovery_run_id,
        )


def _digest(payload: dict[str, object]) -> str:
    """计算恢复 Run 的冻结摘要，不保存用户正文或凭据。"""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = ["HarnessRecoveryResult", "HarnessRecoveryService"]
