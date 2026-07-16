"""PixelFlow 剪映草稿生成 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.gateway.pixelflow_memory import (
    concise_result_summary,
    current_user_id,
    power_mem_service,
    record_power_mem_background,
)
from pixelflow.jianying_draft import (
    JianyingDraftCapability,
    JianyingDraftRequest,
    JianyingDraftResult,
    JianyingDraftService,
    JianyingDraftStatus,
    UnavailableJianyingDraftSkill,
)

router = APIRouter(prefix="/agent/flows/video/jianying-draft", tags=["pixelflow-flows"])

# 真实 Provider 接入前固定使用不可用实现，避免生成伪草稿或占位任务。
_JIANYING_DRAFT_SKILL = UnavailableJianyingDraftSkill()
_JIANYING_DRAFT_SERVICE = JianyingDraftService(skill=_JIANYING_DRAFT_SKILL)
_TERMINAL_EXPERIENCE_JOB_IDS: set[str] = set()
_TERMINAL_STATUSES = {
    JianyingDraftStatus.SUCCEEDED,
    JianyingDraftStatus.FAILED,
    JianyingDraftStatus.TIMEOUT,
    JianyingDraftStatus.NOT_CONFIGURED,
}


def get_jianying_draft_service() -> JianyingDraftService:
    """返回进程内剪映草稿任务 Service，供路由和测试共用。"""

    return _JIANYING_DRAFT_SERVICE


@router.get("/capability", response_model=JianyingDraftCapability)
async def get_jianying_draft_capability() -> JianyingDraftCapability:
    """查询剪映草稿 Provider 的当前可用性。"""

    return await _JIANYING_DRAFT_SKILL.capability()


@router.post("/start", response_model=JianyingDraftResult)
async def start_jianying_draft(body: JianyingDraftRequest) -> JianyingDraftResult:
    """启动或复用当前对话和分镜版本的剪映草稿任务。"""

    result = await get_jianying_draft_service().start(body)
    if result.status == JianyingDraftStatus.NOT_CONFIGURED:
        raise HTTPException(status_code=503, detail=result.model_dump(mode="json"))
    return result


@router.get("/jobs/{job_id}", response_model=JianyingDraftResult)
async def get_jianying_draft_job(job_id: str, request: Request) -> JianyingDraftResult:
    """读取剪映草稿任务状态；终态只异步沉淀一次安全经验摘要。"""

    result = await get_jianying_draft_service().get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="剪映草稿任务不存在或已过期")

    if result.status in _TERMINAL_STATUSES and job_id not in _TERMINAL_EXPERIENCE_JOB_IDS:
        _TERMINAL_EXPERIENCE_JOB_IDS.add(job_id)
        record_power_mem_background(
            power_mem_service(request),
            user_id=await current_user_id(request),
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
                "source": "jianying_draft_job",
                "job_id": job_id,
                "status": result.status.value,
            },
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    return result
