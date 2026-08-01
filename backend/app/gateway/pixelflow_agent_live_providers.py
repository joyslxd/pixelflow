"""受控构造视频 live Workflow 使用的有限 Provider adapters。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pixelflow.agent_runtime.jobs import ExistingJobService, ProviderJobAdapter

VIDEO_LIVE_HANDLER_NOT_READY = "video_live_handler_not_ready"
VIDEO_LIVE_PROVIDER_STAGES = (
    "generate_scene_video",
    "merge_video",
    "quality_review",
    "jianying_draft",
)


@dataclass(frozen=True, slots=True)
class VideoLiveProviderAdapters:
    """保存全量有限 adapter 集或固定失败关闭结果。"""

    adapters: Mapping[str, ProviderJobAdapter]
    reason_code: str | None

    @property
    def ready(self) -> bool:
        """只有四个 stage 全部存在时才声明可注册。"""

        return (
            self.reason_code is None
            and tuple(self.adapters) == VIDEO_LIVE_PROVIDER_STAGES
        )


def make_video_live_provider_adapters(
    *,
    generate_scene_video: ExistingJobService | None = None,
    merge_video: ExistingJobService | None = None,
    quality_review: ExistingJobService | None = None,
    jianying_draft: ExistingJobService | None = None,
) -> VideoLiveProviderAdapters:
    """仅包装显式注入的四类 Service，构造期绝不发起 Provider 调用。"""

    services = {
        "generate_scene_video": generate_scene_video,
        "merge_video": merge_video,
        "quality_review": quality_review,
        "jianying_draft": jianying_draft,
    }
    if any(
        not isinstance(services[stage], ExistingJobService)
        for stage in VIDEO_LIVE_PROVIDER_STAGES
    ):
        return VideoLiveProviderAdapters(
            adapters=MappingProxyType({}),
            reason_code=VIDEO_LIVE_HANDLER_NOT_READY,
        )
    adapters = {
        stage: ProviderJobAdapter(services[stage])  # type: ignore[arg-type]
        for stage in VIDEO_LIVE_PROVIDER_STAGES
    }
    return VideoLiveProviderAdapters(
        adapters=MappingProxyType(adapters),
        reason_code=None,
    )


__all__ = [
    "VIDEO_LIVE_HANDLER_NOT_READY",
    "VIDEO_LIVE_PROVIDER_STAGES",
    "VideoLiveProviderAdapters",
    "make_video_live_provider_adapters",
]
