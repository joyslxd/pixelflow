"""生成任务额度中断的稳定公开身份。"""

from __future__ import annotations

import hashlib
from typing import Any

_QUOTA_INSUFFICIENT_STATUS_CODE = 402
_QUOTA_INSUFFICIENT_KEYWORDS = frozenset(
    {"额度不足", "余额不足", "quota insufficient", "insufficient quota", "insufficient balance", "payment required", "not enough quota"}
)


def is_quota_insufficient(value: Any) -> bool:
    """判断 Provider 摘要是否表示额度不足，供 M06 状态机统一暂停恢复。"""

    if value is None:
        return False
    if isinstance(value, dict):
        if value.get("status_code") == _QUOTA_INSUFFICIENT_STATUS_CODE:
            return True
        summary = " ".join(str(value.get(key, "")) for key in ("message", "msg", "error", "detail", "code", "status")).lower()
        return any(keyword in summary for keyword in _QUOTA_INSUFFICIENT_KEYWORDS) or any(is_quota_insufficient(child) for child in value.values())
    if isinstance(value, list):
        return any(is_quota_insufficient(item) for item in value)
    return any(keyword in str(value).lower() for keyword in _QUOTA_INSUFFICIENT_KEYWORDS)


def build_start_quota_interrupt_id(job_id: str) -> str:
    """只由内部 GenerationJob ID 派生 start 402 中断身份，不泄露请求内容。"""

    normalized = job_id.strip() if isinstance(job_id, str) else ""
    if not normalized or normalized != job_id or len(normalized) > 64:
        raise ValueError("job_id必须是1到64个无首尾空白字符")
    digest = hashlib.sha256(
        f"pixelflow:video-agent:start-quota:v1:{normalized}".encode()
    ).hexdigest()
    return f"video_start_quota_{digest[:32]}"


__all__ = ["build_start_quota_interrupt_id", "is_quota_insufficient"]
