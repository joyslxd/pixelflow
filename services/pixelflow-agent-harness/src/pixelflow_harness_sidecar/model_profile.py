"""定义 Sidecar 复算的 Harness 模型发布档案。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class HarnessModelProfile:
    """仅保存公开模型身份；Provider 地址和密钥不进入 Run DTO。"""

    logical_name: str
    model_id: str
    capability_version: str
    budget_version: str

    @property
    def digest(self) -> str:
        """使用与 Gateway 完全相同的规范化 JSON 计算摘要。"""

        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @classmethod
    def from_env(cls) -> HarnessModelProfile:
        """只接受完整发布配置，禁止遗留手工 digest 覆盖。"""

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
