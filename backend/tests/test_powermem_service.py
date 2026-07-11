from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

import pixelflow.memory.service as powermem_service_module
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
async def test_powermem_service_searches_categories_sequentially_and_keeps_partial_results():
    active_requests = 0
    max_active_requests = 0
    seen_categories: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, max_active_requests
        payload = json.loads(request.content.decode("utf-8"))
        category = str(payload.get("filters", {}).get("category") or "")
        seen_categories.append(category)
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        await asyncio.sleep(0.01)
        active_requests -= 1
        if category == "brand":
            return httpx.Response(
                500,
                request=request,
                json={
                    "success": False,
                    "error": {
                        "code": "SEARCH_FAILED",
                        "message": "Search failed: temporary backend failure",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {
                            "memory_id": f"{category}-1",
                            "content": f"{category} memory",
                            "score": 0.8,
                            "metadata": {"category": category},
                        }
                    ]
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret", fail_open=True),
        http_client=client,
    )

    results = await service.search(user_id="u1", query="偏好", categories=["preference", "brand", "skill"])

    assert seen_categories == ["preference", "brand", "skill"]
    assert max_active_requests == 1
    assert [item.memory_id for item in results] == ["preference-1", "skill-1"]
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_retries_ob_session_error_for_search():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                500,
                request=request,
                json={
                    "success": False,
                    "error": {
                        "code": "SEARCH_FAILED",
                        "message": "connect failed OB_SESSION_ENTRY_EXIST(4661)",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {
                            "memory_id": "recovered-1",
                            "content": "重试后恢复",
                            "score": 0.9,
                            "metadata": {},
                        }
                    ]
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret", fail_open=True),
        http_client=client,
    )

    results = await service.search(user_id="u1", query="恢复检索", categories=["experience"])

    assert attempts == 3
    assert [item.memory_id for item in results] == ["recovered-1"]
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_retries_ob_session_error_for_health():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                500,
                request=request,
                json={"success": False, "error": {"message": "OB_SESSION_ENTRY_EXIST(4661)"}},
            )
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret", fail_open=True),
        http_client=client,
    )

    result = await service.health()

    assert attempts == 3
    assert result["status"] == "ok"
    await client.aclose()


@pytest.mark.parametrize(
    "response_kwargs",
    [
        {"json": {"success": False, "error": {"message": "NOT_OB_SESSION_ENTRY_EXIST(4661)"}}},
        {
            "json": {
                "success": False,
                "error": {"code": "OB_SESSION_ENTRY_EXISTS", "message": "temporary backend failure"},
            }
        },
        {
            "json": {
                "success": False,
                "error": {"code": "SEARCH_FAILED", "message": "temporary backend failure"},
                "details": "OB_SESSION_ENTRY_EXIST(4661)",
            }
        },
        {"text": "NOT_OB_SESSION_ENTRY_EXIST(4661)"},
        {"text": "OB_SESSION_ENTRY_EXISTS(4661)"},
    ],
    ids=[
        "structured-prefixed-token",
        "structured-suffixed-token",
        "structured-unrelated-field",
        "text-prefixed-token",
        "text-suffixed-token",
    ],
)
@pytest.mark.asyncio
async def test_powermem_service_does_not_retry_similar_or_unrelated_ob_text(
    monkeypatch: pytest.MonkeyPatch,
    response_kwargs: dict,
):
    attempts = 0
    retry_delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        retry_delays.append(delay)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, request=request, **response_kwargs)

    monkeypatch.setattr(powermem_service_module.asyncio, "sleep", fake_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret", fail_open=True),
        http_client=client,
    )

    results = await service.search(user_id="u1", query="相似错误", categories=["experience"])

    assert results == []
    assert attempts == 1
    assert retry_delays == []
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_uses_ordered_backoff_for_non_json_ob_error(
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = 0
    retry_delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        retry_delays.append(delay)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                500,
                request=request,
                text="temporary backend failure: OB_SESSION_ENTRY_EXIST(4661)",
            )
        return httpx.Response(200, json={"success": True, "data": {"results": []}})

    monkeypatch.setattr(powermem_service_module.asyncio, "sleep", fake_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret", fail_open=True),
        http_client=client,
    )

    results = await service.search(user_id="u1", query="退避顺序", categories=["experience"])

    assert results == []
    assert attempts == 3
    assert retry_delays == [0.05, 0.1]
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_ob_backoff_stays_inside_total_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = 0
    retry_delays: list[float] = []
    never_release = asyncio.Event()
    sleep_cancelled = asyncio.Event()

    async def controlled_sleep(delay: float) -> None:
        retry_delays.append(delay)
        try:
            await never_release.wait()
        finally:
            sleep_cancelled.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            500,
            request=request,
            json={"success": False, "error": {"message": "OB_SESSION_ENTRY_EXIST(4661)"}},
        )

    monkeypatch.setattr(powermem_service_module.asyncio, "sleep", controlled_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(
            enabled=True,
            base_url="https://example.test",
            api_key="secret",
            timeout_seconds=0.01,
            fail_open=True,
        ),
        http_client=client,
    )

    results = await service.search(user_id="u1", query="总预算", categories=["experience"])

    assert results == []
    assert attempts == 1
    assert retry_delays == [0.05]
    assert sleep_cancelled.is_set()
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_does_not_retry_ob_session_error_for_record():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            500,
            request=request,
            json={"success": False, "error": {"message": "OB_SESSION_ENTRY_EXIST(4661)"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret", fail_open=True),
        http_client=client,
    )

    ok = await service.record(user_id="u1", content="不能重复写", category="experience", infer=False)

    assert ok is False
    assert attempts == 1
    await client.aclose()


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/system/health", True),
        ("POST", "/memories/search", True),
        ("GET", "/memories/search", False),
        ("GET", "/system/info", False),
        ("DELETE", "/memories/search", False),
        ("POST", "/tenant/memories/search", False),
        ("POST", "/memories/search/", False),
        ("get", "/system/health", False),
    ],
)
def test_powermem_service_only_retries_exact_allowed_requests(method: str, path: str, expected: bool):
    assert powermem_service_module._is_ob_retryable_request(method, path) is expected


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
async def test_powermem_service_serializes_parallel_record_requests():
    active_requests = 0
    max_active_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, max_active_requests
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        await asyncio.sleep(0.01)
        active_requests -= 1
        return httpx.Response(200, json={"success": True, "data": [{"memory_id": "1"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(enabled=True, base_url="https://example.test", api_key="secret"),
        http_client=client,
    )

    await asyncio.gather(
        service.record(user_id="u1", content="偏好 A", category="preference"),
        service.record(user_id="u1", content="偏好 B", category="preference"),
    )

    assert max_active_requests == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_serializes_search_behind_slow_record():
    active_requests = 0
    max_active_requests = 0
    record_started = asyncio.Event()
    release_record = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, max_active_requests
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        try:
            if request.url.path.endswith("/memories/search"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "results": [
                                {
                                    "memory_id": "search-1",
                                    "content": "search result",
                                    "score": 0.9,
                                    "metadata": {},
                                }
                            ]
                        },
                    },
                )
            record_started.set()
            await release_record.wait()
            return httpx.Response(200, json={"success": True, "data": [{"memory_id": "record-1"}]})
        finally:
            active_requests -= 1

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(
            enabled=True,
            base_url="https://example.test",
            api_key="secret",
            timeout_seconds=0.5,
            record_timeout_seconds=1.0,
        ),
        http_client=client,
    )

    record_task = asyncio.create_task(
        service.record(user_id="u1", content="后台记忆", category="experience", infer=False)
    )
    await asyncio.wait_for(record_started.wait(), timeout=1)
    search_task = asyncio.create_task(
        service.search(user_id="u1", query="检索记忆", categories=["experience"])
    )
    await asyncio.sleep(0.02)

    release_record.set()
    results = await search_task

    assert await record_task is True
    assert [item.memory_id for item in results] == ["search-1"]
    assert max_active_requests == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_powermem_service_search_lock_wait_uses_short_total_budget():
    record_started = asyncio.Event()
    release_record = asyncio.Event()
    search_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_requests
        if request.url.path.endswith("/memories/search"):
            search_requests += 1
            return httpx.Response(200, json={"success": True, "data": {"results": []}})
        record_started.set()
        await release_record.wait()
        return httpx.Response(200, json={"success": True, "data": [{"memory_id": "record-1"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(
            enabled=True,
            base_url="https://example.test",
            api_key="secret",
            timeout_seconds=0.03,
            record_timeout_seconds=1.0,
            fail_open=True,
        ),
        http_client=client,
    )

    record_task = asyncio.create_task(
        service.record(user_id="u1", content="慢速后台记忆", category="experience", infer=False)
    )
    try:
        await asyncio.wait_for(record_started.wait(), timeout=1)
        started_at = time.monotonic()

        results = await service.search(user_id="u1", query="不能重叠", categories=["experience"])
        elapsed = time.monotonic() - started_at

        assert results == []
        assert search_requests == 0
        assert elapsed < 0.15
    finally:
        release_record.set()
        record_result = await record_task
        await client.aclose()
    assert record_result is True


@pytest.mark.asyncio
async def test_powermem_service_serializes_health_behind_slow_record():
    active_requests = 0
    max_active_requests = 0
    record_started = asyncio.Event()
    release_record = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, max_active_requests
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        try:
            if request.url.path.endswith("/system/health"):
                return httpx.Response(200, json={"status": "ok"})
            record_started.set()
            await release_record.wait()
            return httpx.Response(200, json={"success": True, "data": [{"memory_id": "record-1"}]})
        finally:
            active_requests -= 1

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = PowerMemService(
        PowerMemConfig(
            enabled=True,
            base_url="https://example.test",
            api_key="secret",
            timeout_seconds=0.5,
            record_timeout_seconds=1.0,
        ),
        http_client=client,
    )

    record_task = asyncio.create_task(
        service.record(user_id="u1", content="后台记忆", category="experience", infer=False)
    )
    await asyncio.wait_for(record_started.wait(), timeout=1)
    health_task = asyncio.create_task(service.health())
    await asyncio.sleep(0.02)
    release_record.set()

    assert await record_task is True
    assert (await health_task)["status"] == "ok"
    assert max_active_requests == 1
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
