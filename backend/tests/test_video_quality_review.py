from __future__ import annotations

import asyncio

from pixelflow.qc.video_review import (
    VideoQCRequest,
    brief_to_scene_packages,
    generated_assets_to_scene_videos,
    review_video_quality,
)
from pixelflow.skills import StoryboardResult, VideoQualityReviewResult


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


def test_review_video_quality_prefers_issue_scene_ids_over_broad_supplier_affected_ids():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="第2个分镜主体错误",
                issues=[
                    {
                        "code": "product_mismatch",
                        "category": "product_consistency",
                        "severity": "major",
                        "scene_id": "scene-2",
                        "message": "方案要求白色电动牙刷，画面主体变成红色手机",
                    }
                ],
                affected_scene_ids=["scene-1", "scene-2", "scene-3"],
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-3", "scene_index": 3, "video_url": "https://x/scene-3.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "牙刷开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "牙刷清洁力展示"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "牙刷防水续航"},
                ],
                checks=["product_consistency"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.affected_scene_ids == ["scene-2"]


def test_review_video_quality_narrows_broad_supplier_issues_to_explicit_feedback_scene():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="供应商误判多个分镜都需要修复",
                issues=[
                    {
                        "code": "product_mismatch",
                        "category": "product_consistency",
                        "severity": "major",
                        "scene_id": "scene-1",
                        "message": "第1个分镜也被误判",
                    },
                    {
                        "code": "product_mismatch",
                        "category": "product_consistency",
                        "severity": "major",
                        "scene_id": "scene-2",
                        "message": "第2个分镜出现红色手机",
                    },
                    {
                        "code": "product_mismatch",
                        "category": "product_consistency",
                        "severity": "major",
                        "scene_id": "scene-3",
                        "message": "第3个分镜也被误判",
                    },
                ],
                affected_scene_ids=["scene-1", "scene-2", "scene-3"],
                revision_prompt="修复全部分镜",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-3", "scene_index": 3, "video_url": "https://x/scene-3.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "保温杯开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "保温杯卖点证明"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "保温杯收口"},
                ],
                user_feedback="第2个分镜画面出现红色手机，和保温杯产品无关。请只修复第2个分镜。第1个分镜和第3个分镜没有问题，不要重新生成。",
                checks=["product_consistency"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.affected_scene_ids == ["scene-2"]
    assert [issue.scene_id for issue in result.issues] == ["scene-2"]
    assert "第2个分镜" in result.revision_prompt
    assert "全部" not in result.revision_prompt


def test_review_video_quality_falls_back_to_user_feedback_when_supplier_misses_clear_scene_issue():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="未发现明显问题",
                issues=[],
                affected_scene_ids=[],
                revision_prompt="",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-3", "scene_index": 3, "video_url": "https://x/scene-3.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "牙刷开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "牙刷清洁力和续航证明"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "牙刷防水续航"},
                ],
                user_feedback="原方案要求第2个分镜应该展示产品核心卖点证明。如果实际第2个分镜出现红色手机等无关内容，请指出问题，并结合质检结果修复有问题的分镜。",
                checks=["product_consistency"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.ok is True
    assert result.passed is True
    assert result.affected_scene_ids == ["scene-2"]
    assert result.issues[0].category == "product_consistency"
    assert result.issues[0].scene_id == "scene-2"
    assert "红色手机" in result.issues[0].observed
    assert "第2个分镜" in result.revision_prompt


def test_review_video_quality_auto_detects_product_mismatch_with_scene_storyboards():
    class FakeQualitySkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="未发现明显问题",
                issues=[],
                affected_scene_ids=[],
                revision_prompt="",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    class FakeDecomposeSkill:
        async def decompose_video_to_storyboard(self, video_url: str):
            descriptions = {
                "https://x/scene-1.mp4": "蓝牙耳机在桌面上清晰展示，突出佩戴舒适",
                "https://x/scene-2.mp4": "一台红色手机放在桌面上，屏幕亮起，展示手机外观",
                "https://x/scene-3.mp4": "蓝牙耳机回到画面，展示降噪和充电仓续航",
            }
            return StoryboardResult(ok=True, shots=[{"visual_description": descriptions[video_url]}])

        async def batch_decompose_video_to_storyboard(self, video_urls: list[str]):
            raise AssertionError("scene-level QC should use single scene decomposition")

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-3", "scene_index": 3, "video_url": "https://x/scene-3.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "蓝牙耳机开场展示", "prompt": "蓝牙耳机桌面特写"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "继续展示蓝牙耳机降噪卖点", "prompt": "蓝牙耳机佩戴和降噪证明"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "蓝牙耳机续航收口", "prompt": "蓝牙耳机充电仓和续航"},
                ],
                user_feedback="请对当前视频做综合质检，自动判断是否存在方案一致性、产品一致性、分镜覆盖、播放稳定性和手机端规格问题。",
                checks=["product_consistency", "plan_consistency", "storyboard_coverage"],
            ),
            skill=FakeQualitySkill(),
            decompose_skill=FakeDecomposeSkill(),
        )
    )

    assert result.ok is True
    assert result.affected_scene_ids == ["scene-2"]
    assert len(result.issues) == 1
    assert result.issues[0].category == "product_consistency"
    assert result.issues[0].scene_id == "scene-2"
    assert "红色手机" in result.issues[0].observed
    assert "蓝牙耳机" in result.issues[0].expected
    assert "第2个分镜" in result.revision_prompt


def test_review_video_quality_uses_original_product_contract_when_scene_package_was_edited_wrong():
    class FakeQualitySkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="未发现明显问题",
                issues=[],
                affected_scene_ids=[],
                revision_prompt="",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    class FakeDecomposeSkill:
        async def decompose_video_to_storyboard(self, video_url: str):
            descriptions = {
                "https://x/scene-1.mp4": "白色电动牙刷刷头震动，泡沫清洁牙齿污渍",
                "https://x/scene-2.mp4": "一台红色手机放在桌面上，屏幕亮起，展示手机外观细节",
                "https://x/scene-3.mp4": "白色电动牙刷在水流下冲洗，展示防水和续航卖点",
            }
            return StoryboardResult(ok=True, shots=[{"visual_description": descriptions[video_url]}])

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                    {"scene_id": "scene-3", "scene_index": 3, "video_url": "https://x/scene-3.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "白色电动牙刷清洁力展示"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "红色手机屏幕、外观、桌面细节展示"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "白色电动牙刷防水续航展示"},
                ],
                brief={
                    "form_values": {
                        "product_info": "白色电动牙刷",
                        "video_goal": "白色电动牙刷宣传短视频",
                    },
                    "plan": {
                        "markdown": "原始方案要求唯一产品主体是白色电动牙刷，突出清洁力、续航和防水。",
                    },
                },
                checks=["product_consistency"],
            ),
            skill=FakeQualitySkill(),
            decompose_skill=FakeDecomposeSkill(),
        )
    )

    assert result.ok is True
    assert result.affected_scene_ids == ["scene-2"]
    assert result.issues[0].code == "auto_scene_product_mismatch"
    assert result.issues[0].scene_id == "scene-2"
    assert "白色电动牙刷" in result.issues[0].expected
    assert "红色手机" in result.issues[0].observed


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
