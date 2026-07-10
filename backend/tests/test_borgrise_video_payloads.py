from __future__ import annotations

import pytest

from pixelflow.skills.borgrise import run_generation


@pytest.fixture
def captured_requests(monkeypatch):
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(run_generation, "get_headers", lambda *args, **kwargs: {})

    def fake_make_request(endpoint: str, data: dict, *args, **kwargs):
        calls.append((endpoint, data))
        return {"data": {"taskId": f"task-{len(calls)}"}}

    monkeypatch.setattr(run_generation, "make_request", fake_make_request)
    return calls


def test_text_to_video_payload_matches_content_app_dto(captured_requests):
    run_generation.text_to_video(
        "456",
        model="seedance-2.0",
        ratio="9:16",
        size="1080p",
        duration=5,
        sound="on",
        video_count=1,
        auto_poll=False,
    )

    assert captured_requests == [
        (
            "/video/text-to-video",
            {
                "prompt": "456",
                "model": "seedance-2.0",
                "ratio": "9:16",
                "size": "1080p",
                "duration": 5,
                "videoCount": 1,
                "sound": "on",
            },
        )
    ]


def test_image_to_video_payload_has_no_unsupported_fields(captured_requests):
    run_generation.image_to_video(
        "https://x/first.png",
        prompt="一双鞋子",
        duration=5,
        ratio="9:16",
        model="seedance-2.0",
        size="1080p",
        sound="on",
        video_count=1,
        auto_poll=False,
    )

    assert captured_requests == [
        (
            "/video/image-to-video",
            {
                "image_url": "https://x/first.png",
                "prompt": "一双鞋子",
                "duration": 5,
                "ratio": "9:16",
                "model": "seedance-2.0",
                "size": "1080p",
                "sound": "on",
                "videoCount": 1,
            },
        )
    ]


def test_two_image_to_video_payload_matches_content_app_dto(captured_requests):
    run_generation.two_image_to_video(
        "https://x/first.png",
        "https://x/last.png",
        prompt="一束阳光",
        duration=5,
        ratio="9:16",
        model="seedance-2.0",
        size="1080p",
        sound="on",
        video_count=1,
        auto_poll=False,
    )

    assert captured_requests == [
        (
            "/video/two-image-to-video",
            {
                "prompt": "一束阳光",
                "first_frame_image_url": "https://x/first.png",
                "last_frame_image_url": "https://x/last.png",
                "ratio": "9:16",
                "duration": 5,
                "model": "seedance-2.0",
                "size": "1080p",
                "videoCount": 1,
                "sound": "on",
            },
        )
    ]


def test_reference_mode_video_payload_matches_content_app_dto(captured_requests):
    run_generation.reference_mode_video(
        "路飞厉害",
        image_urls=["https://x/ref-1.png", "https://x/ref-2.png"],
        video_urls=["https://x/ref.mp4"],
        audio_urls=[],
        duration=5,
        ratio="9:16",
        model="seedance-2.0",
        size="1080p",
        sound="on",
        video_count=1,
        auto_poll=False,
    )

    assert captured_requests == [
        (
            "/video/reference-mode-video",
            {
                "prompt": "路飞厉害",
                "imageUrls": ["https://x/ref-1.png", "https://x/ref-2.png"],
                "videoUrls": ["https://x/ref.mp4"],
                "audioUrls": [],
                "duration": 5,
                "ratio": "9:16",
                "sound": "on",
                "model": "seedance-2.0",
                "size": "1080p",
                "videoCount": 1,
            },
        )
    ]


def test_edit_video_payload_matches_content_app_dto(captured_requests):
    run_generation.edit_video(
        "https://x/source.mp4",
        prompt="修改视频",
        ref_image="https://x/ref.png",
        duration=5,
        ratio="9:16",
        model="seedance-2.0",
        size="1080p",
        sound="on",
        video_count=1,
        auto_poll=False,
    )

    assert captured_requests == [
        (
            "/video/edit-video",
            {
                "prompt": "修改视频",
                "refImage": "https://x/ref.png",
                "refVideo": "https://x/source.mp4",
                "model": "seedance-2.0",
                "duration": 5,
                "size": "1080p",
                "ratio": "9:16",
                "videoCount": 1,
                "sound": "on",
            },
        )
    ]


@pytest.mark.parametrize("duration", [4, 15])
def test_seedance_video_payloads_accept_full_supported_duration_range(captured_requests, duration):
    result = run_generation.text_to_video("边界时长", duration=duration, model="seedance-2.0", auto_poll=False)

    assert result["success"] is True
    assert captured_requests[0][1]["duration"] == duration


@pytest.mark.parametrize("duration", [3, 16])
def test_seedance_video_payloads_reject_out_of_range_duration(captured_requests, duration):
    result = run_generation.text_to_video("非法时长", duration=duration, model="seedance-2.0", auto_poll=False)

    assert result["error"] is True
    assert captured_requests == []
