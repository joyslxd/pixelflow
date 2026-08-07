from __future__ import annotations

import pytest

from pixelflow.skills.base import StoryboardResult
from pixelflow.video_agent.adapters.video_domain import (
    PixelFlowVideoDomainAdapter,
)
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import (
    VideoToolContext,
    VideoToolRegistry,
    VideoToolValidationError,
)
from pixelflow.video_agent.tools.reference import (
    AnalyzeReferenceVideoTool,
    ReferenceAnalysisOperationJob,
)


def _context(payload: dict | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        plan_id="plan-1",
        step_id="step-reference-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload=payload
            or {
                "assets": [
                    {
                        "artifact_ref": "artifact:ref-1",
                        "media_type": "video",
                        "url": "https://example.invalid/reference.mp4?token=sensitive",
                    }
                ]
            },
        ),
    )


class FakeReferenceAnalysisOperationPort:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def start_reference_analysis(
        self,
        context: VideoToolContext,
        *,
        artifact_ref: str,
        video_url: str,
        attempt: int,
    ) -> ReferenceAnalysisOperationJob:
        del context, attempt
        self.urls.append(video_url)
        return ReferenceAnalysisOperationJob(
            job_id="reference-job-1",
            artifact_ref=artifact_ref,
            status="succeeded",
            storyboard=(
                {
                    "description": "开场展示痛点",
                    "duration": 3,
                    "shot_type": "近景",
                },
                {
                    "description": "商品解决问题",
                    "duration": 5,
                    "shot_type": "特写",
                },
            ),
        )


@pytest.mark.asyncio
async def test_reference_analysis_persists_safe_storyboard_and_scene_evidence() -> None:
    operation_port = FakeReferenceAnalysisOperationPort()
    result = await AnalyzeReferenceVideoTool(operation_port=operation_port).execute(
        _context(),
        {"reference_asset_ref": "artifact:ref-1"},
    )

    reference = result.workspace_patch["reference_videos"][0]
    assert reference["artifact_ref"] == "artifact:ref-1"
    assert reference["job_id"] == "reference-job-1"
    assert reference["status"] == "done"
    assert reference["storyboard"][0]["scene_id"]
    assert result.workspace_patch["scenes"] == reference["storyboard"]
    assert result.artifact_refs == ("artifact:ref-1",)
    assert operation_port.urls == ["https://example.invalid/reference.mp4?token=sensitive"]
    assert "sensitive" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_reference_analysis_replay_reuses_persisted_job() -> None:
    operation_port = FakeReferenceAnalysisOperationPort()
    tool = AnalyzeReferenceVideoTool(operation_port=operation_port)
    first = await tool.execute(
        _context(),
        {"reference_asset_ref": "artifact:ref-1"},
    )
    replay_payload = {
        **_context().workspace.payload,
        **first.workspace_patch,
    }
    replay = await tool.execute(
        _context(replay_payload),
        {"reference_asset_ref": "artifact:ref-1"},
    )

    assert replay.workspace_patch == {}
    assert replay.artifact_refs == first.artifact_refs
    assert len(operation_port.urls) == 1


@pytest.mark.asyncio
async def test_reference_analysis_rejects_unknown_or_non_video_artifact() -> None:
    tool = AnalyzeReferenceVideoTool(
        operation_port=FakeReferenceAnalysisOperationPort()
    )

    with pytest.raises(VideoToolValidationError, match="参考视频素材不存在"):
        await tool.execute(
            _context(),
            {"reference_asset_ref": "artifact:missing"},
        )


class FakeDecomposeSkill:
    async def decompose_video_to_storyboard(self, video_url: str) -> StoryboardResult:
        return StoryboardResult(
            ok=True,
            shots=[
                {
                    "description": "安全镜头摘要",
                    "duration": 3,
                    "provider_secret": "secret-value",
                    "source_url": video_url,
                }
            ],
            raw={"token": "secret-value"},
        )


@pytest.mark.asyncio
async def test_domain_adapter_whitelists_provider_storyboard_fields() -> None:
    adapter = PixelFlowVideoDomainAdapter(
        decompose_skill_factory=FakeDecomposeSkill,
    )

    analysis = await adapter.analyze_reference_video(
        "https://example.invalid/reference.mp4?token=sensitive"
    )

    assert analysis.storyboard[0]["description"] == "安全镜头摘要"
    assert "provider_secret" not in analysis.storyboard[0]
    assert "source_url" not in analysis.storyboard[0]
    assert "sensitive" not in analysis.model_dump_json()


class FailingReferenceAnalysisOperationPort:
    async def start_reference_analysis(
        self,
        context: VideoToolContext,
        *,
        artifact_ref: str,
        video_url: str,
        attempt: int,
    ) -> ReferenceAnalysisOperationJob:
        del context, artifact_ref, video_url, attempt
        raise RuntimeError("Bearer secret-value")


@pytest.mark.asyncio
async def test_registry_maps_reference_provider_failure_to_fixed_summary() -> None:
    tool = AnalyzeReferenceVideoTool(
        operation_port=FailingReferenceAnalysisOperationPort()
    )
    result = await VideoToolRegistry([tool]).execute(
        _context(),
        tool.spec.name,
        {"reference_asset_ref": "artifact:ref-1"},
    )

    assert result.public_summary == "工具执行失败，请稍后重试"
    assert result.workspace_patch == {}
    assert "secret-value" not in result.model_dump_json()


class PollingReferenceAnalysisOperationPort:
    async def start_reference_analysis(
        self,
        context: VideoToolContext,
        *,
        artifact_ref: str,
        video_url: str,
        attempt: int,
    ) -> ReferenceAnalysisOperationJob:
        del context, video_url, attempt
        return ReferenceAnalysisOperationJob(
            job_id="operation-reference-1",
            artifact_ref=artifact_ref,
            status="polling",
        )


@pytest.mark.asyncio
async def test_reference_analysis_keeps_operation_pending_without_fake_completion() -> None:
    result = await AnalyzeReferenceVideoTool(
        operation_port=PollingReferenceAnalysisOperationPort()
    ).execute(
        _context(),
        {"reference_asset_ref": "artifact:ref-1"},
    )

    assert result.pending_operation_job_ids == ("operation-reference-1",)
    assert result.workspace_patch["reference_videos"][0]["status"] == "polling"
    assert result.workspace_patch.get("scenes") is None
