"""历史 frontend_v2 会话升级为原生 Video Agent（同事务、幂等、失败不部分写入）。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pixelflow.agent_runtime.contracts import OrchestrationMode
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.tasks.store import AGENT_RUNTIME_CONTEXT_KEY
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.workspace import VideoAgentRepository
from pixelflow.video_agent.workspace.ids import video_workspace_id_for_conversation

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LegacyUpgradeResult:
    """升级结果；upgraded=False 表示本已是原生模式或无需映射。"""

    workspace: VideoWorkspace
    upgraded: bool
    orchestration_mode: str
    artifact_refs: tuple[str, ...]


def _workspace_id_for(conversation_id: str) -> str:
    return video_workspace_id_for_conversation(conversation_id)


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _collect_artifact_refs(
    *,
    context: Mapping[str, Any],
    messages: Sequence[Any],
) -> list[str]:
    refs: list[str] = []
    for key in ("artifact_refs", "artifactRefs"):
        raw = context.get(key)
        if isinstance(raw, list):
            refs.extend(str(item) for item in raw if isinstance(item, str) and item.strip())

    for message in messages:
        artifact = getattr(message, "artifact", None)
        if not isinstance(artifact, Mapping):
            continue
        for key in ("artifact_ref", "artifact_id", "id"):
            value = artifact.get(key)
            if isinstance(value, str) and value.startswith("artifact:"):
                refs.append(value)
        nested = artifact.get("artifact_refs")
        if isinstance(nested, list):
            refs.extend(
                str(item) for item in nested if isinstance(item, str) and item.startswith("artifact:")
            )
    # 稳定去重保序
    return list(dict.fromkeys(refs))[:64]


def map_frontend_v2_payload(
    *,
    conversation_id: str,
    context: Mapping[str, Any] | None,
    messages: Sequence[Any] = (),
) -> dict[str, Any]:
    """把旧会话 context / 消息中的可验证产物映射进 Workspace payload。"""

    ctx = _as_mapping(context)
    payload: dict[str, Any] = {
        "legacy_upgrade": {
            "from": "frontend_v2",
            "mapped_keys": [],
        },
        "artifact_refs": _collect_artifact_refs(context=ctx, messages=messages),
    }
    mapped: list[str] = []

    # 常见旧 context 键 → Workspace 字段
    key_map = (
        ("script", "script"),
        ("scriptContent", "script"),
        ("videoScenePackages", "scene_packages"),
        ("video_scene_packages", "scene_packages"),
        ("generatedSceneVideos", "scene_videos"),
        ("generated_scene_videos", "scene_videos"),
        ("mergedVideo", "merged_video"),
        ("merged_video", "merged_video"),
        ("creation_contract", "creation_contract"),
        ("creationContract", "creation_contract"),
        ("dirty_scene_ids", "dirty_scene_ids"),
        ("videoScenePackageEditedSceneIds", "dirty_scene_ids"),
    )
    for source_key, target_key in key_map:
        if source_key in ctx and ctx[source_key] is not None:
            value = ctx[source_key]
            if target_key == "script" and isinstance(value, str):
                payload["script"] = {"content": value, "source": "frontend_v2"}
            else:
                payload[target_key] = value
            mapped.append(source_key)

    # 从最近助手消息 artifact 兜底场景包 / 脚本
    for message in reversed(list(messages)):
        artifact = getattr(message, "artifact", None)
        if not isinstance(artifact, Mapping):
            continue
        if "scene_packages" not in payload and artifact.get("videoScenePackages"):
            payload["scene_packages"] = artifact.get("videoScenePackages")
            mapped.append("message.videoScenePackages")
        if "script" not in payload:
            plan_md = artifact.get("plan_markdown") or artifact.get("content")
            if isinstance(plan_md, str) and plan_md.strip() and artifact.get("type") in {
                None,
                "plan",
                "script",
                "script_preview",
            }:
                payload["script"] = {"content": plan_md, "source": "frontend_v2_message"}
                mapped.append("message.script")
        if "merged_video" not in payload and artifact.get("mergedVideo"):
            payload["merged_video"] = artifact.get("mergedVideo")
            mapped.append("message.mergedVideo")

    payload["legacy_upgrade"]["mapped_keys"] = mapped
    payload["conversation_id"] = conversation_id
    return payload


class FrontendV2LegacyUpgrader:
    """打开仍只读；首次 Turn/编辑时同事务升级。"""

    def __init__(
        self,
        *,
        task_store: object,
        video_repository: VideoAgentRepository,
    ) -> None:
        self._task_store = task_store
        self._video_repository = video_repository

    async def upgrade_if_needed(
        self,
        *,
        user_id: str,
        conversation: object,
        now: datetime,
        messages: Sequence[Any] | None = None,
    ) -> LegacyUpgradeResult:
        owner = user_id.strip()
        conversation_id = str(getattr(conversation, "conversation_id", "") or "").strip()
        if not owner or not conversation_id:
            raise ValueError("legacy upgrade 需要 user/conversation")

        mode = str(getattr(conversation, "orchestration_mode", "") or "")
        workspace_id = _workspace_id_for(conversation_id)
        existing = await self._video_repository.get_workspace(owner, workspace_id)

        if mode == OrchestrationMode.VIDEO_AGENT_V2.value:
            if existing is not None:
                refs = existing.payload.get("artifact_refs")
                return LegacyUpgradeResult(
                    workspace=existing,
                    upgraded=False,
                    orchestration_mode=mode,
                    artifact_refs=tuple(refs) if isinstance(refs, list) else (),
                )
            # 已是原生模式但缺 Workspace：补建空壳，不算「历史升级」。
            workspace = await self._video_repository.create_workspace(
                owner,
                VideoWorkspace(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    payload={"artifact_refs": [], "legacy_upgrade": None},
                    created_at=now,
                    updated_at=now,
                ),
            )
            return LegacyUpgradeResult(
                workspace=workspace,
                upgraded=False,
                orchestration_mode=mode,
                artifact_refs=(),
            )

        if mode != OrchestrationMode.FRONTEND_V2.value:
            raise AgentRuntimeRecordConflictError(
                f"不支持的 orchestration_mode 升级来源: {mode}"
            )

        context = getattr(conversation, "context", None)
        ctx = _as_mapping(context)
        loaded_messages = list(messages or ())
        if not loaded_messages:
            list_messages = getattr(self._task_store, "list_conversation_messages", None)
            if callable(list_messages):
                loaded_messages = list(
                    await list_messages(conversation_id, user_id=owner, limit=80)
                )

        payload = map_frontend_v2_payload(
            conversation_id=conversation_id,
            context=ctx,
            messages=loaded_messages,
        )
        artifact_refs = tuple(payload.get("artifact_refs") or ())
        revision = getattr(conversation, "revision", None)
        if not isinstance(revision, int):
            raise AgentRuntimeRecordConflictError("会话 revision 无效，无法升级")

        runtime = _as_mapping(ctx.get(AGENT_RUNTIME_CONTEXT_KEY))
        runtime_patch = {
            "mode": runtime.get("mode") or "primary",
            "primary_execution_ready": True,
            "enabled_intents": list(
                dict.fromkeys(
                    [
                        *(
                            runtime.get("enabled_intents")
                            if isinstance(runtime.get("enabled_intents"), list)
                            else []
                        ),
                        "video",
                    ]
                )
            ),
            "legacy_upgraded_from": "frontend_v2",
        }

        merge_patch: dict[str, Any] | None = None
        if existing is not None:
            merge_patch = {
                "legacy_upgrade": payload.get("legacy_upgrade"),
            }
            existing_refs = existing.payload.get("artifact_refs")
            merged_refs = list(
                dict.fromkeys(
                    [
                        *(existing_refs if isinstance(existing_refs, list) else []),
                        *artifact_refs,
                    ]
                )
            )[:64]
            merge_patch["artifact_refs"] = merged_refs
            for key in (
                "script",
                "scene_packages",
                "scene_videos",
                "merged_video",
                "creation_contract",
                "dirty_scene_ids",
            ):
                if key not in existing.payload and key in payload:
                    merge_patch[key] = payload[key]

        # 同库 SQL：Workspace + Conversation mode 同一事务，避免中间态。
        commit_atomic = getattr(self._video_repository, "commit_legacy_upgrade", None)
        task_sf = getattr(self._task_store, "session_factory", None)
        repo_sf = getattr(self._video_repository, "session_factory", None)
        if (
            callable(commit_atomic)
            and task_sf is not None
            and repo_sf is not None
            and task_sf is repo_sf
        ):
            workspace = await commit_atomic(
                user_id=owner,
                conversation_id=conversation_id,
                expected_conversation_revision=revision,
                workspace_id=workspace_id,
                create_workspace=(
                    None
                    if existing is not None
                    else VideoWorkspace(
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        payload=payload,
                        created_at=now,
                        updated_at=now,
                    )
                ),
                workspace_patch=merge_patch,
                expected_workspace_revision=(
                    existing.revision if existing is not None else None
                ),
                orchestration_mode=OrchestrationMode.VIDEO_AGENT_V2.value,
                orchestration_version=1,
                runtime_patch=runtime_patch,
                now=now,
            )
            return LegacyUpgradeResult(
                workspace=workspace,
                upgraded=True,
                orchestration_mode=OrchestrationMode.VIDEO_AGENT_V2.value,
                artifact_refs=artifact_refs,
            )

        # Memory / 跨库：先写 Workspace，模式切换失败则补偿删除新建 Workspace。
        created_new = existing is None
        prior_revision = existing.revision if existing is not None else None
        if existing is None:
            workspace = await self._video_repository.create_workspace(
                owner,
                VideoWorkspace(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    payload=payload,
                    created_at=now,
                    updated_at=now,
                ),
            )
        else:
            assert merge_patch is not None
            workspace = await self._video_repository.apply_workspace_patch(
                owner,
                existing.workspace_id,
                merge_patch,
                expected_revision=existing.revision,
                now=now,
            )

        update = getattr(self._task_store, "update_conversation", None)
        if not callable(update):
            if created_new:
                await self._video_repository.discard_workspace(owner, workspace_id)
            raise AgentRuntimeRecordConflictError("任务 Store 不支持会话升级")
        try:
            updated = await update(
                conversation_id,
                user_id=owner,
                expected_revision=revision,
                orchestration_mode=OrchestrationMode.VIDEO_AGENT_V2.value,
                orchestration_version=1,
                _agent_runtime_patch=runtime_patch,
            )
        except Exception:
            if created_new:
                await self._video_repository.discard_workspace(owner, workspace_id)
            raise
        if updated is None:
            if created_new:
                await self._video_repository.discard_workspace(owner, workspace_id)
            elif prior_revision is not None:
                logger.warning(
                    "legacy upgrade mode switch conflict conversation_id=%s",
                    conversation_id,
                )
            raise AgentRuntimeRecordConflictError("会话升级冲突，请重试")

        return LegacyUpgradeResult(
            workspace=workspace,
            upgraded=True,
            orchestration_mode=OrchestrationMode.VIDEO_AGENT_V2.value,
            artifact_refs=artifact_refs,
        )


__all__ = [
    "FrontendV2LegacyUpgrader",
    "LegacyUpgradeResult",
    "map_frontend_v2_payload",
]
