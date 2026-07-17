"""剪映草稿运行配置加载。"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_ENABLED = False
DEFAULT_BASE_URL = ""
DEFAULT_TOKEN = ""
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_CREATE_READ_TIMEOUT_SECONDS = 30.0
DEFAULT_QUERY_READ_TIMEOUT_SECONDS = 15.0


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.warning("%s has invalid boolean value; using default", name)
    return default


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s has invalid numeric value; using default", name)
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning("%s must be a finite positive number; using default", name)
        return default
    return value


def _non_negative_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s has invalid integer value; using default", name)
        return default
    if value < 0:
        logger.warning("%s must be non-negative; using default", name)
        return default
    return value


@dataclass(frozen=True)
class JianyingDraftRuntimeConfig:
    """由 profile_config 写入环境变量后的剪映草稿运行参数。"""

    enabled: bool = DEFAULT_ENABLED
    base_url: str = DEFAULT_BASE_URL
    token: str = DEFAULT_TOKEN
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    create_read_timeout_seconds: float = DEFAULT_CREATE_READ_TIMEOUT_SECONDS
    query_read_timeout_seconds: float = DEFAULT_QUERY_READ_TIMEOUT_SECONDS


def load_jianying_draft_runtime_config() -> JianyingDraftRuntimeConfig:
    """读取并校验剪映草稿运行配置；非法值使用稳定默认值。"""

    return JianyingDraftRuntimeConfig(
        enabled=_bool_env("PIXELFLOW_JIANYING_DRAFT_ENABLED", DEFAULT_ENABLED),
        base_url=os.getenv("PIXELFLOW_JIANYING_DRAFT_BASE_URL", DEFAULT_BASE_URL).strip(),
        token=os.getenv("PIXELFLOW_JIANYING_DRAFT_TOKEN", DEFAULT_TOKEN).strip(),
        poll_interval_seconds=_positive_float_env(
            "PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS",
            DEFAULT_POLL_INTERVAL_SECONDS,
        ),
        timeout_seconds=_positive_float_env(
            "PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS",
            DEFAULT_TIMEOUT_SECONDS,
        ),
        max_retries=_non_negative_int_env(
            "PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES",
            DEFAULT_MAX_RETRIES,
        ),
        connect_timeout_seconds=_positive_float_env(
            "PIXELFLOW_JIANYING_DRAFT_CONNECT_TIMEOUT_SECONDS",
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
        ),
        create_read_timeout_seconds=_positive_float_env(
            "PIXELFLOW_JIANYING_DRAFT_CREATE_READ_TIMEOUT_SECONDS",
            DEFAULT_CREATE_READ_TIMEOUT_SECONDS,
        ),
        query_read_timeout_seconds=_positive_float_env(
            "PIXELFLOW_JIANYING_DRAFT_QUERY_READ_TIMEOUT_SECONDS",
            DEFAULT_QUERY_READ_TIMEOUT_SECONDS,
        ),
    )
