# PowerMem 会话串行化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 PixelFlow 单进程内 PowerMem search、record、health 的重叠请求，并在不阻塞主流程的前提下定向恢复幂等的 `OB_SESSION_ENTRY_EXIST` 失败。

**Architecture:** `PowerMemService` 使用独立的 Client 初始化锁和统一 HTTP 请求锁。search/health 的锁等待与 HTTP 共用短总预算；record 共用长总预算。只有 search/health 对明确的 OceanBase 会话占用错误最多尝试 3 次，record 不自动重试。

**Tech Stack:** Python 3.12、asyncio、httpx、pytest、pytest-asyncio、FastAPI gateway。

## Global Constraints

- PowerMem 保持 fail-open，任何记忆故障不得阻断图片、视频、视频分析或 PPT 主流程。
- search/health 默认总预算 3 秒，record 默认总预算 60 秒。
- search、record、health 的实际 HTTP 请求在同一进程内不得重叠。
- `OB_SESSION_ENTRY_EXIST` 的幂等请求最多尝试 3 次，退避固定为 50ms、100ms。
- record 不因该错误自动重试，避免重复记忆。
- 不新增 Redis、数据库锁或跨进程协调依赖。
- 不记录 token、PowerMem key、完整异常堆栈或原始大 Prompt。
- 新增注释、测试说明、日志说明、文档和提交信息使用中文；程序标识符保持英文。

---

### Task 1: 用失败测试复现 search 与 record 交叉并发

**Files:**
- Modify: `backend/tests/test_powermem_service.py:1-230`
- Test: `backend/tests/test_powermem_service.py`

**Interfaces:**
- Consumes: `PowerMemService.record(...) -> bool`、`PowerMemService.search(...) -> list[SemanticMemoryItem]`
- Produces: 跨类型请求不能重叠、等待锁受总预算限制的回归合同。

- [ ] **Step 1: 把旧的“慢 record 不阻塞 search”测试替换为跨类型串行化测试**

在 imports 增加 `import time`，删除 `test_powermem_service_search_is_not_blocked_by_slow_background_record`，加入：

```python
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
```

- [ ] **Step 2: 增加 search 等待锁超时但不发送重叠请求的失败测试**

```python
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
    await asyncio.wait_for(record_started.wait(), timeout=1)
    started_at = time.monotonic()

    results = await service.search(user_id="u1", query="不能重叠", categories=["experience"])
    elapsed = time.monotonic() - started_at

    assert results == []
    assert search_requests == 0
    assert elapsed < 0.15
    release_record.set()
    assert await record_task is True
    await client.aclose()
```

- [ ] **Step 3: 增加 health 与慢 record 也不能重叠的失败测试**

```python
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
```

- [ ] **Step 4: 运行三个测试并确认 RED**

Run:

```powershell
cd backend
$env:PYTHONPATH='.'
py -3.13 -m uv run pytest tests/test_powermem_service.py::test_powermem_service_serializes_search_behind_slow_record tests/test_powermem_service.py::test_powermem_service_search_lock_wait_uses_short_total_budget tests/test_powermem_service.py::test_powermem_service_serializes_health_behind_slow_record -v
```

Expected: FAIL。当前拆锁实现的 search/health 都能与 record 重叠，`max_active_requests` 为 2，且锁等待测试实际发送了 search 请求。

- [ ] **Step 5: 暂不修改生产代码，提交 RED 测试**

```powershell
git add backend/tests/test_powermem_service.py
git commit -m "test: 复现 PowerMem 跨类型会话并发"
```

### Task 2: 实现统一请求闸门与总超时预算

**Files:**
- Modify: `backend/pixelflow/memory/service.py:108-330`
- Test: `backend/tests/test_powermem_service.py`

**Interfaces:**
- Consumes: Task 1 的失败测试。
- Produces: `_client_lock: asyncio.Lock`、`_request_lock: asyncio.Lock`，以及总预算内串行执行的 `_request(...)`。

- [ ] **Step 1: 将构造器中的三把锁改为两把职责明确的锁**

把：

```python
self._lock = asyncio.Lock()
self._search_lock = asyncio.Lock()
self._record_lock = asyncio.Lock()
```

替换为：

```python
self._client_lock = asyncio.Lock()
self._request_lock = asyncio.Lock()
```

- [ ] **Step 2: 移除 search 和 record 外层的专用锁**

`search()` 保留多分类顺序循环与部分结果逻辑，但删除 `async with self._search_lock:`。`record()` 保留 infer fallback 的两次连续写入，但删除 `async with self._record_lock:`。

- [ ] **Step 3: 在 `_request()` 中实现共享锁与总预算**

将 `_request()` 的请求部分改为：

```python
        request_timeout = timeout if timeout is not None else self.config.timeout_seconds
        async with asyncio.timeout(request_timeout):
            async with self._request_lock:
                response = await client.request(
                    method,
                    self._url(path),
                    headers=headers,
                    json=json,
                    timeout=request_timeout,
                )
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {}
```

`asyncio.timeout()` 必须包住锁等待和 HTTP 请求，不能只包 HTTP。

- [ ] **Step 4: 用 `_client_lock` 保护 Client 初始化**

```python
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        return self._client
```

- [ ] **Step 5: 运行 Task 1 测试并确认 GREEN**

Run:

```powershell
cd backend
$env:PYTHONPATH='.'
py -3.13 -m uv run pytest tests/test_powermem_service.py::test_powermem_service_serializes_search_behind_slow_record tests/test_powermem_service.py::test_powermem_service_search_lock_wait_uses_short_total_budget tests/test_powermem_service.py::test_powermem_service_serializes_health_behind_slow_record -v
```

Expected: `3 passed`，search/health 都不与 record 重叠，search 在短预算内 fail-open。

- [ ] **Step 6: 运行完整 PowerMem 单测**

```powershell
cd backend
$env:PYTHONPATH='.'
py -3.13 -m uv run pytest tests/test_powermem_service.py -v
```

Expected: 全部通过；原多分类部分成功、record infer fallback 和独立 timeout 测试仍通过。

- [ ] **Step 7: 提交统一请求闸门**

```powershell
git add backend/pixelflow/memory/service.py backend/tests/test_powermem_service.py
git commit -m "fix: 串行化 PowerMem 全部请求"
```

### Task 3: 增加幂等 OB_SESSION_ENTRY_EXIST 定向重试

**Files:**
- Modify: `backend/pixelflow/memory/service.py:1-430`
- Modify: `backend/tests/test_powermem_service.py`

**Interfaces:**
- Consumes: Task 2 的 `_request_lock`。
- Produces: `_is_ob_session_entry_exist(exc) -> bool`、`_is_ob_retryable_request(method, path) -> bool`、最多 3 次幂等请求尝试。

- [ ] **Step 1: 让原有“多分类部分成功”测试只表达普通失败语义**

在 `test_powermem_service_searches_categories_sequentially_and_keeps_partial_results` 中，把 brand 分类返回的错误消息：

```python
"message": "Search failed: connect failed OB_SESSION_ENTRY_EXIST(4661)",
```

改为：

```python
"message": "Search failed: temporary backend failure",
```

该测试继续断言 `seen_categories == ["preference", "brand", "skill"]`；OB 重试次数由下面独立测试负责，不能把两个合同混在一个测试中。

- [ ] **Step 2: 写 search 前两次 OB 错误、第三次成功的失败测试**

```python
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
```

- [ ] **Step 3: 写 health 前两次 OB 错误、第三次成功的失败测试**

```python
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
```

- [ ] **Step 4: 写 record 遇到同一错误也不得重试的保护测试**

```python
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
```

- [ ] **Step 5: 运行新测试并确认 search/health 测试 RED**

```powershell
cd backend
$env:PYTHONPATH='.'
py -3.13 -m uv run pytest tests/test_powermem_service.py::test_powermem_service_retries_ob_session_error_for_search tests/test_powermem_service.py::test_powermem_service_retries_ob_session_error_for_health tests/test_powermem_service.py::test_powermem_service_does_not_retry_ob_session_error_for_record -v
```

Expected: search/health 测试 FAIL，当前实现都只请求 1 次；record 保护测试 PASS。

- [ ] **Step 6: 增加常量和错误识别 helper**

在模块常量区加入：

```python
OB_SESSION_MAX_ATTEMPTS = 3
OB_SESSION_RETRY_DELAYS = (0.05, 0.1)
```

在模块底部加入：

```python
def _is_ob_session_entry_exist(exc: httpx.HTTPStatusError) -> bool:
    return "OB_SESSION_ENTRY_EXIST" in exc.response.text.upper()


def _is_ob_retryable_request(method: str, path: str) -> bool:
    return method.upper() == "GET" or path.rstrip("/").endswith("/memories/search")
```

- [ ] **Step 7: 把共享锁内的单次 HTTP 调用改为有限重试循环**

```python
            async with self._request_lock:
                max_attempts = OB_SESSION_MAX_ATTEMPTS if _is_ob_retryable_request(method, path) else 1
                for attempt in range(max_attempts):
                    try:
                        response = await client.request(
                            method,
                            self._url(path),
                            headers=headers,
                            json=json,
                            timeout=request_timeout,
                        )
                        response.raise_for_status()
                        if response.content:
                            return response.json()
                        return {}
                    except httpx.HTTPStatusError as exc:
                        if not _is_ob_session_entry_exist(exc) or attempt + 1 >= max_attempts:
                            raise
                        await asyncio.sleep(OB_SESSION_RETRY_DELAYS[attempt])
```

外层 `asyncio.timeout(request_timeout)` 保持不变，确保锁等待、退避和 HTTP 总和不超过预算。

- [ ] **Step 8: 运行 PowerMem 单测并确认 GREEN**

```powershell
cd backend
$env:PYTHONPATH='.'
py -3.13 -m uv run pytest tests/test_powermem_service.py -v
```

Expected: 全部通过，search/health 总尝试 3 次，record 总尝试 1 次，原多分类测试仍只访问每个分类一次。

- [ ] **Step 9: 提交定向重试**

```powershell
git add backend/pixelflow/memory/service.py backend/tests/test_powermem_service.py
git commit -m "fix: 恢复 PowerMem 幂等会话错误"
```

### Task 4: 同步 PowerMem 文档并做定向回归

**Files:**
- Modify: `README.md:175-210`
- Modify: `AGENTS.md:165-205`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md:306-345`
- Modify: `CONTENT_APP_API_CALLS.md`

**Interfaces:**
- Consumes: Tasks 1-3 的最终行为。
- Produces: 单进程串行化、短预算 fail-open、多实例边界的权威文档。

- [ ] **Step 1: 在四份文档写入一致规则**

加入以下中文规则，按各文档原有章节放置：

```markdown
- PixelFlow 进程内所有 PowerMem search、record、health HTTP 请求共用同一请求闸门，避免 OceanBase `OB_SESSION_ENTRY_EXIST`。
- search/health 的锁等待和 HTTP 共用短总预算，超时直接 fail-open，不绕过闸门并发请求；record 使用独立长预算。
- 只有幂等的 search/health 对 `OB_SESSION_ENTRY_EXIST` 最多尝试 3 次，record 不自动重试。
- 该闸门不跨进程；多 worker、多容器或多副本部署仍需要 PowerMem 服务端正确管理数据库 Session。
```

- [ ] **Step 2: 运行定向测试、格式检查和敏感信息扫描**

```powershell
cd backend
$env:PYTHONPATH='.'
py -3.13 -m uv run pytest tests/test_powermem_service.py -v
py -3.13 -m uv run ruff check pixelflow/memory/service.py tests/test_powermem_service.py
cd ..
git diff --check
$changed = git diff --name-only
if (Select-String -Path $changed -Pattern 'Bearer\s+eyJ|powermem_api_key:\s*[^<\s]' -Quiet) { throw '改动中疑似包含敏感凭据' }
```

Expected: pytest 0 failures、ruff 0 errors、`git diff --check` 无输出、敏感信息扫描无命中。

- [ ] **Step 3: 提交文档**

```powershell
git add README.md AGENTS.md docs/pixelflow-agent-skill-flow-latest-design.md CONTENT_APP_API_CALLS.md
git commit -m "docs: 说明 PowerMem 会话串行化边界"
```

## Completion Gate

- [ ] search、record、health 的 MockTransport 最大同时请求数为 1。
- [ ] search 等待慢 record 时在短总预算内 fail-open，且没有绕过闸门发送重叠 HTTP。
- [ ] search/health 遇到 `OB_SESSION_ENTRY_EXIST` 最多尝试 3 次并受总预算约束。
- [ ] record 遇到同一错误只尝试 1 次，避免重复写记忆。
- [ ] `backend/tests/test_powermem_service.py` 和目标 ruff 检查全部通过。
- [ ] 四份当前事实文档都说明单进程保证、fail-open 预算和多 worker 边界。
