"""Gateway 从平台配置解析并冻结 Harness Run 限制档案。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

_ENV_KEY = "PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES"
_PROFILE_BY_TRIGGER = {
    "user_turn": "video_interactive_v1",
    "operation_resume": "operation_resume_v1",
    "confirmation_resume": "confirmation_resume_v1",
    # 授权/表单恢复沿用确认恢复的严格预算，避免因交互类型改变放宽执行上限。
    "authorization_resume": "confirmation_resume_v1",
    "form_resume": "confirmation_resume_v1",
    "run_recovery": "run_recovery_v1",
}


@dataclass(frozen=True, slots=True)
class LimitProfile:
    """一份 Gateway 配置冻结后的不可变 Run 预算。"""

    profile: str
    max_model_steps: int
    max_business_tools: int
    max_billable_batch_starts: int
    deadline_seconds: int

    @property
    def digest(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(body).hexdigest()


class LimitProfileResolver:
    """类似配置绑定 Service：仅按可信 trigger 映射平台 YAML 的预算。"""

    def __init__(self, profiles: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        source = profiles if profiles is not None else self._profiles_from_environment()
        self._profiles = {name: self._parse_profile(name, value) for name, value in source.items()}
        missing = set(_PROFILE_BY_TRIGGER.values()) - set(self._profiles)
        if missing:
            raise ValueError("Harness 限制档案缺失")

    def resolve(self, trigger_type: str) -> LimitProfile:
        try:
            return self._profiles[_PROFILE_BY_TRIGGER[trigger_type]]
        except KeyError as error:
            raise ValueError("Run 类型没有限制档案") from error

    @staticmethod
    def _profiles_from_environment() -> Mapping[str, Mapping[str, Any]]:
        raw = os.environ.get(_ENV_KEY, "").strip()
        if not raw:
            raise ValueError("Harness 限制档案未由平台配置注入")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("Harness 限制档案配置不是 JSON 对象") from error
        if not isinstance(value, dict) or not all(isinstance(item, dict) for item in value.values()):
            raise ValueError("Harness 限制档案配置无效")
        return value

    @staticmethod
    def _parse_profile(name: str, value: Mapping[str, Any]) -> LimitProfile:
        if name not in _PROFILE_BY_TRIGGER.values() or set(value) != {
            "deadline_seconds", "max_model_steps", "max_business_tools", "max_billable_batch_starts",
        }:
            raise ValueError("Harness 限制档案字段无效")
        deadline = value["deadline_seconds"]
        steps = value["max_model_steps"]
        tools = value["max_business_tools"]
        billable = value["max_billable_batch_starts"]
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (deadline, steps, tools, billable)):
            raise ValueError("Harness 限制档案必须为整数")
        if not 1 <= deadline <= 3_600 or not 1 <= steps <= 64 or not 0 <= tools <= 32 or not 0 <= billable <= 8:
            raise ValueError("Harness 限制档案超出允许范围")
        return LimitProfile(name, steps, tools, billable, deadline)


__all__ = ["LimitProfile", "LimitProfileResolver"]
