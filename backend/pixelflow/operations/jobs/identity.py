"""External Job Operation 的稳定身份与请求摘要。"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from pixelflow.agent_control_plane.contracts import OperationRequest


def _require_identity_text(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} 不能为空")
    if len(normalized) > 64:
        raise ValueError(f"{field} 不能超过 64 个字符")
    return normalized


def _require_positive_integer(field: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} 必须是正整数")
    return value


def _validate_json_value(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} 必须是有限 JSON 数值")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} 的 JSON 对象键必须是字符串")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} 包含 JSON 合同外的值")


def hash_operation_request(provider_request: Any) -> str:
    """按稳定 JSON 编码计算供应商请求 SHA-256，不返回原始请求。"""

    _validate_json_value(provider_request)
    try:
        canonical = json.dumps(
            provider_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("provider_request 必须满足 JSON 合同") from exc
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def build_operation_idempotency_key(
    workflow_id: str,
    stage: str,
    stage_version: int,
    attempt: int,
) -> str:
    """将四段 operation 身份编码为带版本的固定长度幂等键。"""

    identity = {
        "attempt": _require_positive_integer("attempt", attempt),
        "stage": _require_identity_text("stage", stage),
        "stage_version": _require_positive_integer(
            "stage_version",
            stage_version,
        ),
        "workflow_id": _require_identity_text("workflow_id", workflow_id),
    }
    return f"operation:v1:{hash_operation_request(identity)}"


def build_operation_request(
    *,
    workflow_id: str,
    stage: str,
    stage_version: int,
    attempt: int,
    provider_request: Any,
) -> OperationRequest:
    """由身份和供应商请求构造不可错配的 OperationRequest。"""

    normalized_workflow = _require_identity_text("workflow_id", workflow_id)
    normalized_stage = _require_identity_text("stage", stage)
    normalized_stage_version = _require_positive_integer(
        "stage_version",
        stage_version,
    )
    normalized_attempt = _require_positive_integer("attempt", attempt)
    return OperationRequest(
        workflow_id=normalized_workflow,
        stage=normalized_stage,
        stage_version=normalized_stage_version,
        attempt=normalized_attempt,
        request_hash=hash_operation_request(provider_request),
        idempotency_key=build_operation_idempotency_key(
            normalized_workflow,
            normalized_stage,
            normalized_stage_version,
            normalized_attempt,
        ),
    )
