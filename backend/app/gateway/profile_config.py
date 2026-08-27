"""Profile YAML loader for PixelFlow gateway startup.

这个模块的职责类似 Spring Boot 在启动时加载 ``application-dev.yml``：

1. 根据 ``PIXELFLOW_CONFIG_ENV`` 选择 ``config.dev.yml`` / ``config.prod.yml``；
2. 或者根据 ``PIXELFLOW_CONFIG_FILE`` 直接指定某个 YAML 文件；
3. 把 YAML 中的业务配置映射到现有代码已经在读取的环境变量。

为什么还要写入环境变量？

当前项目里很多第三方 Client / Skill 已经通过 ``os.getenv(...)`` 读取配置。
一次性把 YAML 映射到环境变量，可以在不大改各层代码的前提下，把配置入口统一
到 profile 文件里。后续如果要进一步演进，可以再把这些配置收敛成 Pydantic
Settings 对象。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_ENV_VAR = "PIXELFLOW_CONFIG_ENV"
CONFIG_FILE_VAR = "PIXELFLOW_CONFIG_FILE"
DEFAULT_PROFILE = "dev"

_BACKEND_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LoadedProfileConfig:
    """已加载的 profile 配置元数据。"""

    profile: str
    path: Path


_loaded_profile: LoadedProfileConfig | None = None
_managed_env_keys: set[str] = set()
# YAML 路径 -> 环境变量名映射表。
#
# 左侧是面向用户阅读的分组配置，右侧是现有 Python 代码实际读取的 env key。
# 这层转换相当于 Java 项目里把 application.yml 的属性绑定给老代码用的
# System properties / Environment。
_ENV_KEY_MAP: dict[tuple[str, ...], str] = {
    ("gateway", "host"): "GATEWAY_HOST",
    ("gateway", "port"): "GATEWAY_PORT",
    ("gateway", "enable_docs"): "GATEWAY_ENABLE_DOCS",
    ("gateway", "cors_origins"): "GATEWAY_CORS_ORIGINS",
    ("pixelflow", "mysql_url"): "PIXELFLOW_MYSQL_URL",
    ("pixelflow", "long_term_memory_enabled"): "PIXELFLOW_LONG_TERM_MEMORY_ENABLED",
    ("pixelflow", "volcengine_mem0_base_url"): "PIXELFLOW_VOLCENGINE_MEM0_BASE_URL",
    ("pixelflow", "volcengine_mem0_api_key"): "PIXELFLOW_VOLCENGINE_MEM0_API_KEY",
    ("pixelflow", "long_term_memory_timeout_seconds"): "PIXELFLOW_LONG_TERM_MEMORY_TIMEOUT_SECONDS",
    ("pixelflow", "long_term_memory_search_limit"): "PIXELFLOW_LONG_TERM_MEMORY_SEARCH_LIMIT",
    ("pixelflow", "jianying_draft_enabled"): "PIXELFLOW_JIANYING_DRAFT_ENABLED",
    ("pixelflow", "jianying_draft_base_url"): "PIXELFLOW_JIANYING_DRAFT_BASE_URL",
    ("pixelflow", "jianying_draft_token"): "PIXELFLOW_JIANYING_DRAFT_TOKEN",
    ("pixelflow", "jianying_draft_poll_interval_seconds"): "PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS",
    ("pixelflow", "jianying_draft_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS",
    ("pixelflow", "jianying_draft_max_retries"): "PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES",
    ("pixelflow", "jianying_draft_connect_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_CONNECT_TIMEOUT_SECONDS",
    ("pixelflow", "jianying_draft_create_read_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_CREATE_READ_TIMEOUT_SECONDS",
    ("pixelflow", "jianying_draft_query_read_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_QUERY_READ_TIMEOUT_SECONDS",
    ("pixelflow", "content_app_internal_upload_enabled"): "PIXELFLOW_CONTENT_APP_INTERNAL_UPLOAD_ENABLED",
    ("database", "backend"): "PIXELFLOW_DATABASE_BACKEND",
    ("database", "sqlite_dir"): "PIXELFLOW_DATABASE_SQLITE_DIR",
    ("database", "url"): "PIXELFLOW_DATABASE_URL",
    ("database", "echo_sql"): "PIXELFLOW_DATABASE_ECHO_SQL",
    ("database", "pool_size"): "PIXELFLOW_DATABASE_POOL_SIZE",
    ("harness", "sidecar_base_url"): "PIXELFLOW_HARNESS_SIDECAR_BASE_URL",
    ("harness", "gateway_instance_id"): "PIXELFLOW_GATEWAY_INSTANCE_ID",
    ("harness", "request_timeout_seconds"): "PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS",
    ("harness", "run_limit_profiles"): "PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES",
    ("content_app", "base_url"): "BORGRISE_BASE_URL",
    ("content_app", "remote_verify_enabled"): "BORGRISE_REMOTE_VERIFY_ENABLED",
    ("content_app", "verify_timeout_seconds"): "BORGRISE_VERIFY_TIMEOUT_SECONDS",
    ("content_app", "skip_ssl_verify"): "BORGRISE_SKIP_SSL_VERIFY",
}


def _profile_path() -> tuple[str, Path]:
    """解析本次启动要使用的 profile 文件。"""
    explicit_file = os.getenv(CONFIG_FILE_VAR)
    if explicit_file:
        path = Path(explicit_file)
        if not path.is_absolute():
            path = (_BACKEND_DIR / path).resolve()
        return path.stem.removeprefix("config."), path

    profile = os.getenv(CONFIG_ENV_VAR, DEFAULT_PROFILE).strip() or DEFAULT_PROFILE
    return profile, _BACKEND_DIR / f"config.{profile}.yml"


def _read_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 配置文件，并保证顶层结构是对象。"""
    if not path.exists():
        raise FileNotFoundError(f"PixelFlow profile config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"PixelFlow profile config must be a YAML object: {path}")
    return data


def _get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    """按 ``(\"a\", \"b\")`` 这种路径从嵌套 dict 中取值。"""
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _to_env_value(value: Any) -> str:
    """把 YAML 值转换成环境变量字符串。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return ""
    return str(value)


def _set_env(key: str, value: Any) -> None:
    """写入环境变量，同时保留命令行显式传入值的最高优先级。

    第一次加载 profile 时，如果用户已经在 shell 里写了 ``GATEWAY_PORT=9000``，
    就说明用户想临时覆盖 YAML；此时不再覆盖它。由本加载器写入过的 key 会记录
    在 ``_managed_env_keys``，后续测试重置或重复加载时可以识别。
    """
    # YAML 中的空字符串表示“使用代码默认值/自动推断”，不写入环境变量。
    # 空字符串保持代码默认值，不覆盖启动时推断出的项目目录或运行配置。
    if value == "":
        return
    if key in os.environ and key not in _managed_env_keys:
        return
    os.environ[key] = _to_env_value(value)
    _managed_env_keys.add(key)


def _apply_known_mappings(data: dict[str, Any]) -> None:
    """把显式分组配置映射到现有 env key。"""
    for yaml_path, env_key in _ENV_KEY_MAP.items():
        value = _get_nested(data, yaml_path)
        if value is not None:
            _set_env(env_key, value)



def _apply_extra_environment(data: dict[str, Any]) -> None:
    """写入 ``environment.variables`` 直通配置。

    这个分组用于暂时没有独立中文字段的少量第三方变量，例如某个 MCP server
    要求的专用 token。它的 key 会原样作为环境变量名。
    """
    variables = _get_nested(data, ("environment", "variables"))
    if variables is None:
        return
    if not isinstance(variables, dict):
        raise ValueError("environment.variables must be a YAML object")
    for key, value in variables.items():
        if not isinstance(key, str) or not key:
            raise ValueError("environment.variables keys must be non-empty strings")
        _set_env(key, value)


def load_profile_config() -> LoadedProfileConfig:
    """加载 PixelFlow profile 配置，并返回加载结果。

    该函数是幂等的：同一进程内多次调用只会加载一次。测试需要切换配置时使用
    ``reset_profile_config_for_tests()`` 清掉缓存。
    """
    global _loaded_profile

    if _loaded_profile is not None:
        return _loaded_profile

    profile, path = _profile_path()
    path = path.resolve()
    data = _read_yaml(path)

    _apply_known_mappings(data)
    _apply_extra_environment(data)

    _loaded_profile = LoadedProfileConfig(profile=profile, path=path)
    logger.info("Loaded PixelFlow profile config: %s", path)
    return _loaded_profile


def reset_profile_config_for_tests() -> None:
    """测试专用：清理 profile 加载缓存和本模块写入的环境变量。"""
    global _loaded_profile

    _loaded_profile = None
    for key in tuple(_managed_env_keys):
        os.environ.pop(key, None)
    _managed_env_keys.clear()
