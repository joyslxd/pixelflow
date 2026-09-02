"""图片生成 Provider 的稳定 start/status Port。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from pixelflow.generation_jobs.providers import ProviderJobSnapshot


@runtime_checkable
class ImageGenerationProvider(Protocol):
    provider_id: str
    profile_version: str

    def prepare_operation_request(self, request: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]: ...

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> ProviderJobSnapshot: ...

    async def status(
        self,
        provider_job_id: str,
        *,
        user_id: str,
        conversation_id: str,
        authorization: str = "",
    ) -> ProviderJobSnapshot: ...

    def as_operation_adapter(self) -> object: ...
