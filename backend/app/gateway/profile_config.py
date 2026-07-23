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
_AGENT_RUNTIME_PROFILE_FIELDS = {
    "mode",
    "enabled_intents",
    "new_conversation_rollout_percent",
    "context_compaction_enabled",
}


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
    ("pixelflow", "agent_runtime", "mode"): "PIXELFLOW_AGENT_RUNTIME_MODE",
    ("pixelflow", "agent_runtime", "enabled_intents"): "PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS",
    (
        "pixelflow",
        "agent_runtime",
        "new_conversation_rollout_percent",
    ): "PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT",
    (
        "pixelflow",
        "agent_runtime",
        "context_compaction_enabled",
    ): "PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED",
    ("pixelflow", "mysql_url"): "PIXELFLOW_MYSQL_URL",
    ("pixelflow", "mem0_enabled"): "PIXELFLOW_MEM0_ENABLED",
    ("pixelflow", "semantic_memory_enabled"): "PIXELFLOW_SEMANTIC_MEMORY_ENABLED",
    ("pixelflow", "semantic_memory_provider"): "PIXELFLOW_SEMANTIC_MEMORY_PROVIDER",
    ("pixelflow", "powermem_base_url"): "PIXELFLOW_POWERMEM_BASE_URL",
    ("pixelflow", "powermem_api_key"): "PIXELFLOW_POWERMEM_API_KEY",
    ("pixelflow", "powermem_timeout_seconds"): "PIXELFLOW_POWERMEM_TIMEOUT_SECONDS",
    ("pixelflow", "powermem_record_timeout_seconds"): "PIXELFLOW_POWERMEM_RECORD_TIMEOUT_SECONDS",
    ("pixelflow", "powermem_search_limit"): "PIXELFLOW_POWERMEM_SEARCH_LIMIT",
    ("pixelflow", "powermem_write_enabled"): "PIXELFLOW_POWERMEM_WRITE_ENABLED",
    ("pixelflow", "powermem_fail_open"): "PIXELFLOW_POWERMEM_FAIL_OPEN",
    ("pixelflow", "media_skill"): "PIXELFLOW_MEDIA_SKILL",
    ("pixelflow", "edit_skill"): "PIXELFLOW_EDIT_SKILL",
    ("pixelflow", "draft_root"): "PIXELFLOW_DRAFT_ROOT",
    ("pixelflow", "render_root"): "PIXELFLOW_RENDER_ROOT",
    ("pixelflow", "caption_font"): "PIXELFLOW_CAPTION_FONT",
    ("pixelflow", "jianying_draft_enabled"): "PIXELFLOW_JIANYING_DRAFT_ENABLED",
    ("pixelflow", "jianying_draft_base_url"): "PIXELFLOW_JIANYING_DRAFT_BASE_URL",
    ("pixelflow", "jianying_draft_token"): "PIXELFLOW_JIANYING_DRAFT_TOKEN",
    ("pixelflow", "jianying_draft_poll_interval_seconds"): "PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS",
    ("pixelflow", "jianying_draft_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS",
    ("pixelflow", "jianying_draft_max_retries"): "PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES",
    ("pixelflow", "jianying_draft_connect_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_CONNECT_TIMEOUT_SECONDS",
    ("pixelflow", "jianying_draft_create_read_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_CREATE_READ_TIMEOUT_SECONDS",
    ("pixelflow", "jianying_draft_query_read_timeout_seconds"): "PIXELFLOW_JIANYING_DRAFT_QUERY_READ_TIMEOUT_SECONDS",
    ("borgrise", "base_url"): "BORGRISE_BASE_URL",
    ("borgrise", "remote_verify_enabled"): "BORGRISE_REMOTE_VERIFY_ENABLED",
    ("borgrise", "verify_timeout_seconds"): "BORGRISE_VERIFY_TIMEOUT_SECONDS",
    ("borgrise", "skip_ssl_verify"): "BORGRISE_SKIP_SSL_VERIFY",
    ("borgrise", "video_poll_timeout"): "BORGRISE_VIDEO_POLL_TIMEOUT",
    ("borgrise", "video_merge_request_timeout"): "BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT",
    ("borgrise", "image_poll_timeout"): "BORGRISE_IMAGE_POLL_TIMEOUT",
    ("borgrise", "video_analysis_poll_timeout"): "BORGRISE_VIDEO_ANALYSIS_POLL_TIMEOUT",
    ("borgrise", "max_retries"): "BORGRISE_MAX_RETRIES",
    ("deerflow", "environment"): "DEER_FLOW_ENV",
    ("deerflow", "home"): "DEER_FLOW_HOME",
    ("deerflow", "project_root"): "DEER_FLOW_PROJECT_ROOT",
    ("deerflow", "host_base_dir"): "DEER_FLOW_HOST_BASE_DIR",
    ("deerflow", "host_skills_path"): "DEER_FLOW_HOST_SKILLS_PATH",
    ("deerflow", "skills_path"): "DEER_FLOW_SKILLS_PATH",
    ("deerflow", "extensions_config_path"): "DEER_FLOW_EXTENSIONS_CONFIG_PATH",
    ("deerflow", "sandbox_bind_host"): "DEER_FLOW_SANDBOX_BIND_HOST",
    ("deerflow", "sandbox_host"): "DEER_FLOW_SANDBOX_HOST",
    ("tracing", "langsmith", "enabled"): "LANGSMITH_TRACING",
    ("tracing", "langsmith", "api_key"): "LANGSMITH_API_KEY",
    ("tracing", "langsmith", "project"): "LANGSMITH_PROJECT",
    ("tracing", "langsmith", "endpoint"): "LANGSMITH_ENDPOINT",
    ("tracing", "langfuse", "enabled"): "LANGFUSE_TRACING",
    ("tracing", "langfuse", "public_key"): "LANGFUSE_PUBLIC_KEY",
    ("tracing", "langfuse", "secret_key"): "LANGFUSE_SECRET_KEY",
    ("tracing", "langfuse", "base_url"): "LANGFUSE_BASE_URL",
    ("third_party", "serper_api_key"): "SERPER_API_KEY",
    ("third_party", "jina_api_key"): "JINA_API_KEY",
    ("third_party", "infoquest_api_key"): "INFOQUEST_API_KEY",
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
    # 这样像 deerflow.project_root: "" 这类配置不会覆盖加载器推断出的 backend 根目录。
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


def _validate_agent_runtime_profile(data: dict[str, Any]) -> None:
    """区分配置缺失与显式空值，防止 Agent Runtime 静默回退默认值。"""

    pixelflow = data.get("pixelflow")
    if not isinstance(pixelflow, dict) or "agent_runtime" not in pixelflow:
        return
    runtime = pixelflow["agent_runtime"]
    if not isinstance(runtime, dict):
        raise ValueError("pixelflow.agent_runtime 必须是 YAML 对象")
    unknown_fields = set(runtime) - _AGENT_RUNTIME_PROFILE_FIELDS
    if unknown_fields:
        unknown_text = ", ".join(sorted(str(field) for field in unknown_fields))
        raise ValueError(f"pixelflow.agent_runtime 包含不支持的配置键：{unknown_text}")
    for field_name, value in runtime.items():
        if value is None or value == "":
            raise ValueError(f"pixelflow.agent_runtime.{field_name} 不能是 null 或空字符串")
        if field_name == "enabled_intents" and not isinstance(value, list):
            raise ValueError("pixelflow.agent_runtime.enabled_intents 必须是 YAML 数组")


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

    # 让 DeerFlow harness 也读取同一份 profile YAML。这个 YAML 同时包含
    # AppConfig 需要的 sandbox/models/database 等字段，因此不再依赖旧 config.yaml。
    _set_env("DEER_FLOW_CONFIG_PATH", str(path))

    # 如果用户没有显式指定项目根目录，则使用 profile 文件所在目录。这样从仓库根目录
    # 或 IDE 启动时，skills、.deer-flow 等相对路径仍然落在 backend 下。
    if "DEER_FLOW_PROJECT_ROOT" not in os.environ:
        _set_env("DEER_FLOW_PROJECT_ROOT", str(path.parent))

    _validate_agent_runtime_profile(data)
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
