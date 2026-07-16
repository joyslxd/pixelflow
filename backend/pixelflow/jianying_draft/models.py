"""剪映草稿流程使用的 PixelFlow 内部领域模型。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator


class JianyingDraftStatus(StrEnum):
    """剪映草稿任务的统一内部状态。"""

    NOT_CONFIGURED = "not_configured"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


class JianyingDraftScene(BaseModel):
    """当前视频版本中的一个有效分镜。"""

    scene_id: str = Field(min_length=1)
    scene_index: int = Field(ge=1)
    video_url: AnyHttpUrl
    task_id: str | None = None

    @field_validator("video_url")
    @classmethod
    def require_https_video_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("video_url must use HTTPS")
        return value


def compute_storyboard_version_id(scenes: Sequence[JianyingDraftScene]) -> str:
    """按固定规范为有效分镜集合计算稳定的 FNV-1a 64 位版本 ID。"""

    if not scenes:
        raise ValueError("scenes cannot be empty")

    scene_indexes = [scene.scene_index for scene in scenes]
    if len(scene_indexes) != len(set(scene_indexes)):
        raise ValueError("scene_index values must be unique")

    ordered = sorted(scenes, key=lambda item: item.scene_index)
    payload = [
        {
            "scene_id": item.scene_id,
            "scene_index": item.scene_index,
            "task_id": item.task_id or "",
            "video_url": str(item.video_url),
        }
        for item in ordered
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    value = 0xCBF29CE484222325
    for byte in canonical.encode("utf-8"):
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"storyboard-{value:016x}"


class JianyingDraftRequest(BaseModel):
    """提交剪映草稿生成所需的 PixelFlow 内部请求。"""

    conversation_id: str = Field(min_length=1)
    storyboard_version_id: str = Field(min_length=1)
    scenes: list[JianyingDraftScene]
    video_task_id: str | None = None
    project_name: str | None = None

    @model_validator(mode="after")
    def validate_storyboard(self) -> JianyingDraftRequest:
        expected_version_id = compute_storyboard_version_id(self.scenes)
        if self.storyboard_version_id != expected_version_id:
            raise ValueError("storyboard_version_id does not match the supplied scenes")
        return self


class JianyingDraftStartRequest(JianyingDraftRequest):
    """网关启动 DTO；仅用户明确重试失败任务时传递 ``retry_failed``。"""

    retry_failed: bool = False


class JianyingDraftResult(BaseModel):
    """剪映草稿任务对 Service/API 暴露的统一结果。"""

    status: JianyingDraftStatus
    job_id: str | None = None
    provider_task_id: str | None = None
    conversation_id: str | None = None
    storyboard_version_id: str | None = None
    download_url: AnyHttpUrl | None = None
    file_name: str | None = None
    expire_at: datetime | None = None
    message: str = ""

    @field_validator("download_url")
    @classmethod
    def require_https_download_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("download_url must use HTTPS")
        return value

    @model_validator(mode="after")
    def require_download_url_for_succeeded_result(self) -> JianyingDraftResult:
        if self.status == JianyingDraftStatus.SUCCEEDED and self.download_url is None:
            raise ValueError("succeeded result requires an HTTPS download_url")
        return self
