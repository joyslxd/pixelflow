"""安全解析工作区参考视频 Artifact 的受控工具。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.contracts.plan import VideoAgentContract

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)


class AnalyzeReferenceVideoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_asset_ref: str = Field(
        min_length=10,
        max_length=256,
        pattern=r"^artifact:[A-Za-z0-9._:-]+$",
    )
    attempt: int = Field(default=1, ge=1, le=10)


class ReferenceAnalysisOperationJob(VideoAgentContract):
    """M06参考视频Operation暴露给工具的安全任务快照。"""

    job_id: str = Field(min_length=1, max_length=64)
    artifact_ref: str = Field(
        min_length=10,
        max_length=256,
        pattern=r"^artifact:[A-Za-z0-9._:-]+$",
    )
    status: Literal["polling", "succeeded"]
    storyboard: tuple[dict[str, JsonValue], ...] = ()

    @model_validator(mode="after")
    def validate_storyboard(self) -> ReferenceAnalysisOperationJob:
        if self.status == "succeeded" and not self.storyboard:
            raise ValueError("已完成参考视频Operation必须包含分镜")
        if self.status == "polling" and self.storyboard:
            raise ValueError("运行中的参考视频Operation不能提前返回分镜")
        return self


class ReferenceAnalysisOperationPort(Protocol):
    """隔离工具与M06 Operation、临时凭据及供应商Client。"""

    async def start_reference_analysis(
        self,
        context: VideoToolContext,
        *,
        artifact_ref: str,
        video_url: str,
        attempt: int,
    ) -> ReferenceAnalysisOperationJob: ...


class AnalyzeReferenceVideoTool:
    spec = VideoToolSpec(
        name="analyze_reference_video",
        description="解析工作区中的参考视频并保存安全分镜证据",
        input_model=AnalyzeReferenceVideoInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("reference_videos", "scenes"),
    )

    def __init__(
        self,
        *,
        operation_port: ReferenceAnalysisOperationPort | None = None,
    ) -> None:
        self._operation_port = operation_port

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = AnalyzeReferenceVideoInput.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VideoToolValidationError("参考视频工具参数无效") from exc
        artifact_ref = request.reference_asset_ref
        references = _records(context.workspace.payload.get("reference_videos"))
        existing = next(
            (
                item
                for item in references
                if item.get("artifact_ref") == artifact_ref and item.get("status") == "done"
            ),
            None,
        )
        if existing is not None:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="已复用参考视频解析结果",
                artifact_refs=(artifact_ref,),
            )

        asset = _resolve_video_asset(context.workspace.payload, artifact_ref)
        video_url = _safe_video_url(asset)
        if self._operation_port is None:
            raise VideoToolExecutionError("参考视频Operation尚未装配")
        try:
            analysis = await self._operation_port.start_reference_analysis(
                context,
                artifact_ref=artifact_ref,
                video_url=video_url,
                attempt=request.attempt,
            )
        except VideoToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            # 用途：在Operation边界隐藏供应商和持久层异常；影响：本次工具返回固定失败摘要。
            raise VideoToolExecutionError("参考视频解析失败") from exc
        if analysis.artifact_ref != artifact_ref:
            raise VideoToolExecutionError("参考视频Operation结果身份不一致")
        if analysis.status == "polling":
            reference_record: dict[str, JsonValue] = {
                "artifact_ref": artifact_ref,
                "job_id": analysis.job_id,
                "status": "polling",
                "storyboard": [],
            }
            next_references = [
                item for item in references if item.get("artifact_ref") != artifact_ref
            ]
            next_references.append(reference_record)
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="参考视频解析任务已启动",
                workspace_patch={"reference_videos": next_references},
                artifact_refs=(artifact_ref,),
                pending_operation_job_ids=(analysis.job_id,),
            )
        storyboard = [
            _normalized_scene(
                context.workspace.workspace_id,
                artifact_ref,
                index,
                shot,
            )
            for index, shot in enumerate(analysis.storyboard, start=1)
        ]
        reference_record: dict[str, JsonValue] = {
            "artifact_ref": artifact_ref,
            "job_id": analysis.job_id,
            "status": "done",
            "storyboard": storyboard,
        }
        next_references = [
            item for item in references if item.get("artifact_ref") != artifact_ref
        ]
        next_references.append(reference_record)
        scenes = [
            item
            for item in _records(context.workspace.payload.get("scenes"))
            if item.get("source_reference_ref") != artifact_ref
        ]
        scenes.extend(storyboard)
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"参考视频已解析为 {len(storyboard)} 个镜头",
            workspace_patch={
                "reference_videos": next_references,
                "scenes": scenes,
            },
            artifact_refs=(artifact_ref,),
        )


def _records(value: object) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _resolve_video_asset(
    payload: Mapping[str, object],
    artifact_ref: str,
) -> Mapping[str, object]:
    for collection_name in ("assets", "materials"):
        value = payload.get(collection_name)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            ref = item.get("artifact_ref") or item.get("asset_ref")
            if ref != artifact_ref:
                continue
            media_type = str(item.get("media_type") or item.get("type") or "video")
            if "video" not in media_type.lower():
                raise VideoToolValidationError("参考素材不是视频")
            return item
    raise VideoToolValidationError("参考视频素材不存在")


def _safe_video_url(asset: Mapping[str, object]) -> str:
    value = asset.get("url") or asset.get("video_url") or asset.get("source_url")
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise VideoToolValidationError("参考视频缺少安全下载地址")
    return url


def _normalized_scene(
    workspace_id: str,
    artifact_ref: str,
    scene_index: int,
    shot: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    digest = hashlib.sha256(
        f"{workspace_id}:{artifact_ref}:{scene_index}".encode()
    ).hexdigest()[:24]
    safe: dict[str, JsonValue] = {
        key: value
        for key, value in shot.items()
        if key
        in {
            "description",
            "visual_description",
            "duration",
            "duration_sec",
            "shot_type",
            "camera_movement",
            "narration",
            "narration_text",
            "onscreen_text",
            "scene_type",
            "start_time",
            "end_time",
        }
    }
    safe.update(
        {
            "scene_id": f"reference_scene_{digest}",
            "scene_index": scene_index,
            "source_reference_ref": artifact_ref,
        }
    )
    safe["description"] = str(
        safe.get("description") or safe.get("visual_description") or f"参考镜头 {scene_index}"
    )[:2_000]
    return safe
