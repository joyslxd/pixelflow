"""策划阶段能力入口。

CREATIVE 阶段先用 LLM 生成结构化 Brief，再用本地纯逻辑执行硬约束校验和可确定
修复。下游 GENERATE、EDIT、QC 都依赖这个 Brief 合同。
"""

from .brief_generate import brief_generate
from .models import Brief, GlobalVisual, HardConstraints, Shot, ShotAudio
from .plan_markdown import (
    IMAGE_PLAN_TEMPLATE_PATH,
    PLAN_TEMPLATE_PATH,
    VIDEO_PLAN_TEMPLATE_PATH,
    PlanMarkdownResult,
    build_plan_markdown,
    build_plan_markdown_with_llm,
    restore_plan_version,
    revise_plan_markdown_with_llm,
)
from .validator import validate_and_fix

__all__ = [
    "Brief",
    "GlobalVisual",
    "HardConstraints",
    "IMAGE_PLAN_TEMPLATE_PATH",
    "PLAN_TEMPLATE_PATH",
    "PlanMarkdownResult",
    "Shot",
    "ShotAudio",
    "VIDEO_PLAN_TEMPLATE_PATH",
    "brief_generate",
    "build_plan_markdown",
    "build_plan_markdown_with_llm",
    "restore_plan_version",
    "revise_plan_markdown_with_llm",
    "validate_and_fix",
]
