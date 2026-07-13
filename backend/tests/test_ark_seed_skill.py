from __future__ import annotations

import httpx
import pytest

from pixelflow.skills import get_image_skill, get_video_skill
from pixelflow.skills.ark_seed import SeedanceSkill, SeedreamSkill
from pixelflow.skills.ark_seed.client import ArkSeedClient, extract_urls


class FakeArkClient:
    def __init__(self):
        self.video_payloads = []
        self.image_payloads = []

    def create_video_task(self, payload):
        self.video_payloads.append(payload)
        return {"id": "task-1", "status": "queued"}

    def wait_video_task(self, task_id):
        return {"id": task_id, "status": "succeeded", "content": {"video_url": "https://example.com/out.mp4"}}

    def get_video_task(self, task_id):
        return {"id": task_id, "status": "succeeded", "content": {"video_url": "https://example.com/out.mp4"}}

    def generate_images(self, payload):
        self.image_payloads.append(payload)
        return {"data": [{"url": "https://example.com/a.png"}, {"url": "https://example.com/b.png"}]}


class PollFailingArkClient(FakeArkClient):
    def wait_video_task(self, task_id):
        raise TimeoutError("poll timeout")


@pytest.mark.asyncio
async def test_seedance_image_to_video_builds_ark_content():
    client = FakeArkClient()
    skill = SeedanceSkill(client=client, model="seedance-test", resolution="720p")

    result = await skill.image_to_video("https://example.com/product.png", prompt="rotate", duration=5, ratio="9:16")

    assert result.ok
    assert result.url == "https://example.com/out.mp4"
    assert client.video_payloads == [
        {
            "model": "seedance-test",
            "content": [
                {"type": "text", "text": "rotate"},
                {"type": "image_url", "image_url": {"url": "https://example.com/product.png"}, "role": "reference_image"},
            ],
            "ratio": "9:16",
            "duration": 5,
            "watermark": False,
            "resolution": "720p",
        }
    ]


@pytest.mark.asyncio
async def test_seedance_reference_to_video_accepts_multiple_reference_types():
    client = FakeArkClient()
    skill = SeedanceSkill(client=client, model="seedance-test")

    result = await skill.reference_to_video(
        prompt="match references",
        image_urls=["https://example.com/a.png"],
        video_urls=["https://example.com/ref.mp4"],
        audio_urls=["https://example.com/ref.wav"],
        duration=6,
        ratio="16:9",
    )

    assert result.ok
    content = client.video_payloads[0]["content"]
    assert client.video_payloads[0]["ratio"] == "16:9"
    assert client.video_payloads[0]["duration"] == 6
    assert content[0]["text"] == "match references"
    assert {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}, "role": "reference_image"} in content
    assert {"type": "video_url", "video_url": {"url": "https://example.com/ref.mp4"}, "role": "reference_video"} in content
    assert {"type": "audio_url", "audio_url": {"url": "https://example.com/ref.wav"}, "role": "reference_audio"} in content


@pytest.mark.asyncio
async def test_seedream_reference_group_payload():
    client = FakeArkClient()
    skill = SeedreamSkill(client=client, model="seedream-test")

    result = await skill.reference_group_images(["https://example.com/a.png", "https://example.com/b.png"], "make a group", max_images=4)

    assert result.ok
    assert result.urls == ["https://example.com/a.png", "https://example.com/b.png"]
    assert client.image_payloads == [
        {
            "model": "seedream-test",
            "prompt": "make a group",
            "image": ["https://example.com/a.png", "https://example.com/b.png"],
            "size": "2K",
            "ratio": "1:1",
            "n": 4,
            "response_format": "url",
            "sequential_image_generation": "auto",
            "stream": False,
            "watermark": True,
        }
    ]


def test_extract_urls_dedupes_nested_shapes():
    assert extract_urls({"data": [{"url": "https://x/a.png"}, {"image_url": "https://x/a.png"}, {"video_url": "https://x/v.mp4"}]}) == [
        "https://x/a.png",
        "https://x/v.mp4",
    ]


@pytest.mark.asyncio
async def test_seedance_preserves_task_id_when_poll_fails():
    result = await SeedanceSkill(client=PollFailingArkClient()).text_to_video("test", duration=5, ratio="16:9")

    assert not result.ok
    assert result.task_id == "task-1"
    assert "poll timeout" in (result.error or "")


@pytest.mark.asyncio
async def test_seedance_poll_video_task_extracts_url():
    result = await SeedanceSkill(client=FakeArkClient()).poll_video_task("task-1")

    assert result.ok
    assert result.task_id == "task-1"
    assert result.url == "https://example.com/out.mp4"


def test_skill_factories_select_ark_seed(monkeypatch):
    monkeypatch.setenv("PIXELFLOW_VIDEO_SKILL", "Seedance")
    monkeypatch.setenv("PIXELFLOW_IMAGE_SKILL", "Seedream")
    assert isinstance(get_video_skill(), SeedanceSkill)
    assert isinstance(get_image_skill(), SeedreamSkill)


def test_ark_plan_defaults_and_key_fallback(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_BASE_URL", raising=False)
    monkeypatch.delenv("ARK_SEEDANCE_MODEL", raising=False)
    monkeypatch.delenv("ARK_SEEDREAM_MODEL", raising=False)
    monkeypatch.setenv("ARK_PLAN_API_KEY", "plan-key")

    client = ArkSeedClient()

    assert client.api_key == "plan-key"
    assert client.base_url == "https://ark.cn-beijing.volces.com/api/plan/v3"
    assert SeedanceSkill(client=client).model == "doubao-seedance-2.0"
    assert SeedreamSkill(client=client).model == "doubao-seedream-5.0-lite"


def test_ark_plan_base_url_prefers_plan_key(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "legacy-media-key")
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "volcengine-key")
    monkeypatch.setenv("ARK_PLAN_API_KEY", "plan-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3")

    client = ArkSeedClient()

    assert client.api_key == "plan-key"


def test_ark_client_reuses_http_client_between_requests(monkeypatch):
    created_clients = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    class FakeHttpClient:
        is_closed = False

        def __init__(self, timeout):
            self.timeout = timeout
            self.requests = []
            created_clients.append(self)

        def request(self, method, url, headers=None, json=None):
            self.requests.append((method, url, headers, json))
            return FakeResponse()

        def close(self):
            self.is_closed = True

    monkeypatch.setenv("ARK_PLAN_API_KEY", "plan-key")
    monkeypatch.setattr(httpx, "Client", FakeHttpClient)

    client = ArkSeedClient()
    client.get_video_task("task-1")
    client.get_video_task("task-2")

    assert len(created_clients) == 1
    assert len(created_clients[0].requests) == 2


def test_ark_client_rebuilds_http_client_after_transport_error(monkeypatch):
    created_clients = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    class FakeHttpClient:
        def __init__(self, timeout):
            self.timeout = timeout
            self.is_closed = False
            self.calls = 0
            created_clients.append(self)

        def request(self, method, url, headers=None, json=None):
            self.calls += 1
            if len(created_clients) == 1:
                raise httpx.ConnectTimeout("_ssl.c:993: The handshake operation timed out")
            return FakeResponse()

        def close(self):
            self.is_closed = True

    monkeypatch.setenv("ARK_PLAN_API_KEY", "plan-key")
    monkeypatch.setenv("ARK_MAX_RETRIES", "1")
    monkeypatch.setattr(httpx, "Client", FakeHttpClient)

    client = ArkSeedClient()
    assert client.get_video_task("task-1") == {"ok": True}
    assert len(created_clients) == 2
    assert created_clients[0].is_closed is True
