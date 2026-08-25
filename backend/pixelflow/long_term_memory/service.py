"""以 Volcengine Mem0 为实现的长期记忆应用服务。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

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
    """隔离 Mem0 SDK 的稳定应用 Port。"""

    async def search(self, *, user_id: str, query: str, limit: int) -> list[LongTermMemoryItem]: ...

    async def add(self, *, user_id: str, content: str, category: str, write_key: str) -> str | None: ...

    async def get(self, *, memory_id: str) -> LongTermMemoryItem | None: ...

    async def delete(self, *, memory_id: str) -> bool: ...

    async def delete_all(self, *, user_id: str) -> bool: ...


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
    """Mem0 SDK 防腐适配器，不把供应商响应或异常正文传播给业务层。"""

    def __init__(self, config: LongTermMemoryConfig) -> None:
        self._config = config
        self._client: Any | None = None

    def _get_client(self) -> Any | None:
        if not self._config.available:
            return None
        if self._client is not None:
            return self._client
        try:
            from mem0 import MemoryClient

            self._client = MemoryClient(
                api_key=self._config.api_key,
                host=self._config.base_url,
            )
            return self._client
        except Exception as exc:  # noqa: BLE001 - SDK 初始化失败必须 fail-open。
            logger.warning("Mem0 client unavailable exception_type=%s", type(exc).__name__)
            return None

    async def _v1_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        payload: dict[str, object] | None = None,
    ) -> object | None:
        """兼容仅提供 Mem0 v1 路径的火山环境；仅在 SDK v3 明确 404 时调用。"""

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
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None
    async def search(self, *, user_id: str, query: str, limit: int) -> list[LongTermMemoryItem]:
        client = self._get_client()
        anonymous_user_id = _anonymous_user_id(user_id)
        if client is None or not query.strip() or not anonymous_user_id:
            return []
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(client.search, query, user_id=anonymous_user_id, limit=limit),
                timeout=self._config.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - 检索不能阻断 Harness Run。
            if _is_not_found_error(exc):
                payload = await self._v1_request(
                    "POST",
                    "/v1/memories/search/",
                    payload={"query": query, "user_id": anonymous_user_id, "limit": limit},
                )
            else:
                logger.warning("Mem0 search failed exception_type=%s", type(exc).__name__)
                return []
        records = payload.get("results", payload) if isinstance(payload, Mapping) else payload
        if not isinstance(records, list):
            return []
        items: list[LongTermMemoryItem] = []
        for record in records[:limit]:
            if not isinstance(record, Mapping):
                continue
            content = str(record.get("memory") or record.get("content") or "").strip()
            memory_id = str(record.get("id") or record.get("memory_id") or "").strip()
            if content and memory_id:
                items.append(LongTermMemoryItem(memory_id=memory_id, content=content[:1_000], category="preference"))
        return items

    async def add(self, *, user_id: str, content: str, category: str, write_key: str) -> str | None:
        client = self._get_client()
        anonymous_user_id = _anonymous_user_id(user_id)
        if client is None or not content.strip() or not anonymous_user_id:
            return None
        try:
            payload = await asyncio.to_thread(
                client.add,
                [{"role": "user", "content": content}],
                user_id=anonymous_user_id,
                metadata={"category": category, "memory_write_key": write_key},
            )
        except Exception as exc:  # noqa: BLE001 - 后台写入失败只能记录安全元数据。
            if _is_not_found_error(exc):
                payload = await self._v1_request(
                    "POST",
                    "/v1/memories/",
                    payload={
                        "messages": [{"role": "user", "content": content}],
                        "user_id": anonymous_user_id,
                        "metadata": {"category": category, "memory_write_key": write_key},
                    },
                )
            else:
                logger.warning("Mem0 add failed exception_type=%s", type(exc).__name__)
                return None
        if isinstance(payload, Mapping):
            event_id = payload.get("event_id") or payload.get("id")
            return str(event_id).strip() or None
        return None

    async def get(self, *, memory_id: str) -> LongTermMemoryItem | None:
        """按 Mem0 memory ID 查询已稳定记录，只映射安全字段。"""

        client = self._get_client()
        if client is None or not memory_id.strip():
            return None
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(client.get, memory_id),
                timeout=self._config.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - 远程读取不得阻断主流程。
            logger.warning("Mem0 get failed exception_type=%s", type(exc).__name__)
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

    async def delete(self, *, memory_id: str) -> bool:
        """删除单条远程记忆；失败只返回 false，避免泄露供应商正文。"""

        client = self._get_client()
        if client is None or not memory_id.strip():
            return False
        try:
            await asyncio.wait_for(
                asyncio.to_thread(client.delete, memory_id),
                timeout=self._config.timeout_seconds,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mem0 delete failed exception_type=%s", type(exc).__name__)
            return False

    async def delete_all(self, *, user_id: str) -> bool:
        """按匿名主体删除远程记忆，绝不把原始 owner 发送给 Mem0。"""

        client = self._get_client()
        anonymous_user_id = _anonymous_user_id(user_id)
        if client is None or not anonymous_user_id:
            return False
        try:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(client.delete_all, user_id=anonymous_user_id),
                    timeout=self._config.timeout_seconds,
                )
            except Exception as exc:
                if not _is_not_found_error(exc):
                    raise
                payload = await self._v1_request(
                    "DELETE",
                    "/v1/memories/",
                    params={"user_id": anonymous_user_id},
                )
                if payload is None:
                    return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mem0 delete_all failed exception_type=%s", type(exc).__name__)
            return False


class LongTermMemoryService:
    """为 Harness Context Builder 提供读和受控后台写入。"""

    def __init__(self, adapter: LongTermMemoryPort, config: LongTermMemoryConfig) -> None:
        self._adapter = adapter
        self._config = config
        self._tasks: set[asyncio.Task[None]] = set()

    async def search(self, *, user_id: str, query: str) -> list[LongTermMemoryItem]:
        return await self._adapter.search(user_id=user_id, query=query, limit=self._config.search_limit)

    async def get(self, *, memory_id: str) -> LongTermMemoryItem | None:
        """查询单条已稳定记忆。"""

        return await self._adapter.get(memory_id=memory_id)

    async def delete(self, *, memory_id: str) -> bool:
        """删除单条记忆。"""

        return await self._adapter.delete(memory_id=memory_id)

    async def delete_all(self, *, user_id: str) -> bool:
        """删除当前用户的全部远程记忆。"""

        return await self._adapter.delete_all(user_id=user_id)

    def write_background(self, *, user_id: str, content: str, category: str, write_key: str) -> None:
        """异步写入不会阻塞当前 Run；退出时会等待已接收任务。"""

        async def _write() -> None:
            await self._adapter.add(user_id=user_id, content=content, category=category, write_key=write_key)

        task = asyncio.create_task(_write())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def _is_not_found_error(error: Exception) -> bool:
    """仅识别供应商明确的 HTTP 404，避免把鉴权或协议错误错误降级为 v1。"""

    response = getattr(error, "response", None)
    return (
        getattr(response, "status_code", None) == httpx.codes.NOT_FOUND
        or "404" in str(error)
        or error.__class__.__name__ == "MemoryNotFoundError"
    )
