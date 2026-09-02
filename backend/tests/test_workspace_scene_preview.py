"""分镜成片预览返回白名单 TOS 地址，不由 Gateway 中转视频字节。"""

from __future__ import annotations

from pixelflow.video.workspace.digest import public_workspace_media_url, workspace_scene_preview_url


def test_scene_preview_url_uses_approved_variant() -> None:
    payload = {
        "scenes": [
            {
                "scene_id": "s1",
                "approved_variant_id": "v2",
                "variants": [
                    {"variant_id": "v1", "video_url": "https://bucket.tos-cn-beijing.volces.com/old.mp4"},
                    {"variant_id": "v2", "video_url": "https://bucket.tos-cn-beijing.volces.com/new.mp4"},
                ],
            }
        ],
    }

    url = workspace_scene_preview_url(payload, "s1")

    assert url == "https://bucket.tos-cn-beijing.volces.com/new.mp4"
    assert public_workspace_media_url(url) == url


def test_scene_preview_url_matches_segment_id_and_job_result() -> None:
    payload = {
        "scenes": [
            {
                "segment_id": "s2",
                "generation_jobs": [
                    {"job_id": "job-1", "status": "polling"},
                    {
                        "job_id": "job-2",
                        "status": "succeeded",
                        "video_url": "https://cdn.vitamazing.top/s2.mp4",
                    },
                ],
            }
        ],
    }

    assert workspace_scene_preview_url(payload, "s2") == "https://cdn.vitamazing.top/s2.mp4"


def test_scene_preview_rejects_non_allowlisted_host_and_missing_scene() -> None:
    payload = {
        "scenes": [{"scene_id": "s3", "video_url": "https://evil.example/s3.mp4"}],
    }

    assert workspace_scene_preview_url(payload, "s3") is None
    assert workspace_scene_preview_url(payload, "missing") is None
    assert public_workspace_media_url("https://evil.example/s3.mp4") is None
