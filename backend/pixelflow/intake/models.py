"""Intake-phase schemas (PRD §8).

``ProductInfo`` (§8.1) and ``VideoParams`` (§8.4) are the structured demand the
采集 phase collects; ``demand_integrity_check`` (§8.7) gates the hand-off to
CREATIVE. ``ProductInfo.main_image_url`` is the authoritative product-image
source the GENERATE phase needs for ``use_real_asset`` shots.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Video-duration buckets the platform supports (§8.4). Off-bucket values are
# snapped to the nearest one.
DURATION_BUCKETS = (15, 30, 60, 90)
# MVP fixes resolution at 1080p (§8.4).
FIXED_RESOLUTION = "1080p"
# Investment platforms the MVP can target (§8.4). Unknown values are flagged,
# not rejected outright.
SUPPORTED_PLATFORMS = ("douyin", "kuaishou", "xiaohongshu", "shipinhao", "taobao")

PromotionType = Literal["limited_discount", "full_reduction", "gift", "flash_sale"]


class PromotionInfo(BaseModel):
    type: PromotionType | None = None
    description: str = ""  # "限时特惠¥99"
    value: str = ""  # "¥99" / "满200减30"


class ProductInfo(BaseModel):
    """Structured product info (§8.1). Most fields may be empty; the integrity
    check enforces the required ones."""

    product_name: str = ""
    price: float | None = None
    original_price: float | None = None
    category: str = ""  # "美妆/面部护肤/精华"
    spec: str = ""
    selling_points: list[str] = Field(default_factory=list)  # 建议 3-5 条
    main_image_url: str = ""
    extra_images: list[str] = Field(default_factory=list)  # 最多 9 张
    promotion_info: PromotionInfo | None = None


class VideoParams(BaseModel):
    """Output/video parameters collected via dialog (§8.4)."""

    platform: str = ""
    business_goal: str = ""
    video_duration_sec: int | None = None
    video_resolution: str = FIXED_RESOLUTION
    ratio: str = "9:16"
    segment_strategy: str = "auto"


class IntegrityItem(BaseModel):
    item: str  # 检查项名称
    status: Literal["pass", "fail", "warn"]
    message: str = ""
    action: str = ""  # 建议 Agent 采取的动作


class IntegrityResult(BaseModel):
    is_complete: bool  # true → 可进入阶段 2
    check_results: list[IntegrityItem] = Field(default_factory=list)

    def questions(self, limit: int = 3) -> list[str]:
        """The first ``limit`` follow-up actions for blocking (fail) items."""
        return [c.action for c in self.check_results if c.status == "fail" and c.action][:limit]
