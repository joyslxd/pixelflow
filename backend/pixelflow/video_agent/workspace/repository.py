"""VideoAgent workspace、plan 与 step 的双持久化实现。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pixelflow.agent_runtime.persistence.models import (
    PixelFlowVideoAgentPlanRow,
    PixelFlowVideoAgentPlanStepRow,
    PixelFlowVideoAgentWorkspaceRow,
)
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRecordConflictError
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)


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


@runtime_checkable
class VideoAgentRepository(Protocol):
    async def create_workspace(self, user_id: str, workspace: VideoWorkspace) -> VideoWorkspace: ...

    async def get_workspace(self, user_id: str, workspace_id: str) -> VideoWorkspace | None: ...

    async def save_plan(
        self,
        user_id: str,
        plan: AgentPlan,
        steps: list[AgentPlanStep],
    ) -> AgentPlan: ...

    async def start_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlanStep: ...

    async def complete_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        result: VideoToolResult,
        *,
        now: datetime,
    ) -> AgentPlanStep: ...

    async def list_plan_steps(self, user_id: str, plan_id: str) -> list[AgentPlanStep]: ...


class MemoryVideoAgentRepository:
    def __init__(self) -> None:
        self._workspace_by_owner: dict[tuple[str, str], VideoWorkspace] = {}
        self._workspace_owner_by_id: dict[str, str] = {}
        self._plan_by_owner: dict[tuple[str, str], AgentPlan] = {}
        self._plan_owner_by_id: dict[str, str] = {}
        self._steps_by_owner: dict[tuple[str, str, str], AgentPlanStep] = {}

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
        if {step.plan_id for step in steps} != {plan.plan_id} or len({step.sequence for step in steps}) != len(steps):
            raise AgentRuntimeRecordConflictError("VideoAgent plan steps 不符合当前 plan 或 sequence 唯一约束")
        stored = plan.model_copy(
            update={"created_at": _stored_time(plan.created_at), "updated_at": _stored_time(plan.updated_at)}
        )
        self._plan_owner_by_id[stored.plan_id] = owner
        self._plan_by_owner[(owner, stored.plan_id)] = _clone(stored)
        for step in steps:
            self._steps_by_owner[(owner, stored.plan_id, step.step_id)] = _clone(step)
        return _clone(stored)

    async def start_step(
        self, user_id: str, plan_id: str, step_id: str, *, now: datetime
    ) -> AgentPlanStep:
        key = (_owner(user_id), plan_id, step_id)
        step = self._steps_by_owner.get(key)
        if step is None:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不存在或不属于当前用户")
        if step.status is PlanStepStatus.RUNNING:
            return _clone(step)
        if step.status is not PlanStepStatus.PENDING:
            raise AgentRuntimeRecordConflictError("VideoAgent step 不能开始")
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

    async def list_plan_steps(self, user_id: str, plan_id: str) -> list[AgentPlanStep]:
        owner = _owner(user_id)
        steps = [
            step
            for (step_owner, stored_plan_id, _), step in self._steps_by_owner.items()
            if step_owner == owner and stored_plan_id == plan_id
        ]
        return [_clone(step) for step in sorted(steps, key=lambda item: item.sequence)]


class SQLVideoAgentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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

    async def save_plan(
        self,
        user_id: str,
        plan: AgentPlan,
        steps: list[AgentPlanStep],
    ) -> AgentPlan:
        owner = _owner(user_id)
        if {step.plan_id for step in steps} != {plan.plan_id} or len({step.sequence for step in steps}) != len(steps):
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
                    update={"created_at": _stored_time(plan.created_at), "updated_at": _stored_time(plan.updated_at)}
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
                            public_summary=step.public_summary,
                            artifact_refs_json=list(step.artifact_refs),
                            started_at=step.started_at,
                            completed_at=step.completed_at,
                        )
                    )
        return stored

    async def start_step(
        self, user_id: str, plan_id: str, step_id: str, *, now: datetime
    ) -> AgentPlanStep:
        owner = _owner(user_id)
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_step(session, owner, plan_id, step_id)
                step = _step_from_row(row)
                if step.status is PlanStepStatus.RUNNING:
                    return step
                if step.status is not PlanStepStatus.PENDING:
                    raise AgentRuntimeRecordConflictError("VideoAgent step 不能开始")
                row.status = PlanStepStatus.RUNNING.value
                row.started_at = now
                await session.flush()
                return _step_from_row(row)

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
        public_summary=row.public_summary,
        artifact_refs=tuple(row.artifact_refs_json),
        started_at=_restore_utc(row.started_at),
        completed_at=_restore_utc(row.completed_at),
    )
