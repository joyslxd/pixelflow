"""Provider Job 的稳定 DTO；生成任务生命周期由 GenerationJob 承担。"""

from .providers import (
    ProviderJobCallError,
    ProviderJobMappingError,
    ProviderJobOutcome,
    ProviderJobSnapshot,
)

__all__ = [
    "ProviderJobCallError",
    "ProviderJobMappingError",
    "ProviderJobOutcome",
    "ProviderJobSnapshot",
]
