"""Minimal wizard writers used by setup tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        values[key] = value.strip()
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    existing_lines = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    remaining = set(values.keys())
    updated_lines: list[str] = []
    for line in existing_lines:
        if not line.strip() or line.lstrip().startswith("#"):
            updated_lines.append(line)
            continue
        if "=" not in line:
            updated_lines.append(line)
            continue
        key = line.split("=", 1)[0]
        if key in values:
            updated_lines.append(f"{key}={values[key]}")
            remaining.discard(key)
        else:
            updated_lines.append(line)

    for key in remaining:
        updated_lines.append(f"{key}={values[key]}")

    path.write_text("\n".join(updated_lines) + ("\n" if updated_lines else ""), encoding="utf-8")


def build_minimal_config(
    *,
    provider_use: str,
    model_name: str,
    display_name: str,
    api_key_field: str,
    env_var: str | None,
    search_use: str | None = None,
    search_extra_config: dict | None = None,
    web_fetch_use: str | None = None,
    web_fetch_extra_config: dict | None = None,
    sandbox_use: str = "deerflow.sandbox.local:LocalSandboxProvider",
    include_bash_tool: bool = False,
    include_write_tools: bool = True,
    extra_model_config: dict | None = None,
    config_version: int = 5,
) -> str:
    config: dict[str, object] = {
        "models": [],
        "config_version": config_version,
    }

    model_data = {
        "name": model_name,
        "use": provider_use,
        "model": model_name,
    }
    if env_var:
        model_data[api_key_field] = f"${env_var}"
    if extra_model_config:
        model_data.update(extra_model_config)
    config["models"] = [model_data]

    tools: list[dict] = []
    if search_use:
        tool = {
            "name": "web_search",
            "group": "web",
            "use": search_use,
        }
        if search_extra_config:
            tool.update(search_extra_config)
        tools.append(tool)
    if web_fetch_use:
        tool = {
            "name": "web_fetch",
            "group": "web",
            "use": web_fetch_use,
        }
        if web_fetch_extra_config:
            tool.update(web_fetch_extra_config)
        tools.append(tool)
    if include_write_tools:
        tools.extend(
            [
                {"name": "write_file", "group": "file:write", "use": "deerflow.sandbox.tools:write_file_tool"},
                {"name": "str_replace", "group": "file:write", "use": "deerflow.sandbox.tools:str_replace_tool"},
            ]
        )
    if include_bash_tool:
        tools.append({"name": "bash", "group": "bash", "use": "deerflow.sandbox.tools:bash_tool"})

    config["tools"] = tools

    if sandbox_use == "deerflow.sandbox.local:LocalSandboxProvider":
        config["sandbox"] = {"use": sandbox_use, "allow_host_bash": False}
    else:
        config["sandbox"] = {"use": sandbox_use}

    config["display_name"] = display_name

    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True).strip()


def _merge_tool_list(base: list[dict], additions: list[dict]) -> list[dict]:
    seen = set()
    merged: list[dict] = []
    for item in base:
        name = item.get("name")
        if name:
            if name in seen:
                continue
            seen.add(name)
        merged.append(item)

    for item in additions:
        name = item.get("name")
        if name and name in seen:
            continue
        if name:
            seen.add(name)
        merged.append(item)
    return merged


def write_config_yaml(
    path: Path,
    *,
    provider_use: str,
    model_name: str,
    display_name: str,
    api_key_field: str,
    env_var: str | None,
    search_use: str | None = None,
    search_extra_config: dict | None = None,
    web_fetch_use: str | None = None,
    web_fetch_extra_config: dict | None = None,
    sandbox_use: str = "deerflow.sandbox.local:LocalSandboxProvider",
    include_bash_tool: bool = False,
    include_write_tools: bool = True,
    extra_model_config: dict | None = None,
    config_version: int = 5,
) -> None:
    base = {}
    example_path = path.parent / "config.example.yaml"
    if example_path.exists():
        with example_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            base.update(loaded)

    if example_path.exists() and isinstance(base.get("config_version"), int):
        config_version = int(base["config_version"])

    content = build_minimal_config(
        provider_use=provider_use,
        model_name=model_name,
        display_name=display_name,
        api_key_field=api_key_field,
        env_var=env_var,
        search_use=search_use,
        search_extra_config=search_extra_config,
        web_fetch_use=web_fetch_use,
        web_fetch_extra_config=web_fetch_extra_config,
        sandbox_use=sandbox_use,
        include_bash_tool=include_bash_tool,
        include_write_tools=include_write_tools,
        extra_model_config=extra_model_config,
        config_version=config_version,
    )

    generated = yaml.safe_load(content) or {}
    if not isinstance(generated, dict):
        generated = {}

    merged = dict(base)
    merged.update(generated)
    merged["config_version"] = config_version

    if "tools" in generated:
        merged["tools"] = _merge_tool_list(base.get("tools", []), generated.get("tools", []))

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False, allow_unicode=True)

