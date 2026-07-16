import pytest
from pydantic import ValidationError

from pixelflow.jianying_draft.models import (
    JianyingDraftRequest,
    JianyingDraftScene,
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


def test_storyboard_version_changes_when_scene_video_changes():
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
            task_id="t2",
        )
    ]

    assert compute_storyboard_version_id(before) != compute_storyboard_version_id(after)


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
            storyboard_version_id=compute_storyboard_version_id(scenes),
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
