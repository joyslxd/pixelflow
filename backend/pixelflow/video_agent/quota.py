"""VideoAgent额度中断的稳定公开身份。"""

from __future__ import annotations

import hashlib


def build_start_quota_interrupt_id(job_id: str) -> str:
    """只由内部Operation ID派生start 402中断身份，不泄露请求内容。"""

    normalized = job_id.strip() if isinstance(job_id, str) else ""
    if not normalized or normalized != job_id or len(normalized) > 64:
        raise ValueError("job_id必须是1到64个无首尾空白字符")
    digest = hashlib.sha256(
        f"pixelflow:video-agent:start-quota:v1:{normalized}".encode()
    ).hexdigest()
    return f"video_start_quota_{digest[:32]}"


__all__ = ["build_start_quota_interrupt_id"]
