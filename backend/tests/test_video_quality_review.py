from __future__ import annotations

import asyncio

from pixelflow.qc.video_review import (
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


def test_review_video_quality_maps_supplier_issues_and_blocker_fails():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            assert kwargs["checks"] == ["plan_consistency", "playback_stability"]
            return VideoQualityReviewResult(
                ok=True,
                task_id="qc-task-1",
                summary_markdown="检测到黑屏",
                issues=[
                    {
                        "code": "black_screen",
                        "category": "playback_stability",
                        "severity": "blocker",
                        "scene_id": "scene-1",
                        "message": "检测到连续黑屏片段",
                        "expected": "正常画面",
                        "observed": "黑屏",
                        "suggestion": "重新生成该分镜",
                    }
                ],
                affected_scene_ids=["scene-1"],
                revision_prompt="重生成 scene-1，避免黑屏",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
                scene_packages=[{"scene_id": "scene-1", "storyline": "白色耳机展示"}],
                checks=["plan_consistency", "playback_stability"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.ok is True
    assert result.passed is False
    assert result.task_id == "qc-task-1"
    assert result.summary_markdown == "检测到黑屏"
    assert result.affected_scene_ids == ["scene-1"]
    assert result.revision_prompt == "重生成 scene-1，避免黑屏"
    assert result.issues[0].category == "playback_stability"
    assert result.issues[0].severity == "blocker"
    assert any(item.item == "播放稳定性" and item.status == "fail" for item in result.check_results)


def test_review_video_quality_merges_deterministic_qc_and_supplier_issues(monkeypatch):
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="语义检查发现方案偏差",
                issues=[
                    {
                        "code": "plan_mismatch",
                        "category": "plan_consistency",
                        "severity": "major",
                        "scene_id": "scene-1",
                        "message": "方案要求白色耳机，成片主体偏成黑色手表",
                    }
                ],
                affected_scene_ids=["scene-1"],
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    monkeypatch.setattr("pixelflow.qc.check._probe_video", lambda path: {"width": 1080, "height": 1920, "duration": 5.0})
    monkeypatch.setattr("pixelflow.qc.check._has_black_frames", lambda path: False)
    monkeypatch.setattr("pixelflow.qc.check._has_freeze_frames", lambda path: True)

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="/tmp/freeze.mp4",
                scene_videos=[{"scene_id": "scene-1", "scene_index": 1, "video_url": "/tmp/scene-1.mp4"}],
                scene_packages=[{"scene_id": "scene-1", "storyline": "白色耳机展示"}],
                brief={"ratio": "9:16", "size": "1080x1920", "duration_sec": 5},
                checks=["plan_consistency", "playback_stability", "mobile_requirements"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.ok is True
    assert result.passed is False
    assert any(item.item == "卡顿/冻结检测" and item.status == "fail" for item in result.check_results)
    assert any(item.item == "手机端画幅适配" and item.status == "pass" for item in result.check_results)
    assert any(item.item == "方案一致性" and item.status == "warn" for item in result.check_results)
    assert any(issue.category == "playback_stability" and issue.severity == "blocker" for issue in result.issues)
    assert any(issue.category == "plan_consistency" and issue.severity == "major" for issue in result.issues)


def test_review_video_quality_supplier_failure_fails_report():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(ok=False, error="供应商不可用", raw={"endpoint": "/api/creative/analyze_video_flaws"})

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.ok is False
    assert result.passed is False
    assert result.error == "供应商不可用"
    assert any(item.item == "综合语义质检" and item.status == "fail" for item in result.check_results)
