"""剪映草稿 Provider 的内部能力协议。"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from .models import JianyingDraftRequest, JianyingDraftResult, JianyingDraftStatus


class JianyingDraftCapability(BaseModel):
    """当前剪映草稿 Provider 是否可用。"""

    available: bool
    reason: str = ""
    poll_interval_seconds: float = Field(default=2.0, gt=0)


class JianyingDraftSkill(Protocol):
    """隔离第三方剪映草稿生成能力的稳定内部协议。"""

    async def capability(self) -> JianyingDraftCapability: ...

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult: ...


class UnavailableJianyingDraftSkill:
    """真实 Provider 尚未接入时的安全默认实现。"""

    async def capability(self) -> JianyingDraftCapability:
        return JianyingDraftCapability(available=False, reason="剪映草稿服务待接入")

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        return JianyingDraftResult(
            status=JianyingDraftStatus.NOT_CONFIGURED,
            message="剪映草稿服务待接入",
        )


class DisabledJianyingDraftSkill(UnavailableJianyingDraftSkill):
    """内部开关关闭时装配的安全 Skill。"""


class MissingProviderJianyingDraftSkill(UnavailableJianyingDraftSkill):
    """已开启但真实 Provider 尚未接入时装配的安全 Skill。"""
