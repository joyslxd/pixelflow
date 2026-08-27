"""视频生成 Provider 的稳定业务 Port，不暴露 content-app HTTP DTO。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from pixelflow.operations.jobs.providers import ProviderJobSnapshot


@runtime_checkable
class VideoGenerationProvider(Protocol):
    """类似 Java 的 Provider SPI：M06 只依赖此处的 start/status 语义。"""

    provider_id: str
    profile_version: str

    def prepare_operation_request(
        self,
        request: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        """冻结 provider_id/profile_version 到 request_hash 输入。"""

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> ProviderJobSnapshot:
        """只在当前 Tool 请求链路调用 Provider start。"""

    async def status(
        self,
        provider_job_id: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> ProviderJobSnapshot:
        """重启后仅用部署服务凭据查询既有 Job。"""


__all__ = ["VideoGenerationProvider"]
