"""PixelFlow 剪映草稿生成 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.gateway.pixelflow_memory import (
    concise_result_summary,
    current_user_id,
    power_mem_service,
    record_power_mem_background,
)
from pixelflow.jianying_draft import (
    JianyingDraftCapability,
    JianyingDraftResult,
    JianyingDraftStartRequest,
    JianyingDraftStatus,
)

router = APIRouter(prefix="/agent/flows/video/jianying-draft", tags=["pixelflow-flows"])

_TERMINAL_STATUSES = {
    JianyingDraftStatus.SUCCEEDED,
    JianyingDraftStatus.FAILED,
    JianyingDraftStatus.TIMEOUT,
    JianyingDraftStatus.NOT_CONFIGURED,
}


def _jianying_draft_service(request: Request) -> Any:
    """从当前应用取得剪映草稿 Service，缺失时统一返回服务不可用。"""

    service = getattr(request.app.state, "pixelflow_jianying_draft_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="剪映草稿服务暂不可用")
    return service


async def _require_owned_conversation(
    request: Request,
    conversation_id: str | None,
    user_id: str | None,
) -> None:
    """统一隐藏不存在和无权访问的对话，避免越权枚举。"""

    store = getattr(request.app.state, "pixelflow_task_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="对话服务暂不可用")
    if (
        not conversation_id
        or user_id is None
        or await store.get_conversation(conversation_id, user_id=user_id) is None
    ):
        raise HTTPException(status_code=404, detail="对话不存在或无访问权限")


@router.get("/capability", response_model=JianyingDraftCapability)
async def get_jianying_draft_capability(request: Request) -> JianyingDraftCapability:
    """查询剪映草稿 Provider 的当前可用性。"""

    try:
        return await _jianying_draft_service(request).capability()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Provider 边界不可泄露内部异常
        raise HTTPException(status_code=503, detail="剪映草稿服务暂不可用") from None


@router.post("/start", response_model=JianyingDraftResult)
async def start_jianying_draft(
    body: JianyingDraftStartRequest,
    request: Request,
) -> JianyingDraftResult:
    """验证对话归属后，启动或复用当前分镜版本的剪映草稿任务。"""

    service = _jianying_draft_service(request)
    user_id = await current_user_id(request)
    await _require_owned_conversation(request, body.conversation_id, user_id)
    result = await service.start(body, retry_failed=body.retry_failed)
    if result.status == JianyingDraftStatus.NOT_CONFIGURED:
        raise HTTPException(status_code=503, detail=result.model_dump(mode="json"))
    return result


@router.get("/jobs/{job_id}", response_model=JianyingDraftResult)
async def get_jianying_draft_job(job_id: str, request: Request) -> JianyingDraftResult:
    """验证任务所属对话后读取状态，并原子写入一次安全经验摘要。"""

    service = _jianying_draft_service(request)
    result = await service.get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="剪映草稿任务不存在或已过期")

    user_id = await current_user_id(request)
    await _require_owned_conversation(request, result.conversation_id, user_id)
    if (
        result.status in _TERMINAL_STATUSES
        and await service.claim_terminal_experience(job_id)
    ):
        record_power_mem_background(
            power_mem_service(request),
            user_id=user_id,
            content=concise_result_summary(
                "剪映草稿 Agent 异步任务结束",
                {
                    "stage": "jianying_draft",
                    "ok": result.status == JianyingDraftStatus.SUCCEEDED,
                },
            ),
            category="experience",
            source_agent="jianying_draft_agent",
            metadata={
                "source": "video_jianying_draft_job",
                "job_id": job_id,
                "status": result.status.value,
            },
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    return result
