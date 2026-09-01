"""定义 Gateway 与 Sidecar 共同使用的非敏感模型发布档案。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class HarnessModelProfile:
    """表示一次完整发布冻结的模型逻辑身份，而非 Provider 凭据。"""

    logical_name: str
    model_id: str
    capability_version: str
    budget_version: str

    @property
    def digest(self) -> str:
        """返回可跨进程复算的公开档案摘要，禁止由环境手工填写。"""

        payload = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @classmethod
    def from_env(cls) -> HarnessModelProfile:
        """从同一发布配置读取档案字段；缺项时拒绝启动而非回退漂移默认值。"""

        values = {
            "logical_name": os.environ.get("PIXELFLOW_HARNESS_PROFILE_NAME", "").strip(),
            "model_id": os.environ.get("PIXELFLOW_HARNESS_MODEL_ID", "").strip(),
            "capability_version": os.environ.get("PIXELFLOW_HARNESS_CAPABILITY_VERSION", "").strip(),
            "budget_version": os.environ.get("PIXELFLOW_HARNESS_BUDGET_VERSION", "").strip(),
        }
        if any(not value or len(value) > 120 for value in values.values()):
            raise ValueError("Harness 模型发布档案不完整")
        return cls(**values)


__all__ = ["HarnessModelProfile"]
