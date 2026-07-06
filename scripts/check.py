"""Runtime helper for resolving pnpm command in local tool checks."""

from __future__ import annotations

import shutil


def find_pnpm_command() -> list[str]:
    """Return executable invocation list for pnpm.

    Priority:
    1. A directly discoverable ``pnpm`` executable.
    2. ``corepack`` + ``pnpm``.
    3. ``corepack.cmd`` + ``pnpm``.
    """
    direct = shutil.which("pnpm")
    if direct:
        return [direct]

    corepack = shutil.which("corepack")
    if corepack:
        return [corepack, "pnpm"]

    corepack_cmd = shutil.which("corepack.cmd")
    if corepack_cmd:
        return [corepack_cmd, "pnpm"]

    return []

