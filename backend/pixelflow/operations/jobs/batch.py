"""M5 计费批次的稳定身份与子 Operation 编排合同。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pixelflow.agent_control_plane.contracts.base import ContractModel

from ..ports import OperationConflictError
from .identity import build_operation_idempotency_key

MAX_CHILD_OPERATIONS_PER_BATCH = 6


class OperationBatchChild(ContractModel):
    """一个批次内唯一的 scene × variant 子任务身份。"""

    scene_id: str
    variant_index: int
    operation_idempotency_key: str


@dataclass(frozen=True, slots=True)
class OperationBatchPlan:
    """Tool Broker 创建或回读批次时交给 M06 的不可变计划。"""

    batch_id: str
    batch_idempotency_key: str
    children: tuple[OperationBatchChild, ...]


def build_operation_batch_plan(
    *,
    run_id: str,
    tool_call_id: str,
    scene_ids: tuple[str, ...],
    variant_count: int,
    attempt: int,
    batch_index: int = 1,
) -> OperationBatchPlan:
    """由冻结 Run/Tool 身份生成批次及子 Operation 的双重幂等键。"""

    if not run_id.startswith("hrun_") or not tool_call_id.strip():
        raise ValueError("计费批次缺少冻结 Run 或 Tool Call 身份")
    if isinstance(variant_count, bool) or not 1 <= variant_count <= 3:
        raise ValueError("variant_count 必须在 1 到 3 之间")
    if isinstance(attempt, bool) or attempt < 1:
        raise ValueError("attempt 必须为正整数")
    if isinstance(batch_index, bool) or batch_index < 1:
        raise ValueError("batch_index 必须为正整数")
    normalized_scenes = tuple(scene_id.strip() for scene_id in scene_ids)
    if not normalized_scenes or any(not scene_id for scene_id in normalized_scenes):
        raise ValueError("计费批次至少需要一个非空 scene_id")
    if len(set(normalized_scenes)) != len(normalized_scenes):
        raise ValueError("计费批次 scene_id 不能重复")
    child_pairs = tuple(
        (scene_id, variant_index)
        for scene_id in normalized_scenes
        for variant_index in range(1, variant_count + 1)
    )
    if len(child_pairs) > MAX_CHILD_OPERATIONS_PER_BATCH:
        raise OperationConflictError("单个计费批次最多包含 6 个子 Operation")
    # 一个 Tool Call 可选择很多镜头；拆分后的每个批次都必须有稳定且不同的身份。
    # batch_index 只由 Gateway 按用户选择镜头的稳定顺序派生，模型不参与身份生成。
    batch_identity_payload: dict[str, Any] = {
        "run_id": run_id,
        "tool_call_id": tool_call_id,
        "attempt": attempt,
    }
    # 第 1 批沿用 M06 原有身份，升级部署后重放旧 Tool Call 不会重复计费；
    # 只有拆出的后续批次才把索引纳入身份。
    if batch_index > 1:
        batch_identity_payload["batch_index"] = batch_index
    batch_identity = _digest(batch_identity_payload)
    batch_id = "operation-batch-" + batch_identity[:32]
    children = tuple(
        OperationBatchChild(
            scene_id=scene_id,
            variant_index=variant_index,
            # 子项键必须就是 M06 Operation 的键。批次表与 Operation 表各自
            # 幂等，但不能使用两套不相关的身份，否则崩溃重领会误判为新付费 start。
            operation_idempotency_key=build_operation_idempotency_key(
                batch_id,
                f"generate_scene:{hashlib.sha256(scene_id.encode()).hexdigest()[:12]}:v{variant_index}",
                1,
                attempt,
            ),
        )
        for scene_id, variant_index in child_pairs
    )
    return OperationBatchPlan(
        batch_id=batch_id,
        batch_idempotency_key="operation-batch:v1:" + batch_identity,
        children=children,
    )


def build_operation_batch_completion_event_id(batch_id: str) -> str:
    """批次终态事件是 operation_resume 的唯一触发身份，子任务完成不得使用它。"""

    normalized = batch_id.strip()
    if not normalized.startswith("operation-batch-"):
        raise ValueError("OperationBatch 标识无效")
    return "evt_operation_batch_done_" + _digest({"batch_id": normalized})[:32]


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()


__all__ = [
    "MAX_CHILD_OPERATIONS_PER_BATCH",
    "OperationBatchChild",
    "OperationBatchPlan",
    "build_operation_batch_completion_event_id",
    "build_operation_batch_plan",
]
