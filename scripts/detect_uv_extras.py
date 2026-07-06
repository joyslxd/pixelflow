"""Detect required uv extras from environment and PowerFlow config."""

from __future__ import annotations

import re
import sys
from pathlib import Path


_EXTRA_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def parse_env_extras(value: str | None) -> list[str]:
    """Parse ``UV_EXTRAS`` style input into a safe extras list."""
    if not value:
        return []

    parts = re.split(r"[,\s]+", value.strip())
    extras: list[str] = []
    seen = set()
    for raw in parts:
        token = raw.strip().strip(",")
        if not token:
            continue

        if _EXTRA_RE.fullmatch(token):
            if token not in seen:
                extras.append(token)
                seen.add(token)
            continue

        print(f"ignoring invalid UV_EXTRAS entry: {token!r}", file=sys.stderr)

    return extras


def _strip_comment(line: str) -> str:
    in_quote = None
    out = []
    for i, char in enumerate(line):
        if char in {'"', "'"}:
            if in_quote is None:
                in_quote = char
            elif in_quote == char:
                in_quote = None
        elif char == "#" and in_quote is None:
            return "".join(out).rstrip()
        out.append(char)
    return "".join(out).rstrip()


def section_value(lines: list[str], section: str, key: str) -> str | None:
    """Return ``section.key`` value from simple indentation-based YAML text."""
    in_section = False
    section_indent = 0
    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip())
        if indent == 0 and raw.endswith(":"):
            current_section = raw[:-1].strip()
            in_section = current_section == section
            section_indent = indent
            continue

        if not in_section:
            continue

        if indent <= section_indent:
            if raw.endswith(":"):
                if raw[:-1].strip() == section:
                    in_section = True
                    section_indent = indent
                else:
                    in_section = False
            else:
                in_section = False
            continue

        child_indent = section_indent + 2
        if indent == child_indent and ":" in stripped:
            child_key, child_value = stripped.split(":", 1)
            if child_key.strip() == key:
                value = child_value.strip().strip('"').strip("'")
                return value or None
    return None


def detect_from_config(path: Path) -> list[str]:
    """Inspect config yaml text and return known uv extras."""
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    found = set()
    for item in (section_value(lines, "database", "backend"), section_value(lines, "checkpointer", "type")):
        if item and item.lower() == "postgres":
            found.add("postgres")
    return sorted(found)


def resolve_extras() -> list[str]:
    """Resolve extras from env override or config file detection."""
    env_value = parse_env_extras(_read_env("UV_EXTRAS"))
    if env_value:
        return env_value

    explicit_config = _read_env("DEER_FLOW_CONFIG_PATH")
    cfg_path = Path(explicit_config) if explicit_config else None
    if cfg_path and cfg_path.exists():
        extras = detect_from_config(cfg_path)
        return extras

    search_targets = [
        Path.cwd() / "config.yaml",
    ]
    if search_targets[0].exists():
        extras = detect_from_config(search_targets[0])
        return extras
    extras = detect_from_config(Path.cwd() / "backend" / "config.yaml")
    if extras:
        return extras

    return []


def format_flags(extras: list[str]) -> str:
    """Format extras as ``uv`` CLI flags."""
    return " ".join([f"--extra {item}" for item in extras])


def _read_env(name: str) -> str | None:
    import os

    return os.environ.get(name)
