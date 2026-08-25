"""视频生产字段的确定性校验，供 Harness Tool 与工作区摘要共用。"""

from __future__ import annotations

import re
from collections.abc import Mapping

_RATIO_PATTERN = re.compile(r"^(?:9:16|16:9|1:1)$")


def workspace_resolved_aspect_ratio(payload: Mapping[str, object]) -> str | None:
    """从权威工作区读取已确认画幅；不触发模型调用或旧 Prompt 链。"""

    candidates: list[object] = [payload.get("aspect_ratio")]
    form_values = payload.get("form_values")
    if isinstance(form_values, Mapping):
        candidates.extend((form_values.get("ratio"), form_values.get("aspect_ratio")))
    script = payload.get("script")
    if isinstance(script, Mapping):
        candidates.append(script.get("aspect_ratio"))
    for value in candidates:
        ratio = str(value or "").strip()
        if _RATIO_PATTERN.fullmatch(ratio):
            return ratio
    return None


def workspace_has_ending_cta(payload: Mapping[str, object]) -> bool:
    """判断工作区是否已有明确的结尾行动引导或明确选择“不需要”。"""

    for container in (payload, payload.get("form_values"), payload.get("script")):
        if not isinstance(container, Mapping):
            continue
        value = container.get("ending_cta") or container.get("cta")
        if isinstance(value, str) and value.strip():
            return True
        if container.get("has_ending_cta") is True:
            return True
    return False


def workspace_missing_requirements(payload: Mapping[str, object]) -> list[str]:
    """返回继续生成前尚缺的最小生产字段，不做 LLM 推断。"""

    missing: list[str] = []
    if workspace_resolved_aspect_ratio(payload) is None:
        missing.append("画幅")
    if not workspace_has_ending_cta(payload):
        missing.append("结尾行动引导")
    return missing


def reconcile_missing_with_workspace(
    missing: list[str],
    payload: Mapping[str, object],
) -> list[str]:
    """以最新权威工作区复核历史缺项，避免旧快照阻塞 Tool 调用。"""

    current = set(workspace_missing_requirements(payload))
    return [item for item in missing if item in current] or sorted(current)
