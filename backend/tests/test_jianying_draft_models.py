import pytest
from pydantic import ValidationError

from pixelflow.jianying_draft.models import (
    JianyingDraftRequest,
    JianyingDraftResult,
    JianyingDraftScene,
    JianyingDraftStatus,
    compute_storyboard_version_id,
)


def test_storyboard_version_is_stable_after_input_reordering():
    scenes = [
        JianyingDraftScene(
            scene_id="scene-2",
            scene_index=2,
            video_url="https://cdn/2.mp4",
            task_id="t2",
        ),
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=1,
            video_url="https://cdn/1.mp4",
            task_id="t1",
        ),
    ]

    assert compute_storyboard_version_id(scenes) == compute_storyboard_version_id(
        list(reversed(scenes))
    )


def test_storyboard_version_changes_when_scene_video_url_changes():
    before = [
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=1,
            video_url="https://cdn/1.mp4",
            task_id="t1",
        )
    ]
    after = [
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=1,
            video_url="https://cdn/1-v2.mp4",
            task_id="t1",
        )
    ]

    assert compute_storyboard_version_id(before) != compute_storyboard_version_id(after)


def test_storyboard_version_changes_when_scene_task_id_changes():
    before = [
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=1,
            video_url="https://cdn/1.mp4",
            task_id="t1",
        )
    ]
    after = [
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=1,
            video_url="https://cdn/1.mp4",
            task_id="t2",
        )
    ]

    assert compute_storyboard_version_id(before) != compute_storyboard_version_id(after)


def test_storyboard_version_matches_fixed_fnv1a64_value():
    scenes = [
        JianyingDraftScene(
            scene_id="scene-1",
            scene_index=1,
            video_url="https://cdn/1.mp4",
            task_id="t1",
        ),
        JianyingDraftScene(
            scene_id="scene-2",
            scene_index=2,
            video_url="https://cdn/2.mp4",
            task_id="t2",
        ),
    ]

    assert compute_storyboard_version_id(scenes) == "storyboard-459f6271da98fbff"


def test_compute_storyboard_version_rejects_empty_scenes():
    with pytest.raises(ValueError):
        compute_storyboard_version_id([])


def test_compute_storyboard_version_rejects_duplicate_scene_indexes():
    scenes = [
        JianyingDraftScene(
            scene_id="scene-1", scene_index=1, video_url="https://cdn/1.mp4"
        ),
        JianyingDraftScene(
            scene_id="scene-2", scene_index=1, video_url="https://cdn/2.mp4"
        ),
    ]

    with pytest.raises(ValueError):
        compute_storyboard_version_id(scenes)


@pytest.mark.parametrize("url", ["", "blob:https://local/1", "file:///tmp/1.mp4"])
def test_scene_rejects_non_http_video_url(url: str):
    with pytest.raises(ValidationError):
        JianyingDraftScene(scene_id="scene-1", scene_index=1, video_url=url)


def test_request_rejects_empty_scenes():
    with pytest.raises(ValidationError):
        JianyingDraftRequest(
            conversation_id="conversation-1",
            storyboard_version_id="storyboard-0000000000000000",
            scenes=[],
        )


def test_request_rejects_duplicate_scene_indexes():
    scenes = [
        JianyingDraftScene(
            scene_id="scene-1", scene_index=1, video_url="https://cdn/1.mp4"
        ),
        JianyingDraftScene(
            scene_id="scene-2", scene_index=1, video_url="https://cdn/2.mp4"
        ),
    ]

    with pytest.raises(ValidationError):
        JianyingDraftRequest(
            conversation_id="conversation-1",
            storyboard_version_id="storyboard-invalid",
            scenes=scenes,
        )


def test_request_rejects_mismatched_storyboard_version():
    scenes = [
        JianyingDraftScene(
            scene_id="scene-1", scene_index=1, video_url="https://cdn/1.mp4"
        )
    ]

    with pytest.raises(ValidationError):
        JianyingDraftRequest(
            conversation_id="conversation-1",
            storyboard_version_id="storyboard-0000000000000000",
            scenes=scenes,
        )


def test_result_does_not_expose_raw_provider_payload():
    assert "raw" not in JianyingDraftResult.model_fields
    assert "replaced_by_job_id" not in JianyingDraftResult.model_fields


@pytest.mark.parametrize("url", ["", "blob:https://local/1", "file:///tmp/draft.zip"])
def test_result_rejects_non_http_download_url(url: str):
    with pytest.raises(ValidationError):
        JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED, download_url=url)


def test_result_serializes_https_download_url():
    result = JianyingDraftResult(
        status=JianyingDraftStatus.SUCCEEDED,
        download_url="https://cdn.example.com/draft.zip",
    )

    assert result.download_url is not None
    assert "https://cdn.example.com/draft.zip" in result.model_dump_json()
