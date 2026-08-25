"""视频工作区、计划与步骤的内存 Repository 实现。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import JsonValue

from pixelflow.agent_control_plane.contracts import AgentEvent
from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
)
from pixelflow.video.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video.services.workflow_events import (
    build_step_completed_event,
    build_step_failed_event,
    build_step_progressed_event,
    build_step_started_event,
)
from pixelflow.video.workspace.ids import video_workspace_id_for_conversation


def _clone[T](record: T) -> T:
    return deepcopy(record)


def _now() -> datetime:
    return datetime.now(UTC)


def _stored_time(value: datetime | None) -> datetime:
    return _now() if value is None else value


def _restore_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sortable_time(value: datetime | None) -> datetime:
    return _restore_utc(value) or datetime.min.replace(tzinfo=UTC)


def _pick_authoritative_workspace[T](
    conversation_id: str,
    workspaces: list[T],
    *,
    workspace_id_of,
    updated_at_of,
    plan_workspace_ids: set[str],
) -> T:
    """多 Workspace 时择一权威：稳定 ID > 有 Plan > 最近更新。"""

    if not workspaces:
        raise AgentRuntimeRecordConflictError("VideoAgent 会话缺少 workspace")
    if len(workspaces) == 1:
        return workspaces[0]
    preferred_id = video_workspace_id_for_conversation(conversation_id)
    for item in workspaces:
        if workspace_id_of(item) == preferred_id:
            return item
    with_plans = [
        item for item in workspaces if workspace_id_of(item) in plan_workspace_ids
    ]
    pool = with_plans or workspaces
    return max(
        pool,
        key=lambda item: (
            _sortable_time(updated_at_of(item)),
            workspace_id_of(item),
        ),
    )


def _workspace_patch(patch: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    normalized = deepcopy(dict(patch))
    for key in normalized:
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise AgentRuntimeRecordConflictError("VideoAgent workspace patch 键无效")
    return normalized


def _merge_scenes_by_id(existing: object, incoming: object) -> list[JsonValue]:
    """按 scene_id 合并镜头列表：incoming 覆盖同 id，保留现有未提及镜。

    并发生成时，后到的 generate_scenes / Operation 完成补丁若整表替换，
    会把另一镜已写回的 video_url 盖掉；合并写入可避免丢成片。
    """

    existing_list = (
        [dict(item) for item in existing if isinstance(item, dict)]
        if isinstance(existing, list)
        else []
    )
    incoming_list = (
        [dict(item) for item in incoming if isinstance(item, dict)]
        if isinstance(incoming, list)
        else []
    )
    if not existing_list:
        return list(incoming_list)
    by_id: dict[str, dict[str, JsonValue]] = {}
    order: list[str] = []
    for scene in existing_list:
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue
        if scene_id not in by_id:
            order.append(scene_id)
        by_id[scene_id] = scene
    for scene in incoming_list:
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue
        if scene_id not in by_id:
            order.append(scene_id)
        by_id[scene_id] = scene
    return [by_id[scene_id] for scene_id in order]


def _is_workspace_patch_replay(
    workspace: VideoWorkspace,
    patch: Mapping[str, JsonValue],
    *,
    expected_revision: int,
) -> bool:
    # scenes_replace 只是写入指令，不会落进 payload，比较时排除。
    comparable = {
        key: value
        for key, value in patch.items()
        if key != "scenes_replace"
    }
    if workspace.revision != expected_revision + 1:
        return False
    for key, value in comparable.items():
        if key in {"scenes", "scene_packages"} and isinstance(value, list):
            # 合并写入后 payload 可能比补丁更长，按 id 比对补丁内各镜即可。
            current = workspace.payload.get(key)
            current_list = current if isinstance(current, list) else []
            current_by_id = {
                str(item.get("scene_id") or "").strip(): item
                for item in current_list
                if isinstance(item, dict)
            }
            for item in value:
                if not isinstance(item, dict):
                    return False
                scene_id = str(item.get("scene_id") or "").strip()
                if not scene_id or current_by_id.get(scene_id) != item:
                    return False
            continue
        if workspace.payload.get(key) != value:
            return False
    return True


def _assert_expected_revision(expected_revision: int) -> None:
    if isinstance(expected_revision, bool) or expected_revision < 1:
        raise AgentRuntimeRecordConflictError(
            "VideoAgent workspace expected_revision 必须是正整数"
        )


_SCRIPT_SKILL_TOOL_NAME = "run_script_skill_stage"
_SCRIPT_SKILL_PLAN_TOOLS = frozenset(
    {
        _SCRIPT_SKILL_TOOL_NAME,
        "confirm_script_creative",
    }
)
_ACTIVE_SCRIPT_PLAN_STATUSES = frozenset(
    {
        AgentPlanStatus.PLANNING,
        AgentPlanStatus.RUNNING,
        AgentPlanStatus.AWAITING_CONFIRMATION,
    }
)
_OPEN_SCRIPT_STEP_STATUSES = frozenset(
    {
        PlanStepStatus.PENDING,
        PlanStepStatus.RUNNING,
        PlanStepStatus.AWAITING_CONFIRMATION,
    }
)
_SCRIPT_SKILL_SUPERSEDED_SUMMARY = "用户已确认脚本并开始生成资产包，本步已跳过"


def _is_active_script_skill_plan(plan: AgentPlan) -> bool:
    """脚本 Skill 计划仍在推进时，确认成片后可整单取消。"""

    if plan.status not in _ACTIVE_SCRIPT_PLAN_STATUSES:
        return False
    if not plan.steps:
        return plan.public_goal == "成稿自检与导出"
    # Path A 在 /start 后插入 confirm_script_creative，仍视为脚本 Skill 计划。
    return all(step.tool_name in _SCRIPT_SKILL_PLAN_TOOLS for step in plan.steps) and any(
        step.tool_name == _SCRIPT_SKILL_TOOL_NAME for step in plan.steps
    )


def _updated_workspace(
    workspace: VideoWorkspace,
    patch: Mapping[str, JsonValue],
    *,
    now: datetime,
) -> VideoWorkspace:
    raw_patch = dict(patch)
    # 用途：prepare / 全量重建时整表替换；默认按 scene_id 合并，避免并发生成互相覆盖。
    replace_scenes = bool(raw_patch.pop("scenes_replace", False))
    payload = dict(workspace.payload)
    for key in ("scenes", "scene_packages"):
        if key not in raw_patch:
            continue
        incoming = raw_patch.pop(key)
        if replace_scenes:
            payload[key] = deepcopy(incoming)
        else:
            payload[key] = _merge_scenes_by_id(payload.get(key), incoming)
    payload.update(raw_patch)
    return VideoWorkspace.model_validate(
        {
            **workspace.model_dump(mode="python"),
            "revision": workspace.revision + 1,
            "payload": payload,
            "updated_at": now,
        }
    )


_PLAN_STATUS_TRANSITIONS: dict[AgentPlanStatus, frozenset[AgentPlanStatus]] = {
    AgentPlanStatus.PLANNING: frozenset(
        {
            AgentPlanStatus.RUNNING,
            AgentPlanStatus.AWAITING_CONFIRMATION,
            AgentPlanStatus.WAITING_FOR_INPUT,
            AgentPlanStatus.FAILED,
            AgentPlanStatus.CANCELLED,
        }
    ),
    AgentPlanStatus.RUNNING: frozenset(
        {
            AgentPlanStatus.AWAITING_CONFIRMATION,
            AgentPlanStatus.COMPLETED,
            AgentPlanStatus.FAILED,
            AgentPlanStatus.CANCELLED,
        }
    ),
    AgentPlanStatus.AWAITING_CONFIRMATION: frozenset(
        {
            AgentPlanStatus.RUNNING,
            AgentPlanStatus.FAILED,
            AgentPlanStatus.CANCELLED,
        }
    ),
    # 用户补齐信息或新 Turn 覆盖时，结束等待态。
    AgentPlanStatus.WAITING_FOR_INPUT: frozenset(
        {
            AgentPlanStatus.CANCELLED,
            AgentPlanStatus.COMPLETED,
        }
    ),
    AgentPlanStatus.COMPLETED: frozenset(),
    AgentPlanStatus.FAILED: frozenset(),
    AgentPlanStatus.CANCELLED: frozenset(),
}


def _assert_plan_transition(
    current: AgentPlanStatus,
    target: AgentPlanStatus,
) -> None:
    if target is current:
        return
    if target not in _PLAN_STATUS_TRANSITIONS[current]:
        raise AgentRuntimeRecordConflictError(
            f"VideoAgent plan 状态不能从 {current.value} 变更为 {target.value}"
        )



class MemoryVideoAgentRepository:
    def __init__(
        self,
        *,
        event_repository: AgentRuntimeRepository | None = None,
    ) -> None:
        self._workspace_by_owner: dict[tuple[str, str], VideoWorkspace] = {}
        self._workspace_owner_by_id: dict[str, str] = {}
        self._plan_by_owner: dict[tuple[str, str], AgentPlan] = {}
        self._plan_owner_by_id: dict[str, str] = {}
        self._steps_by_owner: dict[tuple[str, str, str], AgentPlanStep] = {}
        self._event_repository = event_repository
        self._transition_lock = asyncio.Lock()

    async def create_workspace(self, user_id: str, workspace: VideoWorkspace) -> VideoWorkspace:
        owner = _owner(user_id)
        existing_owner = self._workspace_owner_by_id.get(workspace.workspace_id)
        if existing_owner is not None:
            if existing_owner != owner:
                raise AgentRuntimeRecordConflictError("VideoAgent workspace 已属于其他用户")
            return _clone(self._workspace_by_owner[(owner, workspace.workspace_id)])
        stored = workspace.model_copy(
            update={
                "created_at": _stored_time(workspace.created_at),
                "updated_at": _stored_time(workspace.updated_at),
            }
        )
        self._workspace_owner_by_id[stored.workspace_id] = owner
        self._workspace_by_owner[(owner, stored.workspace_id)] = _clone(stored)
        return _clone(stored)

    async def get_workspace(self, user_id: str, workspace_id: str) -> VideoWorkspace | None:
        record = self._workspace_by_owner.get((_owner(user_id), workspace_id))
        return None if record is None else _clone(record)

    async def discard_workspace(self, user_id: str, workspace_id: str) -> None:
        owner = _owner(user_id)
        key = (owner, workspace_id)
        async with self._transition_lock:
            existing = self._workspace_by_owner.get(key)
            if existing is None:
                return
            del self._workspace_by_owner[key]
            self._workspace_owner_by_id.pop(workspace_id, None)

    async def load_conversation_state(
        self,
        user_id: str,
        conversation_id: str,
    ) -> tuple[VideoWorkspace, AgentPlan | None] | None:
        """按会话一次读取 Snapshot 所需的工作区和最新计划。"""

        owner = _owner(user_id)
        workspaces = [
            workspace
            for (workspace_owner, _), workspace in self._workspace_by_owner.items()
            if workspace_owner == owner
            and workspace.conversation_id == conversation_id
        ]
        if not workspaces:
            return None
        plan_workspace_ids = {
            plan.workspace_id
            for (plan_owner, _), plan in self._plan_by_owner.items()
            if plan_owner == owner and plan.conversation_id == conversation_id
        }
        workspace = _pick_authoritative_workspace(
            conversation_id,
            workspaces,
            workspace_id_of=lambda item: item.workspace_id,
            updated_at_of=lambda item: item.updated_at,
            plan_workspace_ids=plan_workspace_ids,
        )
        # 清理无 Plan 的历史双写孤儿，避免会话长期卡在多权威状态。
        for item in workspaces:
            if (
                item.workspace_id != workspace.workspace_id
                and item.workspace_id not in plan_workspace_ids
            ):
                await self.discard_workspace(owner, item.workspace_id)
        plans = [
            plan
            for (plan_owner, _), plan in self._plan_by_owner.items()
            if plan_owner == owner
            and plan.conversation_id == conversation_id
            and plan.workspace_id == workspace.workspace_id
        ]
        if not plans:
            return _clone(workspace), None
        latest = max(
            plans,
            key=lambda item: (
                _sortable_time(item.updated_at or item.created_at),
                item.plan_id,
            ),
        )
        steps = await self.list_plan_steps(owner, latest.plan_id)
        return _clone(workspace), _clone(latest.model_copy(update={"steps": tuple(steps)}))

    async def list_conversation_plans(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[AgentPlan]:
        owner = _owner(user_id)
        workspaces = [
            workspace
            for (workspace_owner, _), workspace in self._workspace_by_owner.items()
            if workspace_owner == owner
            and workspace.conversation_id == conversation_id
        ]
        if not workspaces:
            return []
        plan_workspace_ids = {
            plan.workspace_id
            for (plan_owner, _), plan in self._plan_by_owner.items()
            if plan_owner == owner and plan.conversation_id == conversation_id
        }
        workspace = _pick_authoritative_workspace(
            conversation_id,
            workspaces,
            workspace_id_of=lambda item: item.workspace_id,
            updated_at_of=lambda item: item.updated_at,
            plan_workspace_ids=plan_workspace_ids,
        )
        for item in workspaces:
            if (
                item.workspace_id != workspace.workspace_id
                and item.workspace_id not in plan_workspace_ids
            ):
                await self.discard_workspace(owner, item.workspace_id)
        plans = [
            plan
            for (plan_owner, _), plan in self._plan_by_owner.items()
            if plan_owner == owner
            and plan.conversation_id == conversation_id
            and plan.workspace_id == workspace.workspace_id
        ]
        plans.sort(
            key=lambda item: (
                _sortable_time(item.created_at),
                item.plan_id,
            )
        )
        result: list[AgentPlan] = []
        for plan in plans:
            steps = await self.list_plan_steps(owner, plan.plan_id)
            result.append(_clone(plan.model_copy(update={"steps": tuple(steps)})))
        return result

    async def apply_workspace_patch(
        self,
        user_id: str,
        workspace_id: str,
        patch: Mapping[str, JsonValue],
        *,
        expected_revision: int,
        now: datetime,
    ) -> VideoWorkspace:
        _assert_expected_revision(expected_revision)
        normalized_patch = _workspace_patch(patch)
        owner = _owner(user_id)
        key = (owner, workspace_id)
        async with self._transition_lock:
            workspace = self._workspace_by_owner.get(key)
            if workspace is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent workspace 不存在或不属于当前用户"
                )
            if _is_workspace_patch_replay(
                workspace,
                normalized_patch,
                expected_revision=expected_revision,
            ):
                return _clone(workspace)
            if workspace.revision != expected_revision:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent workspace revision 已变化"
                )
            updated = _updated_workspace(
                workspace,
                normalized_patch,
                now=now,
            )
            self._workspace_by_owner[key] = _clone(updated)
            return _clone(updated)

    async def get_plan(self, user_id: str, plan_id: str) -> AgentPlan | None:
        owner = _owner(user_id)
        plan = self._plan_by_owner.get((owner, plan_id))
        if plan is None:
            return None
        steps = await self.list_plan_steps(owner, plan_id)
        return _clone(plan.model_copy(update={"steps": tuple(steps)}))

    async def save_plan(
        self,
        user_id: str,
        plan: AgentPlan,
        steps: list[AgentPlanStep],
    ) -> AgentPlan:
        owner = _owner(user_id)
        if await self.get_workspace(owner, plan.workspace_id) is None:
            raise AgentRuntimeRecordConflictError("VideoAgent workspace 不存在或不属于当前用户")
        existing_owner = self._plan_owner_by_id.get(plan.plan_id)
        if existing_owner is not None:
            if existing_owner != owner:
                raise AgentRuntimeRecordConflictError("VideoAgent plan 已属于其他用户")
            return _clone(self._plan_by_owner[(owner, plan.plan_id)])
        if not steps:
            if plan.status not in {
                AgentPlanStatus.WAITING_FOR_INPUT,
                AgentPlanStatus.RUNNING,
            }:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent plan 缺少 steps（仅 waiting_for_input / running 观察计划允许空步骤）"
                )
        elif (
            {step.plan_id for step in steps} != {plan.plan_id}
            or len({step.sequence for step in steps}) != len(steps)
        ):
            raise AgentRuntimeRecordConflictError("VideoAgent plan steps 不符合当前 plan 或 sequence 唯一约束")
        stored = plan.model_copy(
            update={
                "steps": tuple(steps),
                "created_at": _stored_time(plan.created_at),
                "updated_at": _stored_time(plan.updated_at),
            }
        )
        self._plan_owner_by_id[stored.plan_id] = owner
        self._plan_by_owner[(owner, stored.plan_id)] = _clone(stored)
        for step in steps:
            self._steps_by_owner[(owner, stored.plan_id, step.step_id)] = _clone(step)
        return _clone(stored)

    async def update_plan_status(
        self,
        user_id: str,
        plan_id: str,
        status: AgentPlanStatus,
        *,
        now: datetime,
    ) -> AgentPlan:
        owner = _owner(user_id)
        key = (owner, plan_id)
        plan = self._plan_by_owner.get(key)
        if plan is None:
            raise AgentRuntimeRecordConflictError("VideoAgent plan 不存在或不属于当前用户")
        _assert_plan_transition(plan.status, status)
        steps = await self.list_plan_steps(owner, plan_id)
        updated = plan.model_copy(
            update={"status": status, "steps": tuple(steps), "updated_at": now}
        )
        self._plan_by_owner[key] = _clone(updated)
        return _clone(updated)

    async def start_step(
        self, user_id: str, plan_id: str, step_id: str, *, now: datetime
    ) -> AgentPlanStep:
        key = (_owner(user_id), plan_id, step_id)
        step = self._steps_by_owner.get(key)
        if step is None:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
        if step.status is PlanStepStatus.RUNNING:
            # 陈旧重跑会传入新的 now；普通 resume 传入原 started_at，保持耗时连续。
            if step.started_at == now:
                return _clone(step)
            started = step.model_copy(update={"started_at": now})
            self._steps_by_owner[key] = _clone(started)
            return _clone(started)
        if step.status is not PlanStepStatus.PENDING:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不能开始")
        if step.confirmation_required:
            raise AgentRuntimeRecordConflictError("VideoAgent step 需先确认才能开始")
        started = step.model_copy(update={"status": PlanStepStatus.RUNNING, "started_at": now})
        self._steps_by_owner[key] = _clone(started)
        return _clone(started)

    async def complete_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        result: VideoToolResult,
        *,
        now: datetime,
    ) -> AgentPlanStep:
        key = (_owner(user_id), plan_id, step_id)
        step = self._steps_by_owner.get(key)
        if step is None:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
        if step.tool_name != result.tool_name:
            raise AgentRuntimeRecordConflictError("VideoAgent 工具结果与 step 不匹配")
        if step.status is PlanStepStatus.COMPLETED:
            if step.public_summary == result.public_summary and step.artifact_refs == result.artifact_refs:
                return _clone(step)
            raise AgentRuntimeRecordConflictError("VideoAgent step 已用不同结果完成")
        if step.status is not PlanStepStatus.RUNNING:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不能完成")
        completed = step.model_copy(
            update={
                "status": PlanStepStatus.COMPLETED,
                "public_summary": result.public_summary,
                "artifact_refs": result.artifact_refs,
                "completed_at": now,
            }
        )
        self._steps_by_owner[key] = _clone(completed)
        return _clone(completed)

    async def request_step_confirmation(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
    ) -> AgentPlanStep:
        key = (_owner(user_id), plan_id, step_id)
        step = self._steps_by_owner.get(key)
        if step is None:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
        if step.status is PlanStepStatus.AWAITING_CONFIRMATION:
            return _clone(step)
        if step.status is not PlanStepStatus.PENDING or not step.confirmation_required:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不能请求确认")
        waiting = step.model_copy(update={"status": PlanStepStatus.AWAITING_CONFIRMATION})
        self._steps_by_owner[key] = _clone(waiting)
        return _clone(waiting)

    async def confirm_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlanStep:
        key = (_owner(user_id), plan_id, step_id)
        step = self._steps_by_owner.get(key)
        if step is None:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
        if step.status is PlanStepStatus.RUNNING:
            return _clone(step)
        if step.status is not PlanStepStatus.AWAITING_CONFIRMATION:
            raise AgentRuntimeRecordConflictError("VideoAgent step 当前不等待确认")
        running = step.model_copy(
            update={"status": PlanStepStatus.RUNNING, "started_at": now}
        )
        self._steps_by_owner[key] = _clone(running)
        return _clone(running)

    async def cancel_step_confirmation(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlan:
        """原子跳过待确认步骤并取消计划，避免留下可再次提交的确认单。"""

        async with self._transition_lock:
            owner = _owner(user_id)
            plan_key = (owner, plan_id)
            step_key = (owner, plan_id, step_id)
            plan = self._plan_by_owner.get(plan_key)
            step = self._steps_by_owner.get(step_key)
            if plan is None or step is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent plan或step不存在或不属于当前用户"
                )
            if plan.status is AgentPlanStatus.CANCELLED:
                return _clone(plan)
            if (
                plan.status is not AgentPlanStatus.AWAITING_CONFIRMATION
                or step.status is not PlanStepStatus.AWAITING_CONFIRMATION
            ):
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent step当前不能取消确认"
                )
            skipped = step.model_copy(
                update={
                    "status": PlanStepStatus.SKIPPED,
                    "public_summary": "用户已取消执行",
                    "started_at": now,
                    "completed_at": now,
                }
            )
            self._steps_by_owner[step_key] = _clone(skipped)
            steps = await self.list_plan_steps(owner, plan_id)
            cancelled = plan.model_copy(
                update={
                    "status": AgentPlanStatus.CANCELLED,
                    "steps": tuple(steps),
                    "updated_at": now,
                }
            )
            self._plan_by_owner[plan_key] = _clone(cancelled)
            return _clone(cancelled)

    async def cancel_quota_interrupted_plan(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        quota_interrupt_id: str,
        job_id: str,
        quota_pause_revision: int,
        now: datetime,
    ) -> AgentPlan:
        """在同一内存临界区取消额度中断对应步骤、Plan和卡片。"""

        async with self._transition_lock:
            owner = _owner(user_id)
            plan_key = (owner, plan_id)
            plan = self._plan_by_owner.get(plan_key)
            if plan is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent plan 不存在或不属于当前用户"
                )
            workspace_key = (owner, plan.workspace_id)
            workspace = self._workspace_by_owner.get(workspace_key)
            step_key = (owner, plan_id, step_id)
            step = self._steps_by_owner.get(step_key)
            interrupt = (
                workspace.payload.get("quota_interrupt")
                if workspace is not None
                else None
            )
            resolution = (
                workspace.payload.get("last_quota_resolution")
                if workspace is not None
                else None
            )
            if (
                plan.status is AgentPlanStatus.CANCELLED
                and step is not None
                and step.status is PlanStepStatus.SKIPPED
                and interrupt is None
                and isinstance(resolution, Mapping)
                and resolution.get("event_id") == quota_interrupt_id
                and resolution.get("job_id") == job_id
                and resolution.get("quota_pause_revision")
                == quota_pause_revision
                and resolution.get("state") == "cancelled"
            ):
                steps = sorted(
                    (
                        item
                        for (step_owner, stored_plan_id, _), item
                        in self._steps_by_owner.items()
                        if step_owner == owner and stored_plan_id == plan_id
                    ),
                    key=lambda item: item.sequence,
                )
                return _clone(plan).model_copy(update={"steps": tuple(steps)})
            if (
                workspace is None
                or step is None
                or step.status is not PlanStepStatus.RUNNING
                or not isinstance(interrupt, Mapping)
                or interrupt.get("quota_interrupt_id") != quota_interrupt_id
                or interrupt.get("plan_id") != plan_id
                or interrupt.get("step_id") != step_id
                or interrupt.get("job_id") != job_id
                or interrupt.get("quota_pause_revision")
                != quota_pause_revision
            ):
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent额度取消与当前权威状态不匹配"
                )
            _assert_plan_transition(plan.status, AgentPlanStatus.CANCELLED)
            skipped = step.model_copy(
                update={
                    "status": PlanStepStatus.SKIPPED,
                    "public_summary": "用户已取消额度中断任务",
                    "completed_at": now,
                }
            )
            cancelled = plan.model_copy(
                update={"status": AgentPlanStatus.CANCELLED, "updated_at": now}
            )
            cleared = _updated_workspace(
                workspace,
                {
                    "quota_interrupt": None,
                    "last_quota_resolution": {
                        "event_id": quota_interrupt_id,
                        "job_id": job_id,
                        "quota_pause_revision": quota_pause_revision,
                        "state": "cancelled",
                    },
                },
                now=now,
            )
            self._steps_by_owner[step_key] = _clone(skipped)
            self._plan_by_owner[plan_key] = _clone(cancelled)
            self._workspace_by_owner[workspace_key] = _clone(cleared)
            steps = sorted(
                (
                    item
                    for (step_owner, stored_plan_id, _), item
                    in self._steps_by_owner.items()
                    if step_owner == owner and stored_plan_id == plan_id
                ),
                key=lambda item: item.sequence,
            )
            return _clone(cancelled).model_copy(update={"steps": tuple(steps)})

    async def cancel_active_script_skill_plans(
        self,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
    ) -> list[AgentPlan]:
        """确认脚本成片后取消仍在跑的脚本 Skill 计划，避免假忙碌。"""

        async with self._transition_lock:
            owner = _owner(user_id)
            conversation = conversation_id.strip()
            plans = await self.list_conversation_plans(owner, conversation)
            cancelled_plans: list[AgentPlan] = []
            for plan in plans:
                if not _is_active_script_skill_plan(plan):
                    continue
                _assert_plan_transition(plan.status, AgentPlanStatus.CANCELLED)
                for step in plan.steps:
                    if step.status not in _OPEN_SCRIPT_STEP_STATUSES:
                        continue
                    key = (owner, plan.plan_id, step.step_id)
                    skipped = step.model_copy(
                        update={
                            "status": PlanStepStatus.SKIPPED,
                            "public_summary": _SCRIPT_SKILL_SUPERSEDED_SUMMARY,
                            "started_at": step.started_at or now,
                            "completed_at": now,
                        }
                    )
                    self._steps_by_owner[key] = _clone(skipped)
                steps = await self.list_plan_steps(owner, plan.plan_id)
                cancelled = plan.model_copy(
                    update={
                        "status": AgentPlanStatus.CANCELLED,
                        "steps": tuple(steps),
                        "updated_at": now,
                    }
                )
                self._plan_by_owner[(owner, plan.plan_id)] = _clone(cancelled)
                cancelled_plans.append(_clone(cancelled))
            return cancelled_plans

    async def cancel_waiting_for_input_plans(
        self,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
        exclude_plan_id: str | None = None,
    ) -> list[AgentPlan]:
        """新 Turn 推进前取消旧的等待补充 Plan，避免时间线叠多个等待卡。"""

        async with self._transition_lock:
            owner = _owner(user_id)
            conversation = conversation_id.strip()
            excluded = (exclude_plan_id or "").strip() or None
            plans = await self.list_conversation_plans(owner, conversation)
            cancelled_plans: list[AgentPlan] = []
            for plan in plans:
                if plan.status is not AgentPlanStatus.WAITING_FOR_INPUT:
                    continue
                if excluded and plan.plan_id == excluded:
                    continue
                _assert_plan_transition(plan.status, AgentPlanStatus.CANCELLED)
                cancelled = plan.model_copy(
                    update={
                        "status": AgentPlanStatus.CANCELLED,
                        "updated_at": now,
                    }
                )
                self._plan_by_owner[(owner, plan.plan_id)] = _clone(cancelled)
                cancelled_plans.append(_clone(cancelled))
            return cancelled_plans

    async def start_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]:
        async with self._transition_lock:
            owner = _owner(user_id)
            key = (owner, plan_id, step_id)
            before = self._steps_by_owner.get(key)
            if before is None:
                raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
            step = await self.start_step(owner, plan_id, step_id, now=now)
            try:
                event = await self._persist_memory_event(
                    owner,
                    plan_id,
                    step,
                    run_id=run_id,
                    now=now,
                    completed=False,
                )
            except Exception:
                self._steps_by_owner[key] = before
                raise
            return step, event

    async def progress_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        public_summary: str,
        progress_phase: str,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]:
        """更新运行中步骤的公开阶段摘要，并写入 progressed 事件。"""

        async with self._transition_lock:
            owner = _owner(user_id)
            key = (owner, plan_id, step_id)
            before = self._steps_by_owner.get(key)
            if before is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent step 不存在或不属于当前用户"
                )
            if before.status is not PlanStepStatus.RUNNING:
                raise AgentRuntimeRecordConflictError("VideoAgent step 当前不能推送进度")
            summary = public_summary.strip()
            phase = progress_phase.strip()
            if not summary or not phase:
                raise ValueError("进度摘要与阶段标识不能为空")
            if before.public_summary == summary:
                step = _clone(before)
            else:
                step = before.model_copy(update={"public_summary": summary})
                self._steps_by_owner[key] = step
            try:
                event = await self._persist_memory_progress_event(
                    owner,
                    plan_id,
                    step,
                    progress_phase=phase,
                    run_id=run_id,
                    now=now,
                )
            except Exception:
                self._steps_by_owner[key] = before
                raise
            return _clone(step), event

    async def complete_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        result: VideoToolResult,
        *,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]:
        async with self._transition_lock:
            owner = _owner(user_id)
            key = (owner, plan_id, step_id)
            before = self._steps_by_owner.get(key)
            if before is None:
                raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
            step = await self.complete_step(owner, plan_id, step_id, result, now=now)
            try:
                event = await self._persist_memory_event(
                    owner,
                    plan_id,
                    step,
                    run_id=run_id,
                    now=now,
                    completed=True,
                )
            except Exception:
                self._steps_by_owner[key] = before
                raise
            return step, event

    async def fail_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        reason_code: str,
        public_summary: str,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]:
        """把运行中步骤与安全失败事件放入同一内存临界区。"""

        async with self._transition_lock:
            owner = _owner(user_id)
            key = (owner, plan_id, step_id)
            plan_key = (owner, plan_id)
            before = self._steps_by_owner.get(key)
            before_plan = self._plan_by_owner.get(plan_key)
            if before is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent step 不存在或不属于当前用户"
                )
            if before_plan is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent plan 不存在或不属于当前用户"
                )
            if before.status is PlanStepStatus.FAILED:
                failed = _clone(before)
            elif before.status is PlanStepStatus.RUNNING:
                failed = before.model_copy(
                    update={
                        "status": PlanStepStatus.FAILED,
                        "public_summary": public_summary,
                        "completed_at": now,
                    }
                )
                self._steps_by_owner[key] = _clone(failed)
            else:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent step 当前不能标记失败"
                )
            _assert_plan_transition(before_plan.status, AgentPlanStatus.FAILED)
            failed_plan = before_plan.model_copy(
                update={"status": AgentPlanStatus.FAILED, "updated_at": now}
            )
            self._plan_by_owner[plan_key] = _clone(failed_plan)
            try:
                event = await self._persist_memory_failure_event(
                    owner,
                    plan_id,
                    failed,
                    reason_code=reason_code,
                    run_id=run_id,
                    now=now,
                )
            except Exception:
                self._steps_by_owner[key] = before
                self._plan_by_owner[plan_key] = before_plan
                raise
            return failed, event

    async def list_plan_steps(self, user_id: str, plan_id: str) -> list[AgentPlanStep]:
        owner = _owner(user_id)
        steps = [
            step
            for (step_owner, stored_plan_id, _), step in self._steps_by_owner.items()
            if step_owner == owner and stored_plan_id == plan_id
        ]
        return [_clone(step) for step in sorted(steps, key=lambda item: item.sequence)]

    async def _persist_memory_event(
        self,
        owner: str,
        plan_id: str,
        step: AgentPlanStep,
        *,
        run_id: str,
        now: datetime,
        completed: bool,
    ) -> AgentEvent:
        if self._event_repository is None:
            raise RuntimeError("VideoAgent step event repository 未配置")
        plan = self._plan_by_owner.get((owner, plan_id))
        if plan is None:
            raise AgentRuntimeRecordConflictError("VideoAgent plan 不存在或不属于当前用户")
        event_id = _step_event_id(step, completed=completed)
        existing = await self._event_repository.get_event(owner, event_id)
        if existing is not None:
            return existing
        events = await self._event_repository.list_events(owner, plan.conversation_id)
        event = _build_step_event(
            step,
            completed=completed,
            event_id=event_id,
            sequence=1 if not events else events[-1].sequence + 1,
            conversation_id=plan.conversation_id,
            run_id=run_id,
            occurred_at=now,
        )
        return await self._event_repository.create_event(owner, event)

    async def _persist_memory_failure_event(
        self,
        owner: str,
        plan_id: str,
        step: AgentPlanStep,
        *,
        reason_code: str,
        run_id: str,
        now: datetime,
    ) -> AgentEvent:
        if self._event_repository is None:
            raise RuntimeError("VideoAgent step event repository 未配置")
        plan = self._plan_by_owner.get((owner, plan_id))
        if plan is None:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent plan 不存在或不属于当前用户"
            )
        event_id = _step_event_id_from_parts(plan_id, step.step_id, phase="failed")
        existing = await self._event_repository.get_event(owner, event_id)
        if existing is not None:
            return existing
        events = await self._event_repository.list_events(owner, plan.conversation_id)
        event = build_step_failed_event(
            event_id=event_id,
            cursor=f"cursor_{event_id[4:]}",
            sequence=1 if not events else events[-1].sequence + 1,
            conversation_id=plan.conversation_id,
            run_id=run_id,
            occurred_at=now,
            step=step,
            reason_code=reason_code,
        )
        return await self._event_repository.create_event(owner, event)

    async def _persist_memory_progress_event(
        self,
        owner: str,
        plan_id: str,
        step: AgentPlanStep,
        *,
        progress_phase: str,
        run_id: str,
        now: datetime,
    ) -> AgentEvent:
        if self._event_repository is None:
            raise RuntimeError("VideoAgent step event repository 未配置")
        plan = self._plan_by_owner.get((owner, plan_id))
        if plan is None:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent plan 不存在或不属于当前用户"
            )
        event_id = _step_event_id_from_parts(
            plan_id,
            step.step_id,
            phase=f"progressed:{progress_phase}",
        )
        existing = await self._event_repository.get_event(owner, event_id)
        if existing is not None:
            return existing
        events = await self._event_repository.list_events(owner, plan.conversation_id)
        event = build_step_progressed_event(
            event_id=event_id,
            cursor=f"cursor_{event_id[4:]}",
            sequence=1 if not events else events[-1].sequence + 1,
            conversation_id=plan.conversation_id,
            run_id=run_id,
            occurred_at=now,
            step=step,
            progress_phase=progress_phase,
        )
        return await self._event_repository.create_event(owner, event)

def _owner(user_id: str) -> str:
    owner = user_id.strip()
    if not owner:
        raise ValueError("user_id 不能为空")
    return owner


def _step_event_id_from_parts(
    plan_id: str,
    step_id: str,
    *,
    completed: bool | None = None,
    phase: str | None = None,
) -> str:
    resolved_phase = phase or ("completed" if completed else "started")
    if resolved_phase in {"started", "completed", "failed"}:
        event_phase = resolved_phase
    elif resolved_phase.startswith("progressed:"):
        phase_key = resolved_phase.removeprefix("progressed:").strip()
        if not phase_key or len(phase_key) > 64 or "/" in phase_key or "\\" in phase_key:
            raise ValueError("VideoAgent step进度阶段无效")
        event_phase = f"progressed:{phase_key}"
    else:
        raise ValueError("VideoAgent step事件阶段无效")
    value = (
        f"pixelflow-video-agent:step-event:{plan_id}:{step_id}:"
        f"{event_phase}"
    )
    return f"evt_{uuid5(NAMESPACE_URL, value).hex}"


def _step_event_id(step: AgentPlanStep, *, completed: bool) -> str:
    return _step_event_id_from_parts(
        step.plan_id,
        step.step_id,
        completed=completed,
    )


def _build_step_event(
    step: AgentPlanStep,
    *,
    completed: bool,
    event_id: str,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
) -> AgentEvent:
    cursor = f"cursor_{event_id[4:]}"
    if completed:
        return build_step_completed_event(
            event_id=event_id,
            cursor=cursor,
            sequence=sequence,
            conversation_id=conversation_id,
            run_id=run_id,
            occurred_at=occurred_at,
            step=step,
        )
    return build_step_started_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        step=step,
    )
__all__ = ["MemoryVideoAgentRepository"]
