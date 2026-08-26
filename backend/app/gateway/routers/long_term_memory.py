"""长期记忆 WriteOutbox 的人工重放 Controller。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.gateway.deps import get_current_user
from pixelflow.long_term_memory import LongTermMemoryService

router = APIRouter(prefix="/agent/memories", tags=["pixelflow-long-term-memory"])


class MemoryWriteReplayResponse(BaseModel):
    """人工重放结果只返回稳定写入键，不暴露 Mem0 event 或远程错误。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    write_key: str = Field(min_length=1, max_length=128)
    status: str = "pending"


def _memory_service(request: Request) -> LongTermMemoryService:
    """读取 Gateway 生命周期装配的长期记忆 Service，缺失时明确拒绝人工重放。"""

    service = getattr(request.app.state, "pixelflow_long_term_memory_service", None)
    if not isinstance(service, LongTermMemoryService):
        raise HTTPException(status_code=503, detail={"code": "long_term_memory_unavailable"})
    return service


@router.post(
    "/writes/{write_key}/requeue",
    response_model=MemoryWriteReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def requeue_memory_write(write_key: str, request: Request) -> MemoryWriteReplayResponse:
    """当前用户确认提交边界后重放自己的人工审核记录；无 event 的记录才会再次 add。"""

    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated"})
    replayed = await _memory_service(request).requeue_manual_review(
        user_id=user_id,
        write_key=write_key,
    )
    if not replayed:
        raise HTTPException(status_code=404, detail={"code": "memory_write_manual_review_not_found"})
    return MemoryWriteReplayResponse(write_key=write_key)
