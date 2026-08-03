"""验证 Gateway 视频 live Provider 的全有或全无装配。"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.gateway.pixelflow_agent_live_providers import (
    VIDEO_LIVE_HANDLER_NOT_READY,
    make_video_live_provider_adapters,
)
from pixelflow.agent_runtime.jobs import ProviderJobAdapter


class _ExistingJobService:
    """记录调用但不访问网络的 ExistingJobService 测试实现。"""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.start_calls = 0
        self.status_calls = 0

    async def start(
        self,
        request: Mapping[str, object],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        del request, authorization, idempotency_key
        self.start_calls += 1
        return {
            "job_id": f"{self.stage}-job-1",
            "status": "running",
        }

    async def status(self, provider_job_id: str) -> dict[str, object]:
        self.status_calls += 1
        return {
            "job_id": provider_job_id,
            "status": "running",
        }


def _services() -> dict[str, _ExistingJobService]:
    return {
        "generate_scene_video": _ExistingJobService("scene"),
        "merge_video": _ExistingJobService("merge"),
        "quality_review": _ExistingJobService("quality"),
        "jianying_draft": _ExistingJobService("jianying"),
    }


def test_provider_factory_returns_complete_finite_adapter_set() -> None:
    services = _services()

    result = make_video_live_provider_adapters(
        generate_scene_video=services["generate_scene_video"],
        merge_video=services["merge_video"],
        quality_review=services["quality_review"],
        jianying_draft=services["jianying_draft"],
    )

    assert result.ready is True
    assert result.reason_code is None
    assert tuple(result.adapters) == (
        "generate_scene_video",
        "merge_video",
        "quality_review",
        "jianying_draft",
    )
    assert all(
        isinstance(adapter, ProviderJobAdapter)
        for adapter in result.adapters.values()
    )
    assert all(service.start_calls == 0 for service in services.values())
    assert all(service.status_calls == 0 for service in services.values())


@pytest.mark.parametrize(
    "missing_stage",
    [
        "generate_scene_video",
        "merge_video",
        "quality_review",
        "jianying_draft",
    ],
)
def test_provider_factory_fails_closed_when_any_stage_is_missing(
    missing_stage: str,
) -> None:
    services = _services()
    services[missing_stage] = None  # type: ignore[assignment]

    result = make_video_live_provider_adapters(
        generate_scene_video=services["generate_scene_video"],
        merge_video=services["merge_video"],
        quality_review=services["quality_review"],
        jianying_draft=services["jianying_draft"],
    )

    assert result.ready is False
    assert result.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
    assert result.adapters == {}


def test_provider_factory_defaults_to_not_ready_without_injected_services() -> None:
    result = make_video_live_provider_adapters()

    assert result.ready is False
    assert result.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
    assert result.adapters == {}
