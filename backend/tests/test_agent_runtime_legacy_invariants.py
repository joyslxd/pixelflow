"""Characterization tests for v2 behavior that the Agent runtime must preserve.

M00-A.1 intentionally exercises the current public DTOs, job query contract, and
conversation repository without introducing the new runtime.  Later migrations
can run this file as the backend component of the flag-off compatibility gate.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.gateway.routers.pixelflow_image import (
    _IMAGE_GENERATION_JOBS,
    ImagePrepareResponse,
    get_generate_image_job,
)
from app.gateway.routers.pixelflow_planning import PlanMarkdownResponse
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
)


class _LegacyScenePackagesReviewContract(BaseModel):
    """表征测试用：旧 PrepareScenePackagesResponse 的确认规则快照。"""

    ok: bool = True
    target_duration_ms: int = 0
    requires_confirmation: bool = True
    review_timeout_sec: int | None = Field(default=None)


def test_legacy_review_contracts_keep_existing_manual_and_timed_confirmation_rules() -> None:
    plan = PlanMarkdownResponse(
        output_type="image",
        plan_markdown="# plan.md",
        template_path="templates/plan_image.md",
    )
    scene_packages = _LegacyScenePackagesReviewContract(ok=True, target_duration_ms=4_000)
    image_result = ImagePrepareResponse(
        ok=True,
        method="text_to_image",
        endpoint="/api/picture/text_to_image",
        prompt="characterization prompt",
        negative_prompt="",
    )

    assert plan.review_timeout_sec is None
    assert scene_packages.requires_confirmation is True
    assert scene_packages.review_timeout_sec is None
    assert image_result.review_timeout_sec == 60


@pytest.mark.asyncio
async def test_legacy_pending_job_metadata_round_trips_across_conversation_updates() -> None:
    store = MemoryPixelFlowTaskStore()
    pending_jobs = {
        "pendingIntakeJob": {"job_id": "intake-job", "kind": "intake_analysis"},
        "pendingPlanJob": {"job_id": "plan-job", "kind": "plan_generation"},
        "pendingImageJob": {"job_id": "image-job", "kind": "image_generation"},
        "pendingScenePackageJob": {"job_id": "scene-package-job", "kind": "scene_package_generation"},
        "pendingVideoJob": {"job_id": "video-job", "kind": "scene_generation"},
        "pendingPptJob": {"job_id": "ppt-job", "kind": "ppt_file"},
    }
    conversation = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="legacy-pending-conversation",
            user_id="legacy-user",
            last_phase="image_generation_running",
            context=deepcopy(pending_jobs),
        )
    )

    restored = await store.get_conversation(conversation.conversation_id, user_id="legacy-user")
    assert restored is not None
    assert restored.context == pending_jobs

    updated = await store.update_conversation(
        conversation.conversation_id,
        user_id="legacy-user",
        last_phase="image_generation_quota_paused",
    )
    assert updated is not None
    assert updated.context == pending_jobs


@pytest.mark.asyncio
async def test_legacy_job_polling_preserves_job_id_and_quota_paused_state(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = "existing-image-job"
    monkeypatch.setitem(
        _IMAGE_GENERATION_JOBS,
        job_id,
        {
            "status": "quota_paused",
            "result": {
                "ok": False,
                "method": "text_to_image",
                "endpoint": "/api/picture/text_to_image",
                "quota_insufficient": True,
                "message": "请充值后从当前阶段继续。",
            },
            "error": None,
        },
    )

    status = await get_generate_image_job(job_id)

    assert status.ok is True
    assert status.job_id == job_id
    assert status.status == "quota_paused"
    assert status.result is not None
    assert status.result.quota_insufficient is True

    with pytest.raises(HTTPException) as exc_info:
        await get_generate_image_job("replacement-job-that-was-never-started")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_legacy_delivery_completes_only_after_explicit_download_metadata_is_saved() -> None:
    store = MemoryPixelFlowTaskStore()
    conversation_id = "legacy-delivery-conversation"
    message_id = "image-result-message"
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id="legacy-user",
        )
    )
    artifact = {
        "type": "image_result",
        "imageResult": {
            "ok": True,
            "images": [{"url": "https://cdn.example/final.png"}],
        },
    }
    await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id=message_id,
            conversation_id=conversation_id,
            user_id="legacy-user",
            role="assistant",
            payload={"artifact": deepcopy(artifact)},
        )
    )

    before_download = await store.list_conversation_messages(conversation_id, user_id="legacy-user")
    assert "deliveryDownloadedAt" not in before_download[0].payload["artifact"]
    assert "deliveryDownloadedUrl" not in before_download[0].payload["artifact"]

    downloaded_artifact = deepcopy(artifact)
    downloaded_artifact.update(
        {
            "deliveryDownloadedAt": "2026-07-22T22:30:00+08:00",
            "deliveryDownloadedUrl": "https://cdn.example/final.png",
        }
    )
    updated = await store.update_conversation_message(
        conversation_id,
        message_id,
        user_id="legacy-user",
        payload={"artifact": downloaded_artifact},
    )

    assert updated is not None
    assert updated.payload["artifact"]["deliveryDownloadedAt"] == "2026-07-22T22:30:00+08:00"
    assert updated.payload["artifact"]["deliveryDownloadedUrl"] == "https://cdn.example/final.png"
    assert updated.payload["artifact"]["imageResult"] == artifact["imageResult"]
