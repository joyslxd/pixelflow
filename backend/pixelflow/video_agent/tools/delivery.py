"""VideoAgent视频合成与交付前置校验工具。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.contracts.plan import VideoAgentContract
from pixelflow.video_agent.quota import build_start_quota_interrupt_id

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

DeliveryOutputType = Literal["mp4", "jianying_package"]


class ComposeOrExportVideoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_type: DeliveryOutputType
    attempt: int = Field(default=1, ge=1, le=10)


class DeliveryOperationJob(VideoAgentContract):
    """交付Operation返回的安全内部产物引用。"""

    job_id: str = Field(min_length=1, max_length=64)
    output_type: DeliveryOutputType
    status: Literal["polling", "start_paused_quota", "succeeded"]
    artifact_ref: str | None = Field(
        default=None,
        pattern=r"^artifact:[A-Za-z0-9._:-]+$",
        max_length=256,
    )
    # 成片 HTTPS URL；仅 succeeded 时写入，供工作台资产包预览回填。
    delivery_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_artifact(self) -> DeliveryOperationJob:
        if self.status == "succeeded" and self.artifact_ref is None:
            raise ValueError("已完成交付Operation必须包含产物引用")
        if self.status in {"polling", "start_paused_quota"} and self.artifact_ref is not None:
            raise ValueError("运行中的交付Operation不能提前包含产物")
        if self.status in {"polling", "start_paused_quota"} and self.delivery_url is not None:
            raise ValueError("运行中的交付Operation不能提前包含成片URL")
        if self.delivery_url is not None:
            normalized = self.delivery_url.strip()
            if not normalized.lower().startswith("https://"):
                raise ValueError("交付URL必须是HTTPS")
            object.__setattr__(self, "delivery_url", normalized)
        return self


class DeliveryOperationPort(Protocol):
    """隔离交付工具与合成、剪映M06 Operation及供应商Client。"""

    async def start_delivery(
        self,
        context: VideoToolContext,
        *,
        output_type: DeliveryOutputType,
        scenes: Sequence[Mapping[str, JsonValue]],
        attempt: int,
    ) -> DeliveryOperationJob: ...


class ComposeOrExportVideoTool:
    spec = VideoToolSpec(
        name="compose_or_export_video",
        description=(
            "合并已生成的分镜视频为 MP4 成片，或导出剪映工程包。"
            "用户说「合并视频/合成成片」时调用，output_type 用 mp4；"
            "不要虚构 merge_videos 工具名。"
        ),
        input_model=ComposeOrExportVideoInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("deliveries", "outputs", "merged_video"),
    )

    def __init__(self, *, operation_port: DeliveryOperationPort | None = None) -> None:
        self._operation_port = operation_port

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = ComposeOrExportVideoInput.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VideoToolValidationError("视频交付参数无效") from exc
        selected_scenes = _validated_delivery_scenes(context.workspace.payload)
        if self._operation_port is None:
            raise VideoToolExecutionError("视频交付Operation尚未装配")
        try:
            job = await self._operation_port.start_delivery(
                context,
                output_type=request.output_type,
                scenes=selected_scenes,
                attempt=request.attempt,
            )
        except VideoToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            # 用途：隐藏供应商原文，但保留异常类型便于对照网关/租约类故障。
            cause = type(exc).__name__
            raise VideoToolExecutionError(
                f"视频交付Operation执行失败：{cause}"
            ) from exc
        if job.output_type != request.output_type:
            raise VideoToolExecutionError("视频交付Operation结果身份不一致")
        deliveries = _records(context.workspace.payload.get("deliveries"))
        delivery_record: dict[str, JsonValue] = {
            "job_id": job.job_id,
            "plan_step_id": context.step_id,
            "output_type": job.output_type,
            "status": job.status,
            "artifact_ref": job.artifact_ref,
        }
        if job.delivery_url:
            delivery_record["video_url" if job.output_type == "mp4" else "download_url"] = (
                job.delivery_url
            )
        next_deliveries = [
            item for item in deliveries if item.get("output_type") != job.output_type
        ]
        next_deliveries.append(delivery_record)
        if job.status in {"polling", "start_paused_quota"}:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="视频交付任务已启动",
                workspace_patch={
                    "deliveries": next_deliveries,
                    **(
                        {
                            "quota_interrupt": {
                                "quota_interrupt_id": build_start_quota_interrupt_id(job.job_id),
                                "plan_id": context.plan_id,
                                "step_id": context.step_id,
                                "job_id": job.job_id,
                                "quota_pause_revision": 0,
                                "phase": "start",
                                "state": "paused",
                                "reason_code": "provider_quota_insufficient",
                            }
                        }
                        if job.status == "start_paused_quota"
                        else {"quota_interrupt": None}
                    ),
                },
                pending_operation_job_ids=(job.job_id,),
                requires_confirmation=True,
            )
        outputs = _records(context.workspace.payload.get("outputs"))
        next_outputs = [
            item for item in outputs if item.get("output_type") != job.output_type
        ]
        output_record: dict[str, JsonValue] = {
            "output_type": job.output_type,
            "artifact_ref": job.artifact_ref,
            "source_job_id": job.job_id,
            "plan_step_id": context.step_id,
        }
        if job.delivery_url:
            output_record["video_url" if job.output_type == "mp4" else "download_url"] = (
                job.delivery_url
            )
        next_outputs.append(output_record)
        workspace_patch: dict[str, JsonValue] = {
            "deliveries": next_deliveries,
            "outputs": next_outputs,
        }
        # 与 Legacy 资产包卡片字段对齐，便于右侧「查看合并后的视频」直接读 URL。
        if job.output_type == "mp4" and job.delivery_url:
            workspace_patch["merged_video"] = {
                "ok": True,
                "merged_video_url": job.delivery_url,
                "task_id": job.job_id,
            }
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=(
                "MP4成片已生成"
                if job.output_type == "mp4"
                else "剪映工程包已生成"
            ),
            workspace_patch=workspace_patch,
            artifact_refs=(job.artifact_ref,),
            requires_confirmation=True,
        )


def _validated_delivery_scenes(
    payload: Mapping[str, object],
) -> list[dict[str, JsonValue]]:
    dirty = _text_list(payload.get("dirty_scene_ids"))
    if dirty:
        raise VideoToolValidationError("dirty_scene_ids仍有未完成镜头")
    qc = payload.get("qc")
    if isinstance(qc, Mapping):
        unresolved = [
            str(scene_id)
            for scene_id, value in qc.items()
            if isinstance(value, Mapping)
            and str(value.get("status") or "").lower()
            in {"dirty", "failed", "repairable", "unresolved"}
        ]
        if unresolved:
            raise VideoToolValidationError("仍有未处理质检问题")
    scenes = _records(payload.get("scenes"))
    if not scenes:
        raise VideoToolValidationError("工作区没有可交付镜头")
    selected: list[dict[str, JsonValue]] = []
    seen_indexes: set[int] = set()
    for scene in scenes:
        scene_id = str(scene.get("scene_id") or "").strip()
        scene_index = scene.get("scene_index")
        if (
            not scene_id
            or isinstance(scene_index, bool)
            or not isinstance(scene_index, int)
            or scene_index < 1
            or scene_index in seen_indexes
        ):
            raise VideoToolValidationError("工作区镜头顺序无效")
        seen_indexes.add(scene_index)
        approved = _resolve_delivery_variant(scene)
        artifact_ref = approved.get("artifact_ref") if approved else None
        if not approved or not _is_artifact_ref(artifact_ref):
            raise VideoToolValidationError("所有镜头必须选用审核通过的内部版本")
        selected.append(
            {
                "scene_id": scene_id,
                "_scene_index": scene_index,
                "variant_id": str(approved.get("variant_id") or "").strip(),
                "artifact_ref": str(artifact_ref),
            }
        )
    ordered = sorted(selected, key=lambda item: int(item["_scene_index"]))
    return [
        {key: value for key, value in item.items() if key != "_scene_index"}
        for item in ordered
    ]


def _resolve_delivery_variant(
    scene: Mapping[str, object],
) -> dict[str, JsonValue] | None:
    """优先 approved_variant_id；否则取已选中且含 HTTPS 成片的版本。"""

    variants = _records(scene.get("variants"))
    approved_variant_id = str(scene.get("approved_variant_id") or "").strip()
    if approved_variant_id:
        approved = next(
            (
                item
                for item in variants
                if item.get("variant_id") == approved_variant_id
                and item.get("review_status") == "approved"
                and item.get("selected") is True
            ),
            None,
        )
        if approved is not None:
            return approved
    # 异步回写后可能只有 selected/approved 标记、未写 approved_variant_id。
    selected_ready = [
        item
        for item in variants
        if item.get("selected") is True
        and str(item.get("review_status") or "") == "approved"
        and _is_https_url(item.get("video_url"))
        and _is_artifact_ref(item.get("artifact_ref"))
    ]
    if len(selected_ready) == 1:
        return selected_ready[0]
    # 单版本成片：默认可交付。
    ready = [
        item
        for item in variants
        if _is_https_url(item.get("video_url")) and _is_artifact_ref(item.get("artifact_ref"))
    ]
    if len(ready) == 1:
        return ready[0]
    return None


def _is_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower().startswith("https://")


def _records(value: object) -> list[dict[str, JsonValue]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _text_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _is_artifact_ref(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("artifact:"):
        return False
    suffix = value.removeprefix("artifact:")
    return bool(suffix) and all(
        character.isalnum() or character in "._:-" for character in suffix
    )
