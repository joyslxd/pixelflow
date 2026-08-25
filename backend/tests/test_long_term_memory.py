"""验证 Mem0 长期记忆 Port 的安全映射与后台写入。"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pixelflow.long_term_memory.outbox import SQLWriteOutbox
from pixelflow.long_term_memory.service import (
    LongTermMemoryConfig,
    LongTermMemoryService,
    VolcengineMem0Adapter,
)
from pixelflow.platform.persistence import Base


class _Mem0Client:
    """以 SDK 同形接口模拟 Mem0，不触发真实网络请求。"""

    def search(self, query: str, **kwargs: object) -> dict[str, object]:
        assert query == "偏好"
        assert str(kwargs["user_id"]).startswith("pfu_")
        assert kwargs["user_id"] != "user-1"
        return {"results": [{"id": "memory-1", "memory": "偏好暖色调"}]}

    def add(self, messages: list[dict[str, str]], **kwargs: object) -> dict[str, str]:
        assert messages[0]["content"] == "偏好暖色调"
        assert str(kwargs["user_id"]).startswith("pfu_")
        return {"event_id": "event-1"}


@pytest.mark.asyncio
async def test_mem0_adapter_maps_sdk_result_and_background_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 输出只转换成安全摘要，写入在后台任务中完成。"""

    monkeypatch.setenv("PIXELFLOW_LONG_TERM_MEMORY_USER_SALT", "测试匿名化盐")

    config = LongTermMemoryConfig(
        enabled=True,
        base_url="https://mem0.example.invalid",
        api_key="test-key",
        timeout_seconds=1,
        search_limit=5,
    )
    adapter = VolcengineMem0Adapter(config)
    adapter._client = _Mem0Client()  # type: ignore[attr-defined]
    service = LongTermMemoryService(adapter, config)

    items = await service.search(user_id="user-1", query="偏好")
    assert [(item.memory_id, item.content) for item in items] == [("memory-1", "偏好暖色调")]

    service.write_background(
        user_id="user-1",
        content="偏好暖色调",
        category="preference",
        write_key="write-1",
    )
    await service.aclose()


@pytest.mark.asyncio
async def test_mem0_adapter_uses_anonymous_user_id_and_maps_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mem0 请求不得携带 PixelFlow 原始用户标识，并支持删除稳定 DTO。"""

    monkeypatch.setenv("PIXELFLOW_LONG_TERM_MEMORY_USER_SALT", "测试匿名化盐")

    class Client(_Mem0Client):
        def get(self, memory_id: str) -> dict[str, str]:
            assert memory_id == "memory-1"
            return {"id": memory_id, "memory": "偏好暖色调", "category": "preference"}

        def delete(self, memory_id: str) -> dict[str, str]:
            assert memory_id == "memory-1"
            return {"status": "deleted"}

        def delete_all(self, **kwargs: object) -> dict[str, str]:
            assert str(kwargs["user_id"]).startswith("pfu_")
            assert kwargs["user_id"] != "user-1"
            return {"status": "deleted"}

    config = LongTermMemoryConfig(True, "https://mem0.example.invalid", "test-key", 1, 5)
    adapter = VolcengineMem0Adapter(config)
    adapter._client = Client()  # type: ignore[attr-defined]

    assert await adapter.get(memory_id="memory-1") is not None
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

        async def get(self, *, memory_id: str):
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
        assert await adapter.get(memory_id=second.event_id) is None
        await outbox.release(write_key=second.write_key, worker_id="worker")
        third = await outbox.claim(worker_id="worker", now=datetime.now(UTC))
        assert third is not None and third.event_id == "memory-1"
        assert await adapter.get(memory_id=third.event_id) is not None
        await outbox.complete(write_key=third.write_key, worker_id="worker", event_id=third.event_id)
        assert adapter.add_calls == 1
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
        await adapter.search(user_id=user_id, query="M1 隔离测试偏好", limit=3)
    finally:
        assert await adapter.delete_all(user_id=user_id)
