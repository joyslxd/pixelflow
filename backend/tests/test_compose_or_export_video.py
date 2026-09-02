"""验证成片交付前置校验：缺序号、残留脏镜、多版本和生成中镜头。"""

from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolValidationError
from pixelflow.agent_tools.video.delivery import (
    ComposeOrExportVideoTool,
    DeliveryOperationJob,
)
from pixelflow.video.contracts import VideoWorkspace


class _RecordingPort:
    def __init__(self) -> None:
        self.scenes: list[dict[str, object]] | None = None

    async def start_delivery(self, context, *, output_type, scenes, attempt):
        del context, attempt
        self.scenes = [dict(item) for item in scenes]
        return DeliveryOperationJob(
            job_id="delivery-test-succeeded",
            output_type=output_type,
            status="succeeded",
            artifact_ref="artifact:merge:test1",
            delivery_url="https://cdn.example.invalid/merged.mp4",
        )


def _variant(suffix: str, *, selected: bool = False, review: str = "pending") -> dict[str, object]:
    return {
        "variant_id": f"variant:{suffix}",
        "artifact_ref": f"artifact:video:{suffix}",
        "video_url": f"https://cdn.example.invalid/{suffix}.mp4",
        "selected": selected,
        "review_status": review,
    }


def _context(payload: dict[str, object]) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-delivery",
            conversation_id="conversation-delivery",
            payload=payload,
        ),
        run_id="hrun_delivery_test",
        tool_call_id="tool-call-delivery",
    )


@pytest.mark.asyncio
async def test_compose_uses_list_order_and_latest_variant_when_index_and_review_missing() -> None:
    port = _RecordingPort()
    result = await ComposeOrExportVideoTool(operation_port=port).execute(
        _context(
            {
                "dirty_scene_ids": ["scene_a", "scene_b"],
                "scenes": [
                    {
                        "scene_id": "scene_a",
                        "variants": [
                            _variant("a1", selected=False, review="pending"),
                            _variant("a2", selected=False, review="pending"),
                        ],
                    },
                    {
                        "scene_id": "scene_b",
                        "variants": [_variant("b1", selected=False, review="pending")],
                    },
                ],
            }
        ),
        {"output_type": "mp4"},
    )

    assert result.public_summary == "MP4成片已生成"
    assert port.scenes == [
        {
            "scene_id": "scene_a",
            "variant_id": "variant:a2",
            "artifact_ref": "artifact:video:a2",
        },
        {
            "scene_id": "scene_b",
            "variant_id": "variant:b1",
            "artifact_ref": "artifact:video:b1",
        },
    ]


@pytest.mark.asyncio
async def test_compose_rejects_inflight_scene_even_when_other_scenes_are_ready() -> None:
    with pytest.raises(VideoToolValidationError, match="仍有镜头正在生成"):
        await ComposeOrExportVideoTool(operation_port=_RecordingPort()).execute(
            _context(
                {
                    "dirty_scene_ids": ["scene_b"],
                    "scenes": [
                        {"scene_id": "scene_a", "scene_index": 1, "variants": [_variant("a1")]},
                        {
                            "scene_id": "scene_b",
                            "scene_index": 2,
                            "generation_jobs": [{"job_id": "job-b", "status": "polling"}],
                            "variants": [],
                        },
                    ],
                }
            ),
            {"output_type": "mp4"},
        )


@pytest.mark.asyncio
async def test_compose_without_port_still_reports_unassembled() -> None:
    with pytest.raises(VideoToolValidationError, match="工作区没有可交付镜头"):
        await ComposeOrExportVideoTool().execute(
            _context({"scenes": []}),
            {"output_type": "mp4"},
        )
