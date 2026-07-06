from __future__ import annotations

import asyncio

from pixelflow.qc.revision_scope import RevisionScopeResult
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
    assert result.passed is False


def test_review_video_quality_fails_when_expected_scene_video_is_missing():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="供应商未发现问题",
                issues=[],
                affected_scene_ids=[],
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="",
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-3", "scene_index": 3, "video_url": "https://x/scene-3.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "牙刷开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "牙刷清洁力展示"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "牙刷防水续航"},
                ],
                checks=["storyboard_coverage"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.ok is True
    assert result.passed is False
    assert result.affected_scene_ids == ["scene-2"]
    assert any(item.item == "片段完整性" and item.status == "fail" for item in result.check_results)
    assert any(issue.category == "storyboard_coverage" and issue.scene_id == "scene-2" for issue in result.issues)


def test_review_video_quality_fails_when_scene_video_ids_do_not_match_packages():
    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(ok=True, summary_markdown="供应商未发现问题", issues=[], raw={})

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                scene_videos=[
                    {"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"},
                    {"scene_id": "scene-extra", "scene_index": 99, "video_url": "https://x/scene-extra.mp4"},
                ],
                scene_packages=[
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "牙刷开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "牙刷清洁力展示"},
                ],
                checks=["storyboard_coverage"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.passed is False
    assert result.affected_scene_ids == ["scene-2"]
    assert any(issue.code == "missing_scene_video" and issue.scene_id == "scene-2" for issue in result.issues)


def test_review_video_quality_narrows_broad_supplier_issues_to_explicit_feedback_scene(monkeypatch):
    async def fake_resolve_revision_scope(**_kwargs):
        return RevisionScopeResult(
            target_scene_ids=["scene-2"],
            excluded_scene_ids=["scene-1", "scene-3"],
            action="fix_specific",
            confidence="high",
            llm_used=True,
        )

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

    monkeypatch.setattr("pixelflow.qc.video_review.resolve_revision_scope", fake_resolve_revision_scope)

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


def test_review_video_quality_uses_llm_scope_for_multiple_scene_revision(monkeypatch):
    async def fake_resolve_revision_scope(**kwargs):
        assert kwargs["feedback"] == "第2个分镜和第3个分镜内容错误，第1个分镜没有问题，不要重新生成。"
        return RevisionScopeResult(
            target_scene_ids=["scene-2", "scene-3"],
            excluded_scene_ids=["scene-1"],
            action="fix_specific",
            confidence="high",
            llm_used=True,
        )

    class FakeSkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="供应商返回了宽泛问题",
                issues=[
                    {"code": "product_mismatch", "category": "product_consistency", "severity": "major", "scene_id": "scene-1", "message": "第1个分镜误判"},
                    {"code": "product_mismatch", "category": "product_consistency", "severity": "major", "scene_id": "scene-2", "message": "第2个分镜错误"},
                    {"code": "product_mismatch", "category": "product_consistency", "severity": "major", "scene_id": "scene-3", "message": "第3个分镜错误"},
                ],
                affected_scene_ids=["scene-1", "scene-2", "scene-3"],
                revision_prompt="修复全部分镜",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    monkeypatch.setattr("pixelflow.qc.video_review.resolve_revision_scope", fake_resolve_revision_scope)

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
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "蓝牙耳机开场"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "蓝牙耳机降噪证明"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "蓝牙耳机续航收口"},
                ],
                user_feedback="第2个分镜和第3个分镜内容错误，第1个分镜没有问题，不要重新生成。",
                checks=["product_consistency"],
            ),
            skill=FakeSkill(),
        )
    )

    assert result.target_scene_ids == ["scene-2", "scene-3"]
    assert result.excluded_scene_ids == ["scene-1"]
    assert result.affected_scene_ids == ["scene-2", "scene-3"]
    assert [issue.scene_id for issue in result.issues] == ["scene-2", "scene-3"]
    assert "第2个分镜" in result.revision_prompt
    assert "第3个分镜" in result.revision_prompt
    assert "第1个分镜" not in result.revision_prompt


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


def test_review_video_quality_uses_original_scene_packages_for_scene_level_contract(monkeypatch):
    async def fake_evaluate_scene_semantic_contracts(*, global_contract_text, items):
        assert len(items) == 1
        assert "有线耳机插入手机特写" in items[0]["scene_contract_text"]
        assert "红色口红" not in items[0]["scene_contract_text"]
        return [
            {
                "scene_id": "scene-2",
                "passed": False,
                "category": "product_consistency",
                "severity": "major",
                "message": "第2个分镜实际视频内容与原始分镜合同不一致",
                "expected": items[0]["scene_contract_text"],
                "observed": items[0]["observed_text"],
                "suggestion": "请只重生成第2个分镜，恢复有线耳机产品介入画面",
            }
        ]

    monkeypatch.setattr("pixelflow.qc.video_review.evaluate_scene_semantic_contracts", fake_evaluate_scene_semantic_contracts)

    class FakeQualitySkill:
        async def review_video_quality(self, **kwargs):
            assert "original_scene_packages" not in kwargs
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
            return StoryboardResult(ok=True, shots=[{"visual_description": "一支红色口红放在化妆台上，模特正在涂口红"}])

    result = asyncio.run(
        review_video_quality(
            VideoQCRequest(
                merged_video_url="https://x/merged.mp4",
                scene_videos=[
                    {"scene_id": "scene-2", "scene_index": 2, "video_url": "https://x/scene-2.mp4"},
                ],
                scene_packages=[
                    {
                        "scene_id": "scene-2",
                        "scene_index": 2,
                        "storyline": "画面突然切换到一支红色口红的电商展示场景",
                        "prompt": "有线耳机插入手机特写",
                        "narration": "这支口红显色自然",
                    },
                ],
                original_scene_packages=[
                    {
                        "scene_id": "scene-2",
                        "scene_index": 2,
                        "storyline": "产品介入：有线耳机插入手机后，用户进入沉浸通勤状态",
                        "prompt": "有线耳机插入手机特写，屏幕亮起音频波纹，用户表情放松",
                        "narration": "换上这条有线耳机，即插即用，高解析音质，一秒沉浸在自己的世界里。",
                        "shot_description": {"text": "特写有线耳机插入手机接口，随后切到用户戴上耳机听音乐。"},
                    },
                ],
                checks=["product_consistency", "plan_consistency", "storyboard_coverage"],
            ),
            skill=FakeQualitySkill(),
            decompose_skill=FakeDecomposeSkill(),
        )
    )

    assert result.affected_scene_ids == ["scene-2"]
    assert "有线耳机插入手机特写" in result.issues[0].expected
    assert "红色口红" not in result.issues[0].expected


def test_review_video_quality_does_not_treat_mobile_delivery_context_as_product_contract():
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
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "白色电动牙刷清洁力和续航展示"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "白色电动牙刷防水续航展示"},
                ],
                brief={
                    "form_values": {
                        "product_info": "白色电动牙刷",
                        "video_goal": "生成一个 9:16 手机端短视频广告",
                    },
                    "plan": {
                        "markdown": "白色电动牙刷是唯一产品主体，适合手机端投放，突出清洁力、续航和防水。",
                    },
                },
                checks=["product_consistency", "plan_consistency", "storyboard_coverage"],
            ),
            skill=FakeQualitySkill(),
            decompose_skill=FakeDecomposeSkill(),
        )
    )

    assert result.ok is True
    assert result.passed is False
    assert result.affected_scene_ids == ["scene-2"]
    assert result.issues[0].code == "auto_scene_product_mismatch"
    assert result.issues[0].expected.startswith("原始产品主体：电动牙刷")
    assert "手机、" not in result.issues[0].expected
    assert "白色电动牙刷" in result.issues[0].expected
    assert "红色手机" in result.issues[0].observed


def test_review_video_quality_llm_detects_multiple_scene_mismatches_without_keyword_limits(monkeypatch):
    class FakeQualitySkill:
        async def review_video_quality(self, **kwargs):
            return VideoQualityReviewResult(
                ok=True,
                summary_markdown="供应商未发现明显问题",
                issues=[],
                affected_scene_ids=[],
                revision_prompt="",
                raw={"endpoint": "/api/creative/analyze_video_flaws"},
            )

    class FakeDecomposeSkill:
        async def decompose_video_to_storyboard(self, video_url: str):
            descriptions = {
                "https://x/scene-1.mp4": "地铁里一位用户佩戴有线耳机连接手机听音乐，耳机线清晰可见，表现稳定连接和低延迟。",
                "https://x/scene-2.mp4": "一支红色口红放在化妆台上，镜头展示口红外壳、膏体颜色和模特上唇效果。",
                "https://x/scene-3.mp4": "厨房台面上摆放一台白色智能电饭煲，镜头展示打开锅盖、米饭冒热气和预约煮饭按钮。",
            }
            return StoryboardResult(ok=True, shots=[{"visual_description": descriptions[video_url]}])

    async def fake_evaluate_scene_semantic_contracts(**kwargs):
        observed_by_scene = {item["scene_id"]: item["observed_text"] for item in kwargs["items"]}
        assert "有线耳机" in kwargs["global_contract_text"]
        assert "口红" in observed_by_scene["scene-2"]
        assert "电饭煲" in observed_by_scene["scene-3"]
        return [
            {
                "scene_id": "scene-1",
                "passed": True,
                "category": "product_consistency",
                "severity": "info",
                "message": "",
                "expected": "有线耳机广告",
                "observed": observed_by_scene["scene-1"],
                "suggestion": "",
            },
            {
                "scene_id": "scene-2",
                "passed": False,
                "category": "product_consistency",
                "severity": "major",
                "message": "第2个分镜实际画面是口红美妆内容，和有线耳机广告方案不一致。",
                "expected": "有线耳机广告",
                "observed": observed_by_scene["scene-2"],
                "suggestion": "请只重生成第2个分镜，恢复有线耳机产品展示。",
            },
            {
                "scene_id": "scene-3",
                "passed": False,
                "category": "product_consistency",
                "severity": "major",
                "message": "第3个分镜实际画面是智能电饭煲厨房内容，和有线耳机广告方案不一致。",
                "expected": "有线耳机广告",
                "observed": observed_by_scene["scene-3"],
                "suggestion": "请只重生成第3个分镜，恢复有线耳机收口卖点。",
            },
        ]

    monkeypatch.setattr("pixelflow.qc.video_review.evaluate_scene_semantic_contracts", fake_evaluate_scene_semantic_contracts)

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
                    {"scene_id": "scene-1", "scene_index": 1, "storyline": "有线耳机通勤开场", "prompt": "用户佩戴有线耳机听音乐"},
                    {"scene_id": "scene-2", "scene_index": 2, "storyline": "有线耳机稳定连接和低延迟展示", "prompt": "有线耳机连接手机，突出音质和线控"},
                    {"scene_id": "scene-3", "scene_index": 3, "storyline": "有线耳机线控麦克风和耐用收口", "prompt": "有线耳机产品定格和卖点字幕"},
                ],
                brief={
                    "form_values": {
                        "product_info": "有线耳机",
                        "video_goal": "有线耳机宣传短视频",
                    },
                    "plan": {
                        "markdown": "原始方案要求唯一产品主体是有线耳机，突出稳定连接、低延迟、音质和线控麦克风。",
                    },
                },
                user_feedback="请对这个最终视频做一次综合质检，自动判断哪些分镜存在问题。",
                checks=["product_consistency", "plan_consistency", "storyboard_coverage"],
            ),
            skill=FakeQualitySkill(),
            decompose_skill=FakeDecomposeSkill(),
        )
    )

    assert result.ok is True
    assert result.passed is False
    assert result.affected_scene_ids == ["scene-2", "scene-3"]
    assert [issue.scene_id for issue in result.issues] == ["scene-2", "scene-3"]
    assert "scene-1" not in result.affected_scene_ids
    assert "第2个分镜" in result.revision_prompt
    assert "第3个分镜" in result.revision_prompt


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
    assert any(item.item == "方案一致性" and item.status == "fail" for item in result.check_results)
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
