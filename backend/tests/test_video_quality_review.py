from __future__ import annotations

import asyncio

from pixelflow.qc.video_review import (
    CONTENT_APP_QC_ENDPOINT,
    VideoQCRequest,
    brief_to_scene_packages,
    generated_assets_to_scene_videos,
    review_video_quality,
)
from pixelflow.skills import VideoQualityReviewResult


def test_brief_to_scene_packages_preserves_shot_contract():
    packages = brief_to_scene_packages(
        {
            "shots": [
                {
                    "shot_id": "shot_001",
                    "visual_description": "白色耳机在桌面特写",
                    "generation_prompt": "白色耳机，真实摄影",
                    "narration_text": "通勤降噪",
                    "onscreen_text": "降噪开启",
                }
            ]
        }
    )

    assert packages == [
        {
            "scene_id": "shot_001",
            "scene_index": 1,
            "storyline": "白色耳机在桌面特写",
            "prompt": "白色耳机，真实摄影",
            "narration": "通勤降噪",
            "onscreen_text": "降噪开启",
            "shot_id": "shot_001",
        }
    ]


def test_generated_assets_to_scene_videos_keeps_successful_segments_only():
    videos = generated_assets_to_scene_videos(
        [
            {"segment_index": 0, "shot_indices": [0, 1], "ok": True, "url": "https://x/seg0.mp4"},
            {"segment_index": 1, "shot_indices": [2], "ok": False, "url": "https://x/seg1.mp4"},
            {"segment_index": 2, "shot_indices": [3], "ok": True, "url": ""},
        ]
    )

    assert videos == [
        {
            "scene_id": "segment-0",
            "scene_index": 1,
            "video_url": "https://x/seg0.mp4",
            "segment_index": 0,
            "shot_indices": [0, 1],
        }
    ]


def test_review_video_quality_uses_supplier_qc_only():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            assert kwargs["checks"] == [
                "video_artifact",
                "product_visibility",
                "prompt_alignment",
                "subtitle_accuracy",
                "brief_alignment",
                "playback_stability",
                "constraint_compliance",
            ]
            return VideoQualityReviewResult(
                ok=True,
                task_id="qc-task-1",
                summary_markdown="检测到黑屏和商品露出不足",
                quality_report_markdown="检测到黑屏和商品露出不足",
                issues=[
                    {
                        "code": "black_screen",
                        "category": "playback_stability",
                        "severity": "blocker",
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "message": "第1个分镜出现连续黑屏",
                        "expected": "正常展示商品",
                        "observed": "黑屏",
                        "suggestion": "重新生成第1个分镜",
                    },
                    {
                        "code": "product_not_clear",
                        "category": "product_visibility",
                        "severity": "medium",
                        "scene_id": "scene-2",
                        "message": "商品主体不清晰",
                    },
                ],
                affected_scene_ids=["scene-1", "scene-2"],
                revision_prompt="重生成 scene-1 和 scene-2",
                raw={
                    "endpoint": CONTENT_APP_QC_ENDPOINT,
                    "passed": False,
                    "score": 0.46,
                    "check_results": [
                        {"item": "播放稳定性", "status": "fail", "message": "有黑屏"},
                        {"item": "商品清晰与露出", "status": "warn", "message": "露出不足"},
                    ],
                },
            )

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "storyline": "商品开场"},
                    {"scene_id": "scene-2", "storyline": "卖点展示"},
                ],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.ok is True
    assert result.passed is False
    assert result.score == 0.46
    assert result.endpoint == CONTENT_APP_QC_ENDPOINT
    assert result.summary_markdown == "检测到黑屏和商品露出不足"
    assert result.quality_report_markdown == "检测到黑屏和商品露出不足"
    assert result.affected_scene_ids == ["scene-1", "scene-2"]
    assert result.revision_prompt == "重生成 scene-1 和 scene-2"
    assert [issue.category for issue in result.issues] == ["playback_stability", "product_visibility"]
    assert result.issues[1].severity == "minor"
    assert [item.item for item in result.check_results] == ["播放稳定性", "商品清晰与露出"]


def test_review_video_quality_supplier_failure_does_not_run_local_fallbacks():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=False,
                error="content-app QC failed",
                raw={"endpoint": CONTENT_APP_QC_ENDPOINT},
            )

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[],
                scene_packages=[{"scene_id": "scene-1"}],
                expected_duration_sec=300,
            ),
            skill=FakeSkill(),
        )
    )

    assert result.ok is False
    assert result.passed is False
    assert result.issues == []
    assert result.error == "content-app QC failed"
    assert result.check_results[0].item == "视频 QC 质检"
