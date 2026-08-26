"""验证 Mem0 长期记忆 Port 的安全映射与后台写入。"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pixelflow.long_term_memory.outbox import MemoryWriteWorker, SQLWriteOutbox
from pixelflow.long_term_memory.service import (
    LongTermMemoryConfig,
    LongTermMemoryService,
    VolcengineMem0Adapter,
)
from pixelflow.platform.persistence import Base


@pytest.mark.asyncio
async def test_mem0_adapter_maps_v1_result_and_background_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 HTTP 输出只转换成安全摘要，写入在后台任务中完成。"""

    monkeypatch.setenv("PIXELFLOW_LONG_TERM_MEMORY_USER_SALT", "测试匿名化盐")

    config = LongTermMemoryConfig(
        enabled=True,
        base_url="https://mem0.example.invalid",
        api_key="test-key",
        timeout_seconds=1,
        search_limit=5,
    )
    adapter = VolcengineMem0Adapter(config)

    async def v1_request(
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        payload: dict[str, object] | None = None,
    ) -> object | None:
        assert method == "POST"
        assert path == "/v1/memories/search/"
        assert params is None
        assert payload is not None
        assert payload["query"] == "偏好"
        assert str(payload["user_id"]).startswith("pfu_")
        assert payload["user_id"] != "user-1"
        assert payload["limit"] == 5
        return {"results": [{"id": "memory-1", "memory": "偏好暖色调"}]}

    adapter._v1_request = v1_request  # type: ignore[method-assign]

    class Outbox:
        writes: list[dict[str, str]] = []

        async def enqueue(self, **kwargs: str) -> None:
            self.writes.append(kwargs)

    outbox = Outbox()
    service = LongTermMemoryService(adapter, config, outbox=outbox)

    items = await service.search(user_id="user-1", query="偏好")
    assert [(item.memory_id, item.content) for item in items] == [("memory-1", "偏好暖色调")]

    service.write_background(
        user_id="user-1",
        content="偏好暖色调",
        category="preference",
        write_key="write-1",
    )
    await service.aclose()
    assert outbox.writes == [
        {
            "user_id": "user-1",
            "content": "偏好暖色调",
            "category": "preference",
            "write_key": "write-1",
        }
    ]


@pytest.mark.asyncio
async def test_mem0_adapter_persists_nested_v1_event_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """火山 v1 add 的 results 内 event_id 必须成为 Outbox 的唯一恢复身份。"""

    monkeypatch.setenv("PIXELFLOW_LONG_TERM_MEMORY_USER_SALT", "测试匿名化盐")
    config = LongTermMemoryConfig(True, "https://mem0.example.invalid", "test-key", 1, 5)
    adapter = VolcengineMem0Adapter(config)

    async def v1_request(
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        payload: dict[str, object] | None = None,
    ) -> object | None:
        assert method == "POST"
        assert path == "/v1/memories/"
        assert params is None
        assert payload is not None
        assert str(payload["user_id"]).startswith("pfu_")
        assert payload["metadata"] == {"category": "preference", "memory_write_key": "write-1"}
        return {"results": [{"event_id": "event-1", "status": "PENDING"}]}

    adapter._v1_request = v1_request  # type: ignore[method-assign]
    assert await adapter.add(
        user_id="user-1",
        content="偏好暖色调",
        category="preference",
        write_key="write-1",
    ) == "event-1"


@pytest.mark.asyncio
async def test_mem0_adapter_uses_anonymous_user_id_and_maps_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mem0 请求不得携带 PixelFlow 原始用户标识，并支持删除稳定 DTO。"""

    monkeypatch.setenv("PIXELFLOW_LONG_TERM_MEMORY_USER_SALT", "测试匿名化盐")

    config = LongTermMemoryConfig(True, "https://mem0.example.invalid", "test-key", 1, 5)
    adapter = VolcengineMem0Adapter(config)

    async def v1_request(
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        payload: dict[str, object] | None = None,
    ) -> object | None:
        if method == "POST":
            assert path == "/v1/memories/search/"
            assert payload is not None
            assert payload["query"] == "偏好暖色调"
            assert str(payload["user_id"]).startswith("pfu_")
            assert payload["limit"] == 20
            return {
                "results": [
                    {
                        "id": "memory-1",
                        "memory": "偏好暖色调",
                        "metadata": {"category": "preference", "memory_write_key": "write-1"},
                    }
                ]
            }
        if method == "GET":
            assert path == "/v1/memories/memory-1/"
            assert payload is None
            return {"id": "memory-1", "memory": "偏好暖色调", "category": "preference"}
        if method == "DELETE" and path == "/v1/memories/memory-1/":
            assert payload is None
            return {}
        assert method == "DELETE"
        assert path == "/v1/memories/"
        assert params is not None
        assert str(params["user_id"]).startswith("pfu_")
        assert params["user_id"] != "user-1"
        return {}

    adapter._v1_request = v1_request  # type: ignore[method-assign]

    assert await adapter.get(memory_id="memory-1") is not None
    assert await adapter.get_event(
        event_id="event-1",
        user_id="user-1",
        content="偏好暖色调",
        write_key="write-1",
    ) is not None
    assert await adapter.delete(memory_id="memory-1")
    assert await adapter.delete_all(user_id="user-1")


@pytest.mark.asyncio
async def test_write_outbox_is_idempotent_and_recoverable() -> None:
    """同一写入键只保留一条记录，过期租约允许下一 Worker 接管。"""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        outbox = SQLWriteOutbox(async_sessionmaker(engine, expire_on_commit=False))
        await outbox.enqueue(
            user_id="user-1",
            content="偏好暖色调",
            category="preference",
            write_key="write-1",
        )
        await outbox.enqueue(
            user_id="user-1",
            content="偏好暖色调",
            category="preference",
            write_key="write-1",
        )
        first = await outbox.claim(worker_id="worker-1", now=datetime.now(UTC))
        assert first is not None
        await outbox.complete(write_key="write-1", worker_id="worker-1", event_id="event-1")
        assert await outbox.claim(worker_id="worker-2", now=datetime.now(UTC)) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_write_outbox_persists_event_then_polls_same_identity() -> None:
    """异步写入先持久化 event_id，重试只轮询同一身份而不重复 add。"""

    class Adapter:
        add_calls = 0
        get_calls = 0

        async def add(self, **_: object) -> str:
            self.add_calls += 1
            return "memory-1"

        async def get_event(self, *, event_id: str, **_: object):
            self.get_calls += 1
            return None if self.get_calls == 1 else object()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        outbox = SQLWriteOutbox(async_sessionmaker(engine, expire_on_commit=False))
        await outbox.enqueue(user_id="user-1", content="偏好", category="preference", write_key="write-poll")
        adapter = Adapter()
        first = await outbox.claim(worker_id="worker", now=datetime.now(UTC))
        assert first is not None and first.event_id is None
        event_id = await adapter.add(user_id=first.user_id, content=first.content, category=first.category, write_key=first.write_key)
        assert await outbox.save_event_id(write_key=first.write_key, worker_id="worker", event_id=event_id)
        await outbox.release(write_key=first.write_key, worker_id="worker")
        second = await outbox.claim(worker_id="worker", now=datetime.now(UTC))
        assert second is not None and second.event_id == "memory-1"
        assert await adapter.get_event(event_id=second.event_id, user_id="user-1", content="偏好", write_key="write-poll") is None
        await outbox.release(write_key=second.write_key, worker_id="worker")
        third = await outbox.claim(worker_id="worker", now=datetime.now(UTC))
        assert third is not None and third.event_id == "memory-1"
        assert await adapter.get_event(event_id=third.event_id, user_id="user-1", content="偏好", write_key="write-poll") is not None
        await outbox.complete(write_key=third.write_key, worker_id="worker", event_id=third.event_id)
        assert adapter.add_calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_persists_remote_event_then_only_polls_same_event() -> None:
    """Worker 首次 add 后必须落 event_id，后续轮次不得再次调用 add。"""

    class Adapter:
        add_calls = 0
        event_calls = 0

        async def add(self, **_: object) -> str:
            self.add_calls += 1
            return "event-1"

        async def get_event(self, *, event_id: str, **_: object):
            assert event_id == "event-1"
            self.event_calls += 1
            return None if self.event_calls == 1 else object()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        outbox = SQLWriteOutbox(async_sessionmaker(engine, expire_on_commit=False))
        await outbox.enqueue(user_id="user-1", content="偏好", category="preference", write_key="write-worker")
        adapter = Adapter()
        worker = MemoryWriteWorker(
            outbox,
            adapter,
            worker_id="worker",
            event_poll_delay_seconds=0,
        )
        assert await worker.run_once()
        assert await worker.run_once()
        assert await worker.run_once()
        assert adapter.add_calls == 1
        assert adapter.event_calls == 2
        assert await outbox.claim(worker_id="worker-2", now=datetime.now(UTC)) is None
    finally:
        await engine.dispose()


@pytest.mark.mem0_real
@pytest.mark.asyncio
async def test_real_mem0_search_add_and_cleanup() -> None:
    """仅在隔离 Secret 环境验证 Mem0 读写和清理，不将任何凭据或正文写入报告。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_MEM0") != "1":
        pytest.skip("未显式开启隔离 Mem0 真实验证")
    config = LongTermMemoryConfig(
        enabled=True,
        base_url=os.environ.get("PIXELFLOW_VOLCENGINE_MEM0_BASE_URL", "").strip(),
        api_key=os.environ.get("PIXELFLOW_VOLCENGINE_MEM0_API_KEY", "").strip(),
        timeout_seconds=10,
        search_limit=3,
    )
    if not config.available or not os.environ.get("PIXELFLOW_LONG_TERM_MEMORY_USER_SALT", "").strip():
        pytest.skip("缺少隔离 Mem0 地址、密钥或匿名化盐")
    adapter = VolcengineMem0Adapter(config)
    user_id = "m1-real-mem0-test-user"
    try:
        event_id = await adapter.add(
            user_id=user_id,
            content="M1 隔离测试偏好，请在测试结束后删除。",
            category="preference",
            write_key="m1-real-mem0-cleanup",
        )
        assert event_id
        completed = None
        for _ in range(10):
            completed = await adapter.get_event(
                event_id=event_id,
                user_id=user_id,
                content="M1 隔离测试偏好，请在测试结束后删除。",
                write_key="m1-real-mem0-cleanup",
            )
            if completed is not None:
                break
            await asyncio.sleep(2)
        assert completed is not None
        await adapter.search(user_id=user_id, query="M1 隔离测试偏好", limit=3)
    finally:
        assert await adapter.delete_all(user_id=user_id)
