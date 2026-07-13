"""采集阶段能力入口。

这里统一导出 INTAKE 阶段会用到的函数和 DTO：商品页信息抽取（§8.1）、
视频参数归一化（§8.4）、需求完整性检查（§8.7）以及参考视频摘要准备。
``nodes.intake_node`` 只负责流程编排，具体规则都从这里引用。
"""

from .forms import CreativeDirection, FormField, FormSchema, FormValidationResult, draft_creative_directions, get_form_schema, validate_form
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
    "CreativeDirection",
    "FormField",
    "FormSchema",
    "FormValidationResult",
    "draft_creative_directions",
    "demand_integrity_check",
    "get_form_schema",
    "normalize_video_params",
    "product_info_extract",
    "summarize_storyboards",
    "validate_form",
]
