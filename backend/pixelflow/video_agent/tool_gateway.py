"""Video Tool Gateway：把原生 Tool Call 接到 Registry，并强制确认/额度/revision 闸门。"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.video_agent.confirmation import (
    confirmation_cost_summary,
    native_confirmation_id,
)
from pixelflow.video_agent.contracts import (
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.credentials import TransientVideoAgentCredential
from pixelflow.video_agent.executor.events import build_confirmation_requested_event
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolRegistry,
    VideoToolSpec,
    VideoToolValidationError,
)

logger = logging.getLogger(__name__)


def _json_safe_value(value: object) -> object:
    """把 Tool 参数里的 tuple 等非 JSON 值收敛为可入库结构。"""

    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item)
            for key, item in value.items()
        }
    return value


_CONTEXT_SECRET_KEYS = frozenset(
    {
        "user_id",
        "workspace_id",
        "workspace",
        "plan_id",
        "step_id",
        "authorization",
        "credential",
        "revision",
        "runtime",
        "approved_confirmation",
        "tool_call_id",
        "turn_id",
        "conversation_id",
    }
)


@dataclass
class VideoToolGateway:
    """统一进入 Registry / Executor 的执行门面；不决定业务 Tool 顺序。"""

    registry: VideoToolRegistry
    executor: object | None = None
    plan_middleware: object | None = None
    video_repository: object | None = None
    runtime_repository: object | None = None
    clock: Any = field(default=None)

    def __post_init__(self) -> None:
        if not isinstance(self.registry, VideoToolRegistry):
            raise TypeError("registry 必须是 VideoToolRegistry")
        if self.clock is None:
            self.clock = lambda: datetime.now(UTC)

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, object] | None = None,
        *,
        context: VideoToolContext | None = None,
        runtime_context: Mapping[str, object] | None = None,
    ) -> str:
        """执行单个 Tool，并返回模型可消费的安全 JSON 字符串。"""

        name = (tool_name or "").strip()
        args = dict(arguments or {})
        for secret in _CONTEXT_SECRET_KEYS:
            args.pop(secret, None)

        try:
            tool_context = context or self.build_context(runtime_context)
        except ValueError as exc:
            return self._safe_payload(
                tool_name=name or "unknown_tool",
                public_summary=str(exc).strip() or "工具上下文无效，请稍后重试",
            )

        runtime = dict(runtime_context or {})
        if self.registry.resolve(name) is None:
            return self._safe_payload(
                tool_name=name or "unknown_tool",
                public_summary="未注册工具，请改用已登记能力",
            )

        spec = self.registry.resolve(name)
        assert spec is not None
        tool_spec = spec.spec

        # 用户本轮是「确认并生成分镜视频（scene-x）」时，禁止模型改走合并成片。
        if name == "compose_or_export_video":
            latest = tool_context.workspace.payload.get("latest_input")
            latest_text = str(latest or "").strip() if latest is not None else ""
            marker = "\n\n【本轮指令】"
            instruction = (
                latest_text.partition(marker)[2].strip()
                if marker in latest_text
                else latest_text
            )
            compact = re.sub(r"\s+", "", instruction)
            generate_scenes_turn = (
                "确认并生成分镜视频" in compact
                or "重新生成已修改的分镜视频" in compact
                or "继续生成失败的分镜视频" in compact
            )
            merge_turn = any(
                token in compact
                for token in ("合并视频", "合并成片", "合成视频", "合成成片", "导出成片")
            )
            if generate_scenes_turn and not merge_turn:
                return self._safe_payload(
                    tool_name=name,
                    public_summary=(
                        "当前用户要求生成/重跑分镜视频，请调用 generate_scenes；"
                        "合并成片需用户明确说「合并视频/合成成片」。"
                    ),
                )

        quota_block = self._quota_block_summary(tool_spec, tool_context.workspace)
        if quota_block:
            return self._safe_payload(tool_name=name, public_summary=quota_block)

        if tool_spec.confirmation_required:
            # 确认裁决前重读 workspace：resume 写入的 approved 可能晚于 invoke 快照；
            # 也避免用过期 revision 去 persist pending 导致「要确认但落库失败」卡死。
            tool_context, approved = await self._refresh_confirmation_inputs(
                tool_context=tool_context,
                runtime=runtime,
            )
            gate = self._evaluate_confirmation_gate(
                spec=tool_spec,
                workspace=tool_context.workspace,
                approved=approved,
                tool_call_id=str(runtime.get("tool_call_id") or "").strip(),
                plan_id=tool_context.plan_id,
                arguments=args,
            )
            if gate is not None:
                confirmation_id, summary, pending = gate
                persisted = await self._persist_pending_confirmation(
                    user_id=tool_context.user_id,
                    workspace=tool_context.workspace,
                    pending=pending,
                )
                if not persisted:
                    return self._safe_payload(
                        tool_name=name,
                        public_summary=(
                            "确认闸门暂未能写入工作区，请刷新后重试合并/计费步骤"
                        ),
                    )
                await self._emit_confirmation_requested(
                    user_id=tool_context.user_id,
                    conversation_id=str(
                        runtime.get("conversation_id")
                        or tool_context.workspace.conversation_id
                    ),
                    turn_id=str(runtime.get("turn_id") or tool_context.plan_id or "turn"),
                    plan_id=tool_context.plan_id or "plan-native",
                    step_id=tool_context.step_id or "step-native",
                    tool_name=name,
                    confirmation_id=confirmation_id,
                    cost_summary=summary,
                )
                # 确认闸门也会进入 tools 节点：写入观察 Plan，避免 UI 仍停在上一轮「分镜视频」。
                note = getattr(self.plan_middleware, "note_business_tool", None)
                if callable(note):
                    note(name)
                # 明确要求模型停手：否则 ReAct 会连打同一计费 Tool，撞 LoopDetection。
                stop_hint = (
                    f"{summary}"
                    " 确认单已发出：请勿再次调用本工具；直接结束本轮，"
                    "提示用户在界面点击确认后再继续。"
                )
                return self._safe_payload(
                    tool_name=name,
                    public_summary=stop_hint,
                    requires_confirmation=True,
                    confirmation_id=confirmation_id,
                )

        try:
            result = await self._execute(tool_context, name, args)
        except VideoToolValidationError as exc:
            detail = str(exc).strip()
            return self._safe_payload(
                tool_name=name,
                public_summary=detail[:280] if detail else "工具参数无效，请修正后重试",
            )
        except VideoToolExecutionError:
            logger.exception("video tool execution failed name=%s", name)
            return self._safe_payload(
                tool_name=name,
                public_summary=f"{name} 执行失败，请稍后重试",
            )
        except Exception:
            logger.exception("video tool unexpected failure name=%s", name)
            return self._safe_payload(
                tool_name=name,
                public_summary=f"{name} 执行失败，请稍后重试",
            )

        await self._clear_pending_confirmation(
            user_id=tool_context.user_id,
            workspace=tool_context.workspace,
            tool_name=name,
        )
        note = getattr(self.plan_middleware, "note_business_tool", None)
        if callable(note):
            note(name)
        return self.serialize_result(result)

    def _evaluate_confirmation_gate(
        self,
        *,
        spec: VideoToolSpec,
        workspace: VideoWorkspace,
        approved: Mapping[str, object] | None,
        tool_call_id: str,
        plan_id: str | None,
        arguments: Mapping[str, object] | None = None,
    ) -> tuple[str, str, dict[str, object]] | None:
        """返回 (confirmation_id, summary, pending) 表示仍需确认；None 表示放行。"""

        call_id = tool_call_id or "missing-tool-call"
        plan = (plan_id or "plan-native").strip() or "plan-native"
        confirmation_id = native_confirmation_id(plan_id=plan, tool_call_id=call_id)
        summary = confirmation_cost_summary(spec)
        safe_args = {
            key: _json_safe_value(value)
            for key, value in dict(arguments or {}).items()
            if isinstance(key, str)
            and not any(
                fragment in key.casefold()
                for fragment in ("authorization", "token", "secret", "password", "credential")
            )
        }

        def _pending(**extra: object) -> dict[str, object]:
            payload: dict[str, object] = {
                "confirmation_id": confirmation_id,
                "tool_name": spec.name,
                "tool_call_id": call_id,
                "expected_revision": workspace.revision,
                "plan_id": plan,
                "arguments": safe_args,
            }
            payload.update(extra)
            return payload

        if approved is None:
            return (confirmation_id, summary, _pending())

        approved_tool = str(approved.get("tool_name") or "").strip()
        approved_call = str(approved.get("tool_call_id") or "").strip()
        approved_confirmation = str(approved.get("confirmation_id") or "").strip()
        expected = approved.get("expected_revision")
        if approved_tool != spec.name:
            return (confirmation_id, summary, _pending())

        # 用户已确认后，resume Turn 里模型常会换新的 tool_call_id；
        # 只要同工具且带着 confirmation_id，就视为同一确认放行。
        call_matched = approved_call == call_id
        resume_approved = bool(approved_confirmation) and approved_tool == spec.name
        if not call_matched and not resume_approved:
            return (confirmation_id, summary, _pending())

        if not isinstance(expected, int) or expected != workspace.revision:
            # 破坏性/计费：无 confirmation_id 的旧批准仍要求 revision 一致。
            # 已点过确认的 resume：确认写入自身与 resume 记账会 bump revision，
            # 不得再次弹确认挡住真正执行。
            if (
                not resume_approved
                and spec.cost_level
                in {
                    VideoToolCostLevel.BILLABLE,
                    VideoToolCostLevel.DESTRUCTIVE,
                }
            ):
                stale_summary = (
                    "工作区版本已变化，请基于最新内容重新确认后再执行"
                    f"（当前 revision={workspace.revision}）。"
                )
                return (
                    confirmation_id,
                    stale_summary,
                    _pending(reason_code="workspace_revision_conflict"),
                )
        return None

    @staticmethod
    def _quota_block_summary(
        spec: VideoToolSpec,
        workspace: VideoWorkspace,
    ) -> str | None:
        if spec.cost_level is not VideoToolCostLevel.BILLABLE:
            return None
        interrupt = workspace.payload.get("quota_interrupt")
        if isinstance(interrupt, Mapping) and interrupt:
            return "当前额度不足，已暂停计费任务。请充值后点「继续」或取消本轮。"
        return None

    async def _refresh_confirmation_inputs(
        self,
        *,
        tool_context: VideoToolContext,
        runtime: Mapping[str, object],
    ) -> tuple[VideoToolContext, Mapping[str, object] | None]:
        """重读最新 workspace，并用其中的 approved 覆盖可能过期的 runtime 快照。"""

        approved_runtime = runtime.get("approved_confirmation")
        approved: Mapping[str, object] | None = (
            approved_runtime if isinstance(approved_runtime, Mapping) else None
        )
        repository = self.video_repository
        getter = getattr(repository, "get_workspace", None)
        if not callable(getter):
            return tool_context, approved
        try:
            current = await getter(
                tool_context.user_id,
                tool_context.workspace.workspace_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("confirmation gate 重读 workspace 失败")
            return tool_context, approved
        if not isinstance(current, VideoWorkspace):
            return tool_context, approved

        fresh = current.payload.get("native_approved_confirmation")
        if isinstance(fresh, Mapping) and fresh:
            approved = fresh
        refreshed = VideoToolContext(
            user_id=tool_context.user_id,
            workspace=current,
            plan_id=tool_context.plan_id,
            step_id=tool_context.step_id,
            credential=tool_context.credential,
            report_progress=tool_context.report_progress,
            report_thinking=tool_context.report_thinking,
        )
        return refreshed, approved

    async def _persist_pending_confirmation(
        self,
        *,
        user_id: str,
        workspace: VideoWorkspace,
        pending: Mapping[str, object],
    ) -> bool:
        """持久化确认单；expected_revision 对齐写入成功后的 workspace.revision。

        闸门生成 pending 时带的是写入前 revision；apply_workspace_patch 成功后会 +1。
        若仍保留旧值，确认 API 会因 revision 校验恒 409。此处一次性写入
        ``当前 revision + 1``，与仓库递增约定一致，避免二次 patch 再次漂移。
        冲突时最多重读重试 1 次；失败返回 False，避免「口头要确认但 DB 无 pending」。
        """

        repository = self.video_repository
        apply = getattr(repository, "apply_workspace_patch", None)
        getter = getattr(repository, "get_workspace", None)
        if not callable(apply):
            return False
        current = workspace
        for attempt in range(2):
            try:
                pending_payload = dict(pending)
                pending_payload["arguments"] = _json_safe_value(
                    pending_payload.get("arguments") or {}
                )
                pending_payload["expected_revision"] = current.revision + 1
                updated = await apply(
                    user_id,
                    current.workspace_id,
                    {"native_pending_confirmation": pending_payload},
                    expected_revision=current.revision,
                    now=self.clock(),
                )
                if pending_payload.get("expected_revision") == updated.revision:
                    return True
                # 极端：revision 步进不是 +1；再对齐一次且预置下一跳。
                pending_payload["expected_revision"] = updated.revision + 1
                await apply(
                    user_id,
                    current.workspace_id,
                    {"native_pending_confirmation": pending_payload},
                    expected_revision=updated.revision,
                    now=self.clock(),
                )
                return True
            except AgentRuntimeRecordConflictError:
                logger.warning(
                    "native confirmation persist revision conflict workspace=%s attempt=%s",
                    current.workspace_id,
                    attempt + 1,
                )
                if attempt == 0 and callable(getter):
                    try:
                        reloaded = await getter(user_id, current.workspace_id)
                    except Exception:  # noqa: BLE001
                        logger.exception("confirmation persist 重读失败")
                        return False
                    if isinstance(reloaded, VideoWorkspace):
                        current = reloaded
                        continue
                return False
            except Exception:  # noqa: BLE001
                logger.exception("persist native_pending_confirmation failed")
                return False
        return False

    async def _clear_pending_confirmation(
        self,
        *,
        user_id: str,
        workspace: VideoWorkspace,
        tool_name: str,
    ) -> None:
        pending = workspace.payload.get("native_pending_confirmation")
        approved = workspace.payload.get("native_approved_confirmation")
        should_clear_pending = (
            isinstance(pending, Mapping) and str(pending.get("tool_name") or "") == tool_name
        )
        should_clear_approved = (
            isinstance(approved, Mapping) and str(approved.get("tool_name") or "") == tool_name
        )
        if not should_clear_pending and not should_clear_approved:
            return
        repository = self.video_repository
        apply = getattr(repository, "apply_workspace_patch", None)
        get_workspace = getattr(repository, "get_workspace", None)
        if not callable(apply) or not callable(get_workspace):
            return
        try:
            current = await get_workspace(user_id, workspace.workspace_id)
            if current is None:
                return
            patch: dict[str, object] = {}
            if should_clear_pending:
                patch["native_pending_confirmation"] = None
            if should_clear_approved:
                patch["native_approved_confirmation"] = None
            await apply(
                user_id,
                current.workspace_id,
                patch,
                expected_revision=current.revision,
                now=self.clock(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("clear native confirmation state failed")

    async def _emit_confirmation_requested(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        plan_id: str,
        step_id: str,
        tool_name: str,
        confirmation_id: str,
        cost_summary: str,
    ) -> None:
        repository = self.runtime_repository
        if repository is None or not hasattr(repository, "create_event"):
            return
        now = self.clock()
        try:
            events = await repository.list_events(user_id, conversation_id)
            sequence = 1 if not events else events[-1].sequence + 1
            event_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"video-native-confirmation:{conversation_id}:{confirmation_id}:{sequence}",
                )
            )
            step =             AgentPlanStep(
                step_id=step_id[:64],
                plan_id=plan_id[:64],
                sequence=1,
                tool_name=tool_name,
                title=_public_tool_step_title(tool_name),
                status=PlanStepStatus.AWAITING_CONFIRMATION,
                arguments={},
                confirmation_required=True,
            )
            event = build_confirmation_requested_event(
                event_id=event_id[:64],
                cursor=f"c_{event_id}"[:64],
                sequence=sequence,
                conversation_id=conversation_id,
                run_id=turn_id[:64],
                occurred_at=now,
                step=step,
                cost_summary=cost_summary,
                confirmation_id=confirmation_id,
            )
            # 附带 turn_id 供原生 Turn 组投影（公开 payload 扩展字段）。
            event.payload["turn_id"] = turn_id
            await repository.create_event(user_id, event)
        except Exception:  # noqa: BLE001
            logger.exception("emit agent.confirmation.requested failed")

    async def _execute(
        self,
        context: VideoToolContext,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        execute_tool_call = getattr(self.executor, "execute_tool_call", None)
        if callable(execute_tool_call):
            result = await execute_tool_call(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
            )
            if not isinstance(result, VideoToolResult):
                raise VideoToolExecutionError("工具结果无效，请稍后重试")
            return result
        return await self.registry.execute(context, tool_name, arguments)

    def build_context(
        self,
        runtime_context: Mapping[str, object] | None,
    ) -> VideoToolContext:
        """从 ToolRuntime.context 构造 VideoToolContext；敏感字段不来自模型参数。"""

        raw = dict(runtime_context or {})
        user_id = str(raw.get("user_id") or "").strip()
        if not user_id:
            raise ValueError("缺少用户上下文，无法执行工具")

        workspace = raw.get("workspace")
        if not isinstance(workspace, VideoWorkspace):
            raise ValueError("缺少视频工作区上下文，无法执行工具")

        plan_id = raw.get("plan_id")
        step_id = raw.get("step_id")
        plan_id_s = str(plan_id).strip() if plan_id is not None else None
        step_id_s = str(step_id).strip() if step_id is not None else None
        if plan_id_s == "":
            plan_id_s = None
        if step_id_s == "":
            step_id_s = None
        if (plan_id_s is None) != (step_id_s is None):
            plan_id_s = None
            step_id_s = None

        credential = raw.get("credential")
        if credential is not None and not isinstance(
            credential, TransientVideoAgentCredential
        ):
            raise ValueError("工具凭证上下文无效")

        return VideoToolContext(
            user_id=user_id,
            workspace=workspace,
            plan_id=plan_id_s,
            step_id=step_id_s,
            credential=credential,
            report_progress=raw.get("report_progress"),
            report_thinking=raw.get("report_thinking"),
        )

    @staticmethod
    def serialize_result(result: VideoToolResult) -> str:
        """只序列化对模型安全的字段。"""

        payload: dict[str, Any] = {
            "tool_name": result.tool_name,
            "public_summary": result.public_summary,
            "artifact_refs": list(result.artifact_refs),
            "pending_operation_job_ids": list(result.pending_operation_job_ids),
            "requires_confirmation": bool(result.requires_confirmation),
            "workspace_revision": None,
            "confirmation_id": None,
        }
        if "revision" in result.workspace_patch:
            revision = result.workspace_patch.get("revision")
            if isinstance(revision, int):
                payload["workspace_revision"] = revision
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _safe_payload(
        *,
        tool_name: str,
        public_summary: str,
        requires_confirmation: bool = False,
        confirmation_id: str | None = None,
    ) -> str:
        return json.dumps(
            {
                "tool_name": tool_name,
                "public_summary": public_summary,
                "artifact_refs": [],
                "pending_operation_job_ids": [],
                "requires_confirmation": requires_confirmation,
                "workspace_revision": None,
                "confirmation_id": confirmation_id,
            },
            ensure_ascii=False,
        )


def _public_tool_step_title(tool_name: str) -> str:
    """确认闸门 / Plan 步骤对外标题；禁止直接暴露工具英文名当「生成视频」。"""

    name = (tool_name or "").strip()
    titles = {
        "compose_or_export_video": "合并分镜视频为成片",
        "generate_scenes": "生成分镜视频",
        "generate_scene_assets": "生成场景参考图",
        "prepare_scene_packages": "生成视频分镜包",
        "patch_scene": "修改分镜",
        "replace_scene_asset": "替换场景包素材",
        "import_script": "导入脚本",
        "apply_production_fields": "补全生产字段",
    }
    return titles.get(name, name or "执行当前步骤")
