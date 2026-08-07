"""现有 start/status Service 到稳定 Provider Job 结果的防腐适配层。"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlparse

from httpx import TimeoutException
from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from pixelflow.skills.base import is_quota_insufficient

from ..contracts.base import ContractModel

_POLLING_STATUSES = frozenset(
    {
        "running",
        "queued",
        "pending",
        "polling",
        "processing",
        "in_progress",
    }
)
_SUCCEEDED_STATUSES = frozenset(
    {
        "succeeded",
        "success",
        "completed",
        "done",
    }
)
_FAILED_STATUSES = frozenset(
    {
        "failed",
        "error",
        "cancelled",
    }
)
_QUOTA_STATUSES = frozenset(
    {
        "paused_quota",
        "quota_paused",
        "quota_insufficient",
        "payment_required",
    }
)
_TIMEOUT_STATUSES = frozenset(
    {
        "timeout",
        "timed_out",
    }
)
_KNOWN_STATUSES = _POLLING_STATUSES | _SUCCEEDED_STATUSES | _FAILED_STATUSES | _QUOTA_STATUSES | _TIMEOUT_STATUSES
_REASON_CODES = Literal[
    "provider_polling",
    "provider_succeeded",
    "provider_business_failed",
    "provider_quota_insufficient",
    "provider_timeout",
    "provider_job_expired",
]
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:authorization|api[_-]?key|secret|password|credential)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:\b(?:authorization|access[_-]?token|refresh[_-]?token|token|"
    r"api[_-]?key|secret|password|credential)\s*[:=]\s*"
    r"(?:bearer\s+)?\S+|\bbearer\s+[a-z0-9._~+/=-]{6,})",
    re.IGNORECASE,
)
_PROVIDER_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}")
_UNSAFE_RESULT_KEYS = frozenset(
    {
        "raw",
        "rawresponse",
        "providerresponse",
        "responsebody",
    }
)


class ProviderJobOutcome(StrEnum):
    """Provider Job Adapter 对 Workflow 暴露的六类稳定结果。"""

    POLLING = "polling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PAUSED_QUOTA = "paused_quota"
    TIMEOUT = "timeout"
    EXPIRED = "expired"


_SNAPSHOT_CONTRACT = {
    ProviderJobOutcome.POLLING: (
        "provider_polling",
        "供应商任务处理中。",
    ),
    ProviderJobOutcome.SUCCEEDED: (
        "provider_succeeded",
        "供应商任务已完成。",
    ),
    ProviderJobOutcome.FAILED: (
        "provider_business_failed",
        "供应商任务执行失败。",
    ),
    ProviderJobOutcome.PAUSED_QUOTA: (
        "provider_quota_insufficient",
        "额度不足，当前任务已暂停，可在充值后继续。",
    ),
    ProviderJobOutcome.TIMEOUT: (
        "provider_timeout",
        "供应商任务等待超时。",
    ),
    ProviderJobOutcome.EXPIRED: (
        "provider_job_expired",
        "供应商原任务已过期，需要用户手动重新发起。",
    ),
}
_RESULT_FORBIDDEN_OUTCOMES = frozenset(
    {
        ProviderJobOutcome.FAILED,
        ProviderJobOutcome.PAUSED_QUOTA,
        ProviderJobOutcome.TIMEOUT,
        ProviderJobOutcome.EXPIRED,
    }
)


class ProviderJobMappingError(ValueError):
    """供应商 DTO 不满足稳定映射合同，拒绝猜测任务状态。"""

    def __init__(self, reason_code: str = "provider_response_invalid") -> None:
        self.reason_code = reason_code
        super().__init__(f"Provider Job 响应映射失败：{reason_code}")


class ProviderJobCallError(RuntimeError):
    """供应商调用发生未分类错误，且不回显原始异常内容。"""

    def __init__(self, reason_code: str = "provider_call_failed") -> None:
        self.reason_code = reason_code
        super().__init__(f"Provider Job 调用失败：{reason_code}")


class ProviderJobSnapshot(ContractModel):
    """供 Operation Coordinator 消费的稳定任务快照。"""

    model_config = ConfigDict(
        frozen=True,
        hide_input_in_errors=True,
    )

    provider_job_id: str | None = None
    outcome: ProviderJobOutcome
    result: JsonValue = None
    reason_code: _REASON_CODES
    message: str

    def model_post_init(self, context: object, /) -> None:
        """把已验证 JSON 递归冻结，避免嵌套引用绕过顶层只读约束。"""

        del context
        object.__setattr__(self, "result", _freeze_result(self.result))

    @field_validator("provider_job_id")
    @classmethod
    def validate_provider_job_id(cls, value: str | None) -> str | None:
        """限制可持久化的供应商任务标识，阻断 URL 和凭据形态。"""

        if value is None:
            return None
        try:
            return _validate_provider_job_id(value)
        except ValueError:
            raise ValueError("provider_job_id_unsafe") from None

    @field_validator("result")
    @classmethod
    def validate_safe_result(cls, value: JsonValue) -> JsonValue:
        """把有限 JSON、凭据和 URL 安全规则固化为模型不变量。"""

        _ensure_safe_result(value)
        return value

    @model_validator(mode="after")
    def validate_stable_contract(self) -> ProviderJobSnapshot:
        """固定六态与 reason/message 的一一映射。"""

        expected_reason, expected_message = _SNAPSHOT_CONTRACT[self.outcome]
        if self.reason_code != expected_reason or self.message != expected_message:
            raise ValueError("provider_snapshot_contract_invalid")
        if self.outcome in _RESULT_FORBIDDEN_OUTCOMES and self.result is not None:
            raise ValueError("provider_snapshot_result_forbidden")
        return self

    @field_serializer("result")
    def serialize_safe_result(self, value: object) -> object:
        """序列化前再次校验并恢复普通 JSON 容器。"""

        thawed = _thaw_result(value)
        _ensure_safe_result(thawed)
        return thawed


@runtime_checkable
class ExistingJobService(Protocol):
    """现有 v2 异步任务 Service 的最小 start/status 边界。"""

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object: ...

    async def status(self, provider_job_id: str) -> object: ...


class ProviderJobAdapter:
    """调用现有 Service，并把供应商状态映射为稳定六态结果。"""

    def __init__(self, service: ExistingJobService) -> None:
        self._service = service

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> ProviderJobSnapshot:
        """透传单次凭据与幂等键，不在 Adapter 或结果中保存它们。"""

        normalized_request = _normalize_request(request)
        normalized_authorization = _require_text(
            "authorization",
            authorization,
        )
        normalized_idempotency_key = _require_text(
            "idempotency_key",
            idempotency_key,
        )
        try:
            response = await self._service.start(
                normalized_request,
                authorization=normalized_authorization,
                idempotency_key=normalized_idempotency_key,
            )
        except Exception as exc:
            return _map_call_exception(exc, provider_job_id=None)
        return _map_response(response, expected_job_id=None)

    async def status(
        self,
        provider_job_id: str,
        *,
        user_id: str | None = None,
        conversation_id: str | None = None,
    ) -> ProviderJobSnapshot:
        """查询原provider job；仅显式scoped Service接收恢复作用域。"""

        normalized_job_id = _require_text(
            "provider_job_id",
            provider_job_id,
        )
        try:
            normalized_job_id = _validate_provider_job_id(normalized_job_id)
        except ValueError:
            raise ProviderJobMappingError("provider_job_id_invalid") from None
        scoped_status = getattr(self._service, "status_scoped", None)
        if callable(scoped_status) and (user_id is None or conversation_id is None):
            raise ProviderJobMappingError("provider_status_scope_required")
        try:
            if callable(scoped_status):
                response = await scoped_status(
                    normalized_job_id,
                    user_id=_require_text("user_id", user_id),
                    conversation_id=_require_text(
                        "conversation_id",
                        conversation_id,
                    ),
                )
            else:
                response = await self._service.status(normalized_job_id)
        except Exception as exc:
            return _map_call_exception(
                exc,
                provider_job_id=normalized_job_id,
            )
        return _map_response(
            response,
            expected_job_id=normalized_job_id,
        )


def _require_text(field: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} 不能为空")
    return normalized


def _normalize_request(
    request: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    if not isinstance(request, Mapping):
        raise ProviderJobMappingError("provider_request_not_object")
    _ensure_safe_provider_request(request)
    try:
        normalized = copy.deepcopy(dict(request))
    except (TypeError, ValueError):
        raise ProviderJobMappingError("provider_request_invalid") from None
    return normalized


def _map_call_exception(
    exc: Exception,
    *,
    provider_job_id: str | None,
) -> ProviderJobSnapshot:
    if isinstance(exc, (TimeoutError, TimeoutException)):
        return _snapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.TIMEOUT,
            reason_code="provider_timeout",
            message="供应商任务等待超时。",
        )
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code in {402, "402"}:
        return _snapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.PAUSED_QUOTA,
            reason_code="provider_quota_insufficient",
            message="额度不足，当前任务已暂停，可在充值后继续。",
        )
    if status_code in {404, "404"}:
        return _snapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.EXPIRED,
            reason_code="provider_job_expired",
            message="供应商原任务已过期，需要用户手动重新发起。",
        )
    raise ProviderJobCallError() from None


def _map_response(
    response: object,
    *,
    expected_job_id: str | None,
) -> ProviderJobSnapshot:
    payload = _response_payload(response)
    if "ok" in payload and type(payload["ok"]) is not bool:
        raise ProviderJobMappingError("provider_ok_invalid")
    provider_status = _normalize_status(payload.get("status"))

    if _is_quota_response(payload, provider_status):
        return _snapshot(
            provider_job_id=_optional_response_job_id(
                payload,
                expected_job_id=expected_job_id,
            ),
            outcome=ProviderJobOutcome.PAUSED_QUOTA,
            reason_code="provider_quota_insufficient",
            message="额度不足，当前任务已暂停，可在充值后继续。",
        )
    provider_job_id = _response_job_id(
        payload,
        expected_job_id=expected_job_id,
    )
    if provider_status in _TIMEOUT_STATUSES:
        return _snapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.TIMEOUT,
            reason_code="provider_timeout",
            message="供应商任务等待超时。",
        )
    if provider_status not in _KNOWN_STATUSES:
        raise ProviderJobMappingError("provider_status_unknown")
    if payload.get("ok") is False or provider_status in _FAILED_STATUSES:
        return _snapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.FAILED,
            reason_code="provider_business_failed",
            message="供应商任务执行失败。",
        )
    if provider_status in _POLLING_STATUSES:
        return _snapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.POLLING,
            result=_project_business_result(payload.get("result")),
            reason_code="provider_polling",
            message="供应商任务处理中。",
        )
    if provider_status in _SUCCEEDED_STATUSES:
        return _snapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.SUCCEEDED,
            result=_project_business_result(payload.get("result")),
            reason_code="provider_succeeded",
            message="供应商任务已完成。",
        )
    raise ProviderJobMappingError("provider_response_inconsistent")


def _response_payload(response: object) -> dict[str, object]:
    if isinstance(response, BaseModel):
        payload = response.model_dump(mode="python")
    elif isinstance(response, Mapping):
        payload = dict(response)
    else:
        raise ProviderJobMappingError("provider_response_not_object")
    return payload


def _response_job_id(
    payload: Mapping[str, object],
    *,
    expected_job_id: str | None,
) -> str:
    values: list[str] = []
    for key in ("job_id", "provider_job_id"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            values.append(
                _validate_provider_job_id(
                    _require_text(key, value),
                )
            )
        except ValueError:
            raise ProviderJobMappingError("provider_job_id_invalid") from None
    if not values:
        raise ProviderJobMappingError("provider_job_id_missing")
    if len(set(values)) != 1:
        raise ProviderJobMappingError("provider_job_id_conflict")
    provider_job_id = values[0]
    if expected_job_id is not None and provider_job_id != expected_job_id:
        raise ProviderJobMappingError("provider_job_id_mismatch")
    return provider_job_id


def _optional_response_job_id(
    payload: Mapping[str, object],
    *,
    expected_job_id: str | None,
) -> str | None:
    """start 402可在供应商创建任务前发生，此时允许缺少provider job ID。"""

    if payload.get("job_id") is None and payload.get("provider_job_id") is None:
        if expected_job_id is not None:
            raise ProviderJobMappingError("provider_job_id_missing")
        return None
    return _response_job_id(payload, expected_job_id=expected_job_id)


def _normalize_status(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderJobMappingError("provider_status_missing")
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _is_quota_response(
    payload: Mapping[str, object],
    provider_status: str,
) -> bool:
    if provider_status in _QUOTA_STATUSES:
        return True
    if payload.get("status_code") in {402, "402"}:
        return True
    if payload.get("quota_insufficient") is True:
        return True
    result = payload.get("result")
    if isinstance(result, Mapping) and result.get("quota_insufficient") is True:
        return True
    quota_probe = {
        key: payload.get(key)
        for key in (
            "status_code",
            "code",
            "error",
            "detail",
            "message",
        )
        if key in payload
    }
    return is_quota_insufficient(quota_probe)


def _snapshot(
    *,
    provider_job_id: str | None,
    outcome: ProviderJobOutcome,
    reason_code: _REASON_CODES,
    message: str,
    result: object = None,
) -> ProviderJobSnapshot:
    if result is not None:
        _ensure_safe_result(result)
    try:
        return ProviderJobSnapshot.model_validate(
            {
                "provider_job_id": provider_job_id,
                "outcome": outcome,
                "result": copy.deepcopy(result),
                "reason_code": reason_code,
                "message": message,
            }
        )
    except (TypeError, ValidationError):
        raise ProviderJobMappingError("provider_result_invalid") from None


def _validate_provider_job_id(value: str) -> str:
    if value != value.strip() or _PROVIDER_JOB_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("provider_job_id_unsafe")
    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    if _SENSITIVE_KEY_PATTERN.search(value) or "token" in normalized:
        raise ValueError("provider_job_id_unsafe")
    return value


def _project_business_result(value: object) -> object:
    """剔除现有 v2 DTO 的供应商原始响应，仅保留业务字段。"""

    if isinstance(value, BaseModel):
        return _project_business_result(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        projected: dict[object, object] = {}
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "", key.lower()) if isinstance(key, str) else ""
            if normalized_key in _UNSAFE_RESULT_KEYS:
                continue
            projected[key] = _project_business_result(child)
        return projected
    if isinstance(value, list):
        return [_project_business_result(child) for child in value]
    return value


def _freeze_result(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_result(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_result(child) for child in value)
    return value


def _thaw_result(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_result(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_result(child) for child in value]
    return value


def _ensure_safe_result(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProviderJobMappingError("provider_result_invalid")
            normalized_key = re.sub(r"[^a-z0-9]+", "", key.lower())
            if _SENSITIVE_KEY_PATTERN.search(key) or "token" in normalized_key or normalized_key in _UNSAFE_RESULT_KEYS:
                raise ProviderJobMappingError("provider_result_sensitive")
            _ensure_safe_result(child)
        return
    if isinstance(value, list):
        for child in value:
            _ensure_safe_result(child)
        return
    if isinstance(value, str):
        if _SENSITIVE_VALUE_PATTERN.search(value):
            raise ProviderJobMappingError("provider_result_sensitive")
        parsed = urlparse(value.strip())
        if parsed.scheme in {"http", "https"} and (parsed.username is not None or parsed.password is not None or bool(parsed.query) or bool(parsed.fragment)):
            raise ProviderJobMappingError("provider_result_unsafe_url")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ProviderJobMappingError("provider_result_non_finite")


def _ensure_safe_provider_request(value: object) -> None:
    """校验只在start调用栈存在的请求，允许HTTPS签名素材URL。"""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProviderJobMappingError("provider_request_invalid")
            normalized_key = re.sub(r"[^a-z0-9]+", "", key.lower())
            if _SENSITIVE_KEY_PATTERN.search(key) or "token" in normalized_key:
                raise ProviderJobMappingError("provider_request_sensitive")
            _ensure_safe_provider_request(child)
        return
    if isinstance(value, list):
        for child in value:
            _ensure_safe_provider_request(child)
        return
    if isinstance(value, str):
        parsed = urlparse(value.strip())
        if parsed.scheme in {"http", "https"}:
            if parsed.scheme != "https" or not parsed.netloc:
                raise ProviderJobMappingError("provider_request_unsafe_url")
            if parsed.username is not None or parsed.password is not None:
                raise ProviderJobMappingError("provider_request_unsafe_url")
            return
        if _SENSITIVE_VALUE_PATTERN.search(value):
            raise ProviderJobMappingError("provider_request_sensitive")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProviderJobMappingError("provider_request_non_finite")
        return
    raise ProviderJobMappingError("provider_request_invalid")
