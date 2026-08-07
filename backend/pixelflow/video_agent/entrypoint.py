"""统一视频输入进入 VideoAgent 的最小 P0 入口。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.executor.events import build_plan_created_event
from pixelflow.video_agent.planner import VideoAgentPlanner
from pixelflow.video_agent.workspace.repository import VideoAgentRepository


def _stable_id(prefix: str, *parts: str) -> str:
    value = ":".join(("pixelflow-video-agent", prefix, *parts))
    return f"{prefix}_{uuid5(NAMESPACE_URL, value).hex}"


def _safe_materials(
    materials: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not materials:
        return []
    safe: list[dict[str, Any]] = []
    for item in materials:
        if not isinstance(item, Mapping):
            continue
        safe.append({str(key): value for key, value in item.items()})
    return safe


def _product_info_from_materials(
    materials: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    images: list[dict[str, str]] = []
    for item in materials:
        mime = str(item.get("mimeType") or item.get("mime_type") or "")
        kind = str(item.get("type") or "")
        is_image = kind == "image" or mime.startswith("image/")
        if not is_image:
            continue
        url = str(item.get("url") or item.get("path") or "").strip()
        if not url:
            continue
        name = str(item.get("name") or item.get("filename") or "").strip()
        entry = {"url": url}
        if name:
            entry["name"] = name
        images.append(entry)
    if not images:
        return {}
    product: dict[str, Any] = {"images": images}
    first_name = images[0].get("name")
    if first_name:
        stem = PurePosixPath(first_name).stem.strip()
        if stem:
            product["name"] = stem
            product["product_name"] = stem
    return product


def _should_seed_script_draft(
    content: str,
    materials: Sequence[Mapping[str, Any]],
) -> bool:
    if materials:
        return True
    lowered = content.casefold()
    markers = (
        "视频",
        "带货",
        "广告",
        "脚本",
        "分镜",
        "成片",
        "video",
        "script",
        "tvc",
    )
    return any(marker in lowered for marker in markers)


def _script_step_title(content: str) -> str:
    lowered = content.casefold()
    if "广告" in lowered or "tvc" in lowered:
        return "生成广告脚本草稿"
    return "生成带货脚本草稿"


def _public_goal(content: str) -> str:
    compact = " ".join(content.split())
    if len(compact) <= 40:
        return f"处理视频创作请求：{compact}"
    return f"处理视频创作请求：{compact[:37]}..."


@dataclass(frozen=True)
class VideoAgentSubmission:
    workspace: VideoWorkspace
    plan: AgentPlan


class VideoAgentEntrypoint:
    """把一个已登记的用户 Turn 转换为可恢复的 VideoAgent 首计划。

    HTTP `turns/start` 路径只落确定性短计划并推送 `agent.plan.created`，
    不在请求内等待大模型规划，避免前端长时间无回执。
    """

    def __init__(
        self,
        *,
        runtime_repository: AgentRuntimeRepository,
        video_repository: VideoAgentRepository,
        planner: VideoAgentPlanner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime_repository = runtime_repository
        self._video_repository = video_repository
        # planner 保留装配位，供后续 Runner 异步扩写；不在 submit_turn 热路径调用。
        self._planner = planner
        self._clock = clock or (lambda: datetime.now(UTC))

    async def submit_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        content: str,
        artifact_refs: tuple[str, ...],
        materials: Sequence[Mapping[str, Any]] | None = None,
    ) -> VideoAgentSubmission:
        owner = user_id.strip()
        text = content.strip()
        if not owner or not conversation_id.strip() or not turn_id.strip() or not text:
            raise ValueError("VideoAgent 输入必须包含用户、对话、Turn 和内容")
        occurred_at = self._clock()
        workspace_id = _stable_id("video_workspace", conversation_id)
        plan_id = _stable_id("video_plan", conversation_id, turn_id)
        existing_plan = await self._video_repository.get_plan(owner, plan_id)
        if existing_plan is not None:
            workspace = await self._video_repository.get_workspace(
                owner,
                existing_plan.workspace_id,
            )
            if workspace is None:
                raise ValueError("VideoAgent plan 缺少对应 workspace")
            return VideoAgentSubmission(workspace=workspace, plan=existing_plan)

        safe_materials = _safe_materials(materials)
        product_info = _product_info_from_materials(safe_materials)
        payload = {
            "latest_input": text,
            "artifact_refs": list(artifact_refs),
            "materials": safe_materials,
            "product_info": product_info,
        }
        workspace = await self._video_repository.create_workspace(
            owner,
            VideoWorkspace(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                payload=payload,
                created_at=occurred_at,
                updated_at=occurred_at,
            ),
        )
        if workspace.payload.get("latest_input") != text or (
            safe_materials and workspace.payload.get("materials") != safe_materials
        ):
            # 同会话后续 Turn 复用 workspace，需要写入本轮输入与素材。
            merged_product = {
                **(
                    dict(workspace.payload["product_info"])
                    if isinstance(workspace.payload.get("product_info"), dict)
                    else {}
                ),
                **product_info,
            }
            workspace = await self._video_repository.apply_workspace_patch(
                owner,
                workspace.workspace_id,
                {
                    "latest_input": text,
                    "artifact_refs": list(artifact_refs),
                    "materials": safe_materials or list(
                        workspace.payload.get("materials") or []
                    ),
                    "product_info": merged_product,
                },
                expected_revision=workspace.revision,
                now=occurred_at,
            )
        plan = self._deterministic_plan(
            conversation_id=conversation_id,
            content=text,
            materials=safe_materials,
            workspace=workspace,
            plan_id=plan_id,
            occurred_at=occurred_at,
        )
        plan = await self._video_repository.save_plan(
            owner,
            plan,
            list(plan.steps),
        )
        events = await self._runtime_repository.list_events(owner, conversation_id)
        if not any(
            event.type.value == "agent.plan.created"
            and event.payload.get("plan_id") == plan.plan_id
            for event in events
        ):
            event_id = _stable_id("video_event", plan.plan_id, "created")
            await self._runtime_repository.create_event(
                owner,
                build_plan_created_event(
                    event_id=event_id,
                    cursor=_stable_id("video_cursor", event_id),
                    sequence=1 if not events else events[-1].sequence + 1,
                    conversation_id=conversation_id,
                    run_id=turn_id,
                    occurred_at=occurred_at,
                    plan=plan,
                ),
            )
        return VideoAgentSubmission(workspace=workspace, plan=plan)

    def _deterministic_plan(
        self,
        *,
        conversation_id: str,
        content: str,
        materials: list[dict[str, Any]],
        workspace: VideoWorkspace,
        plan_id: str,
        occurred_at: datetime,
    ) -> AgentPlan:
        steps = [
            AgentPlanStep(
                step_id=_stable_id("video_step", plan_id, "1"),
                plan_id=plan_id,
                sequence=1,
                tool_name="inspect_video_workspace",
                title="读取项目资料",
                status=PlanStepStatus.PENDING,
            )
        ]
        if _should_seed_script_draft(content, materials):
            product_info = workspace.payload.get("product_info")
            # 长故事只把摘要放进工具参数，完整正文已在 workspace.latest_input。
            direction = content if len(content) <= 2_000 else f"{content[:1_900]}…"
            steps.append(
                AgentPlanStep(
                    step_id=_stable_id("video_step", plan_id, "2"),
                    plan_id=plan_id,
                    sequence=2,
                    tool_name="brainstorm_script",
                    title=_script_step_title(content),
                    status=PlanStepStatus.PENDING,
                    arguments={
                        "product_info": (
                            dict(product_info)
                            if isinstance(product_info, dict)
                            else {}
                        ),
                        "video_params": {},
                        "creative_direction": direction,
                    },
                )
            )
        return AgentPlan(
            plan_id=plan_id,
            workspace_id=workspace.workspace_id,
            conversation_id=conversation_id,
            status=AgentPlanStatus.PLANNING,
            public_goal=_public_goal(content),
            steps=tuple(steps),
            created_at=occurred_at,
            updated_at=occurred_at,
        )
