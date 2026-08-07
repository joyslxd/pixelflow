from __future__ import annotations

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import (
    ComposeOrExportVideoTool,
    DeliveryOperationJob,
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolValidationError,
)


def _scene(
    scene_id: str,
    scene_index: int,
    *,
    approved: bool = True,
) -> dict:
    variant_id = f"{scene_id}-v2"
    return {
        "scene_id": scene_id,
        "scene_index": scene_index,
        "approved_variant_id": variant_id if approved else None,
        "variants": [
            {
                "variant_id": variant_id,
                "artifact_ref": f"artifact:{variant_id}",
                "video_url": f"https://cdn.example.invalid/{variant_id}.mp4",
                "source_job_id": f"job-{variant_id}",
                "review_status": "approved" if approved else "pending",
                "selected": approved,
            }
        ],
    }


def _context(payload: dict | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        plan_id="plan-delivery-1",
        step_id="step-delivery-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-delivery-1",
            conversation_id="conversation-delivery-1",
            payload=payload
            or {
                "scenes": [_scene("scene-2", 2), _scene("scene-1", 1)],
                "dirty_scene_ids": [],
                "qc": {
                    "scene-1": {"status": "resolved"},
                    "scene-2": {"status": "resolved"},
                },
            },
        ),
    )


class RecordingDeliveryOperationPort:
    def __init__(self, *, status: str = "succeeded") -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []

    async def start_delivery(
        self,
        context: VideoToolContext,
        *,
        output_type: str,
        scenes,
        attempt: int,
    ) -> DeliveryOperationJob:
        self.calls.append(
            {
                "context": context,
                "output_type": output_type,
                "scenes": [dict(scene) for scene in scenes],
                "attempt": attempt,
            }
        )
        return DeliveryOperationJob(
            job_id=f"job-{output_type}-1",
            output_type=output_type,
            status=self.status,
            artifact_ref=(
                f"artifact:delivery-{output_type}-1"
                if self.status == "succeeded"
                else None
            ),
        )


@pytest.mark.asyncio
async def test_export_rejects_workspace_with_unresolved_dirty_scenes() -> None:
    port = RecordingDeliveryOperationPort()
    with pytest.raises(VideoToolValidationError, match="dirty_scene_ids"):
        await ComposeOrExportVideoTool(operation_port=port).execute(
            _context(
                {
                    "scenes": [_scene("scene-1", 1)],
                    "dirty_scene_ids": ["scene-1"],
                }
            ),
            {"output_type": "mp4"},
        )

    assert port.calls == []


@pytest.mark.asyncio
async def test_export_rejects_unresolved_qc_or_unapproved_variant() -> None:
    port = RecordingDeliveryOperationPort()
    with pytest.raises(VideoToolValidationError, match="质检"):
        await ComposeOrExportVideoTool(operation_port=port).execute(
            _context(
                {
                    "scenes": [_scene("scene-1", 1)],
                    "dirty_scene_ids": [],
                    "qc": {"scene-1": {"status": "repairable"}},
                }
            ),
            {"output_type": "mp4"},
        )
    with pytest.raises(VideoToolValidationError, match="审核通过"):
        await ComposeOrExportVideoTool(operation_port=port).execute(
            _context(
                {
                    "scenes": [_scene("scene-1", 1, approved=False)],
                    "dirty_scene_ids": [],
                }
            ),
            {"output_type": "mp4"},
        )

    assert port.calls == []


@pytest.mark.asyncio
async def test_mp4_export_orders_approved_variants_and_persists_artifact() -> None:
    port = RecordingDeliveryOperationPort()
    tool = ComposeOrExportVideoTool(operation_port=port)

    result = await tool.execute(_context(), {"output_type": "mp4", "attempt": 2})

    assert tool.spec.confirmation_required is True
    assert tool.spec.cost_level is VideoToolCostLevel.BILLABLE
    assert [scene["scene_id"] for scene in port.calls[0]["scenes"]] == [
        "scene-1",
        "scene-2",
    ]
    assert port.calls[0]["attempt"] == 2
    assert result.workspace_patch["outputs"][0]["artifact_ref"] == (
        "artifact:delivery-mp4-1"
    )
    assert result.artifact_refs == ("artifact:delivery-mp4-1",)
    assert result.requires_confirmation is True


@pytest.mark.asyncio
async def test_jianying_export_keeps_step_running_until_operation_finishes() -> None:
    port = RecordingDeliveryOperationPort(status="polling")

    result = await ComposeOrExportVideoTool(operation_port=port).execute(
        _context(),
        {"output_type": "jianying_package"},
    )

    assert result.pending_operation_job_ids == ("job-jianying_package-1",)
    assert result.workspace_patch["deliveries"][0]["status"] == "polling"
    assert "outputs" not in result.workspace_patch


@pytest.mark.asyncio
async def test_delivery_without_operation_port_fails_closed() -> None:
    with pytest.raises(VideoToolExecutionError, match="尚未装配"):
        await ComposeOrExportVideoTool().execute(
            _context(),
            {"output_type": "mp4"},
        )
