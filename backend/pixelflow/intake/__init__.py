"""IntakeAgent: product-info extraction (§8.1), param normalization (§8.4),
and the demand-integrity gate (§8.7)."""

from .integrity import demand_integrity_check
from .models import IntegrityItem, IntegrityResult, ProductInfo, PromotionInfo, VideoParams
from .params import normalize_video_params
from .product_info_extract import product_info_extract
from .reference_summary import summarize_storyboards

__all__ = [
    "IntegrityItem",
    "IntegrityResult",
    "ProductInfo",
    "PromotionInfo",
    "VideoParams",
    "demand_integrity_check",
    "normalize_video_params",
    "product_info_extract",
    "summarize_storyboards",
]
