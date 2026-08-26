"""以 Volcengine Mem0 为实现的长期记忆应用服务。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LongTermMemoryItem:
    """可注入 Harness 上下文的安全记忆摘要。"""

    memory_id: str
    content: str
    category: str


@dataclass(frozen=True, slots=True)
class LongTermMemoryConfig:
    """长期记忆运行配置；远程异常始终 fail-open。"""

    enabled: bool
    base_url: str
    api_key: str
    timeout_seconds: float
    search_limit: int

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.base_url) and bool(self.api_key)


class LongTermMemoryPort(Protocol):
    """隔离 Volcengine Mem0 HTTP 协议的稳定应用 Port。"""

    async def search(self, *, user_id: str, query: str, limit: int) -> list[LongTermMemoryItem]: ...

    async def add(self, *, user_id: str, content: str, category: str, write_key: str) -> str | None: ...

    async def get(self, *, memory_id: str) -> LongTermMemoryItem | None: ...

    async def history(self, *, memory_id: str) -> list[LongTermMemoryItem]: ...

    async def update(self, *, memory_id: str, content: str) -> LongTermMemoryItem | None: ...

    async def get_event(
        self,
        *,
        event_id: str,
        user_id: str,
        content: str,
        write_key: str,
    ) -> LongTermMemoryItem | None: ...

    async def delete(self, *, memory_id: str) -> bool: ...

    async def delete_all(self, *, user_id: str) -> bool: ...


class MemoryWriteOutboxPort(Protocol):
    """长期记忆写入的持久化入口；业务层不得直接调用远程 add。"""

    async def enqueue(self, *, user_id: str, content: str, category: str, write_key: str) -> None: ...

    async def requeue_manual_review(self, *, user_id: str, write_key: str) -> bool: ...


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


def load_long_term_memory_config_from_env() -> LongTermMemoryConfig:
    """从 Gateway profile 映射后的环境变量读取 Mem0 配置。"""

    try:
        timeout_seconds = float(os.getenv("PIXELFLOW_LONG_TERM_MEMORY_TIMEOUT_SECONDS", "3"))
    except ValueError:
        timeout_seconds = 3.0
    try:
        search_limit = int(os.getenv("PIXELFLOW_LONG_TERM_MEMORY_SEARCH_LIMIT", "5"))
    except ValueError:
        search_limit = 5
    return LongTermMemoryConfig(
        enabled=_bool_env("PIXELFLOW_LONG_TERM_MEMORY_ENABLED", True),
        base_url=os.getenv("PIXELFLOW_VOLCENGINE_MEM0_BASE_URL", "").strip().rstrip("/"),
        api_key=os.getenv("PIXELFLOW_VOLCENGINE_MEM0_API_KEY", "").strip(),
        timeout_seconds=max(0.1, timeout_seconds),
        search_limit=max(1, min(search_limit, 20)),
    )


def _anonymous_user_id(user_id: str) -> str:
    """将 PixelFlow owner 映射为 Mem0 不可逆匿名主体，避免传输原始用户标识。"""

    salt = os.getenv("PIXELFLOW_LONG_TERM_MEMORY_USER_SALT", "").strip()
    if not salt:
        # 未配置部署盐时禁用远程记忆写入/检索，不能退化为上传原始 user_id。
        return ""
    return "pfu_" + hashlib.sha256(f"v1:{salt}:{user_id}".encode()).hexdigest()[:40]


class VolcengineMem0Adapter:
    """Mem0 v1 HTTP 防腐适配器，不把供应商响应或异常正文传播给业务层。"""

    def __init__(self, config: LongTermMemoryConfig) -> None:
        self._config = config

    async def _v1_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        payload: dict[str, object] | None = None,
    ) -> object | None:
        """调用已在火山环境验证的 Mem0 v1 路径，失败只返回空安全结果。"""

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                response = await client.request(
                    method,
                    f"{self._config.base_url}{path}",
                    headers={"Authorization": f"Token {self._config.api_key}"},
                    params=params,
                    json=payload,
                )
            if response.status_code >= httpx.codes.BAD_REQUEST:
                return None
            if response.status_code == httpx.codes.NO_CONTENT or not response.content:
                return {}
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    async def search(self, *, user_id: str, query: str, limit: int) -> list[LongTermMemoryItem]:
        anonymous_user_id = _anonymous_user_id(user_id)
        if not self._config.available or not query.strip() or not anonymous_user_id:
            return []
        records = await self._search_records(
            anonymous_user_id=anonymous_user_id,
            query=query,
            limit=limit,
        )
        return [item for record in records if (item := self._map_item(record)) is not None]

    async def _search_records(
        self,
        *,
        anonymous_user_id: str,
        query: str,
        limit: int,
    ) -> list[Mapping[str, object]]:
        """按匿名主体检索 v1 记录，供公开 Context 与事件完成确认复用。"""

        payload = await self._v1_request(
            "POST",
            "/v1/memories/search/",
            payload={"query": query, "user_id": anonymous_user_id, "limit": limit},
        )
        records = payload.get("results", payload) if isinstance(payload, Mapping) else payload
        if not isinstance(records, list):
            return []
        return [record for record in records[:limit] if isinstance(record, Mapping)]

    @staticmethod
    def _map_item(record: Mapping[str, object]) -> LongTermMemoryItem | None:
        """将供应商记录缩减为可公开注入的安全记忆摘要。"""

        content = str(record.get("memory") or record.get("content") or "").strip()
        memory_id = str(record.get("id") or record.get("memory_id") or "").strip()
        if not content or not memory_id:
            return None
        metadata = record.get("metadata")
        category = metadata.get("category") if isinstance(metadata, Mapping) else None
        return LongTermMemoryItem(
            memory_id=memory_id,
            content=content[:1_000],
            category=str(category or record.get("category") or "preference")[:32],
        )

    async def add(self, *, user_id: str, content: str, category: str, write_key: str) -> str | None:
        anonymous_user_id = _anonymous_user_id(user_id)
        if not self._config.available or not content.strip() or not anonymous_user_id:
            return None
        payload = await self._v1_request(
            "POST",
            "/v1/memories/",
            payload={
                "messages": [{"role": "user", "content": content}],
                "user_id": anonymous_user_id,
                "metadata": {"category": category, "memory_write_key": write_key},
            },
        )
        if isinstance(payload, Mapping):
            event_id = payload.get("event_id") or payload.get("id")
            if not event_id:
                results = payload.get("results")
                if isinstance(results, list) and results and isinstance(results[0], Mapping):
                    event_id = results[0].get("event_id") or results[0].get("id")
            return str(event_id).strip() or None
        return None

    async def get(self, *, memory_id: str) -> LongTermMemoryItem | None:
        """按 Mem0 memory ID 查询已稳定记录，只映射安全字段。"""

        if not self._config.available or not memory_id.strip():
            return None
        payload = await self._v1_request("GET", f"/v1/memories/{memory_id}/")
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            return None
        content = str(payload.get("memory") or payload.get("content") or "").strip()
        resolved_id = str(payload.get("id") or memory_id).strip()
        if not content or not resolved_id:
            return None
        return LongTermMemoryItem(
            memory_id=resolved_id,
            content=content[:1_000],
            category=str(payload.get("category") or "preference")[:32],
        )

    async def history(self, *, memory_id: str) -> list[LongTermMemoryItem]:
        """读取单条记忆的版本历史，只返回安全 DTO，不传播供应商审计字段。"""

        if not self._config.available or not memory_id.strip():
            return []
        payload = await self._v1_request("GET", f"/v1/memories/{memory_id}/history/")
        records = payload.get("results", payload) if isinstance(payload, Mapping) else payload
        if not isinstance(records, list):
            return []
        return [item for record in records if isinstance(record, Mapping) and (item := self._map_item(record)) is not None]

    async def update(self, *, memory_id: str, content: str) -> LongTermMemoryItem | None:
        """更新稳定记忆正文；输入为空或远程异常时返回空而不泄露供应商响应。"""

        if not self._config.available or not memory_id.strip() or not content.strip():
            return None
        payload = await self._v1_request(
            "PUT",
            f"/v1/memories/{memory_id}/",
            payload={"text": content},
        )
        if isinstance(payload, Mapping):
            item = self._map_item(payload)
            if item is not None:
                return item
        return await self.get(memory_id=memory_id)

    async def get_event(
        self,
        *,
        event_id: str,
        user_id: str,
        content: str,
        write_key: str,
    ) -> LongTermMemoryItem | None:
        """以同一 write_key 检索 PENDING event 的最终记录；永不重新提交 add。"""

        anonymous_user_id = _anonymous_user_id(user_id)
        if (
            not self._config.available
            or not event_id.strip()
            or not content.strip()
            or not write_key.strip()
            or not anonymous_user_id
        ):
            return None
        for record in await self._search_records(
            anonymous_user_id=anonymous_user_id,
            query=content,
            limit=20,
        ):
            metadata = record.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("memory_write_key") == write_key:
                return self._map_item(record)
        return None

    async def delete(self, *, memory_id: str) -> bool:
        """删除单条远程记忆；失败只返回 false，避免泄露供应商正文。"""

        if not self._config.available or not memory_id.strip():
            return False
        return await self._v1_request("DELETE", f"/v1/memories/{memory_id}/") is not None

    async def delete_all(self, *, user_id: str) -> bool:
        """按匿名主体删除远程记忆，绝不把原始 owner 发送给 Mem0。"""

        anonymous_user_id = _anonymous_user_id(user_id)
        if not self._config.available or not anonymous_user_id:
            return False
        return (
            await self._v1_request(
                "DELETE",
                "/v1/memories/",
                params={"user_id": anonymous_user_id},
            )
        ) is not None


class LongTermMemoryService:
    """为 Harness Context Builder 提供读和受控后台写入。"""

    def __init__(
        self,
        adapter: LongTermMemoryPort,
        config: LongTermMemoryConfig,
        *,
        outbox: MemoryWriteOutboxPort | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._outbox = outbox
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    async def search(self, *, user_id: str, query: str) -> list[LongTermMemoryItem]:
        return await self._adapter.search(user_id=user_id, query=query, limit=self._config.search_limit)

    async def get(self, *, memory_id: str) -> LongTermMemoryItem | None:
        """查询单条已稳定记忆。"""

        return await self._adapter.get(memory_id=memory_id)

    async def history(self, *, memory_id: str) -> list[LongTermMemoryItem]:
        """查询单条记忆的安全历史投影。"""

        return await self._adapter.history(memory_id=memory_id)

    async def update(self, *, memory_id: str, content: str) -> LongTermMemoryItem | None:
        """更新单条稳定记忆。"""

        return await self._adapter.update(memory_id=memory_id, content=content)

    async def delete(self, *, memory_id: str) -> bool:
        """删除单条记忆。"""

        return await self._adapter.delete(memory_id=memory_id)

    async def delete_all(self, *, user_id: str) -> bool:
        """删除当前用户的全部远程记忆。"""

        return await self._adapter.delete_all(user_id=user_id)

    async def requeue_manual_review(self, *, user_id: str, write_key: str) -> bool:
        """由当前 owner 确认后重新排队人工审核记录，不影响其他用户写入。"""

        if self._outbox is None or not write_key.strip():
            return False
        return await self._outbox.requeue_manual_review(
            user_id=user_id,
            write_key=write_key,
        )

    def write_background(self, *, user_id: str, content: str, category: str, write_key: str) -> None:
        """异步持久化写入意图；远程投递只能由 Outbox Worker 执行。"""

        if self._closing or self._outbox is None:
            logger.warning("Mem0 write skipped reason=outbox_unavailable")
            return

        async def _write() -> None:
            try:
                await self._outbox.enqueue(
                    user_id=user_id,
                    content=content,
                    category=category,
                    write_key=write_key,
                )
            except ValueError:
                logger.warning("Mem0 write skipped reason=write_key_conflict")

        task = asyncio.create_task(_write())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        self._closing = True
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
