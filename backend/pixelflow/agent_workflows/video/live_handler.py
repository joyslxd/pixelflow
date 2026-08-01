"""把 Supervisor 七类动作映射到视频 Workflow 权威状态机。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import Field, JsonValue, model_validator

from pixelflow.agent_runtime.contracts import (
    AgentAction,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
)
from pixelflow.agent_runtime.contracts.base import ContractModel
from pixelflow.agent_runtime.identity import interrupt_id
from pixelflow.agent_runtime.persistence import (
    StoredAgentInterrupt,
    SupervisorProjectionMessage,
    VideoRuntimeRepository,
)
from pixelflow.agent_runtime.ports import OperationPort

from .delivery import (
    VideoDeliveryWorkflowService,
    VideoDeliveryWorkflowState,
    VideoWebArtifactAdapter,
)
from .live_capabilities import (
    Clock,
    TransientTurnCredential,
    TurnCredentialProvider,
    VideoLiveCapabilityPort,
)
from .live_operations import VideoLiveOperationBridge
from .planning import (
    VideoPlanningStage,
    VideoPlanningWorkflowService,
    VideoPlanningWorkflowState,
)
from .postproduction import (
    VideoPostProductionStage,
    VideoPostProductionWorkflowService,
    VideoPostProductionWorkflowState,
)
from .scene_packages import (
    VideoScenePackageStage,
    VideoScenePackageWorkflowService,
    VideoScenePackageWorkflowState,
)
from .state_codec import (
    VideoWorkflowStateEnvelope,
    VideoWorkflowStateKind,
    decode_video_workflow_state,
    encode_video_workflow_state,
    project_video_workflow_state,
)
from .video_generation import (
    VideoSceneGenerationStage,
    VideoSceneGenerationWorkflowService,
    VideoSceneGenerationWorkflowState,
)

if TYPE_CHECKING:
    from pixelflow.agent_runtime.graph.dispatcher import WorkflowCommand

_STAGE_HANDLERS = {
    VideoWorkflowStateKind.SCENE_PACKAGE: "_dispatch_scene_package",
    VideoWorkflowStateKind.SCENE_GENERATION: "_dispatch_scene_generation",
    VideoWorkflowStateKind.POSTPRODUCTION: "_dispatch_postproduction",
    VideoWorkflowStateKind.DELIVERY: "_dispatch_delivery",
}

_INTERRUPT_UI_KINDS = {
    "video_intake_form": "video_intake_form",
    "video_direction_review": "video_direction_review",
    "video_plan_review": "video_plan_review",
    "video_scene_package_review": "video_scene_package_review",
    "video_scene_video_review": "video_result_review",
    "authorization_required": "authorization_required",
}


class VideoLiveStateConflictError(RuntimeError):
    """用固定原因码拒绝不一致、过期或不允许的 live 动作。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class WorkflowDispatchResult(ContractModel):
    """保存一次视频 Handler 派发产生的权威状态与公开投影。"""

    state: VideoWorkflowStateEnvelope
    workflow: WorkflowRecord
    messages: tuple[SupervisorProjectionMessage, ...] = ()
    interrupt: StoredAgentInterrupt | None = None
    turn_status: Literal[TurnStatus.WAITING_USER, TurnStatus.COMPLETED]
    update_active_workflow: bool = False
    active_workflow_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_active_workflow_update(self):
        if not self.update_active_workflow and self.active_workflow_id is not None:
            raise ValueError("未更新 active workflow 时不能携带 active_workflow_id")
        return self


class VideoLiveWorkflowHandler:
    """对应 Java 编排 Service，只调用冻结的 M11 Service 与能力端口。"""

    def __init__(
        self,
        *,
        repository: VideoRuntimeRepository,
        capabilities: VideoLiveCapabilityPort,
        credential_provider: TurnCredentialProvider,
        clock: Clock,
        operation_port: OperationPort | None = None,
        planning_service: VideoPlanningWorkflowService | None = None,
        scene_package_service: VideoScenePackageWorkflowService | None = None,
        generation_service: VideoSceneGenerationWorkflowService | None = None,
        postproduction_service: VideoPostProductionWorkflowService | None = None,
        delivery_service: VideoDeliveryWorkflowService | None = None,
    ) -> None:
        self._repository = repository
        self._capabilities = capabilities
        self._credential_provider = credential_provider
        self._clock = clock
        self._operation_port = operation_port
        self._planning = planning_service or VideoPlanningWorkflowService()
        self._scene_packages = (
            scene_package_service or VideoScenePackageWorkflowService()
        )
        self._generation = generation_service or VideoSceneGenerationWorkflowService(
            operation_port
        )
        self._postproduction = (
            postproduction_service or VideoPostProductionWorkflowService(operation_port)
        )
        self._delivery = delivery_service or VideoDeliveryWorkflowService(
            operation_port
        )
        self._web_artifacts = VideoWebArtifactAdapter(self._delivery)

    async def dispatch(self, command: WorkflowCommand) -> WorkflowDispatchResult:
        """读取权威状态并执行一条隔离且可幂等提交的视频命令。"""

        self._validate_command_identity(command)
        existing_envelope = await self._repository.get_video_state(
            command.user_id,
            command.workflow_id,
        )
        if command.decision.action is AgentAction.START_WORKFLOW:
            return self._start_workflow(command, existing_envelope)
        if existing_envelope is None:
            raise VideoLiveStateConflictError("video_workflow_state_required")
        state = self._decode_existing_state(command, existing_envelope)
        if command.decision.action is AgentAction.SWITCH_WORKFLOW:
            self._require_patch_keys(command, allowed=frozenset())
            return self._result_from_state(
                command,
                state,
                existing_envelope=existing_envelope,
                update_active_workflow=True,
                active_workflow_id=command.workflow_id,
            )
        if command.decision.action is AgentAction.CANCEL_WORKFLOW:
            if command.workflow is None:
                raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")
            if (
                isinstance(state, VideoPlanningWorkflowState)
                and state.current_stage is VideoPlanningStage.INTAKE
            ):
                patch = self._require_patch_keys(
                    command,
                    allowed=frozenset({"form_cancelled"}),
                    required=frozenset({"form_cancelled"}),
                )
                if patch["form_cancelled"] is not True:
                    raise VideoLiveStateConflictError("video_action_patch_invalid")
            else:
                self._require_patch_keys(command, allowed=frozenset())
            try:
                cancelled = self._cancel_state(state)
            except (TypeError, ValueError) as exc:
                raise VideoLiveStateConflictError(
                    "video_action_not_allowed_for_stage"
                ) from exc
            return self._result_from_state(
                command,
                cancelled,
                existing_envelope=existing_envelope,
                update_active_workflow=True,
                active_workflow_id=None,
            )
        if isinstance(state, VideoPlanningWorkflowState):
            return await self._dispatch_planning(
                command,
                state,
                existing_envelope=existing_envelope,
            )
        handler_name = _STAGE_HANDLERS.get(existing_envelope.state_kind)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            return await handler(
                command,
                state,
                existing_envelope=existing_envelope,
            )
        raise VideoLiveStateConflictError("video_action_not_implemented")

    def _cancel_state(self, state):
        """按具体领域类型调用取消 Service，不直接改写嵌套权威字段。"""

        now = self._clock.now()
        if isinstance(state, VideoPlanningWorkflowState):
            return self._planning.cancel(state, now=now)
        if isinstance(state, VideoScenePackageWorkflowState):
            return self._scene_packages.cancel(state, now=now)
        if isinstance(state, VideoSceneGenerationWorkflowState):
            return self._generation.cancel(state, now=now)
        if isinstance(state, VideoPostProductionWorkflowState):
            return self._postproduction.cancel(state, now=now)
        if isinstance(state, VideoDeliveryWorkflowState):
            return self._delivery.cancel(
                state,
                postproduction_service=self._postproduction,
                now=now,
            )
        raise VideoLiveStateConflictError("video_state_kind_mismatch")

    async def _dispatch_postproduction(
        self,
        command: WorkflowCommand,
        state: VideoPostProductionWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if not isinstance(state, VideoPostProductionWorkflowState):
            raise VideoLiveStateConflictError("video_state_kind_mismatch")
        action = command.decision.action
        try:
            if (
                action is AgentAction.RETRY_FAILED
                and state.current_stage is VideoPostProductionStage.MERGE_VIDEO
            ):
                self._require_patch_keys(command, allowed=frozenset())
                live_operations = self._live_operation_bridge()
                credential = (
                    None
                    if live_operations is None
                    else self._credential_provider.get(command.turn_id)
                )
                if live_operations is not None and credential is None:
                    return self._wait_for_authorization(
                        command,
                        state,
                        existing_envelope=existing_envelope,
                    )
                operation_port = self._operation_port_for(command)
                try:
                    updated = await self._postproduction.retry_merge(
                        state,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    if live_operations is not None:
                        assert credential is not None
                        updated = await self._start_postproduction_operation(
                            command,
                            updated,
                            operations=live_operations,
                            operation_port=operation_port,
                            credential=credential,
                        )
                finally:
                    if credential is not None:
                        credential.discard()
                return self._result_from_state(
                    command,
                    updated,
                    existing_envelope=existing_envelope,
                )
            if (
                action is AgentAction.RETRY_FAILED
                and state.current_stage is VideoPostProductionStage.QUALITY_REVIEW
            ):
                self._require_patch_keys(command, allowed=frozenset())
                live_operations = self._live_operation_bridge()
                credential = (
                    None
                    if live_operations is None
                    else self._credential_provider.get(command.turn_id)
                )
                if live_operations is not None and credential is None:
                    return self._wait_for_authorization(
                        command,
                        state,
                        existing_envelope=existing_envelope,
                    )
                operation_port = self._operation_port_for(command)
                try:
                    updated = await self._postproduction.retry_quality_review(
                        state,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    if live_operations is not None:
                        assert credential is not None
                        updated = await self._start_postproduction_operation(
                            command,
                            updated,
                            operations=live_operations,
                            operation_port=operation_port,
                            credential=credential,
                        )
                finally:
                    if credential is not None:
                        credential.discard()
                return self._result_from_state(
                    command,
                    updated,
                    existing_envelope=existing_envelope,
                )
            if state.current_stage is VideoPostProductionStage.VIDEO_REVIEW:
                if action is AgentAction.CONTINUE_WORKFLOW:
                    self._require_patch_keys(command, allowed=frozenset())
                    operation_port = self._operation_port_for(command)
                    finished = await self._postproduction.finish(
                        state,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    delivery = await self._delivery.initialize(
                        finished,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    return self._result_from_state(
                        command,
                        delivery,
                        existing_envelope=existing_envelope,
                        artifact=self._web_artifacts.project(delivery),
                    )
                if action is AgentAction.MODIFY_WORKFLOW:
                    patch = self._require_patch_keys(
                        command,
                        allowed=frozenset(
                            {
                                "scene_patches",
                                "user_feedback",
                                "jianying_action",
                                "project_name",
                            }
                        ),
                    )
                    if set(patch) == {"user_feedback"}:
                        live_operations = self._live_operation_bridge()
                        credential = (
                            None
                            if live_operations is None
                            else self._credential_provider.get(command.turn_id)
                        )
                        if live_operations is not None and credential is None:
                            return self._wait_for_authorization(
                                command,
                                state,
                                existing_envelope=existing_envelope,
                            )
                        operation_port = self._operation_port_for(command)
                        try:
                            updated = await self._postproduction.start_quality_review(
                                state,
                                user_feedback=self._required_text(
                                    patch["user_feedback"],
                                    "user_feedback",
                                ),
                                operation_port=operation_port,
                                now=self._clock.now(),
                            )
                            if live_operations is not None:
                                assert credential is not None
                                updated = await self._start_postproduction_operation(
                                    command,
                                    updated,
                                    operations=live_operations,
                                    operation_port=operation_port,
                                    credential=credential,
                                )
                        finally:
                            if credential is not None:
                                credential.discard()
                    elif set(patch) == {"scene_patches"}:
                        updated = await self._postproduction.apply_user_revision(
                            state,
                            scene_patches=self._scene_patch_map(
                                patch["scene_patches"]
                            ),
                            generation_service=self._generation,
                            operation_port=self._operation_port_for(command),
                            now=self._clock.now(),
                        )
                    elif set(patch) in (
                        {"jianying_action"},
                        {"jianying_action", "project_name"},
                    ) and patch["jianying_action"] == "start":
                        live_operations = self._live_operation_bridge()
                        credential = (
                            None
                            if live_operations is None
                            else self._credential_provider.get(command.turn_id)
                        )
                        if live_operations is not None and credential is None:
                            return self._wait_for_authorization(
                                command,
                                state,
                                existing_envelope=existing_envelope,
                            )
                        operation_port = self._operation_port_for(command)
                        try:
                            delivery = await self._delivery.initialize(
                                state,
                                operation_port=operation_port,
                                now=self._clock.now(),
                            )
                            project_name = (
                                None
                                if "project_name" not in patch
                                else self._required_text(
                                    patch["project_name"],
                                    "project_name",
                                )
                            )
                            updated = await self._delivery.start_jianying_draft(
                                delivery,
                                retry_failed=False,
                                project_name=project_name,
                                operation_port=operation_port,
                                now=self._clock.now(),
                            )
                            if live_operations is not None:
                                assert credential is not None
                                updated = await self._start_delivery_operation(
                                    command,
                                    updated,
                                    operations=live_operations,
                                    operation_port=operation_port,
                                    credential=credential,
                                )
                        finally:
                            if credential is not None:
                                credential.discard()
                        return self._result_from_state(
                            command,
                            updated,
                            existing_envelope=existing_envelope,
                            artifact=self._web_artifacts.project(updated),
                        )
                    else:
                        raise VideoLiveStateConflictError(
                            "video_action_patch_invalid"
                        )
                    return self._result_from_state(
                        command,
                        updated,
                        existing_envelope=existing_envelope,
                    )
            raise VideoLiveStateConflictError(
                "video_action_not_allowed_for_stage"
            )
        except VideoLiveStateConflictError:
            raise
        except (TypeError, ValueError) as exc:
            raise VideoLiveStateConflictError(
                "video_action_not_allowed_for_stage"
            ) from exc

    async def _dispatch_scene_package(
        self,
        command: WorkflowCommand,
        state: VideoScenePackageWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if not isinstance(state, VideoScenePackageWorkflowState):
            raise VideoLiveStateConflictError("video_state_kind_mismatch")
        if command.decision.action is AgentAction.MODIFY_WORKFLOW:
            if state.current_stage is not VideoScenePackageStage.SCENE_PACKAGE_REVIEW:
                raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")
            patch = self._require_patch_keys(
                command,
                allowed=frozenset(
                    {
                        "scene_id",
                        "scene_patch",
                        "asset_action",
                        "asset_group",
                        "asset_id",
                        "asset_patch",
                    }
                ),
            )
            try:
                if set(patch) == {"scene_id", "scene_patch"}:
                    updated = self._scene_packages.modify_review_scene(
                        state,
                        scene_id=self._required_text(patch["scene_id"], "scene_id"),
                        patch=self._json_object(patch["scene_patch"], "scene_patch"),
                        now=self._clock.now(),
                    )
                elif patch.get("asset_action") == "replace" and set(patch) == {
                    "asset_action",
                    "asset_group",
                    "asset_id",
                    "asset_patch",
                }:
                    updated = self._scene_packages.replace_review_asset(
                        state,
                        asset_group=self._required_text(
                            patch["asset_group"], "asset_group"
                        ),
                        asset_id=self._required_text(patch["asset_id"], "asset_id"),
                        asset_patch=self._json_object(
                            patch["asset_patch"], "asset_patch"
                        ),
                        now=self._clock.now(),
                    )
                elif patch.get("asset_action") == "delete" and set(patch) == {
                    "asset_action",
                    "asset_group",
                    "asset_id",
                }:
                    updated = self._scene_packages.delete_review_asset(
                        state,
                        asset_group=self._required_text(
                            patch["asset_group"], "asset_group"
                        ),
                        asset_id=self._required_text(patch["asset_id"], "asset_id"),
                        now=self._clock.now(),
                    )
                elif patch.get("asset_action") == "add" and set(patch) == {
                    "asset_action",
                    "asset_group",
                    "asset_id",
                    "asset_patch",
                }:
                    updated = self._scene_packages.add_review_asset(
                        state,
                        asset_group=self._required_text(
                            patch["asset_group"], "asset_group"
                        ),
                        asset_id=self._required_text(patch["asset_id"], "asset_id"),
                        asset_patch=self._json_object(
                            patch["asset_patch"], "asset_patch"
                        ),
                        now=self._clock.now(),
                    )
                else:
                    raise VideoLiveStateConflictError("video_action_patch_invalid")
            except VideoLiveStateConflictError:
                raise
            except (TypeError, ValueError) as exc:
                raise VideoLiveStateConflictError(
                    "video_action_patch_invalid"
                ) from exc
            return self._result_from_state(
                command,
                updated,
                existing_envelope=existing_envelope,
                interrupt_kind="video_scene_package_review",
                reason_code="video_scene_package_review_required",
                interrupt_payload={
                    "workflow_id": command.workflow_id,
                    "stage": updated.current_stage.value,
                    "artifact_ref": updated.scene_package_artifact_ref,
                },
                artifact=self._web_artifacts.project(updated),
            )
        if command.decision.action is not AgentAction.CONTINUE_WORKFLOW:
            raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")
        self._require_patch_keys(command, allowed=frozenset())
        if state.current_stage is VideoScenePackageStage.GENERATE_SCENE_ASSETS:
            return await self._generate_or_request_scene_assets(
                command,
                state,
                existing_envelope=existing_envelope,
            )
        if state.current_stage is VideoScenePackageStage.SCENE_PACKAGE_REVIEW:
            live_operations = self._live_operation_bridge()
            credential = (
                None
                if live_operations is None
                else self._credential_provider.get(command.turn_id)
            )
            if live_operations is not None and credential is None:
                return self._wait_for_authorization(
                    command,
                    state,
                    existing_envelope=existing_envelope,
                )
            operation_port = self._operation_port_for(command)
            try:
                generated = await self._generation.start_from_reviewed_scene_package(
                    state,
                    operation_port=operation_port,
                    now=self._clock.now(),
                )
                if live_operations is not None:
                    assert credential is not None
                    generated = await self._start_scene_video_operations(
                        command,
                        generated,
                        operations=live_operations,
                        operation_port=operation_port,
                        credential=credential,
                    )
            finally:
                if credential is not None:
                    credential.discard()
            return self._result_from_state(
                command,
                generated,
                existing_envelope=existing_envelope,
            )
        raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")

    async def _start_scene_video_operations(
        self,
        command: WorkflowCommand,
        state: VideoSceneGenerationWorkflowState,
        *,
        operations: VideoLiveOperationBridge,
        operation_port: OperationPort,
        credential: TransientTurnCredential,
    ) -> VideoSceneGenerationWorkflowState:
        """按 M11 权威请求逐个启动分镜 Operation，再只回读原 job。"""

        requests = {
            str(item["scene_id"]): item
            for item in state.generation_requests
        }
        for pending in state.pending_operations:
            scene_id = pending.stage.partition(":")[2]
            provider_request = requests.get(scene_id)
            if provider_request is None:
                raise VideoLiveStateConflictError(
                    "video_operation_request_not_found"
                )
            start_request = operations.start_request_from_claim(
                user_id=command.user_id,
                conversation_id=command.conversation_id,
                job=pending,
                stage_version=state.stage_version,
                provider_request=provider_request,
            )
            started = await operations.start(
                start_request,
                credential=credential,
            )
            if started.job_id != pending.job_id:
                raise VideoLiveStateConflictError("video_operation_job_mismatch")
        return await self._generation.resume(
            state,
            operation_port=operation_port,
            now=self._clock.now(),
        )

    def _live_operation_bridge(self) -> VideoLiveOperationBridge | None:
        return (
            self._operation_port
            if isinstance(self._operation_port, VideoLiveOperationBridge)
            else None
        )

    def _operation_port_for(self, command: WorkflowCommand) -> OperationPort:
        port = self._operation_port
        if port is None:
            raise VideoLiveStateConflictError("video_operation_port_required")
        if isinstance(port, VideoLiveOperationBridge):
            return port.bind(
                user_id=command.user_id,
                conversation_id=command.conversation_id,
            )
        return port

    async def _dispatch_scene_generation(
        self,
        command: WorkflowCommand,
        state: VideoSceneGenerationWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if not isinstance(state, VideoSceneGenerationWorkflowState):
            raise VideoLiveStateConflictError("video_state_kind_mismatch")
        if state.current_stage is not VideoSceneGenerationStage.SCENE_VIDEO_REVIEW:
            raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")
        action = command.decision.action
        try:
            if action is AgentAction.MODIFY_WORKFLOW:
                patch = self._require_patch_keys(
                    command,
                    allowed=frozenset({"scene_id", "scene_patch"}),
                    required=frozenset({"scene_id", "scene_patch"}),
                )
                updated = self._generation.modify_scene(
                    state,
                    scene_id=self._required_text(patch["scene_id"], "scene_id"),
                    patch=self._json_object(patch["scene_patch"], "scene_patch"),
                    now=self._clock.now(),
                )
                return self._wait_for_scene_video_review(
                    command,
                    updated,
                    existing_envelope=existing_envelope,
                )
            if action is AgentAction.REGENERATE_STAGE:
                self._require_patch_keys(command, allowed=frozenset())
                live_operations = self._live_operation_bridge()
                credential = (
                    None
                    if live_operations is None
                    else self._credential_provider.get(command.turn_id)
                )
                if live_operations is not None and credential is None:
                    return self._wait_for_authorization(
                        command,
                        state,
                        existing_envelope=existing_envelope,
                    )
                operation_port = self._operation_port_for(command)
                try:
                    updated = await self._generation.regenerate_modified_scenes(
                        state,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    if live_operations is not None:
                        assert credential is not None
                        updated = await self._start_scene_video_operations(
                            command,
                            updated,
                            operations=live_operations,
                            operation_port=operation_port,
                            credential=credential,
                        )
                finally:
                    if credential is not None:
                        credential.discard()
                return self._result_from_state(
                    command,
                    updated,
                    existing_envelope=existing_envelope,
                )
            if action is AgentAction.RETRY_FAILED:
                patch = self._require_patch_keys(
                    command,
                    allowed=frozenset({"scene_ids"}),
                )
                scene_ids = (
                    None
                    if "scene_ids" not in patch
                    else self._string_list(patch["scene_ids"], "scene_ids")
                )
                live_operations = self._live_operation_bridge()
                credential = (
                    None
                    if live_operations is None
                    else self._credential_provider.get(command.turn_id)
                )
                if live_operations is not None and credential is None:
                    return self._wait_for_authorization(
                        command,
                        state,
                        existing_envelope=existing_envelope,
                    )
                operation_port = self._operation_port_for(command)
                try:
                    updated = await self._generation.retry_failed_scenes(
                        state,
                        scene_ids=scene_ids,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    if live_operations is not None:
                        assert credential is not None
                        updated = await self._start_scene_video_operations(
                            command,
                            updated,
                            operations=live_operations,
                            operation_port=operation_port,
                            credential=credential,
                        )
                finally:
                    if credential is not None:
                        credential.discard()
                return self._result_from_state(
                    command,
                    updated,
                    existing_envelope=existing_envelope,
                )
            if action is AgentAction.CONTINUE_WORKFLOW:
                self._require_patch_keys(command, allowed=frozenset())
                live_operations = self._live_operation_bridge()
                credential = (
                    None
                    if live_operations is None
                    else self._credential_provider.get(command.turn_id)
                )
                if live_operations is not None and credential is None:
                    return self._wait_for_authorization(
                        command,
                        state,
                        existing_envelope=existing_envelope,
                    )
                operation_port = self._operation_port_for(command)
                try:
                    updated = await self._postproduction.start_merge(
                        state,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    if live_operations is not None:
                        assert credential is not None
                        updated = await self._start_postproduction_operation(
                            command,
                            updated,
                            operations=live_operations,
                            operation_port=operation_port,
                            credential=credential,
                        )
                finally:
                    if credential is not None:
                        credential.discard()
                return self._result_from_state(
                    command,
                    updated,
                    existing_envelope=existing_envelope,
                )
        except VideoLiveStateConflictError:
            raise
        except (TypeError, ValueError) as exc:
            raise VideoLiveStateConflictError(
                "video_action_not_allowed_for_stage"
            ) from exc
        raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")

    async def _start_postproduction_operation(
        self,
        command: WorkflowCommand,
        state: VideoPostProductionWorkflowState,
        *,
        operations: VideoLiveOperationBridge,
        operation_port: OperationPort,
        credential: TransientTurnCredential,
    ) -> VideoPostProductionWorkflowState:
        """启动当前合并或质检 Operation，并回读同一内部 job。"""

        pending = state.pending_operation
        if pending is None:
            raise VideoLiveStateConflictError("video_operation_request_not_found")
        if state.current_stage is VideoPostProductionStage.MERGE_VIDEO:
            provider_request = state.merge_request
        elif state.current_stage is VideoPostProductionStage.QUALITY_REVIEW:
            provider_request = self._quality_provider_request(state)
        else:
            raise VideoLiveStateConflictError("video_operation_stage_not_supported")
        start_request = operations.start_request_from_claim(
            user_id=command.user_id,
            conversation_id=command.conversation_id,
            job=pending,
            stage_version=state.stage_version,
            provider_request=provider_request,
        )
        started = await operations.start(start_request, credential=credential)
        if started.job_id != pending.job_id:
            raise VideoLiveStateConflictError("video_operation_job_mismatch")
        return await self._postproduction.resume(
            state,
            operation_port=operation_port,
            now=self._clock.now(),
        )

    @staticmethod
    def _quality_provider_request(
        state: VideoPostProductionWorkflowState,
    ) -> dict[str, Any]:
        """从 M11 权威状态构造与既有 QAAgent Client 一致的请求。"""

        merged = state.merged_video
        if merged is None:
            raise VideoLiveStateConflictError("video_merged_result_required")
        source = state.generation_state
        contract = source.source_scene_package.creation_contract
        return {
            "merged_video_url": merged["video_url"],
            "scene_videos": [
                {
                    "scene_id": item["scene_id"],
                    "scene_index": item["scene_index"],
                    "video_url": item["video_url"],
                }
                for item in sorted(
                    source.scene_videos,
                    key=lambda item: item["scene_index"],
                )
            ],
            "scene_packages": source.scene_packages,
            "brief": {
                "creation_contract": contract,
                "original_scene_packages": (
                    source.source_scene_package.scene_packages
                ),
                "expected_duration_sec": (
                    source.source_scene_package.target_duration_ms // 1000
                ),
            },
            "materials": [
                {"url": url}
                for url in source.source_scene_package.material_image_urls
            ],
            "user_feedback": state.quality_feedback,
            "ratio": contract.get("video_ratio"),
            "size": contract.get("video_size"),
        }

    async def _start_delivery_operation(
        self,
        command: WorkflowCommand,
        state: VideoDeliveryWorkflowState,
        *,
        operations: VideoLiveOperationBridge,
        operation_port: OperationPort,
        credential: TransientTurnCredential,
    ) -> VideoDeliveryWorkflowState:
        """启动当前剪映草稿 Operation，并回读同一内部 job。"""

        pending = state.pending_operation
        provider_request = state.pending_jianying_operation
        if pending is None or provider_request is None:
            raise VideoLiveStateConflictError("video_operation_request_not_found")
        start_request = operations.start_request_from_claim(
            user_id=command.user_id,
            conversation_id=command.conversation_id,
            job=pending,
            stage_version=state.stage_version,
            provider_request=provider_request,
        )
        started = await operations.start(start_request, credential=credential)
        if started.job_id != pending.job_id:
            raise VideoLiveStateConflictError("video_operation_job_mismatch")
        return await self._delivery.resume_jianying_draft(
            state,
            operation_port=operation_port,
            now=self._clock.now(),
        )

    def _wait_for_scene_video_review(
        self,
        command: WorkflowCommand,
        state: VideoSceneGenerationWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        return self._result_from_state(
            command,
            state,
            existing_envelope=existing_envelope,
            interrupt_kind="video_scene_video_review",
            reason_code="video_scene_video_review_required",
            interrupt_payload={
                "workflow_id": command.workflow_id,
                "stage": state.current_stage.value,
                "artifact_ref": state.scene_videos_artifact_ref,
            },
            artifact=self._web_artifacts.project(state),
        )

    async def _dispatch_delivery(
        self,
        command: WorkflowCommand,
        state: VideoDeliveryWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if not isinstance(state, VideoDeliveryWorkflowState):
            raise VideoLiveStateConflictError("video_state_kind_mismatch")
        action = command.decision.action
        try:
            if action is AgentAction.CONTINUE_WORKFLOW:
                patch = self._require_patch_keys(
                    command,
                    allowed=frozenset(
                        {
                            "delivery_download_url",
                            "download_url",
                            "jianying_action",
                            "storyboard_version_id",
                        }
                    ),
                )
                operation_port = self._operation_port_for(command)
                if not patch:
                    finished = await self._postproduction.finish(
                        state.postproduction_state,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    updated = await self._delivery.synchronize_postproduction(
                        state,
                        finished,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                elif set(patch) == {"delivery_download_url"}:
                    updated = await self._delivery.record_final_video_download(
                        state,
                        download_url=self._required_text(
                            patch["delivery_download_url"],
                            "delivery_download_url",
                        ),
                        downloaded_at=self._clock.now(),
                        operation_port=operation_port,
                    )
                elif set(patch) == {
                    "download_url",
                    "jianying_action",
                    "storyboard_version_id",
                } and patch["jianying_action"] == "download":
                    updated = await self._delivery.record_jianying_download(
                        state,
                        storyboard_version_id=self._required_text(
                            patch["storyboard_version_id"],
                            "storyboard_version_id",
                        ),
                        download_url=self._required_text(
                            patch["download_url"],
                            "download_url",
                        ),
                        downloaded_at=self._clock.now(),
                        operation_port=operation_port,
                    )
                else:
                    raise VideoLiveStateConflictError(
                        "video_action_patch_invalid"
                    )
            elif action in {
                AgentAction.MODIFY_WORKFLOW,
                AgentAction.RETRY_FAILED,
            }:
                patch = self._require_patch_keys(
                    command,
                    allowed=frozenset({"jianying_action", "project_name"}),
                    required=frozenset({"jianying_action"}),
                )
                if patch["jianying_action"] != "start":
                    raise VideoLiveStateConflictError(
                        "video_action_patch_invalid"
                    )
                if action is AgentAction.RETRY_FAILED:
                    version_id = state.current_storyboard_version_id
                    current_record = state.jianying_draft_records.get(version_id)
                    if (
                        state.pending_operation is not None
                        or current_record is None
                        or current_record.get("status") not in {"failed", "timeout"}
                    ):
                        raise VideoLiveStateConflictError(
                            "video_jianying_retry_requires_failed_or_timeout"
                        )
                project_name = (
                    None
                    if "project_name" not in patch
                    else self._required_text(patch["project_name"], "project_name")
                )
                live_operations = self._live_operation_bridge()
                credential = (
                    None
                    if live_operations is None
                    else self._credential_provider.get(command.turn_id)
                )
                if live_operations is not None and credential is None:
                    return self._wait_for_authorization(
                        command,
                        state,
                        existing_envelope=existing_envelope,
                    )
                operation_port = self._operation_port_for(command)
                try:
                    updated = await self._delivery.start_jianying_draft(
                        state,
                        retry_failed=action is AgentAction.RETRY_FAILED,
                        project_name=project_name,
                        operation_port=operation_port,
                        now=self._clock.now(),
                    )
                    if live_operations is not None:
                        assert credential is not None
                        updated = await self._start_delivery_operation(
                            command,
                            updated,
                            operations=live_operations,
                            operation_port=operation_port,
                            credential=credential,
                        )
                finally:
                    if credential is not None:
                        credential.discard()
            else:
                raise VideoLiveStateConflictError(
                    "video_action_not_allowed_for_stage"
                )
        except VideoLiveStateConflictError:
            raise
        except (TypeError, ValueError) as exc:
            raise VideoLiveStateConflictError(
                "video_action_not_allowed_for_stage"
            ) from exc
        return self._result_from_state(
            command,
            updated,
            existing_envelope=existing_envelope,
            artifact=cast(
                Mapping[str, JsonValue],
                self._web_artifacts.project(updated),
            ),
        )

    def _start_workflow(
        self,
        command: WorkflowCommand,
        existing: VideoWorkflowStateEnvelope | None,
    ) -> WorkflowDispatchResult:
        if command.workflow is not None:
            raise VideoLiveStateConflictError("video_start_workflow_must_not_exist")
        self._require_patch_keys(command, allowed=frozenset())
        if existing is not None:
            raise VideoLiveStateConflictError("video_start_workflow_already_exists")

        now = self._clock.now()
        state = self._planning.start(
            workflow_id=command.workflow_id,
            conversation_id=command.conversation_id,
            intent="video",
            intake_context={
                "source_prompt": command.current_input,
                "materials": deepcopy_json(command.materials),
                "intake_rounds": 0,
                "reply_to_message_id": command.reply_to_message_id,
                "artifact_refs": list(command.artifact_refs),
            },
            now=now,
        )
        workflow = self._planning.to_workflow_record(state)
        envelope = encode_video_workflow_state(
            user_id=command.user_id,
            state=state,
            workflow_version=1,
            last_turn_id=command.turn_id,
            last_action_key=command.decision.idempotency_key,
        )
        opened_interrupt = self._interrupt(
            command,
            kind="video_intake_form",
            reason_code="video_intake_required",
            payload={
                "workflow_id": command.workflow_id,
                "stage": workflow.current_stage,
                "form_values": deepcopy_json(state.form_values),
                "core_message": self._required_text(
                    state.intake_context.get("source_prompt"),
                    "source_prompt",
                ),
                "materials": deepcopy_json(
                    state.intake_context.get("materials", [])
                ),
                "intake_rounds": state.intake_context.get("intake_rounds", 0),
            },
            workflow=workflow,
            workflow_version=envelope.workflow_version,
        )
        return WorkflowDispatchResult(
            state=envelope,
            workflow=workflow,
            interrupt=opened_interrupt,
            turn_status=TurnStatus.WAITING_USER,
            update_active_workflow=True,
            active_workflow_id=command.workflow_id,
        )

    def _decode_existing_state(
        self,
        command: WorkflowCommand,
        envelope: VideoWorkflowStateEnvelope,
    ):
        if envelope.user_id != command.user_id:
            raise VideoLiveStateConflictError("video_user_mismatch")
        if envelope.conversation_id != command.conversation_id:
            raise VideoLiveStateConflictError("video_conversation_mismatch")
        try:
            state = decode_video_workflow_state(envelope)
            authority = project_video_workflow_state(state)
        except (TypeError, ValueError) as exc:
            raise VideoLiveStateConflictError("video_state_corrupted") from exc
        if command.workflow is None:
            raise VideoLiveStateConflictError("video_workflow_projection_required")
        if command.workflow.model_dump(mode="json") != authority.model_dump(mode="json"):
            raise VideoLiveStateConflictError("video_workflow_projection_stale")
        target_stage = command.decision.target_stage
        if target_stage is not None and target_stage != authority.current_stage:
            raise VideoLiveStateConflictError("video_target_stage_stale")
        artifact_ref = command.decision.target_artifact_ref
        if artifact_ref is not None and artifact_ref not in authority.latest_artifact_refs:
            raise VideoLiveStateConflictError("video_target_artifact_stale")
        return state

    async def _dispatch_planning(
        self,
        command: WorkflowCommand,
        state: VideoPlanningWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if state.current_stage is VideoPlanningStage.INTAKE:
            return await self._continue_intake(
                command,
                state,
                existing_envelope=existing_envelope,
            )
        if state.current_stage is VideoPlanningStage.DIRECTION_REVIEW:
            return await self._dispatch_direction_review(
                command,
                state,
                existing_envelope=existing_envelope,
            )
        if state.current_stage is VideoPlanningStage.PLAN_REVIEW:
            return await self._dispatch_plan_review(
                command,
                state,
                existing_envelope=existing_envelope,
            )
        raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")

    async def _continue_intake(
        self,
        command: WorkflowCommand,
        state: VideoPlanningWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if command.decision.action is not AgentAction.CONTINUE_WORKFLOW:
            raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")
        patch = self._require_patch_keys(
            command,
            allowed=frozenset({"form_values", "intake_rounds"}),
            required=frozenset({"form_values"}),
        )
        form_values = self._json_object(patch["form_values"], "form_values")
        intake_rounds = patch.get("intake_rounds", 0)
        if isinstance(intake_rounds, bool) or not isinstance(intake_rounds, int):
            raise VideoLiveStateConflictError("video_patch_intake_rounds_invalid")
        validation = await self._capabilities.validate_intake(
            form_values,
            intake_rounds=intake_rounds,
        )
        confirmed = self._planning.confirm_intake(
            state,
            validation,
            now=self._clock.now(),
        )
        directions = await self._capabilities.generate_directions(
            confirmed.form_values,
            confirmed.intake_context,
        )
        published = self._planning.publish_directions(
            confirmed,
            directions,
            now=self._clock.now(),
        )
        artifact = self._directions_artifact(published, command)
        return self._result_from_state(
            command,
            published,
            existing_envelope=existing_envelope,
            interrupt_kind="video_direction_review",
            reason_code="video_direction_review_required",
            interrupt_payload={
                "workflow_id": command.workflow_id,
                "stage": published.current_stage.value,
                "directions": deepcopy_json(published.creative_directions),
            },
            artifact=artifact,
        )

    async def _dispatch_direction_review(
        self,
        command: WorkflowCommand,
        state: VideoPlanningWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if command.decision.action is AgentAction.REGENERATE_STAGE:
            self._require_patch_keys(command, allowed=frozenset())
            generating = self._planning.regenerate_directions(
                state,
                now=self._clock.now(),
            )
            directions = await self._capabilities.generate_directions(
                generating.form_values,
                generating.intake_context,
            )
            published = self._planning.publish_directions(
                generating,
                directions,
                now=self._clock.now(),
            )
            return self._result_from_state(
                command,
                published,
                existing_envelope=existing_envelope,
                interrupt_kind="video_direction_review",
                reason_code="video_direction_review_required",
                interrupt_payload={
                    "workflow_id": command.workflow_id,
                    "stage": published.current_stage.value,
                    "directions": deepcopy_json(published.creative_directions),
                },
                artifact=self._directions_artifact(published, command),
            )
        if command.decision.action is not AgentAction.CONTINUE_WORKFLOW:
            raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")
        patch = self._require_patch_keys(
            command,
            allowed=frozenset({"direction_id"}),
            required=frozenset({"direction_id"}),
        )
        direction_id = self._required_text(patch["direction_id"], "direction_id")
        selected = self._planning.select_direction(
            state,
            direction_id,
            now=self._clock.now(),
        )
        plan = await self._capabilities.generate_initial_plan(
            form_values=selected.form_values,
            selected_direction=selected.selected_direction,
            intake_context=selected.intake_context,
            materials=self._planning_materials(selected),
        )
        published = self._planning.publish_initial_plan(
            selected,
            plan,
            now=self._clock.now(),
        )
        return self._wait_for_plan_review(
            command,
            published,
            existing_envelope=existing_envelope,
        )

    async def _dispatch_plan_review(
        self,
        command: WorkflowCommand,
        state: VideoPlanningWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        if command.decision.action is AgentAction.REGENERATE_STAGE:
            self._require_patch_keys(command, allowed=frozenset())
            generating = self._planning.restart_directions_from_plan(
                state,
                now=self._clock.now(),
            )
            directions = await self._capabilities.generate_directions(
                generating.form_values,
                generating.intake_context,
            )
            published = self._planning.publish_directions(
                generating,
                directions,
                now=self._clock.now(),
            )
            return self._result_from_state(
                command,
                published,
                existing_envelope=existing_envelope,
                interrupt_kind="video_direction_review",
                reason_code="video_direction_review_required",
                interrupt_payload={
                    "workflow_id": command.workflow_id,
                    "stage": published.current_stage.value,
                    "directions": deepcopy_json(published.creative_directions),
                },
                artifact=self._directions_artifact(published, command),
            )
        if command.decision.action is AgentAction.CONTINUE_WORKFLOW:
            self._require_patch_keys(command, allowed=frozenset())
            approved = self._planning.approve_plan(
                state,
                now=self._clock.now(),
            )
            prepared = self._scene_packages.prepare_from_approved_plan(
                approved,
                materials=self._planning_materials(approved),
                now=self._clock.now(),
            )
            return await self._generate_or_request_scene_assets(
                command,
                prepared,
                existing_envelope=existing_envelope,
            )
        if command.decision.action is not AgentAction.MODIFY_WORKFLOW:
            raise VideoLiveStateConflictError("video_action_not_allowed_for_stage")
        patch = self._require_patch_keys(
            command,
            allowed=frozenset({"plan_version", "revision_feedback"}),
        )
        if set(patch) == {"revision_feedback"}:
            feedback = self._required_text(
                patch["revision_feedback"],
                "revision_feedback",
            )
            revised = await self._capabilities.revise_plan(
                state,
                revision_feedback=feedback,
            )
            published = self._planning.publish_revision(
                state,
                revised,
                now=self._clock.now(),
            )
        elif set(patch) == {"plan_version"}:
            plan_version = patch["plan_version"]
            if (
                isinstance(plan_version, bool)
                or not isinstance(plan_version, int)
                or plan_version < 1
            ):
                raise VideoLiveStateConflictError(
                    "video_patch_plan_version_invalid"
                )
            restored = await self._capabilities.restore_plan(
                state,
                plan_version=plan_version,
            )
            published = self._planning.restore_plan(
                state,
                restored,
                now=self._clock.now(),
            )
        else:
            raise VideoLiveStateConflictError("video_action_patch_invalid")
        return self._wait_for_plan_review(
            command,
            published,
            existing_envelope=existing_envelope,
        )

    async def _generate_or_request_scene_assets(
        self,
        command: WorkflowCommand,
        state: VideoScenePackageWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        credential = self._credential_provider.get(command.turn_id)
        if credential is None:
            return self._wait_for_authorization(
                command,
                state,
                existing_envelope=existing_envelope,
            )
        try:
            result = await self._capabilities.generate_scene_assets(
                state,
                credential=credential,
            )
        finally:
            credential.discard()
        copied = self._json_object(
            cast(JsonValue, deepcopy_json(result)),
            "scene_asset_result",
        )
        global_assets = copied.get("global_assets")
        if copied.get("ok") is not True or not isinstance(global_assets, dict):
            raise VideoLiveStateConflictError("video_scene_asset_generation_failed")
        published = self._scene_packages.publish_generated_asset_images(
            state,
            global_assets,
            now=self._clock.now(),
        )
        return self._result_from_state(
            command,
            published,
            existing_envelope=existing_envelope,
            interrupt_kind="video_scene_package_review",
            reason_code="video_scene_package_review_required",
            interrupt_payload={
                "workflow_id": command.workflow_id,
                "stage": published.current_stage.value,
                "artifact_ref": published.scene_package_artifact_ref,
            },
            artifact=self._web_artifacts.project(published),
        )

    def _wait_for_authorization(
        self,
        command: WorkflowCommand,
        state,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        """在任何付费 Operation 建立前打开稳定鉴权中断。"""

        return self._result_from_state(
            command,
            state,
            existing_envelope=existing_envelope,
            interrupt_kind="authorization_required",
            reason_code="authorization_required",
            interrupt_payload={
                "workflow_id": command.workflow_id,
                "stage": state.current_stage.value,
                "artifact_ref": command.decision.target_artifact_ref,
                "authorization_action": {
                    "action": command.decision.action.value,
                    "intent": "video",
                    "workflow_id": command.workflow_id,
                    "stage": command.decision.target_stage,
                    "artifact_ref": command.decision.target_artifact_ref,
                    "patch": deepcopy_json(command.decision.patch),
                },
            },
        )

    def _wait_for_plan_review(
        self,
        command: WorkflowCommand,
        state: VideoPlanningWorkflowState,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
    ) -> WorkflowDispatchResult:
        return self._result_from_state(
            command,
            state,
            existing_envelope=existing_envelope,
            interrupt_kind="video_plan_review",
            reason_code="video_plan_review_required",
            interrupt_payload={
                "workflow_id": command.workflow_id,
                "stage": state.current_stage.value,
                "artifact_ref": state.active_plan_artifact_ref,
            },
            artifact=self._plan_artifact(state),
        )

    def _result_from_state(
        self,
        command: WorkflowCommand,
        state,
        *,
        existing_envelope: VideoWorkflowStateEnvelope,
        interrupt_kind: str | None = None,
        reason_code: str | None = None,
        interrupt_payload: Mapping[str, JsonValue] | None = None,
        artifact: Mapping[str, JsonValue] | None = None,
        update_active_workflow: bool = False,
        active_workflow_id: str | None = None,
    ) -> WorkflowDispatchResult:
        workflow = project_video_workflow_state(state)
        envelope = encode_video_workflow_state(
            user_id=command.user_id,
            state=state,
            workflow_version=existing_envelope.workflow_version + 1,
            last_turn_id=command.turn_id,
            last_action_key=command.decision.idempotency_key,
        )
        opened_interrupt = None
        if interrupt_kind is not None:
            if reason_code is None:
                raise ValueError("打开 interrupt 时必须提供 reason_code")
            opened_interrupt = self._interrupt(
                command,
                kind=interrupt_kind,
                reason_code=reason_code,
                payload=interrupt_payload or {},
                workflow=workflow,
                workflow_version=envelope.workflow_version,
            )
        messages = ()
        if artifact is not None:
            messages = (
                _projection_message(
                    workflow=workflow,
                    action_key=command.decision.idempotency_key,
                    artifact=artifact,
                    now=self._clock.now(),
                ),
            )
        return WorkflowDispatchResult(
            state=envelope,
            workflow=workflow,
            messages=messages,
            interrupt=opened_interrupt,
            turn_status=(
                TurnStatus.WAITING_USER
                if opened_interrupt is not None
                else TurnStatus.COMPLETED
            ),
            update_active_workflow=update_active_workflow,
            active_workflow_id=active_workflow_id,
        )

    def _interrupt(
        self,
        command: WorkflowCommand,
        *,
        kind: str,
        reason_code: str,
        payload: Mapping[str, JsonValue],
        workflow: WorkflowRecord,
        workflow_version: int,
    ) -> StoredAgentInterrupt:
        copied_payload = cast(dict[str, JsonValue], deepcopy_json(payload))
        copied_payload["ui_kind"] = _INTERRUPT_UI_KINDS.get(kind, kind)
        return StoredAgentInterrupt(
            interrupt_id=self._interrupt_occurrence_id(
                turn_id=command.turn_id,
                reason_code=reason_code,
                workflow=workflow,
                workflow_version=workflow_version,
            ),
            conversation_id=command.conversation_id,
            workflow_id=command.workflow_id,
            turn_id=command.turn_id,
            kind=kind,
            reason_code=reason_code,
            payload=copied_payload,
            opened_at=self._clock.now(),
            user_id=command.user_id,
            thread_id=command.namespace.thread_id,
            checkpoint_ns="root",
        )

    @staticmethod
    def _interrupt_occurrence_id(
        *,
        turn_id: str,
        reason_code: str,
        workflow: WorkflowRecord,
        workflow_version: int,
    ) -> str:
        """用权威状态版本区分同一原 Turn 内相继发生的同类中断。"""

        return video_interrupt_occurrence_id(
            turn_id=turn_id,
            reason_code=reason_code,
            workflow=workflow,
            workflow_version=workflow_version,
        )

    @staticmethod
    def _require_patch_keys(
        command: WorkflowCommand,
        *,
        allowed: frozenset[str],
        required: frozenset[str] = frozenset(),
    ) -> dict[str, JsonValue]:
        patch = command.decision.patch
        if set(patch).difference(allowed) or not required.issubset(patch):
            raise VideoLiveStateConflictError("video_action_patch_invalid")
        return patch

    @staticmethod
    def _json_object(value: JsonValue, field_name: str) -> dict[str, Any]:
        copied = deepcopy_json(value)
        if not isinstance(copied, dict):
            raise VideoLiveStateConflictError(f"video_patch_{field_name}_invalid")
        return copied

    @staticmethod
    def _required_text(value: JsonValue, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise VideoLiveStateConflictError(f"video_patch_{field_name}_invalid")
        return value.strip()

    @staticmethod
    def _string_list(value: JsonValue, field_name: str) -> list[str]:
        copied = deepcopy_json(value)
        if (
            not isinstance(copied, list)
            or not copied
            or not all(isinstance(item, str) and item.strip() for item in copied)
            or len(set(copied)) != len(copied)
        ):
            raise VideoLiveStateConflictError(f"video_patch_{field_name}_invalid")
        return cast(list[str], copied)

    @classmethod
    def _scene_patch_map(
        cls,
        value: JsonValue,
    ) -> dict[str, dict[str, Any]]:
        copied = cls._json_object(value, "scene_patches")
        if not copied:
            raise VideoLiveStateConflictError("video_patch_scene_patches_invalid")
        normalized: dict[str, dict[str, Any]] = {}
        for scene_id, scene_patch in copied.items():
            if not isinstance(scene_id, str) or not scene_id.strip():
                raise VideoLiveStateConflictError(
                    "video_patch_scene_patches_invalid"
                )
            if not isinstance(scene_patch, dict) or not scene_patch:
                raise VideoLiveStateConflictError(
                    "video_patch_scene_patches_invalid"
                )
            normalized[scene_id] = scene_patch
        return normalized

    @staticmethod
    def _directions_artifact(
        state: VideoPlanningWorkflowState,
        command: WorkflowCommand,
    ) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            deepcopy_json(
                {
                    "type": "directions",
                    "title": "视频创意方向",
                    "description": "请选择一个方向继续生成创作方案。",
                    "actionLabel": "选择方向",
                    "directions": state.creative_directions,
                    "intent": "video",
                    "formValues": state.form_values,
                    "intakeContext": state.intake_context,
                    "materials": command.materials,
                }
            ),
        )

    @staticmethod
    def _plan_artifact(state: VideoPlanningWorkflowState) -> dict[str, JsonValue]:
        active_plan = state.active_plan
        if active_plan is None:
            raise VideoLiveStateConflictError("video_plan_authority_required")
        return cast(
            dict[str, JsonValue],
            deepcopy_json(
                {
                    "type": "plan",
                    "title": "视频创作方案",
                    "description": "请审核方案，确认后进入分镜素材阶段。",
                    "actionLabel": "同意方案",
                    "intent": "video",
                    "formValues": state.form_values,
                    "intakeContext": state.intake_context,
                    "selectedDirection": state.selected_direction,
                    "plan": active_plan.to_dict(),
                    "planVersion": active_plan.plan_version,
                    "planHistory": active_plan.plan_history,
                    "creationContract": active_plan.creation_contract,
                }
            ),
        )

    @staticmethod
    def _planning_materials(
        state: VideoPlanningWorkflowState,
    ) -> list[dict[str, Any]]:
        materials = deepcopy_json(state.intake_context.get("materials", []))
        if not isinstance(materials, list) or not all(
            isinstance(item, dict) for item in materials
        ):
            raise VideoLiveStateConflictError("video_materials_state_invalid")
        return cast(list[dict[str, Any]], materials)

    @staticmethod
    def _validate_command_identity(command: WorkflowCommand) -> None:
        if command.kind is not WorkflowKind.VIDEO:
            raise VideoLiveStateConflictError("video_workflow_kind_required")
        if command.decision.intent.value != "video":
            raise VideoLiveStateConflictError("video_intent_required")
        if command.decision.action is AgentAction.START_WORKFLOW:
            if command.decision.target_workflow_id is not None:
                raise VideoLiveStateConflictError(
                    "video_start_workflow_target_must_be_new"
                )
        elif command.decision.target_workflow_id != command.workflow_id:
            raise VideoLiveStateConflictError("video_target_workflow_mismatch")
        if command.conversation_id == "" or command.user_id == "" or command.turn_id == "":
            raise VideoLiveStateConflictError("video_command_identity_required")


def video_interrupt_occurrence_id(
    *,
    turn_id: str,
    reason_code: str,
    workflow: WorkflowRecord,
    workflow_version: int,
) -> str:
    """为 Handler 与异步完成桥生成同一规则下的稳定视频中断身份。"""

    occurrence_key = json.dumps(
        [
            reason_code,
            workflow.workflow_id,
            workflow.current_stage,
            workflow.stage_version,
            workflow_version,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return interrupt_id(turn_id, occurrence_key)


def deepcopy_json(value: Any) -> JsonValue:
    """校验普通 JSON 并返回无可变别名的深拷贝。"""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("只允许合法 JSON 值") from exc
    if not isinstance(copied, (dict, list, str, int, float, bool)) and copied is not None:
        raise ValueError("只允许合法 JSON 值")
    return copied


def artifact_summary(artifact: Mapping[str, JsonValue]) -> str:
    """把前端 Artifact 类型映射为固定中文阶段摘要。"""

    summaries = {
        "directions": "已生成三个视频创意方向，请选择一个方向。",
        "plan": "视频创作方案已生成，请审核确认。",
        "video_scene_packages": "视频分镜与场景素材已准备，请审核确认。",
        "video_quality_review": "视频质检结果已生成，请确认修改范围。",
        "video_result": "视频成片已生成，请确认或提出修改意见。",
        "jianying_draft": "剪映草稿已生成，可下载交付。",
    }
    artifact_type = artifact.get("type")
    if not isinstance(artifact_type, str) or artifact_type not in summaries:
        raise ValueError("不支持的消息 Artifact 类型")
    return summaries[artifact_type]


def _projection_message(
    *,
    workflow: WorkflowRecord,
    action_key: str,
    artifact: Mapping[str, JsonValue],
    now,
) -> SupervisorProjectionMessage:
    """按 Workflow 阶段动作生成可重放覆盖的助手卡片。"""

    from pixelflow.agent_runtime.identity import projection_message_id

    normalized_artifact = cast(dict[str, JsonValue], deepcopy_json(artifact))
    return SupervisorProjectionMessage(
        message_id=projection_message_id(
            workflow.workflow_id,
            workflow.current_stage,
            workflow.stage_version,
            action_key,
        ),
        conversation_id=workflow.conversation_id,
        run_id=workflow.workflow_id,
        role="assistant",
        content=artifact_summary(normalized_artifact),
        payload={
            "workflow_id": workflow.workflow_id,
            "artifact_ref": (
                workflow.latest_artifact_refs[-1]
                if workflow.latest_artifact_refs
                else None
            ),
            "artifact": normalized_artifact,
        },
        created_at=now,
    )


__all__ = [
    "VideoLiveStateConflictError",
    "VideoLiveWorkflowHandler",
    "WorkflowDispatchResult",
    "artifact_summary",
    "deepcopy_json",
    "video_interrupt_occurrence_id",
]
