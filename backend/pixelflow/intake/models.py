"""INTAKE 阶段的数据模型（PRD §8）。

Pydantic ``BaseModel`` 在这里相当于 Java 里的 DTO/VO 加参数校验。采集阶段会
收集 ``ProductInfo``（商品信息）和 ``VideoParams``（视频参数），再交给
``demand_integrity_check`` 决定是否允许进入 CREATIVE。

特别注意：``ProductInfo.main_image_url`` 是 GENERATE 阶段使用真实商品图生成
视频片段的权威图片来源；这个字段缺失时后续生成会失败。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 平台支持的视频时长档位（§8.4）。非档位值会在归一化时吸附到最近档位。
DURATION_BUCKETS = (15, 30, 60, 90)
# 当前 MVP 固定输出 1080p（§8.4）。
FIXED_RESOLUTION = "1080p"
# 当前 MVP 支持的投放平台（§8.4）。未知平台只提示风险，不直接拒绝任务。
SUPPORTED_PLATFORMS = ("douyin", "kuaishou", "xiaohongshu", "shipinhao", "taobao")

PromotionType = Literal["limited_discount", "full_reduction", "gift", "flash_sale"]


class PromotionInfo(BaseModel):
    type: PromotionType | None = None
    description: str = ""  # "限时特惠¥99"
    value: str = ""  # "¥99" / "满200减30"


class ProductInfo(BaseModel):
    """商品结构化信息（§8.1）。

    大部分字段允许为空，因为信息可能来自商品页抽取、用户补充或后续接口回填。
    真正“哪些字段必须有”由 ``demand_integrity_check`` 统一判断。
    """

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
    """前端参数弹窗收集到的视频输出参数（§8.4）。"""

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
        """取前 ``limit`` 个阻塞项追问动作，供 ``interrupt`` 发给前端。"""
        return [c.action for c in self.check_results if c.status == "fail" and c.action][:limit]
