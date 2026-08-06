from __future__ import annotations

import json
import operator
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from pixelflow.agent_runtime.jobs import (
    ProviderJobAdapter,
    ProviderJobCallError,
    ProviderJobMappingError,
    ProviderJobOutcome,
    ProviderJobSnapshot,
)

AUTHORIZATION = "Bearer provider-secret"
IDEMPOTENCY_KEY = "operation:v1:sha256:" + "a" * 64
PROVIDER_JOB_ID = "provider-job-1"


class _StartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class _ExistingImageResult(BaseModel):
    """复刻现有图片状态 DTO 中携带供应商 raw 的关键结构。"""

    ok: bool
    images: list[dict[str, Any]]
    raw: dict[str, Any]


class _ExistingImageJobStatusResponse(BaseModel):
    """用于锁定现有 v2 Pydantic status DTO 的嵌套结果形状。"""

    ok: bool
    job_id: str
    status: str
    result: _ExistingImageResult | None = None


class _ProviderHttpError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(detail)


class _FakeExistingJobService:
    """模拟现有 v2 Service 的 start/status DTO 和异常边界。"""

    def __init__(
        self,
        *,
        start_response: object,
        status_responses: list[object] | None = None,
    ) -> None:
        self.start_response = start_response
        self.status_responses = list(status_responses or [])
        self.start_calls: list[dict[str, Any]] = []
        self.status_calls: list[str] = []

    async def start(
        self,
        request: Mapping[str, Any],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        self.start_calls.append(
            {
                "request": dict(request),
                "authorization": authorization,
                "idempotency_key": idempotency_key,
            }
        )
        if isinstance(self.start_response, BaseException):
            raise self.start_response
        return self.start_response

    async def status(self, provider_job_id: str) -> object:
        self.status_calls.append(provider_job_id)
        if not self.status_responses:
            raise AssertionError("测试 fake 缺少 status 响应")
        response = self.status_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_status",
    ["running", "queued", "pending", "polling", "processing", "in_progress"],
)
async def test_start_forwards_transient_credentials_and_maps_polling(
    provider_status: str,
) -> None:
    service = _FakeExistingJobService(
        start_response=_StartResponse(
            ok=True,
            job_id=PROVIDER_JOB_ID,
            status=provider_status,
        )
    )
    adapter = ProviderJobAdapter(service)
    request = {
        "prompt": "生成一张商品主图",
        "params": {"count": 1},
    }

    snapshot = await adapter.start(
        request,
        authorization=AUTHORIZATION,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert service.start_calls == [
        {
            "request": request,
            "authorization": AUTHORIZATION,
            "idempotency_key": IDEMPOTENCY_KEY,
        }
    ]
    assert snapshot.provider_job_id == PROVIDER_JOB_ID
    assert snapshot.outcome is ProviderJobOutcome.POLLING
    assert snapshot.reason_code == "provider_polling"
    assert snapshot.message == "供应商任务处理中。"
    serialized = snapshot.model_dump_json()
    assert AUTHORIZATION not in serialized
    assert IDEMPOTENCY_KEY not in serialized


@pytest.mark.asyncio
async def test_start_allows_transient_https_signed_material_url() -> None:
    service = _FakeExistingJobService(
        start_response=_StartResponse(
            ok=True,
            job_id=PROVIDER_JOB_ID,
            status="polling",
        )
    )
    adapter = ProviderJobAdapter(service)
    signed_url = "https://example.invalid/reference.mp4?signature=temporary"

    snapshot = await adapter.start(
        {"video_url": signed_url},
        authorization=AUTHORIZATION,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert service.start_calls[0]["request"] == {"video_url": signed_url}
    assert snapshot.outcome is ProviderJobOutcome.POLLING
    assert signed_url not in snapshot.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_status",
    ["succeeded", "success", "completed", "done"],
)
async def test_status_maps_success_and_preserves_business_result(
    provider_status: str,
) -> None:
    result = {
        "artifact_refs": ["artifact-image-1"],
        "image_count": 1,
    }
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": True,
                "job_id": PROVIDER_JOB_ID,
                "status": provider_status,
                "result": result,
            }
        ],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert service.status_calls == [PROVIDER_JOB_ID]
    assert snapshot.provider_job_id == PROVIDER_JOB_ID
    assert snapshot.outcome is ProviderJobOutcome.SUCCEEDED
    assert snapshot.model_dump()["result"] == result
    assert snapshot.result is not result
    assert snapshot.reason_code == "provider_succeeded"
    assert snapshot.message == "供应商任务已完成。"


@pytest.mark.asyncio
async def test_status_projects_raw_from_existing_v2_pydantic_result() -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            _ExistingImageJobStatusResponse(
                ok=True,
                job_id=PROVIDER_JOB_ID,
                status="completed",
                result=_ExistingImageResult(
                    ok=True,
                    images=[{"artifact_id": "artifact-image-1"}],
                    raw={"Authorization": "Bearer provider-secret"},
                ),
            )
        ],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert snapshot.outcome is ProviderJobOutcome.SUCCEEDED
    assert snapshot.model_dump()["result"] == {
        "ok": True,
        "images": [{"artifact_id": "artifact-image-1"}],
    }
    assert "raw" not in snapshot.model_dump_json()
    assert "provider-secret" not in snapshot.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", ["failed", "error", "cancelled"])
async def test_status_maps_business_failure_without_leaking_provider_error(
    provider_status: str,
) -> None:
    secret_error = "供应商失败：https://provider.example/result?token=secret"
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": False,
                "job_id": PROVIDER_JOB_ID,
                "status": provider_status,
                "error": secret_error,
                "message": secret_error,
            }
        ],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert snapshot.outcome is ProviderJobOutcome.FAILED
    assert snapshot.result is None
    assert snapshot.reason_code == "provider_business_failed"
    assert snapshot.message == "供应商任务执行失败。"
    assert secret_error not in snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_explicit_ok_false_wins_over_running_status() -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": False,
                "job_id": PROVIDER_JOB_ID,
                "status": "running",
                "error": "业务校验失败",
            }
        ],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert snapshot.outcome is ProviderJobOutcome.FAILED
    assert snapshot.reason_code == "provider_business_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_ok", ["false", 0, 1, None])
async def test_explicit_ok_flag_must_be_boolean(
    invalid_ok: object,
) -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": invalid_ok,
                "job_id": PROVIDER_JOB_ID,
                "status": "succeeded",
                "result": {"artifact_refs": ["artifact-1"]},
            }
        ],
    )

    with pytest.raises(ProviderJobMappingError):
        await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "ok": False,
            "job_id": PROVIDER_JOB_ID,
            "status": "failed",
            "status_code": 402,
            "error": "payment required",
        },
        {
            "ok": False,
            "job_id": PROVIDER_JOB_ID,
            "status": "failed",
            "quota_insufficient": True,
            "message": "额度不足",
        },
        {
            "ok": False,
            "job_id": PROVIDER_JOB_ID,
            "status": "quota_paused",
            "message": "请充值",
        },
        {
            "ok": False,
            "job_id": PROVIDER_JOB_ID,
            "status": "failed",
            "result": {"quota_insufficient": True},
        },
    ],
)
async def test_status_maps_structured_quota_to_recoverable_pause(
    payload: dict[str, Any],
) -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[payload],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert snapshot.outcome is ProviderJobOutcome.PAUSED_QUOTA
    assert snapshot.result is None
    assert snapshot.reason_code == "provider_quota_insufficient"
    assert snapshot.message == "额度不足，当前任务已暂停，可在充值后继续。"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", ["timeout", "timed_out"])
async def test_status_maps_explicit_timeout(provider_status: str) -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": False,
                "job_id": PROVIDER_JOB_ID,
                "status": provider_status,
                "error": "内部超时细节不得外泄",
            }
        ],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert snapshot.outcome is ProviderJobOutcome.TIMEOUT
    assert snapshot.result is None
    assert snapshot.reason_code == "provider_timeout"
    assert snapshot.message == "供应商任务等待超时。"
    assert "内部超时细节" not in snapshot.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quota_error",
    [
        _ProviderHttpError(
            402,
            "Authorization: Bearer secret; https://provider.example/?token=x",
        ),
        httpx.HTTPStatusError(
            "Authorization: Bearer secret",
            request=httpx.Request(
                "POST",
                "https://provider.example/start?token=secret",
            ),
            response=httpx.Response(402),
        ),
    ],
)
async def test_start_maps_http_402_exception_without_leaking_detail(
    quota_error: Exception,
) -> None:
    service = _FakeExistingJobService(start_response=quota_error)

    snapshot = await ProviderJobAdapter(service).start(
        {"prompt": "生成视频"},
        authorization=AUTHORIZATION,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert snapshot.provider_job_id is None
    assert snapshot.outcome is ProviderJobOutcome.PAUSED_QUOTA
    assert snapshot.reason_code == "provider_quota_insufficient"
    assert "secret" not in snapshot.model_dump_json()
    assert "provider.example" not in snapshot.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeout_error",
    [
        TimeoutError("token=secret"),
        httpx.ReadTimeout("Authorization: Bearer secret"),
    ],
)
async def test_status_maps_timeout_exception_to_queried_job(
    timeout_error: Exception,
) -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[timeout_error],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert snapshot.provider_job_id == PROVIDER_JOB_ID
    assert snapshot.outcome is ProviderJobOutcome.TIMEOUT
    assert "secret" not in snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_unclassified_provider_exception_fails_closed_without_detail() -> None:
    secret_detail = "provider bug token=secret"
    service = _FakeExistingJobService(start_response=RuntimeError(secret_detail))

    with pytest.raises(ProviderJobCallError) as raised:
        await ProviderJobAdapter(service).start(
            {"prompt": "生成视频"},
            authorization=AUTHORIZATION,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert raised.value.reason_code == "provider_call_failed"
    assert secret_detail not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_response", "status_response"),
    [
        ({"ok": True, "status": "running"}, None),
        (
            None,
            {
                "ok": True,
                "job_id": "provider-job-other",
                "status": "running",
            },
        ),
        (
            None,
            {
                "ok": False,
                "job_id": PROVIDER_JOB_ID,
                "status": "mystery",
            },
        ),
        (["not", "an", "object"], None),
    ],
)
async def test_invalid_or_mismatched_service_response_fails_closed(
    start_response: object | None,
    status_response: object | None,
) -> None:
    service = _FakeExistingJobService(
        start_response=start_response,
        status_responses=[] if status_response is None else [status_response],
    )
    adapter = ProviderJobAdapter(service)

    with pytest.raises(ProviderJobMappingError):
        if start_response is not None:
            await adapter.start(
                {"prompt": "生成图片"},
                authorization=AUTHORIZATION,
                idempotency_key=IDEMPOTENCY_KEY,
            )
        else:
            await adapter.status(PROVIDER_JOB_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"Authorization": "Bearer secret"},
        {"nested": {"access_token": "secret"}},
        {"nested": {"access_token_value": "secret"}},
        {"nested": [{"api_key": "secret"}]},
        {"secret": "credential"},
        {"password": "credential"},
        {"note": "Authorization: Bearer top-secret"},
        {"note": "token=top-secret"},
        {"artifact_url": ("https://provider.example/result.zip?signature=secret")},
        {"artifact_url": (" https://provider.example/result.zip?signature=secret")},
        {"artifact_url": ("https://user:password@provider.example/result.zip")},
        {"artifact_url": ("https://provider.example/result.zip#token=secret")},
    ],
)
async def test_sensitive_result_keys_fail_closed(result: dict[str, Any]) -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": True,
                "job_id": PROVIDER_JOB_ID,
                "status": "succeeded",
                "result": result,
            }
        ],
    )

    with pytest.raises(ProviderJobMappingError) as raised:
        await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert "secret" not in str(raised.value)
    assert "credential" not in str(raised.value)


@pytest.mark.asyncio
async def test_non_json_business_result_fails_closed() -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": True,
                "job_id": PROVIDER_JOB_ID,
                "status": "succeeded",
                "result": object(),
            }
        ],
    )

    with pytest.raises(ProviderJobMappingError):
        await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_finite_value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
async def test_non_finite_business_result_fails_closed(
    non_finite_value: float,
) -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": True,
                "job_id": PROVIDER_JOB_ID,
                "status": "succeeded",
                "result": {"score": non_finite_value},
            }
        ],
    )

    with pytest.raises(ProviderJobMappingError):
        await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)


@pytest.mark.asyncio
async def test_non_json_business_request_fails_with_mapping_error() -> None:
    service = _FakeExistingJobService(
        start_response={
            "ok": True,
            "job_id": PROVIDER_JOB_ID,
            "status": "running",
        }
    )

    with pytest.raises(ProviderJobMappingError):
        await ProviderJobAdapter(service).start(
            {"prompt": object()},
            authorization=AUTHORIZATION,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert service.start_calls == []


@pytest.mark.asyncio
async def test_start_rejects_credentials_embedded_in_business_request() -> None:
    service = _FakeExistingJobService(
        start_response={
            "ok": True,
            "job_id": PROVIDER_JOB_ID,
            "status": "running",
        }
    )

    with pytest.raises(ProviderJobMappingError):
        await ProviderJobAdapter(service).start(
            {
                "prompt": "生成图片",
                "Authorization": "Bearer must-use-transient-argument",
            },
            authorization=AUTHORIZATION,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert service.start_calls == []


@pytest.mark.asyncio
async def test_snapshot_serialization_contains_only_stable_fields() -> None:
    service = _FakeExistingJobService(
        start_response={},
        status_responses=[
            {
                "ok": True,
                "provider_job_id": PROVIDER_JOB_ID,
                "status": "completed",
                "result": {"artifact_refs": ["artifact-1"]},
                "message": "供应商内部完成文案",
                "extra_provider_field": "不会进入规范结果",
            }
        ],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)
    payload = json.loads(snapshot.model_dump_json())

    assert payload == {
        "provider_job_id": PROVIDER_JOB_ID,
        "outcome": "succeeded",
        "result": {"artifact_refs": ["artifact-1"]},
        "reason_code": "provider_succeeded",
        "message": "供应商任务已完成。",
    }


@pytest.mark.parametrize(
    ("unsafe_result", "forbidden_text"),
    [
        ({"access_token": "top-secret"}, "top-secret"),
        ({"score": float("nan")}, "input_value"),
        ({"raw": {"provider": "opaque"}}, "opaque"),
        (
            {"artifact_url": ("https://provider.example/result.zip?signature=secret")},
            "provider.example",
        ),
    ],
)
def test_public_snapshot_model_cannot_bypass_result_safety(
    unsafe_result: dict[str, Any],
    forbidden_text: str,
) -> None:
    with pytest.raises(ValidationError) as raised:
        ProviderJobSnapshot(
            provider_job_id=PROVIDER_JOB_ID,
            outcome=ProviderJobOutcome.SUCCEEDED,
            result=unsafe_result,
            reason_code="provider_succeeded",
            message="供应商任务已完成。",
        )

    assert forbidden_text not in str(raised.value)


@pytest.mark.parametrize(
    "provider_job_id",
    [
        "https://provider.example/job?token=secret",
        "token=top-secret",
        "job id with spaces",
        "a" * 256,
    ],
)
@pytest.mark.asyncio
async def test_start_rejects_unsafe_provider_job_id(
    provider_job_id: str,
) -> None:
    service = _FakeExistingJobService(
        start_response={
            "ok": True,
            "job_id": provider_job_id,
            "status": "running",
        }
    )

    with pytest.raises(ProviderJobMappingError):
        await ProviderJobAdapter(service).start(
            {"prompt": "生成图片"},
            authorization=AUTHORIZATION,
            idempotency_key=IDEMPOTENCY_KEY,
        )


def test_public_snapshot_is_immutable_after_validation() -> None:
    snapshot = ProviderJobSnapshot(
        provider_job_id=PROVIDER_JOB_ID,
        outcome=ProviderJobOutcome.POLLING,
        reason_code="provider_polling",
        message="供应商任务处理中。",
    )

    with pytest.raises(ValidationError):
        snapshot.provider_job_id = "https://provider.example/job?token=secret"
    with pytest.raises(ValidationError):
        snapshot.reason_code = "provider_succeeded"
    with pytest.raises(ValidationError):
        snapshot.message = "unsafe token=top-secret"

    assert snapshot.provider_job_id == PROVIDER_JOB_ID
    assert snapshot.reason_code == "provider_polling"
    assert snapshot.message == "供应商任务处理中。"
    assert "top-secret" not in snapshot.model_dump_json()


def test_public_snapshot_deep_freezes_and_revalidates_result() -> None:
    snapshot = ProviderJobSnapshot(
        provider_job_id=PROVIDER_JOB_ID,
        outcome=ProviderJobOutcome.SUCCEEDED,
        reason_code="provider_succeeded",
        message="供应商任务已完成。",
        result={
            "artifact_refs": ["artifact-1"],
            "metadata": {"count": 1},
        },
    )
    assert isinstance(snapshot.result, Mapping)

    with pytest.raises(TypeError):
        operator.setitem(snapshot.result, "access_token", "top-secret")
    with pytest.raises(TypeError):
        operator.setitem(
            snapshot.result["metadata"],
            "access_token",
            "top-secret",
        )
    with pytest.raises(TypeError):
        operator.setitem(snapshot.result["artifact_refs"], 0, "tampered")

    assert snapshot.model_dump()["result"] == {
        "artifact_refs": ["artifact-1"],
        "metadata": {"count": 1},
    }

    object.__setattr__(
        snapshot,
        "result",
        {"access_token": "top-secret"},
    )
    with pytest.raises(PydanticSerializationError) as raised:
        snapshot.model_dump_json()
    assert "top-secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("outcome", "reason_code", "message", "result"),
    [
        (
            ProviderJobOutcome.SUCCEEDED,
            "provider_polling",
            "供应商任务已完成。",
            {"artifact_refs": ["artifact-1"]},
        ),
        (
            ProviderJobOutcome.SUCCEEDED,
            "provider_succeeded",
            "Authorization: Bearer top-secret",
            {"artifact_refs": ["artifact-1"]},
        ),
        (
            ProviderJobOutcome.FAILED,
            "provider_business_failed",
            "供应商任务执行失败。",
            {"provider_detail": "不应持久化"},
        ),
    ],
)
def test_public_snapshot_enforces_outcome_contract(
    outcome: ProviderJobOutcome,
    reason_code: str,
    message: str,
    result: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError) as raised:
        ProviderJobSnapshot(
            provider_job_id=PROVIDER_JOB_ID,
            outcome=outcome,
            reason_code=reason_code,
            message=message,
            result=result,
        )

    assert "top-secret" not in str(raised.value)
