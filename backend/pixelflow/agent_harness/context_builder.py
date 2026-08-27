"""构造发送给 Sidecar 的受预算、安全上下文投影。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_FORBIDDEN_KEY_FRAGMENTS = (
    "authorization", "credential", "secret", "token", "password", "api_key", "provider",
)


class HarnessContextBudgetExceeded(ValueError):
    """表示上下文超过模型输入预算，Gateway 必须在创建 Run 前拒绝。"""


class HarnessContextUnsafe(ValueError):
    """表示投影中出现了不允许发送给 Sidecar 的字段。"""


@dataclass(frozen=True, slots=True)
class HarnessContextBuildResult:
    """保存冻结投影和其可审计的估算预算。"""

    projection: dict[str, Any]
    estimated_input_tokens: int
    usable_input_tokens: int


class PixelFlowContextBuilder:
    """类似 Application Service：只接收已清洗的领域投影，不读取数据库或 Secret。"""

    def __init__(self, *, effective_context_tokens: int = 896 * 1024, output_reserve_tokens: int = 32 * 1024, safety_reserve_tokens: int = 32 * 1024) -> None:
        self._usable_input_tokens = effective_context_tokens - output_reserve_tokens - safety_reserve_tokens
        if self._usable_input_tokens <= 0:
            raise ValueError("上下文预算必须保留正数输入空间")

    def build(self, projection: dict[str, Any]) -> HarnessContextBuildResult:
        """递归校验敏感字段并按保守字符估算拒绝超预算投影。"""

        self._assert_safe(projection)
        encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        # 中文、代码和结构化 JSON 均按每 2 个字符一个 token 的保守上界估算。
        estimated = (len(encoded) + 1) // 2
        if estimated > self._usable_input_tokens:
            raise HarnessContextBudgetExceeded("上下文超过可用输入预算")
        return HarnessContextBuildResult(
            projection=projection,
            estimated_input_tokens=estimated,
            usable_input_tokens=self._usable_input_tokens,
        )

    def _assert_safe(self, value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold()
                if any(fragment in normalized for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                    raise HarnessContextUnsafe("上下文包含受保护字段")
                self._assert_safe(child)
        elif isinstance(value, list):
            for child in value:
                self._assert_safe(child)
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise HarnessContextUnsafe("上下文包含不支持的值类型")


__all__ = [
    "HarnessContextBudgetExceeded",
    "HarnessContextBuildResult",
    "HarnessContextUnsafe",
    "PixelFlowContextBuilder",
]
