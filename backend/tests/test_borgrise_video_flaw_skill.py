from __future__ import annotations

import asyncio

from pixelflow.skills.borgrise import run_generation
from pixelflow.skills.borgrise.skill import BorgriseSkill


def test_borgrise_reference_mode_video_maps_scene_result(monkeypatch):
    def fake_reference_mode_video(**kwargs):
        assert kwargs["prompt"] == "白色耳机在桌面旋转展示"
        assert kwargs["image_urls"] == ["https://x/role.png", "https://x/scene.png"]
        assert kwargs["video_urls"] == []
        assert kwargs["audio_urls"] == []
        assert kwargs["duration"] == 8
        assert kwargs["ratio"] == "9:16"
        return {
            "success": True,
            "task_id": "scene-task-1",
            "endpoint": "/api/video/reference-mode-video",
            "video_url": "https://x/scene-1.mp4",
        }

    monkeypatch.setattr(run_generation, "reference_mode_video", fake_reference_mode_video)

    result = asyncio.run(
        BorgriseSkill().reference_mode_video(
            prompt="白色耳机在桌面旋转展示",
            image_urls=["https://x/role.png", "https://x/scene.png"],
            video_urls=[],
            audio_urls=[],
            duration=8,
            ratio="9:16",
        )
    )

    assert result.ok is True
    assert result.task_id == "scene-task-1"
    assert result.url == "https://x/scene-1.mp4"


def test_borgrise_merge_videos_maps_merged_url(monkeypatch):
    def fake_merge_videos(**kwargs):
        assert kwargs["video_urls"] == ["https://x/scene-1.mp4", "https://x/scene-2.mp4"]
        assert kwargs["duration"] == 16
        return {
            "success": True,
            "task_id": "merge-task-1",
            "endpoint": "/api/video/merge",
            "video_url": "https://x/merged.mp4",
        }

    monkeypatch.setattr(run_generation, "merge_videos", fake_merge_videos)

    result = asyncio.run(
        BorgriseSkill().merge_videos(
            video_urls=["https://x/scene-1.mp4", "https://x/scene-2.mp4"],
            duration=16,
        )
    )

    assert result.ok is True
    assert result.task_id == "merge-task-1"
    assert result.url == "https://x/merged.mp4"


def test_borgrise_video_flaw_analysis_maps_structured_result(monkeypatch):
    def fake_analyze_video_flaws(**kwargs):
        assert kwargs["merged_video_url"] == "https://x/merged.mp4"
        assert kwargs["scene_videos"] == [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}]
        assert kwargs["scene_packages"] == [{"scene_id": "scene-1", "storyline": "白色耳机展示"}]
        assert kwargs["user_feedback"] == "耳机颜色不一致"
        return {
            "success": True,
            "task_id": "flaw-task-1",
            "endpoint": "/api/creative/analyze_video_flaws",
            "flaw_analysis_markdown": "scene-1 存在颜色穿帮",
            "issues": [{"scene_id": "scene-1", "current": "黑色", "expected": "白色"}],
            "affected_scene_ids": ["scene-1"],
            "revision_prompt": "保持白色耳机",
        }

    monkeypatch.setattr(run_generation, "analyze_video_flaws", fake_analyze_video_flaws)

    result = asyncio.run(
        BorgriseSkill().analyze_video_flaws(
            merged_video_url="https://x/merged.mp4",
            scene_videos=[{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
            scene_packages=[{"scene_id": "scene-1", "storyline": "白色耳机展示"}],
            user_feedback="耳机颜色不一致",
        )
    )

    assert result.ok is True
    assert result.task_id == "flaw-task-1"
    assert result.flaw_analysis_markdown == "scene-1 存在颜色穿帮"
    assert result.issues == [{"scene_id": "scene-1", "current": "黑色", "expected": "白色"}]
    assert result.affected_scene_ids == ["scene-1"]
    assert result.revision_prompt == "保持白色耳机"


def test_borgrise_video_quality_review_passes_quality_context(monkeypatch):
    def fake_analyze_video_flaws(**kwargs):
        assert kwargs["merged_video_url"] == "https://x/merged.mp4"
        assert kwargs["scene_videos"] == [{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}]
        assert kwargs["scene_packages"] == [{"scene_id": "scene-1", "storyline": "白色耳机展示"}]
        assert kwargs["checks"] == ["plan_consistency", "mobile_requirements"]
        assert kwargs["platform"] == "douyin"
        assert kwargs["ratio"] == "9:16"
        assert kwargs["size"] == "1080x1920"
        return {
            "success": True,
            "task_id": "qc-task-1",
            "endpoint": "/api/creative/analyze_video_flaws",
            "summary_markdown": "综合质检通过",
            "issues": [],
            "affected_scene_ids": [],
            "revision_prompt": "",
        }

    monkeypatch.setattr(run_generation, "analyze_video_flaws", fake_analyze_video_flaws)

    result = asyncio.run(
        BorgriseSkill().review_video_quality(
            merged_video_url="https://x/merged.mp4",
            scene_videos=[{"scene_id": "scene-1", "scene_index": 1, "video_url": "https://x/scene-1.mp4"}],
            scene_packages=[{"scene_id": "scene-1", "storyline": "白色耳机展示"}],
            checks=["plan_consistency", "mobile_requirements"],
            platform="douyin",
            ratio="9:16",
            size="1080x1920",
        )
    )

    assert result.ok is True
    assert result.task_id == "qc-task-1"
    assert result.summary_markdown == "综合质检通过"
    assert result.issues == []


def test_borgrise_media_link_extraction_maps_links(monkeypatch):
    def fake_extract_media_links(**kwargs):
        assert "请分析视频" in kwargs["text"]
        assert "https://x/one.mp4" in kwargs["text"]
        return {
            "success": True,
            "endpoint": "/api/creative/extractMediaLinks",
            "links": ["https://x/one.mp4"],
        }

    monkeypatch.setattr(run_generation, "extract_media_links", fake_extract_media_links)

    result = asyncio.run(
        BorgriseSkill().extract_media_links(
            text="请分析视频",
            materials=[{"url": "https://x/one.mp4"}],
        )
    )

    assert result.ok is True
    assert result.links == ["https://x/one.mp4"]


def test_borgrise_batch_decompose_maps_storyboards(monkeypatch):
    def fake_batch_decompose_video_to_storyboard(**kwargs):
        assert kwargs["video_urls"] == ["https://x/one.mp4", "https://x/two.mp4"]
        return {
            "success": True,
            "task_id": "batch-task-1",
            "endpoint": "/api/creative/batch_decompose_video_to_storyboard",
            "storyboards": [{"video_url": "https://x/one.mp4", "analysis_markdown": "one"}],
        }

    monkeypatch.setattr(run_generation, "batch_decompose_video_to_storyboard", fake_batch_decompose_video_to_storyboard)

    result = asyncio.run(
        BorgriseSkill().batch_decompose_video_to_storyboard(
            video_urls=["https://x/one.mp4", "https://x/two.mp4"],
        )
    )

    assert result.ok is True
    assert result.task_id == "batch-task-1"
    assert result.storyboards == [{"video_url": "https://x/one.mp4", "analysis_markdown": "one"}]
