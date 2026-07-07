"""PowerMem HTTP sidecar client for PixelFlow semantic memory."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PIXELFLOW_POWERMEM_AGENT_ID = "pixelflow"
MAX_LOG_ERROR_BODY = 300


def _bool_env(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is invalid, falling back to %s", name, raw, default)
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is invalid, falling back to %s", name, raw, default)
        return default


@dataclass(frozen=True)
class PowerMemConfig:
    enabled: bool = False
    provider: str = "powermem"
    base_url: str = ""
    api_key: str = ""
    # 用于检索 / 健康检查等「同步在请求路径上」的调用：必须短且 fail-open，
    # 不能让 PowerMem 抖动拖慢用户主流程。
    timeout_seconds: float = 3.0
    # 用于 record 写入：record 全部走后台 asyncio.create_task，不在用户请求路径上，
    # 可以给得更宽松。preference 类记录会使用 infer=true，服务端要做 LLM 抽取（~数十秒），
    # 因此独立配置，避免和 search 共用 3s 被误杀。
    record_timeout_seconds: float = 60.0
    search_limit: int = 5
    write_enabled: bool = True
    fail_open: bool = True

    @property
    def available(self) -> bool:
        return self.enabled and self.provider == "powermem" and bool(self.base_url.strip())


@dataclass(frozen=True)
class SemanticMemoryItem:
    memory_id: str
    content: str
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "score": self.score,
            "metadata": self.metadata,
        }


def load_power_mem_config_from_env() -> PowerMemConfig:
    """Load PowerMem settings written by ``profile_config.py``."""
    return PowerMemConfig(
        enabled=_bool_env(os.getenv("PIXELFLOW_SEMANTIC_MEMORY_ENABLED"), default=False),
        provider=os.getenv("PIXELFLOW_SEMANTIC_MEMORY_PROVIDER", "powermem").strip() or "powermem",
        base_url=os.getenv("PIXELFLOW_POWERMEM_BASE_URL", "").strip(),
        api_key=os.getenv("PIXELFLOW_POWERMEM_API_KEY", "").strip(),
        timeout_seconds=_float_env("PIXELFLOW_POWERMEM_TIMEOUT_SECONDS", 3.0),
        record_timeout_seconds=_float_env("PIXELFLOW_POWERMEM_RECORD_TIMEOUT_SECONDS", 60.0),
        search_limit=_int_env("PIXELFLOW_POWERMEM_SEARCH_LIMIT", 5),
        write_enabled=_bool_env(os.getenv("PIXELFLOW_POWERMEM_WRITE_ENABLED"), default=True),
        fail_open=_bool_env(os.getenv("PIXELFLOW_POWERMEM_FAIL_OPEN"), default=True),
    )


class PowerMemService:
    """Small fail-open client around the PowerMem REST API.

    This class is the only place in PixelFlow that knows PowerMem URLs,
    headers, and payload shapes. Routers and Agent flow code should pass
    business summaries into this service instead of calling HTTP directly.
    """

    def __init__(
        self,
        config: PowerMemConfig | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or load_power_mem_config_from_env()
        self._client = http_client
        self._owns_client = http_client is None
        self._lock = asyncio.Lock()

    def status_snapshot(self) -> dict[str, Any]:
        if not self.config.enabled:
            status = "disabled"
        elif not self.config.base_url:
            status = "missing_base_url"
        elif self.config.provider != "powermem":
            status = "unsupported_provider"
        else:
            status = "configured"
        return {
            "enabled": self.config.available,
            "provider": self.config.provider,
            "status": status,
            "write_enabled": self.config.write_enabled,
            "search_limit": self.config.search_limit,
        }

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def health(self) -> dict[str, Any]:
        if not self.config.available:
            return self.status_snapshot()
        try:
            response = await self._request("GET", "/system/health", auth=False)
            return response if isinstance(response, dict) else {"enabled": True, "provider": "powermem"}
        except Exception as exc:
            if self.config.fail_open:
                _log_fail_open("PowerMem health check failed", exc)
                return {**self.status_snapshot(), "status": "unreachable"}
            raise

    async def search(
        self,
        *,
        user_id: str | None,
        query: str,
        categories: list[str] | None = None,
        source_agent: str | None = None,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> list[SemanticMemoryItem]:
        query = query.strip()
        if not self.config.available or not query:
            return []
        search_limit = max(1, min(100, limit or self.config.search_limit))
        category_values = [category for category in (categories or []) if category]
        try:
            if len(category_values) > 1:
                result_groups = await asyncio.gather(
                    *(
                        self._search_once(
                            user_id=user_id,
                            query=query,
                            category=category,
                            source_agent=source_agent,
                            run_id=run_id,
                            limit=search_limit,
                        )
                        for category in category_values
                    )
                )
                return _dedupe_and_sort([item for group in result_groups for item in group])[:search_limit]
            category = category_values[0] if category_values else None
            return await self._search_once(
                user_id=user_id,
                query=query,
                category=category,
                source_agent=source_agent,
                run_id=run_id,
                limit=search_limit,
            )
        except Exception as exc:
            if self.config.fail_open:
                _log_fail_open("PowerMem search failed", exc)
                return []
            raise

    async def record(
        self,
        *,
        user_id: str | None,
        content: str,
        category: str,
        source_agent: str = "",
        metadata: dict[str, Any] | None = None,
        memory_type: str | None = None,
        run_id: str | None = None,
        scope: str = "user",
        infer: bool = True,
    ) -> bool:
        content = content.strip()
        if not self.config.available or not self.config.write_enabled or not content:
            return False
        request_metadata = dict(metadata or {})
        if source_agent:
            request_metadata["source_agent"] = source_agent
        request_metadata["category"] = category
        filters = {"category": category}
        if source_agent:
            filters["source_agent"] = source_agent
        payload: dict[str, Any] = {
            "content": content,
            "user_id": user_id or "",
            "agent_id": PIXELFLOW_POWERMEM_AGENT_ID,
            "metadata": request_metadata,
            "filters": filters,
            "scope": scope,
            "memory_type": memory_type or category,
            "infer": infer,
        }
        if run_id:
            payload["run_id"] = run_id
        try:
            response = await self._request("POST", "/memories", json=payload, timeout=self.config.record_timeout_seconds)
            if _should_retry_record_without_infer(response, category=category, infer=infer):
                fallback_payload = _fallback_payload_without_infer(payload)
                logger.warning(
                    "PowerMem preference infer returned no memories; retrying with infer=false "
                    "source_agent=%s category=%s",
                    source_agent,
                    category,
                )
                fallback_response = await self._request(
                    "POST",
                    "/memories",
                    json=fallback_payload,
                    timeout=self.config.record_timeout_seconds,
                )
                return _record_response_succeeded(fallback_response)
            return _record_response_succeeded(response)
        except Exception as exc:
            if self.config.fail_open:
                _log_fail_open("PowerMem record failed", exc)
                return False
            raise

    async def _search_once(
        self,
        *,
        user_id: str | None,
        query: str,
        category: str | None,
        source_agent: str | None,
        run_id: str | None,
        limit: int,
    ) -> list[SemanticMemoryItem]:
        filters: dict[str, Any] = {}
        if category:
            filters["category"] = category
        if source_agent:
            filters["source_agent"] = source_agent
        payload: dict[str, Any] = {
            "query": query,
            "user_id": user_id or "",
            "agent_id": PIXELFLOW_POWERMEM_AGENT_ID,
            "limit": limit,
        }
        if run_id:
            payload["run_id"] = run_id
        if filters:
            payload["filters"] = filters
        response = await self._request("POST", "/memories/search", json=payload)
        return _parse_search_items(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        auth: bool = True,
        timeout: float | None = None,
    ) -> Any:
        client = await self._get_client()
        headers = {"Content-Type": "application/json"} if json is not None else {}
        if auth and self.config.api_key:
            headers["X-API-Key"] = self.config.api_key
        # 单次请求级超时覆盖 client 默认超时：record 用 record_timeout_seconds，
        # search/health 用 timeout_seconds。
        request_timeout = timeout if timeout is not None else self.config.timeout_seconds
        response = await client.request(method, self._url(path), headers=headers, json=json, timeout=request_timeout)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        return self._client

    def _url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        suffix = path if path.startswith("/") else f"/{path}"
        if base.endswith("/api/v1"):
            return f"{base}{suffix}"
        return f"{base}/api/v1{suffix}"


def _parse_search_items(response: Any) -> list[SemanticMemoryItem]:
    if not isinstance(response, dict):
        return []
    data = response.get("data")
    if isinstance(data, dict):
        raw_items = data.get("results") or data.get("memories") or []
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = response.get("results") if isinstance(response.get("results"), list) else []
    items: list[SemanticMemoryItem] = []
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or raw.get("memory") or "").strip()
        if not content:
            continue
        score_raw = raw.get("score")
        try:
            score = float(score_raw) if score_raw is not None else None
        except (TypeError, ValueError):
            score = None
        items.append(
            SemanticMemoryItem(
                memory_id=str(raw.get("memory_id") or raw.get("id") or ""),
                content=content,
                score=score,
                metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
            )
        )
    return items


def _record_response_succeeded(response: Any) -> bool:
    if not isinstance(response, dict):
        return True
    return bool(response.get("success", True))


def _should_retry_record_without_infer(response: Any, *, category: str, infer: bool) -> bool:
    return infer and category == "preference" and _record_response_succeeded(response) and _response_has_empty_data(response)


def _response_has_empty_data(response: Any) -> bool:
    if not isinstance(response, dict) or "data" not in response:
        return False
    data = response.get("data")
    if data is None:
        return True
    if isinstance(data, (list, tuple, set, dict, str, bytes)):
        return len(data) == 0
    return False


def _fallback_payload_without_infer(payload: dict[str, Any]) -> dict[str, Any]:
    fallback_payload = dict(payload)
    fallback_payload["infer"] = False
    metadata = dict(fallback_payload.get("metadata") if isinstance(fallback_payload.get("metadata"), dict) else {})
    metadata["infer_fallback"] = True
    metadata["infer_fallback_reason"] = "empty_infer_result"
    fallback_payload["metadata"] = metadata
    return fallback_payload


def _dedupe_and_sort(items: list[SemanticMemoryItem]) -> list[SemanticMemoryItem]:
    deduped: dict[str, SemanticMemoryItem] = {}
    for item in items:
        key = item.memory_id or item.content
        previous = deduped.get(key)
        if previous is None or ((item.score or 0) > (previous.score or 0)):
            deduped[key] = item
    return sorted(deduped.values(), key=lambda item: item.score or 0, reverse=True)


def _log_fail_open(message: str, exc: BaseException) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.replace("\n", " ")[:MAX_LOG_ERROR_BODY]
        logger.warning("%s: status=%s body=%s", message, exc.response.status_code, body)
        return
    logger.warning("%s: %s", message, exc)
