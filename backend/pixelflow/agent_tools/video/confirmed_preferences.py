"""保存经用户确认的演示偏好，并通过 Gateway Outbox 异步同步到 Mem0。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pixelflow.video.contracts import VideoToolResult

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
)


class PresentationPreferenceStorePort(Protocol):
    """Gateway 权威用户偏好存储的最小依赖合同。"""

    async def update(self, user_id: str, patch: dict[str, object]) -> object: ...


class LongTermMemoryWritePort(Protocol):
    """仅允许向 Gateway 长期记忆 Outbox 投递受控内容。"""

    def write_background(self, *, user_id: str, content: str, category: str, write_key: str) -> None: ...


class SaveConfirmedPresentationPreferencesInput(BaseModel):
    """只允许保存用户明确确认的长期演示偏好。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    brand_preference: str | None = Field(default=None, min_length=1, max_length=256)
    template_preference: str | None = Field(default=None, min_length=1, max_length=256)
    language_style: str | None = Field(default=None, min_length=1, max_length=256)
    preferred_page_count: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def require_preference(self) -> SaveConfirmedPresentationPreferencesInput:
        if not any(
            value is not None
            for value in (
                self.brand_preference,
                self.template_preference,
                self.language_style,
                self.preferred_page_count,
            )
        ):
            raise ValueError("至少提供一项已确认的演示偏好")
        return self


class SaveConfirmedPresentationPreferencesTool:
    """仅在用户确认后保存演示偏好，禁止记录完整对话或资料正文。"""

    spec = VideoToolSpec(
        name="save_confirmed_presentation_preferences",
        description=(
            "仅在用户明确希望长期保留品牌偏好、PPT 模板偏好、语言风格或常用页数时调用。"
            "该操作会先要求用户确认；不得把模型推测、完整对话或资料正文写入。"
        ),
        input_model=SaveConfirmedPresentationPreferencesInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=(),
    )

    def __init__(
        self,
        *,
        preference_store: PresentationPreferenceStorePort | None = None,
        long_term_memory_service: LongTermMemoryWritePort | None = None,
    ) -> None:
        self._preference_store = preference_store
        self._long_term_memory_service = long_term_memory_service

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        """更新本地权威偏好，并以同一 Tool Call 身份投递 Mem0 写入。"""

        if self._preference_store is None or self._long_term_memory_service is None:
            raise VideoToolExecutionError("长期偏好服务尚未装配")
        request = SaveConfirmedPresentationPreferencesInput.model_validate(dict(arguments))
        if context.run_id is None or context.tool_call_id is None:
            raise VideoToolExecutionError("长期偏好保存缺少冻结 Tool 身份")

        await self._preference_store.update(context.user_id, _local_preference_patch(request))
        self._long_term_memory_service.write_background(
            user_id=context.user_id,
            content=_memory_content(request),
            category="confirmed_presentation_preference",
            write_key=f"mem0-presentation-preference:{context.run_id}:{context.tool_call_id}",
        )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="已保存你确认的演示偏好，长期记忆将在后台同步。",
        )


def _local_preference_patch(request: SaveConfirmedPresentationPreferencesInput) -> dict[str, object]:
    """将四类演示偏好映射到本地权威偏好 Store。"""

    style_preferences: dict[str, object] = {}
    defaults: dict[str, object] = {}
    if request.brand_preference is not None:
        style_preferences["brand_preference"] = request.brand_preference
    if request.template_preference is not None:
        style_preferences["presentation_template"] = request.template_preference
    if request.language_style is not None:
        style_preferences["presentation_language_style"] = request.language_style
    if request.preferred_page_count is not None:
        defaults["presentation_page_count"] = request.preferred_page_count
    return {"style_preferences": style_preferences, "defaults": defaults}


def _memory_content(request: SaveConfirmedPresentationPreferencesInput) -> str:
    """生成有限、可审计的 Mem0 内容，不包含原始对话或业务资料。"""

    values: list[str] = []
    if request.brand_preference is not None:
        values.append(f"品牌偏好={request.brand_preference}")
    if request.template_preference is not None:
        values.append(f"模板偏好={request.template_preference}")
    if request.language_style is not None:
        values.append(f"语言风格={request.language_style}")
    if request.preferred_page_count is not None:
        values.append(f"常用页数={request.preferred_page_count}")
    return f"经用户确认的演示偏好：{'；'.join(values)}。"


__all__ = [
    "SaveConfirmedPresentationPreferencesInput",
    "SaveConfirmedPresentationPreferencesTool",
]
