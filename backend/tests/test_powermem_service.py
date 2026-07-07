from __future__ import annotations

import json

import httpx
import pytest

from pixelflow.memory import PowerMemConfig, PowerMemService, load_power_mem_config_from_env


@pytest.mark.asyncio
async def test_powermem_service_searches_with_api_key_and_category_filter():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/powermem/api/v1/memories/search"
        assert request.headers["X-API-Key"] == "secret"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["query"] == "品牌偏好"
        assert payload["user_id"] == "u1"
        assert payload["agent_id"] == "pixelflow"
        assert payload["filters"] == {"category": "preference"}
        assert payload["limit"] == 3
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {
                            "memory_id": "m1",
                            "content": "用户偏好真实摄影，不要价格文字",
                            "score": 0.88,
                            "metadata": {"source_agent": "intake_agent"},
                        }
                    ]
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test/powermem", api_key="secret", search_limit=3),
        http_client=client,
    )

    results = await service.search(user_id="u1", query="品牌偏好", categories=["preference"])

    assert len(seen) == 1
    assert results[0].memory_id == "m1"
    assert results[0].content == "用户偏好真实摄影，不要价格文字"
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_records_memory_payload():
    payloads: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/memories"
        assert request.headers["X-API-Key"] == "secret"
        payload = json.loads(request.content.decode("utf-8"))
        payloads.append(payload)
        return httpx.Response(200, json={"success": True, "data": [{"memory_id": "1"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret"),
        http_client=client,
    )

    ok = await service.record(
        user_id="u1",
        content="用户喜欢高级感，默认 9:16",
        category="preference",
        source_agent="preference_api",
        metadata={"task_id": "t1"},
        memory_type="preference",
        run_id="run-1",
    )

    assert ok is True
    assert payloads == [
        {
            "content": "用户喜欢高级感，默认 9:16",
            "user_id": "u1",
            "agent_id": "pixelflow",
            "run_id": "run-1",
            "metadata": {"task_id": "t1", "source_agent": "preference_api", "category": "preference"},
            "filters": {"category": "preference", "source_agent": "preference_api"},
            "scope": "user",
            "memory_type": "preference",
            "infer": True,
        }
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_falls_back_when_preference_infer_creates_no_memory():
    payloads: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/memories"
        payload = json.loads(request.content.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [],
                    "message": "No memories were created (likely duplicates detected or no facts extracted)",
                },
            )
        return httpx.Response(200, json={"success": True, "data": [{"memory_id": "fallback-1"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret"),
        http_client=client,
    )

    ok = await service.record(
        user_id="u1",
        content="以后默认真实摄影风格，不要价格文字",
        category="preference",
        source_agent="preference_api",
        metadata={"source": "preferences_feedback"},
        memory_type="preference",
        infer=True,
    )

    assert ok is True
    assert [payload["infer"] for payload in payloads] == [True, False]
    assert payloads[1]["metadata"]["infer_fallback"] is True
    assert payloads[1]["metadata"]["infer_fallback_reason"] == "empty_infer_result"
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_record_uses_record_timeout_and_search_uses_search_timeout():
    """record 写入走独立的 record_timeout_seconds（给后台 LLM 抽取留时间），
    search/health 仍用短的 timeout_seconds（不阻塞用户请求路径）。"""

    class RecordingClient:
        def __init__(self) -> None:
            self.timeouts: list[tuple[bool, float | None]] = []

        async def request(self, method, url, *, headers=None, json=None, timeout=None):
            self.timeouts.append((str(url).endswith("/search"), timeout))

            class _Response:
                content = b"{}"

                def raise_for_status(self) -> None:
                    return None

                def json(self):
                    if str(url).endswith("/search"):
                        return {"success": True, "data": {"results": []}}
                    return {"success": True, "data": [{"memory_id": "1"}]}

            return _Response()

        async def aclose(self) -> None:
            return None

    client = RecordingClient()
    service = PowerMemService(
        PowerMemConfig(
            enabled=True,
            base_url="https://example.test",
            api_key="secret",
            timeout_seconds=3.0,
            record_timeout_seconds=42.0,
        ),
        http_client=client,
    )

    await service.record(user_id="u1", content="x", category="experience")
    await service.search(user_id="u1", query="y")

    record_timeout = next(timeout for is_search, timeout in client.timeouts if not is_search)
    search_timeout = next(timeout for is_search, timeout in client.timeouts if is_search)

    assert record_timeout == 42.0
    assert search_timeout == 3.0


@pytest.mark.asyncio
async def test_powermem_service_fail_open_on_http_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret", fail_open=True),
        http_client=client,
    )

    assert await service.search(user_id="u1", query="anything") == []
    assert await service.record(user_id="u1", content="anything", category="experience") is False
    await client.aclose()


def test_load_power_mem_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PIXELFLOW_SEMANTIC_MEMORY_ENABLED", "true")
    monkeypatch.setenv("PIXELFLOW_SEMANTIC_MEMORY_PROVIDER", "powermem")
    monkeypatch.setenv("PIXELFLOW_POWERMEM_BASE_URL", "https://test-video.borgrise.com/powermem")
    monkeypatch.setenv("PIXELFLOW_POWERMEM_API_KEY", "secret")
    monkeypatch.setenv("PIXELFLOW_POWERMEM_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("PIXELFLOW_POWERMEM_RECORD_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("PIXELFLOW_POWERMEM_SEARCH_LIMIT", "7")
    monkeypatch.setenv("PIXELFLOW_POWERMEM_WRITE_ENABLED", "false")
    monkeypatch.setenv("PIXELFLOW_POWERMEM_FAIL_OPEN", "false")

    config = load_power_mem_config_from_env()

    assert config.enabled is True
    assert config.provider == "powermem"
    assert config.base_url == "https://test-video.borgrise.com/powermem"
    assert config.api_key == "secret"
    assert config.timeout_seconds == 5
    assert config.record_timeout_seconds == 42
    assert config.search_limit == 7
    assert config.write_enabled is False
    assert config.fail_open is False
