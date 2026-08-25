"""视频工作区、计划与步骤的 SQL Repository 实现。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pixelflow.agent_control_plane.contracts import AgentEvent, AgentEventType
from pixelflow.agent_control_plane.persistence.models import (
    PixelFlowAgentEventRow,
    PixelFlowVideoAgentPlanRow,
    PixelFlowVideoAgentPlanStepRow,
    PixelFlowVideoAgentWorkspaceRow,
)
from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRecordConflictError,
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

class SQLVideoAgentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """与 SQLPixelFlowTaskStore 共享同一 session_factory，供同库事务升级。"""

        return self._session_factory

    async def commit_legacy_upgrade(
        self,
        *,
        user_id: str,
        conversation_id: str,
        expected_conversation_revision: int,
        workspace_id: str,
        create_workspace: VideoWorkspace | None,
        workspace_patch: Mapping[str, JsonValue] | None,
        expected_workspace_revision: int | None,
        orchestration_mode: str,
        orchestration_version: int,
        runtime_patch: Mapping[str, object],
        now: datetime,
    ) -> VideoWorkspace:
        """同一数据库事务内写入 Workspace 并切换 conversation orchestration_mode。"""

        from pixelflow.tasks.model import PixelFlowConversationRow
        from pixelflow.tasks.store import (
            _check_conversation_revision,
            _patch_agent_runtime_context,
            _require_conversation_revision,
        )

        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                conversation = (
                    await session.execute(
                        select(PixelFlowConversationRow)
                        .where(
                            PixelFlowConversationRow.conversation_id
                            == conversation_id,
                            PixelFlowConversationRow.user_id == owner,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if conversation is None:
                    raise AgentRuntimeRecordConflictError("会话不存在，无法升级")
                _check_conversation_revision(
                    conversation.revision,
                    expected_conversation_revision,
                )

                workspace_row = (
                    await session.execute(
                        select(PixelFlowVideoAgentWorkspaceRow)
                        .where(
                            PixelFlowVideoAgentWorkspaceRow.workspace_id
                            == workspace_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()

                if create_workspace is not None:
                    if workspace_row is not None:
                        if workspace_row.user_id != owner:
                            raise AgentRuntimeRecordConflictError(
                                "VideoAgent workspace 已属于其他用户"
                            )
                        stored_workspace = _workspace_from_row(workspace_row)
                    else:
                        stored = create_workspace.model_copy(
                            update={
                                "created_at": _stored_time(create_workspace.created_at),
                                "updated_at": _stored_time(create_workspace.updated_at),
                            }
                        )
                        session.add(
                            PixelFlowVideoAgentWorkspaceRow(
                                workspace_id=stored.workspace_id,
                                conversation_id=stored.conversation_id,
                                user_id=owner,
                                revision=stored.revision,
                                payload_json=stored.payload,
                                created_at=stored.created_at,
                                updated_at=stored.updated_at,
                            )
                        )
                        await session.flush()
                        workspace_row = await session.get(
                            PixelFlowVideoAgentWorkspaceRow,
                            stored.workspace_id,
                        )
                        assert workspace_row is not None
                        stored_workspace = _workspace_from_row(workspace_row)
                else:
                    if workspace_row is None or workspace_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError(
                            "VideoAgent workspace 不存在或不属于当前用户"
                        )
                    stored_workspace = _workspace_from_row(workspace_row)
                    if workspace_patch:
                        if expected_workspace_revision is None:
                            raise AgentRuntimeRecordConflictError(
                                "VideoAgent workspace revision 无效"
                            )
                        normalized_patch = _workspace_patch(workspace_patch)
                        if _is_workspace_patch_replay(
                            stored_workspace,
                            normalized_patch,
                            expected_revision=expected_workspace_revision,
                        ):
                            pass
                        elif stored_workspace.revision != expected_workspace_revision:
                            raise AgentRuntimeRecordConflictError(
                                "VideoAgent workspace revision 已变化"
                            )
                        else:
                            updated = _updated_workspace(
                                stored_workspace,
                                normalized_patch,
                                now=now,
                            )
                            workspace_row.revision = updated.revision
                            workspace_row.payload_json = updated.payload
                            workspace_row.updated_at = updated.updated_at
                            await session.flush()
                            stored_workspace = _workspace_from_row(workspace_row)

                conversation.orchestration_mode = orchestration_mode
                conversation.orchestration_version = orchestration_version
                conversation.context_json = _patch_agent_runtime_context(
                    conversation.context_json,
                    dict(runtime_patch),
                )
                conversation.revision = (
                    _require_conversation_revision(conversation.revision) + 1
                )
                conversation.updated_at = now
                await session.flush()
                return stored_workspace

    async def create_workspace(self, user_id: str, workspace: VideoWorkspace) -> VideoWorkspace:
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.get(PixelFlowVideoAgentWorkspaceRow, workspace.workspace_id)
                if existing is not None:
                    if existing.user_id != owner:
                        raise AgentRuntimeRecordConflictError("VideoAgent workspace 已属于其他用户")
                    return _workspace_from_row(existing)
                stored = workspace.model_copy(
                    update={
                        "created_at": _stored_time(workspace.created_at),
                        "updated_at": _stored_time(workspace.updated_at),
                    }
                )
                session.add(
                    PixelFlowVideoAgentWorkspaceRow(
                        workspace_id=stored.workspace_id,
                        conversation_id=stored.conversation_id,
                        user_id=owner,
                        revision=stored.revision,
                        payload_json=stored.payload,
                        created_at=stored.created_at,
                        updated_at=stored.updated_at,
                    )
                )
        return stored

    async def get_workspace(self, user_id: str, workspace_id: str) -> VideoWorkspace | None:
        statement = select(PixelFlowVideoAgentWorkspaceRow).where(
            PixelFlowVideoAgentWorkspaceRow.user_id == _owner(user_id),
            PixelFlowVideoAgentWorkspaceRow.workspace_id == workspace_id,
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _workspace_from_row(row)

    async def discard_workspace(self, user_id: str, workspace_id: str) -> None:
        """补偿删除：升级失败时回滚新建的 Workspace。"""
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowVideoAgentWorkspaceRow, workspace_id)
                if row is None:
                    return
                if row.user_id != owner:
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent workspace 不存在或不属于当前用户"
                    )
                await session.delete(row)

    async def load_conversation_state(
        self,
        user_id: str,
        conversation_id: str,
    ) -> tuple[VideoWorkspace, AgentPlan | None] | None:
        """在同一数据库会话中读取工作区、最新计划和有序步骤。"""

        owner = _owner(user_id)
        workspace_statement = (
            select(PixelFlowVideoAgentWorkspaceRow)
            .where(
                PixelFlowVideoAgentWorkspaceRow.user_id == owner,
                PixelFlowVideoAgentWorkspaceRow.conversation_id == conversation_id,
            )
            .order_by(PixelFlowVideoAgentWorkspaceRow.workspace_id)
        )
        async with self._session_factory() as session:
            async with session.begin():
                workspace_rows = list((await session.scalars(workspace_statement)).all())
                if not workspace_rows:
                    return None
                plan_ids_statement = select(PixelFlowVideoAgentPlanRow.workspace_id).where(
                    PixelFlowVideoAgentPlanRow.user_id == owner,
                    PixelFlowVideoAgentPlanRow.conversation_id == conversation_id,
                )
                plan_workspace_ids = {
                    workspace_id
                    for workspace_id in (await session.scalars(plan_ids_statement)).all()
                    if isinstance(workspace_id, str) and workspace_id
                }
                workspace_row = _pick_authoritative_workspace(
                    conversation_id,
                    workspace_rows,
                    workspace_id_of=lambda item: item.workspace_id,
                    updated_at_of=lambda item: item.updated_at,
                    plan_workspace_ids=plan_workspace_ids,
                )
                for orphan in workspace_rows:
                    if (
                        orphan.workspace_id != workspace_row.workspace_id
                        and orphan.workspace_id not in plan_workspace_ids
                    ):
                        await session.delete(orphan)
                plan_statement = (
                    select(PixelFlowVideoAgentPlanRow)
                    .where(
                        PixelFlowVideoAgentPlanRow.user_id == owner,
                        PixelFlowVideoAgentPlanRow.conversation_id == conversation_id,
                        PixelFlowVideoAgentPlanRow.workspace_id
                        == workspace_row.workspace_id,
                    )
                    .order_by(
                        PixelFlowVideoAgentPlanRow.updated_at.desc(),
                        PixelFlowVideoAgentPlanRow.created_at.desc(),
                        PixelFlowVideoAgentPlanRow.plan_id.desc(),
                    )
                    .limit(1)
                )
                plan_row = (await session.scalars(plan_statement)).one_or_none()
                if plan_row is None:
                    return _workspace_from_row(workspace_row), None
                step_statement = (
                    select(PixelFlowVideoAgentPlanStepRow)
                    .where(
                        PixelFlowVideoAgentPlanStepRow.user_id == owner,
                        PixelFlowVideoAgentPlanStepRow.plan_id == plan_row.plan_id,
                    )
                    .order_by(PixelFlowVideoAgentPlanStepRow.sequence)
                )
                step_rows = (await session.scalars(step_statement)).all()
                plan = _plan_from_row(plan_row).model_copy(
                    update={"steps": tuple(_step_from_row(row) for row in step_rows)}
                )
                return _workspace_from_row(workspace_row), plan

    async def list_conversation_plans(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[AgentPlan]:
        owner = _owner(user_id)
        workspace_statement = (
            select(PixelFlowVideoAgentWorkspaceRow)
            .where(
                PixelFlowVideoAgentWorkspaceRow.user_id == owner,
                PixelFlowVideoAgentWorkspaceRow.conversation_id == conversation_id,
            )
            .order_by(PixelFlowVideoAgentWorkspaceRow.workspace_id)
        )
        async with self._session_factory() as session:
            async with session.begin():
                workspace_rows = list((await session.scalars(workspace_statement)).all())
                if not workspace_rows:
                    return []
                plan_ids_statement = select(PixelFlowVideoAgentPlanRow.workspace_id).where(
                    PixelFlowVideoAgentPlanRow.user_id == owner,
                    PixelFlowVideoAgentPlanRow.conversation_id == conversation_id,
                )
                plan_workspace_ids = {
                    workspace_id
                    for workspace_id in (await session.scalars(plan_ids_statement)).all()
                    if isinstance(workspace_id, str) and workspace_id
                }
                workspace_row = _pick_authoritative_workspace(
                    conversation_id,
                    workspace_rows,
                    workspace_id_of=lambda item: item.workspace_id,
                    updated_at_of=lambda item: item.updated_at,
                    plan_workspace_ids=plan_workspace_ids,
                )
                for orphan in workspace_rows:
                    if (
                        orphan.workspace_id != workspace_row.workspace_id
                        and orphan.workspace_id not in plan_workspace_ids
                    ):
                        await session.delete(orphan)
                plan_statement = (
                    select(PixelFlowVideoAgentPlanRow)
                    .where(
                        PixelFlowVideoAgentPlanRow.user_id == owner,
                        PixelFlowVideoAgentPlanRow.conversation_id == conversation_id,
                        PixelFlowVideoAgentPlanRow.workspace_id
                        == workspace_row.workspace_id,
                    )
                    .order_by(
                        PixelFlowVideoAgentPlanRow.created_at.asc(),
                        PixelFlowVideoAgentPlanRow.plan_id.asc(),
                    )
                )
                plan_rows = list((await session.scalars(plan_statement)).all())
                if not plan_rows:
                    return []
                plan_ids = [row.plan_id for row in plan_rows]
                step_statement = (
                    select(PixelFlowVideoAgentPlanStepRow)
                    .where(
                        PixelFlowVideoAgentPlanStepRow.user_id == owner,
                        PixelFlowVideoAgentPlanStepRow.plan_id.in_(plan_ids),
                    )
                    .order_by(
                        PixelFlowVideoAgentPlanStepRow.plan_id,
                        PixelFlowVideoAgentPlanStepRow.sequence,
                    )
                )
                step_rows = (await session.scalars(step_statement)).all()
                steps_by_plan: dict[str, list[AgentPlanStep]] = {
                    plan_id: [] for plan_id in plan_ids
                }
                for row in step_rows:
                    steps_by_plan.setdefault(row.plan_id, []).append(_step_from_row(row))
                return [
                    _plan_from_row(plan_row).model_copy(
                        update={"steps": tuple(steps_by_plan.get(plan_row.plan_id, []))}
                    )
                    for plan_row in plan_rows
                ]

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
        async with self._session_factory() as session:
            async with session.begin():
                statement = (
                    select(PixelFlowVideoAgentWorkspaceRow)
                    .where(
                        PixelFlowVideoAgentWorkspaceRow.user_id == owner,
                        PixelFlowVideoAgentWorkspaceRow.workspace_id == workspace_id,
                    )
                    .with_for_update()
                )
                row = (await session.scalars(statement)).one_or_none()
                if row is None:
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent workspace 不存在或不属于当前用户"
                    )
                workspace = _workspace_from_row(row)
                if _is_workspace_patch_replay(
                    workspace,
                    normalized_patch,
                    expected_revision=expected_revision,
                ):
                    return workspace
                if workspace.revision != expected_revision:
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent workspace revision 已变化"
                    )
                updated = _updated_workspace(
                    workspace,
                    normalized_patch,
                    now=now,
                )
                row.revision = updated.revision
                row.payload_json = updated.payload
                row.updated_at = updated.updated_at
                await session.flush()
                return _workspace_from_row(row)

    async def get_plan(self, user_id: str, plan_id: str) -> AgentPlan | None:
        owner = _owner(user_id)
        statement = select(PixelFlowVideoAgentPlanRow).where(
            PixelFlowVideoAgentPlanRow.user_id == owner,
            PixelFlowVideoAgentPlanRow.plan_id == plan_id,
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        if row is None:
            return None
        steps = await self.list_plan_steps(owner, plan_id)
        return _plan_from_row(row).model_copy(update={"steps": tuple(steps)})

    async def save_plan(
        self,
        user_id: str,
        plan: AgentPlan,
        steps: list[AgentPlanStep],
    ) -> AgentPlan:
        owner = _owner(user_id)
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
        async with self._session_factory() as session:
            async with session.begin():
                workspace = await session.get(PixelFlowVideoAgentWorkspaceRow, plan.workspace_id)
                if workspace is None or workspace.user_id != owner:
                    raise AgentRuntimeRecordConflictError("VideoAgent workspace 不存在或不属于当前用户")
                existing = await session.get(PixelFlowVideoAgentPlanRow, plan.plan_id)
                if existing is not None:
                    if existing.user_id != owner:
                        raise AgentRuntimeRecordConflictError("VideoAgent plan 已属于其他用户")
                    return _plan_from_row(existing)
                stored = plan.model_copy(
                    update={
                        "steps": tuple(steps),
                        "created_at": _stored_time(plan.created_at),
                        "updated_at": _stored_time(plan.updated_at),
                    }
                )
                session.add(
                    PixelFlowVideoAgentPlanRow(
                        plan_id=stored.plan_id,
                        workspace_id=stored.workspace_id,
                        conversation_id=stored.conversation_id,
                        user_id=owner,
                        status=stored.status.value,
                        public_goal=stored.public_goal,
                        created_at=stored.created_at,
                        updated_at=stored.updated_at,
                    )
                )
                for step in steps:
                    session.add(
                        PixelFlowVideoAgentPlanStepRow(
                            plan_id=step.plan_id,
                            step_id=step.step_id,
                            sequence=step.sequence,
                            user_id=owner,
                            tool_name=step.tool_name,
                            title=step.title,
                            status=step.status.value,
                            arguments_json=step.arguments,
                            confirmation_required=step.confirmation_required,
                            public_summary=step.public_summary,
                            artifact_refs_json=list(step.artifact_refs),
                            started_at=step.started_at,
                            completed_at=step.completed_at,
                        )
                    )
        return stored

    async def update_plan_status(
        self,
        user_id: str,
        plan_id: str,
        status: AgentPlanStatus,
        *,
        now: datetime,
    ) -> AgentPlan:
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                statement = (
                    select(PixelFlowVideoAgentPlanRow)
                    .where(
                        PixelFlowVideoAgentPlanRow.user_id == owner,
                        PixelFlowVideoAgentPlanRow.plan_id == plan_id,
                    )
                    .with_for_update()
                )
                row = (await session.scalars(statement)).one_or_none()
                if row is None:
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent plan 不存在或不属于当前用户"
                    )
                _assert_plan_transition(AgentPlanStatus(row.status), status)
                row.status = status.value
                row.updated_at = now
                await session.flush()
                plan = _plan_from_row(row)
        steps = await self.list_plan_steps(owner, plan_id)
        return plan.model_copy(update={"steps": tuple(steps)})

    async def start_step(
        self, user_id: str, plan_id: str, step_id: str, *, now: datetime
    ) -> AgentPlanStep:
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_step(session, owner, plan_id, step_id)
                step = _step_from_row(row)
                if step.status is PlanStepStatus.RUNNING:
                    if step.started_at == now:
                        return step
                    row.started_at = now
                    await session.flush()
                    return _step_from_row(row)
                if step.status is not PlanStepStatus.PENDING:
                    raise AgentRuntimeRecordConflictError("VideoAgent step 不能开始")
                if step.confirmation_required:
                    raise AgentRuntimeRecordConflictError("VideoAgent step 需先确认才能开始")
                row.status = PlanStepStatus.RUNNING.value
                row.started_at = now
                await session.flush()
                return _step_from_row(row)

    async def request_step_confirmation(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
    ) -> AgentPlanStep:
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_step(session, owner, plan_id, step_id)
                step = _step_from_row(row)
                if step.status is PlanStepStatus.AWAITING_CONFIRMATION:
                    return step
                if (
                    step.status is not PlanStepStatus.PENDING
                    or not step.confirmation_required
                ):
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent step 不能请求确认"
                    )
                row.status = PlanStepStatus.AWAITING_CONFIRMATION.value
                await session.flush()
                return _step_from_row(row)

    async def confirm_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlanStep:
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_step(session, owner, plan_id, step_id)
                step = _step_from_row(row)
                if step.status is PlanStepStatus.RUNNING:
                    return step
                if step.status is not PlanStepStatus.AWAITING_CONFIRMATION:
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent step 当前不等待确认"
                    )
                row.status = PlanStepStatus.RUNNING.value
                row.started_at = now
                await session.flush()
                return _step_from_row(row)

    async def cancel_step_confirmation(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlan:
        """在同一SQL事务中跳过确认步骤并取消计划。"""

        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                plan_statement = (
                    select(PixelFlowVideoAgentPlanRow)
                    .where(
                        PixelFlowVideoAgentPlanRow.user_id == owner,
                        PixelFlowVideoAgentPlanRow.plan_id == plan_id,
                    )
                    .with_for_update()
                )
                plan_row = (await session.scalars(plan_statement)).one_or_none()
                if plan_row is None:
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent plan不存在或不属于当前用户"
                    )
                if AgentPlanStatus(plan_row.status) is AgentPlanStatus.CANCELLED:
                    plan = _plan_from_row(plan_row)
                else:
                    step_row = await self._locked_step(
                        session,
                        owner,
                        plan_id,
                        step_id,
                    )
                    if (
                        AgentPlanStatus(plan_row.status)
                        is not AgentPlanStatus.AWAITING_CONFIRMATION
                        or PlanStepStatus(step_row.status)
                        is not PlanStepStatus.AWAITING_CONFIRMATION
                    ):
                        raise AgentRuntimeRecordConflictError(
                            "VideoAgent step当前不能取消确认"
                        )
                    step_row.status = PlanStepStatus.SKIPPED.value
                    step_row.public_summary = "用户已取消执行"
                    step_row.started_at = now
                    step_row.completed_at = now
                    plan_row.status = AgentPlanStatus.CANCELLED.value
                    plan_row.updated_at = now
                    await session.flush()
                    plan = _plan_from_row(plan_row)
        steps = await self.list_plan_steps(owner, plan_id)
        return plan.model_copy(update={"steps": tuple(steps)})

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
        """在同一SQL事务中取消额度中断对应步骤、Plan和卡片。"""

        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                plan_row = (
                    await session.scalars(
                        select(PixelFlowVideoAgentPlanRow)
                        .where(
                            PixelFlowVideoAgentPlanRow.user_id == owner,
                            PixelFlowVideoAgentPlanRow.plan_id == plan_id,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if plan_row is None:
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent plan不存在或不属于当前用户"
                    )
                workspace_row = (
                    await session.scalars(
                        select(PixelFlowVideoAgentWorkspaceRow)
                        .where(
                            PixelFlowVideoAgentWorkspaceRow.user_id == owner,
                            PixelFlowVideoAgentWorkspaceRow.workspace_id
                            == plan_row.workspace_id,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                step_row = await self._locked_step(
                    session,
                    owner,
                    plan_id,
                    step_id,
                )
                payload = (
                    dict(workspace_row.payload_json)
                    if workspace_row is not None
                    else {}
                )
                interrupt = payload.get("quota_interrupt")
                resolution = payload.get("last_quota_resolution")
                if (
                    AgentPlanStatus(plan_row.status)
                    is AgentPlanStatus.CANCELLED
                    and PlanStepStatus(step_row.status)
                    is PlanStepStatus.SKIPPED
                    and interrupt is None
                    and isinstance(resolution, Mapping)
                    and resolution.get("event_id") == quota_interrupt_id
                    and resolution.get("job_id") == job_id
                    and resolution.get("quota_pause_revision")
                    == quota_pause_revision
                    and resolution.get("state") == "cancelled"
                ):
                    plan = _plan_from_row(plan_row)
                    replay = True
                else:
                    replay = False
                if (
                    not replay
                    and (
                        workspace_row is None
                        or PlanStepStatus(step_row.status)
                        is not PlanStepStatus.RUNNING
                        or not isinstance(interrupt, Mapping)
                        or interrupt.get("quota_interrupt_id")
                        != quota_interrupt_id
                        or interrupt.get("plan_id") != plan_id
                        or interrupt.get("step_id") != step_id
                        or interrupt.get("job_id") != job_id
                        or interrupt.get("quota_pause_revision")
                        != quota_pause_revision
                    )
                ):
                    raise AgentRuntimeRecordConflictError(
                        "VideoAgent额度取消与当前权威状态不匹配"
                    )
                if not replay:
                    _assert_plan_transition(
                        AgentPlanStatus(plan_row.status),
                        AgentPlanStatus.CANCELLED,
                    )
                    step_row.status = PlanStepStatus.SKIPPED.value
                    step_row.public_summary = "用户已取消额度中断任务"
                    step_row.completed_at = now
                    plan_row.status = AgentPlanStatus.CANCELLED.value
                    plan_row.updated_at = now
                    payload["quota_interrupt"] = None
                    payload["last_quota_resolution"] = {
                        "event_id": quota_interrupt_id,
                        "job_id": job_id,
                        "quota_pause_revision": quota_pause_revision,
                        "state": "cancelled",
                    }
                    workspace_row.payload_json = payload
                    workspace_row.revision += 1
                    workspace_row.updated_at = now
                    await session.flush()
                    plan = _plan_from_row(plan_row)
        steps = await self.list_plan_steps(owner, plan_id)
        return plan.model_copy(update={"steps": tuple(steps)})

    async def cancel_active_script_skill_plans(
        self,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
    ) -> list[AgentPlan]:
        """确认脚本成片后取消仍在跑的脚本 Skill 计划，避免假忙碌。"""

        owner = _owner(user_id)
        conversation = conversation_id.strip()
        plans = await self.list_conversation_plans(owner, conversation)
        cancelled_plans: list[AgentPlan] = []
        for plan in plans:
            if not _is_active_script_skill_plan(plan):
                continue
            async with self._session_factory() as session:
                async with session.begin():
                    plan_row = (
                        await session.scalars(
                            select(PixelFlowVideoAgentPlanRow)
                            .where(
                                PixelFlowVideoAgentPlanRow.user_id == owner,
                                PixelFlowVideoAgentPlanRow.plan_id == plan.plan_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    if plan_row is None:
                        continue
                    current_status = AgentPlanStatus(plan_row.status)
                    if current_status is AgentPlanStatus.CANCELLED:
                        continue
                    if current_status not in _ACTIVE_SCRIPT_PLAN_STATUSES:
                        continue
                    _assert_plan_transition(current_status, AgentPlanStatus.CANCELLED)
                    step_rows = (
                        await session.scalars(
                            select(PixelFlowVideoAgentPlanStepRow)
                            .where(
                                PixelFlowVideoAgentPlanStepRow.user_id == owner,
                                PixelFlowVideoAgentPlanStepRow.plan_id == plan.plan_id,
                            )
                            .order_by(PixelFlowVideoAgentPlanStepRow.sequence)
                            .with_for_update()
                        )
                    ).all()
                    for step_row in step_rows:
                        status = PlanStepStatus(step_row.status)
                        if status not in _OPEN_SCRIPT_STEP_STATUSES:
                            continue
                        step_row.status = PlanStepStatus.SKIPPED.value
                        step_row.public_summary = _SCRIPT_SKILL_SUPERSEDED_SUMMARY
                        if step_row.started_at is None:
                            step_row.started_at = now
                        step_row.completed_at = now
                    plan_row.status = AgentPlanStatus.CANCELLED.value
                    plan_row.updated_at = now
                    await session.flush()
                    cancelled_plans.append(_plan_from_row(plan_row))
        result: list[AgentPlan] = []
        for plan in cancelled_plans:
            steps = await self.list_plan_steps(owner, plan.plan_id)
            result.append(plan.model_copy(update={"steps": tuple(steps)}))
        return result

    async def cancel_waiting_for_input_plans(
        self,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
        exclude_plan_id: str | None = None,
    ) -> list[AgentPlan]:
        """新 Turn 推进前取消旧的等待补充 Plan，避免时间线叠多个等待卡。"""

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
            async with self._session_factory() as session:
                async with session.begin():
                    plan_row = (
                        await session.scalars(
                            select(PixelFlowVideoAgentPlanRow)
                            .where(
                                PixelFlowVideoAgentPlanRow.user_id == owner,
                                PixelFlowVideoAgentPlanRow.plan_id == plan.plan_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    if plan_row is None:
                        continue
                    current_status = AgentPlanStatus(plan_row.status)
                    if current_status is not AgentPlanStatus.WAITING_FOR_INPUT:
                        continue
                    _assert_plan_transition(current_status, AgentPlanStatus.CANCELLED)
                    plan_row.status = AgentPlanStatus.CANCELLED.value
                    plan_row.updated_at = now
                    await session.flush()
                    cancelled_plans.append(_plan_from_row(plan_row))
        result: list[AgentPlan] = []
        for plan in cancelled_plans:
            steps = await self.list_plan_steps(owner, plan.plan_id)
            result.append(plan.model_copy(update={"steps": tuple(steps)}))
        return result

    async def start_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]:
        return await self._transition_step_with_event(
            user_id,
            plan_id,
            step_id,
            run_id=run_id,
            now=now,
            result=None,
        )

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
        """在同一 SQL 事务中更新运行中步骤摘要并写入 progressed 事件。"""

        owner = _owner(user_id)
        summary = public_summary.strip()
        phase = progress_phase.strip()
        if not summary or not phase:
            raise ValueError("进度摘要与阶段标识不能为空")
        event_id = _step_event_id_from_parts(
            plan_id,
            step_id,
            phase=f"progressed:{phase}",
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    row = await self._locked_step(session, owner, plan_id, step_id)
                    plan_row = await session.get(PixelFlowVideoAgentPlanRow, plan_id)
                    if plan_row is None or plan_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError(
                            "VideoAgent plan 不存在或不属于当前用户"
                        )
                    if row.status != PlanStepStatus.RUNNING.value:
                        raise AgentRuntimeRecordConflictError(
                            "VideoAgent step 当前不能推送进度"
                        )
                    if row.public_summary != summary:
                        row.public_summary = summary
                        await session.flush()
                    step = _step_from_row(row)
                    existing_event = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow).where(
                                PixelFlowAgentEventRow.event_id == event_id,
                            )
                        )
                    ).one_or_none()
                    if existing_event is not None:
                        if existing_event.user_id != owner:
                            raise AgentRuntimeRecordConflictError(
                                "VideoAgent event 已属于其他用户"
                            )
                        return step, _event_from_row(existing_event)
                    last_event = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow)
                            .where(
                                PixelFlowAgentEventRow.conversation_id
                                == plan_row.conversation_id
                            )
                            .order_by(PixelFlowAgentEventRow.sequence.desc())
                            .limit(1)
                            .with_for_update()
                        )
                    ).first()
                    if last_event is not None and last_event.user_id != owner:
                        raise AgentRuntimeRecordConflictError(
                            "AgentEvent conversation 已被其他所有者占用"
                        )
                    event = build_step_progressed_event(
                        event_id=event_id,
                        cursor=f"cursor_{event_id[4:]}",
                        sequence=1 if last_event is None else last_event.sequence + 1,
                        conversation_id=plan_row.conversation_id,
                        run_id=run_id,
                        occurred_at=now,
                        step=step,
                        progress_phase=phase,
                    )
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=event.schema_version,
                            event_id=event.event_id,
                            sequence=event.sequence,
                            cursor=event.cursor,
                            conversation_id=event.conversation_id,
                            user_id=owner,
                            run_id=event.run_id,
                            occurred_at=event.occurred_at,
                            event_type=event.type.value,
                            payload_json=event.payload,
                            delivery_status="pending",
                            delivery_attempts=0,
                        )
                    )
                    await session.flush()
                    return step, event
        except IntegrityError:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent step event 已存在或 sequence 冲突"
            ) from None

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
        return await self._transition_step_with_event(
            user_id,
            plan_id,
            step_id,
            run_id=run_id,
            now=now,
            result=result,
        )

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
        """在同一SQL事务中保存步骤失败状态与安全失败事件。"""

        owner = _owner(user_id)
        event_id = _step_event_id_from_parts(
            plan_id,
            step_id,
            phase="failed",
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    row = await self._locked_step(
                        session,
                        owner,
                        plan_id,
                        step_id,
                    )
                    plan_row = await session.get(PixelFlowVideoAgentPlanRow, plan_id)
                    if plan_row is None or plan_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError(
                            "VideoAgent plan 不存在或不属于当前用户"
                        )
                    _assert_plan_transition(
                        AgentPlanStatus(plan_row.status),
                        AgentPlanStatus.FAILED,
                    )
                    existing_event = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow).where(
                                PixelFlowAgentEventRow.event_id == event_id,
                            )
                        )
                    ).one_or_none()
                    if row.status == PlanStepStatus.RUNNING.value:
                        row.status = PlanStepStatus.FAILED.value
                        row.public_summary = public_summary
                        row.completed_at = now
                        await session.flush()
                    elif row.status != PlanStepStatus.FAILED.value:
                        raise AgentRuntimeRecordConflictError(
                            "VideoAgent step 当前不能标记失败"
                        )
                    plan_row.status = AgentPlanStatus.FAILED.value
                    plan_row.updated_at = now
                    step = _step_from_row(row)
                    if existing_event is not None:
                        if existing_event.user_id != owner:
                            raise AgentRuntimeRecordConflictError(
                                "VideoAgent event 已属于其他用户"
                            )
                        return step, _event_from_row(existing_event)
                    last_event = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow)
                            .where(
                                PixelFlowAgentEventRow.conversation_id
                                == plan_row.conversation_id
                            )
                            .order_by(PixelFlowAgentEventRow.sequence.desc())
                            .limit(1)
                            .with_for_update()
                        )
                    ).first()
                    if last_event is not None and last_event.user_id != owner:
                        raise AgentRuntimeRecordConflictError(
                            "AgentEvent conversation 已被其他所有者占用"
                        )
                    event = build_step_failed_event(
                        event_id=event_id,
                        cursor=f"cursor_{event_id[4:]}",
                        sequence=1 if last_event is None else last_event.sequence + 1,
                        conversation_id=plan_row.conversation_id,
                        run_id=run_id,
                        occurred_at=now,
                        step=step,
                        reason_code=reason_code,
                    )
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=event.schema_version,
                            event_id=event.event_id,
                            sequence=event.sequence,
                            cursor=event.cursor,
                            conversation_id=event.conversation_id,
                            user_id=owner,
                            run_id=event.run_id,
                            occurred_at=event.occurred_at,
                            event_type=event.type.value,
                            payload_json=event.payload,
                            delivery_status="pending",
                            delivery_attempts=0,
                        )
                    )
                    await session.flush()
                    return step, event
        except IntegrityError:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent step失败事件已存在或sequence冲突"
            ) from None

    async def complete_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        result: VideoToolResult,
        *,
        now: datetime,
    ) -> AgentPlanStep:
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_step(session, owner, plan_id, step_id)
                step = _step_from_row(row)
                if step.tool_name != result.tool_name:
                    raise AgentRuntimeRecordConflictError("VideoAgent 工具结果与 step 不匹配")
                if step.status is PlanStepStatus.COMPLETED:
                    if step.public_summary == result.public_summary and step.artifact_refs == result.artifact_refs:
                        return step
                    raise AgentRuntimeRecordConflictError("VideoAgent step 已用不同结果完成")
                if step.status is not PlanStepStatus.RUNNING:
                    raise AgentRuntimeRecordConflictError("VideoAgent step 不能完成")
                row.status = PlanStepStatus.COMPLETED.value
                row.public_summary = result.public_summary
                row.artifact_refs_json = list(result.artifact_refs)
                row.completed_at = now
                await session.flush()
                return _step_from_row(row)

    async def list_plan_steps(self, user_id: str, plan_id: str) -> list[AgentPlanStep]:
        statement = (
            select(PixelFlowVideoAgentPlanStepRow)
            .where(
                PixelFlowVideoAgentPlanStepRow.user_id == _owner(user_id),
                PixelFlowVideoAgentPlanStepRow.plan_id == plan_id,
            )
            .order_by(PixelFlowVideoAgentPlanStepRow.sequence)
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_step_from_row(row) for row in rows]

    async def _transition_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        run_id: str,
        now: datetime,
        result: VideoToolResult | None,
    ) -> tuple[AgentPlanStep, AgentEvent]:
        owner = _owner(user_id)
        completed = result is not None
        event_id = _step_event_id_from_parts(
            plan_id,
            step_id,
            completed=completed,
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    row = await self._locked_step(session, owner, plan_id, step_id)
                    plan_row = await session.get(PixelFlowVideoAgentPlanRow, plan_id)
                    if plan_row is None or plan_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError("VideoAgent plan 不存在或不属于当前用户")
                    existing_event = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow).where(
                                PixelFlowAgentEventRow.event_id == event_id,
                            )
                        )
                    ).one_or_none()
                    if completed:
                        if result is None or row.tool_name != result.tool_name:
                            raise AgentRuntimeRecordConflictError("VideoAgent 工具结果与 step 不匹配")
                        if row.status == PlanStepStatus.COMPLETED.value:
                            step = _step_from_row(row)
                            if step.public_summary != result.public_summary or step.artifact_refs != result.artifact_refs:
                                raise AgentRuntimeRecordConflictError("VideoAgent step 已用不同结果完成")
                        elif row.status == PlanStepStatus.RUNNING.value:
                            row.status = PlanStepStatus.COMPLETED.value
                            row.public_summary = result.public_summary
                            row.artifact_refs_json = list(result.artifact_refs)
                            row.completed_at = now
                            await session.flush()
                            step = _step_from_row(row)
                        else:
                            raise AgentRuntimeRecordConflictError("VideoAgent step 不能完成")
                    else:
                        if row.status == PlanStepStatus.RUNNING.value:
                            step = _step_from_row(row)
                            if step.started_at != now:
                                row.started_at = now
                                await session.flush()
                                step = _step_from_row(row)
                        elif row.status == PlanStepStatus.PENDING.value:
                            row.status = PlanStepStatus.RUNNING.value
                            row.started_at = now
                            await session.flush()
                            step = _step_from_row(row)
                        else:
                            raise AgentRuntimeRecordConflictError("VideoAgent step 不能开始")
                    if existing_event is not None:
                        if existing_event.user_id != owner:
                            raise AgentRuntimeRecordConflictError("VideoAgent event 已属于其他用户")
                        return step, _event_from_row(existing_event)
                    last_event = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow)
                            .where(PixelFlowAgentEventRow.conversation_id == plan_row.conversation_id)
                            .order_by(PixelFlowAgentEventRow.sequence.desc())
                            .limit(1)
                            .with_for_update()
                        )
                    ).first()
                    if last_event is not None and last_event.user_id != owner:
                        raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                    event = _build_step_event(
                        step,
                        completed=completed,
                        event_id=event_id,
                        sequence=1 if last_event is None else last_event.sequence + 1,
                        conversation_id=plan_row.conversation_id,
                        run_id=run_id,
                        occurred_at=now,
                    )
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=event.schema_version,
                            event_id=event.event_id,
                            sequence=event.sequence,
                            cursor=event.cursor,
                            conversation_id=event.conversation_id,
                            user_id=owner,
                            run_id=event.run_id,
                            occurred_at=event.occurred_at,
                            event_type=event.type.value,
                            payload_json=event.payload,
                            delivery_status="pending",
                            delivery_attempts=0,
                        )
                    )
                    await session.flush()
                    return step, event
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("VideoAgent step event 已存在或 sequence 冲突") from None

    async def _locked_step(
        self,
        session: AsyncSession,
        owner: str,
        plan_id: str,
        step_id: str,
    ) -> PixelFlowVideoAgentPlanStepRow:
        statement = (
            select(PixelFlowVideoAgentPlanStepRow)
            .where(
                PixelFlowVideoAgentPlanStepRow.user_id == owner,
                PixelFlowVideoAgentPlanStepRow.plan_id == plan_id,
                PixelFlowVideoAgentPlanStepRow.step_id == step_id,
            )
            .with_for_update()
        )
        row = (await session.scalars(statement)).one_or_none()
        if row is None:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
        return row


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


def _event_from_row(row: PixelFlowAgentEventRow) -> AgentEvent:
    return AgentEvent(
        schema_version=row.schema_version,
        event_id=row.event_id,
        sequence=row.sequence,
        cursor=row.cursor,
        conversation_id=row.conversation_id,
        run_id=row.run_id,
        occurred_at=_restore_utc(row.occurred_at),
        type=AgentEventType(row.event_type),
        payload=row.payload_json,
    )


def _workspace_from_row(row: PixelFlowVideoAgentWorkspaceRow) -> VideoWorkspace:
    return VideoWorkspace(
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        revision=row.revision,
        payload=row.payload_json,
        created_at=_restore_utc(row.created_at),
        updated_at=_restore_utc(row.updated_at),
    )


def _plan_from_row(row: PixelFlowVideoAgentPlanRow) -> AgentPlan:
    return AgentPlan(
        plan_id=row.plan_id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        status=AgentPlanStatus(row.status),
        public_goal=row.public_goal,
        created_at=_restore_utc(row.created_at),
        updated_at=_restore_utc(row.updated_at),
    )


def _step_from_row(row: PixelFlowVideoAgentPlanStepRow) -> AgentPlanStep:
    return AgentPlanStep(
        step_id=row.step_id,
        plan_id=row.plan_id,
        sequence=row.sequence,
        tool_name=row.tool_name,
        title=row.title,
        status=PlanStepStatus(row.status),
        arguments=row.arguments_json,
        confirmation_required=row.confirmation_required,
        public_summary=row.public_summary,
        artifact_refs=tuple(row.artifact_refs_json),
        started_at=_restore_utc(row.started_at),
        completed_at=_restore_utc(row.completed_at),
    )


__all__ = ["SQLVideoAgentRepository"]
