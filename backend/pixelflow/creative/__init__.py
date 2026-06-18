"""策划阶段能力入口。

CREATIVE 阶段先用 LLM 生成结构化 Brief，再用本地纯逻辑执行硬约束校验和可确定
修复。下游 GENERATE、EDIT、QC 都依赖这个 Brief 合同。
"""

from .brief_generate import brief_generate
from .models import Brief, GlobalVisual, HardConstraints, Shot, ShotAudio
from .validator import validate_and_fix

__all__ = [
    "Brief",
    "GlobalVisual",
    "HardConstraints",
    "Shot",
    "ShotAudio",
    "brief_generate",
    "validate_and_fix",
]
