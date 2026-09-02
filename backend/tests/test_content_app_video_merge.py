"""验证 Content-App 同步拼接：单镜直通、多镜 POST、额度与计费档分流。"""

from __future__ import annotations

import json

import httpx
import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolExecutionError
from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential
from pixelflow.capabilities.video_delivery import ContentAppVideoMergeAdapter
from pixelflow.video.contracts import VideoWorkspace


def _scene(scene_id: str, suffix: str, *, duration: int = 8) -> dict[str, object]:
    return {
        "scene_id": scene_id,
        "duration_sec": duration,
        "variants": [
            {
                "variant_id": f"variant:{suffix}",
                "artifact_ref": f"artifact:video:{suffix}",
                "video_url": f"https://cdn.example.invalid/{suffix}.mp4",
                "selected": True,
                "review_status": "approved",
            }
        ],
    }


def _context(payload: dict[str, object]) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-merge",
            conversation_id="conversation-merge",
            payload=payload,
        ),
        run_id="hrun_merge_test",
        tool_call_id="tool-call-merge",
        credential=TransientVideoAgentCredential("Bearer test-only"),
    )


def _contract_payload(*scenes: dict[str, object]) -> dict[str, object]:
    return {
        "creation_contract": {
            "video_model": "Seedance 2.5",
            "video_size": "1080p",
        },
        "scenes": list(scenes),
    }


@pytest.mark.asyncio
async def test_single_scene_skips_http_and_reuses_existing_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("单镜不得调用 merge")

    adapter = ContentAppVideoMergeAdapter(
        base_url="https://content.example.invalid/api",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    payload = _contract_payload(_scene("scene_a", "a1"))
    job = await adapter.start_delivery(
        _context(payload),
        output_type="mp4",
        scenes=[
            {
                "scene_id": "scene_a",
                "variant_id": "variant:a1",
                "artifact_ref": "artifact:video:a1",
            }
        ],
        attempt=1,
    )

    assert requests == []
    assert job.status == "succeeded"
    assert job.delivery_url == "https://cdn.example.invalid/a1.mp4"
    assert job.artifact_ref is not None and job.artifact_ref.startswith("artifact:merge:")


@pytest.mark.asyncio
async def test_multi_scene_posts_ordered_video_urls_and_canonical_model_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/video/merge")
        return httpx.Response(
            200,
            json={"success": True, "data": {"url": "https://cdn.example.invalid/merged.mp4"}},
        )

    adapter = ContentAppVideoMergeAdapter(
        base_url="https://content.example.invalid/api",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    payload = _contract_payload(_scene("scene_a", "a1", duration=6), _scene("scene_b", "b1", duration=8))
    job = await adapter.start_delivery(
        _context(payload),
        output_type="mp4",
        scenes=[
            {"scene_id": "scene_a", "variant_id": "variant:a1", "artifact_ref": "artifact:video:a1"},
            {"scene_id": "scene_b", "variant_id": "variant:b1", "artifact_ref": "artifact:video:b1"},
        ],
        attempt=1,
    )

    assert job.status == "succeeded"
    assert job.delivery_url == "https://cdn.example.invalid/merged.mp4"
    assert len(requests) == 1
    body = json.loads(requests[0].content.decode())
    assert body == {
        "videoUrls": [
            "https://cdn.example.invalid/a1.mp4",
            "https://cdn.example.invalid/b1.mp4",
        ]
    }
    assert requests[0].headers["modelType"] == "seedance-2.5"
    assert requests[0].headers["billType"] == "3"
    assert requests[0].headers["duration"] == "14"


@pytest.mark.asyncio
async def test_merge_402_quota_pauses_instead_of_failing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(402, json={"success": False, "message": "额度不足"})

    adapter = ContentAppVideoMergeAdapter(
        base_url="https://content.example.invalid/api",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    payload = _contract_payload(_scene("scene_a", "a1"), _scene("scene_b", "b1"))
    job = await adapter.start_delivery(
        _context(payload),
        output_type="mp4",
        scenes=[
            {"scene_id": "scene_a", "variant_id": "variant:a1", "artifact_ref": "artifact:video:a1"},
            {"scene_id": "scene_b", "variant_id": "variant:b1", "artifact_ref": "artifact:video:b1"},
        ],
        attempt=1,
    )

    assert job.status == "start_paused_quota"
    assert job.delivery_url is None


@pytest.mark.asyncio
async def test_merge_402_billing_profile_is_not_quota() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(402, json={"success": False, "message": "价格配置不存在"})

    adapter = ContentAppVideoMergeAdapter(
        base_url="https://content.example.invalid/api",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    payload = _contract_payload(_scene("scene_a", "a1"), _scene("scene_b", "b1"))
    with pytest.raises(VideoToolExecutionError, match="视频交付计费档缺失"):
        await adapter.start_delivery(
            _context(payload),
            output_type="mp4",
            scenes=[
                {"scene_id": "scene_a", "variant_id": "variant:a1", "artifact_ref": "artifact:video:a1"},
                {"scene_id": "scene_b", "variant_id": "variant:b1", "artifact_ref": "artifact:video:b1"},
            ],
            attempt=1,
        )


@pytest.mark.asyncio
async def test_jianying_package_is_not_assembled() -> None:
    adapter = ContentAppVideoMergeAdapter(base_url="https://content.example.invalid/api")
    with pytest.raises(VideoToolExecutionError, match="剪映工程包交付尚未装配"):
        await adapter.start_delivery(
            _context(_contract_payload(_scene("scene_a", "a1"))),
            output_type="jianying_package",
            scenes=[{"scene_id": "scene_a", "artifact_ref": "artifact:video:a1"}],
            attempt=1,
        )
