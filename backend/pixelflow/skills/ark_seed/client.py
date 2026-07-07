"""Volcengine Ark Seed model HTTP client.

The client intentionally keeps the Ark wire contract in one place. Skill
adapters call these small methods and receive raw JSON so they can normalize
results for PixelFlow.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
DEFAULT_SEEDANCE_MODEL = "doubao-seedance-2.0"
DEFAULT_SEEDREAM_MODEL = "doubao-seedream-5.0-lite"


class ArkSeedClientError(RuntimeError):
    """Raised when Ark returns a transport or task-level failure."""


class ArkSeedClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        poll_interval: float | None = None,
        poll_timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ARK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or self._resolve_api_key(self.base_url)
        self.timeout = timeout or float(os.environ.get("ARK_REQUEST_TIMEOUT", "60"))
        self.poll_interval = poll_interval or float(os.environ.get("ARK_POLL_INTERVAL", "5"))
        self.poll_timeout = poll_timeout or float(os.environ.get("ARK_POLL_TIMEOUT", "600"))
        self.max_retries = int(os.environ.get("ARK_MAX_RETRIES", "2"))

    @staticmethod
    def _resolve_api_key(base_url: str) -> str:
        #if "/api/plan/" in base_url:
        return os.environ.get("ARK_PLAN_API_KEY") or os.environ.get("ARK_API_KEY") or os.environ.get("VOLCENGINE_ARK_API_KEY") or ""
        #return os.environ.get("ARK_API_KEY") or os.environ.get("VOLCENGINE_ARK_API_KEY") or os.environ.get("ARK_PLAN_API_KEY") or ""

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ArkSeedClientError("ARK_API_KEY, VOLCENGINE_ARK_API_KEY, or ARK_PLAN_API_KEY is required")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(method, url, headers=self._headers(), json=json)
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ArkSeedClientError(f"Ark returned non-JSON response: HTTP {response.status_code}") from exc
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(min(2**attempt, 5))
                    continue
                if response.status_code >= 400:
                    message = payload.get("error") or payload.get("message") or payload.get("msg") or payload
                    raise ArkSeedClientError(f"Ark request failed: HTTP {response.status_code}: {message}")
                return payload
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2**attempt, 5))
        raise ArkSeedClientError(str(last_error) if last_error else "Ark request failed")

    def create_video_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/contents/generations/tasks", json=payload)

    def get_video_task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/contents/generations/tasks/{task_id}")

    def wait_video_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.poll_timeout
        last_payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_payload = self.get_video_task(task_id)
            status = str(last_payload.get("status") or last_payload.get("task_status") or "").lower()
            if status in {"succeeded", "success", "completed", "done"}:
                return last_payload
            if status in {"failed", "error", "cancelled", "canceled"}:
                message = last_payload.get("error") or last_payload.get("message") or last_payload.get("msg") or status
                raise ArkSeedClientError(f"Ark video task failed: {message}")
            time.sleep(self.poll_interval)
        raise ArkSeedClientError(f"Ark video task timed out after {self.poll_timeout:g}s: {last_payload}")

    def generate_images(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/images/generations", json=payload)


def extract_urls(value: Any) -> list[str]:
    """Best-effort URL extraction across Ark image and video result shapes."""
    urls: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            if item.startswith(("http://", "https://")):
                urls.append(item)
            return
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if isinstance(item, dict):
            for key in (
                "url",
                "urls",
                "video_url",
                "video_urls",
                "image_url",
                "image_urls",
                "content",
                "data",
                "result",
                "results",
                "images",
                "videos",
            ):
                if key in item:
                    visit(item[key])

    visit(value)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped


def extract_task_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return payload.get("id") or payload.get("task_id") or data.get("id") or data.get("task_id")
