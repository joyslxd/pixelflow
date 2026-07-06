"""Development-time health checks for project bootstrap."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


Status = Literal["ok", "warn", "fail", "skip"]


@dataclass
class CheckResult:
    """Standard check result used by CLI output and tests."""

    status: Status
    label: str
    detail: str = ""
    fix: str | None = None


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_app_config(path: Path) -> dict:
    return _read_yaml(path)


def _collect_status(results: list[CheckResult], default: Status) -> Status:
    order = {"fail": 0, "warn": 1, "ok": 2, "skip": 3}
    for item in results:
        if order[item.status] < order[default]:
            default = item.status
    return default


def _env_name_from_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if not value.startswith("$"):
        return None
    env_name = value[1:].strip()
    if not env_name:
        return None
    return env_name


def check_python() -> CheckResult:
    import sys

    if sys.version_info >= (3, 12):
        return CheckResult("ok", "Python version", f"Python {sys.version.split()[0]}")
    return CheckResult("fail", "Python version", f"Python {sys.version.split()[0]}", fix="Please use Python 3.12+")


def check_config_exists(config_path: Path) -> CheckResult:
    if config_path.exists():
        return CheckResult("ok", "config.yaml exists", str(config_path))
    return CheckResult("fail", "config.yaml exists", f"{config_path} does not exist", fix="Create config.yaml")


def check_config_version(config_path: Path, repo_root: Path | None = None) -> CheckResult:
    if not config_path.exists():
        return CheckResult("skip", "config_version", "config.yaml not found")

    repo_root = repo_root or config_path.parent
    example_path = repo_root / "config.example.yaml"
    try:
        config = _load_app_config(config_path)
        version = int(config.get("config_version", 0))
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("fail", "config_version", f"Cannot parse config.yaml: {exc}", fix="Fix YAML syntax")

    if not example_path.exists():
        return CheckResult("skip", "config_version", "config.example.yaml not found")

    try:
        example = _load_app_config(example_path)
        expected = int(example.get("config_version", 0))
    except Exception as exc:  # pragma: no cover
        return CheckResult("warn", "config_version", f"Invalid example version: {exc}", fix="Regenerate from template")

    if version >= expected:
        return CheckResult("ok", "config_version", f"{version} >= {expected}")
    return CheckResult(
        "warn",
        "config_version",
        f"{version} < {expected}",
        fix="Run make setup to regenerate configuration",
    )


def check_config_loadable(config_path: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("skip", "config parse", "config.yaml not found")
    try:
        _load_app_config(config_path)
    except Exception as exc:
        return CheckResult("fail", "config parse", str(exc), fix="Fix config YAML syntax")
    return CheckResult("ok", "config parse", "config.yaml loaded successfully")


def check_models_configured(config_path: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("skip", "models", "config.yaml not found")
    cfg = _load_app_config(config_path)
    models = cfg.get("models")
    if isinstance(models, list) and models:
        return CheckResult("ok", "models", f"{len(models)} model(s) configured")
    return CheckResult("fail", "models", "No models configured", fix="Add at least one model block")


def check_llm_api_key(config_path: Path) -> list[CheckResult]:
    if not config_path.exists():
        return []
    cfg = _load_app_config(config_path)
    results: list[CheckResult] = []
    models = cfg.get("models") or []

    for model in models:
        if not isinstance(model, dict):
            continue
        model_name = model.get("name", model.get("model", "unknown"))
        handled = False
        for key, value in model.items():
            if not isinstance(key, str) or "api_key" not in key:
                continue
            env_name = _env_name_from_value(value)
            if not env_name:
                continue
            handled = True
            if os.environ.get(env_name):
                results.append(CheckResult("ok", f"{model_name}:{env_name}", "set"))
            else:
                results.append(
                    CheckResult(
                        "fail",
                        f"{model_name}:{env_name}",
                        "missing",
                        fix=f"Set {env_name} in environment",
                    )
                )
        if not handled:
            continue
    return results


def check_llm_auth(config_path: Path) -> list[CheckResult]:
    if not config_path.exists():
        return []

    cfg = _load_app_config(config_path)
    results: list[CheckResult] = []
    for model in cfg.get("models") or []:
        if not isinstance(model, dict):
            continue
        model_name = model.get("name", model.get("model", "unknown"))
        use = str(model.get("use", ""))
        if "deerflow.models.openai_codex_provider:CodexChatModel" in use:
            path = os.environ.get("CODEX_AUTH_PATH", "")
            if path and Path(path).exists():
                results.append(CheckResult("ok", "Codex CLI auth available", f"{model_name} auth file loaded"))
            else:
                results.append(
                    CheckResult(
                        "fail",
                        "Codex CLI auth available",
                        f"{model_name} missing CODEX_AUTH_PATH",
                        fix="Set CODEX_AUTH_PATH to a valid auth file",
                    )
                )
        if "deerflow.models.claude_provider:ClaudeChatModel" in use:
            if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
                results.append(CheckResult("ok", "Claude auth available", f"{model_name} oauth token set"))
            else:
                results.append(CheckResult("warn", "Claude auth available", "token not set", fix="Set CLAUDE_CODE_OAUTH_TOKEN"))

    if not results:
        results.append(CheckResult("ok", "llm_auth", "No extra auth checks required"))
    return results


def _read_tools(config: dict) -> list[dict]:
    tools = config.get("tools")
    if isinstance(tools, list):
        return [t for t in tools if isinstance(t, dict)]
    return []


def check_web_search(config_path: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("skip", "web_search", "config.yaml not found")
    cfg = _load_app_config(config_path)
    web_search = [t for t in _read_tools(cfg) if t.get("name") == "web_search"]
    if not web_search:
        return CheckResult("warn", "web_search", "No web_search tool configured", fix="Run make setup")

    tool = web_search[0]
    use = str(tool.get("use", ""))
    if "ddg" in use:
        return CheckResult("ok", "web_search", "DuckDuckGo")
    if "tavily" in use:
        if os.environ.get("TAVILY_API_KEY"):
            return CheckResult("ok", "web_search", "Tavily")
        return CheckResult("warn", "web_search", "Tavily key missing", fix="Run make setup")
    return CheckResult("fail", "web_search", f"Unknown tool provider: {use}")


def check_web_fetch(config_path: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("skip", "web_fetch", "config.yaml not found")
    cfg = _load_app_config(config_path)
    web_fetch = [t for t in _read_tools(cfg) if t.get("name") == "web_fetch"]
    if not web_fetch:
        return CheckResult("warn", "web_fetch", "No web_fetch tool configured", fix="Run make setup")

    tool = web_fetch[0]
    use = str(tool.get("use", ""))
    if "jina" in use:
        return CheckResult("ok", "web_fetch", "Jina AI")
    if "firecrawl" in use:
        if os.environ.get("FIRECRAWL_API_KEY"):
            return CheckResult("ok", "web_fetch", "Firecrawl")
        return CheckResult("warn", "web_fetch", "Firecrawl key missing", fix="Set FIRECRAWL_API_KEY")
    return CheckResult("fail", "web_fetch", f"Unknown tool provider: {use}")


def check_env_file(repo_root: Path) -> CheckResult:
    if (repo_root / ".env").exists():
        return CheckResult("ok", ".env", "present")
    return CheckResult("warn", ".env", "missing", fix="Create .env with required credentials")


def check_frontend_env(repo_root: Path) -> CheckResult:
    if (repo_root / "frontend" / ".env").exists():
        return CheckResult("ok", "frontend/.env", "present")
    return CheckResult("warn", "frontend/.env", "missing", fix="Create frontend/.env")


def check_sandbox(config_path: Path) -> list[CheckResult]:
    if not config_path.exists():
        return [CheckResult("fail", "sandbox", "config.yaml not found", fix="Create config.yaml first")]

    cfg = _load_app_config(config_path)
    sandbox = cfg.get("sandbox")
    if not isinstance(sandbox, dict):
        return [CheckResult("fail", "sandbox", "Missing sandbox config", fix="Add sandbox.use to config.yaml")]

    provider = str(sandbox.get("use", ""))
    tools = _read_tools(cfg)
    has_bash_tool = any(t.get("name") == "bash" for t in tools)
    results: list[CheckResult] = []

    if "deerflow.sandbox.local:LocalSandboxProvider" in provider:
        if sandbox.get("allow_host_bash") is False and has_bash_tool:
            results.append(CheckResult("warn", "bash tool", "Host bash tool disabled by sandbox policy"))
        else:
            results.append(CheckResult("ok", "sandbox", "Local sandbox configured"))
        return results

    if "deerflow.community.aio_sandbox:AioSandboxProvider" in provider:
        runtime = shutil.which("docker") or shutil.which("podman")
        if runtime:
            return [CheckResult("ok", "sandbox", "Container runtime available")]
        return [CheckResult("warn", "container runtime available", "No docker/podman found", fix="Install docker or podman")]

    return [CheckResult("fail", "sandbox", f"Unsupported sandbox provider: {provider}")]


def main() -> int:
    repo_root = Path.cwd()
    config_path = repo_root / "config.yaml"

    results: list[CheckResult | list[CheckResult]] = [
        check_python(),
        check_config_exists(config_path),
        check_config_version(config_path, repo_root),
        check_config_loadable(config_path),
        check_models_configured(config_path),
        check_llm_api_key(config_path),
        check_llm_auth(config_path),
        check_web_search(config_path),
        check_web_fetch(config_path),
        check_env_file(repo_root),
        check_frontend_env(repo_root),
        check_sandbox(config_path),
    ]

    flattened: list[CheckResult] = []
    for item in results:
        if isinstance(item, list):
            flattened.extend(item)
        else:
            flattened.append(item)

    for item in flattened:
        print(f"[{item.status}] {item.label}: {item.detail}")
        if item.fix:
            print(f"  fix: {item.fix}")

    return 0 if _collect_status(flattened, "ok") in {"ok", "skip", "warn"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
