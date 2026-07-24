"""把大型 tool/artifact 载荷移出模型输入并保留最小证据。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
_TOOL_MESSAGE_TYPES = {"tool", "tool_result"}
_TOOL_FIELDS = ("tool_output", "tool_result")
_ARTIFACT_FIELDS = ("artifact", "artifact_payload")
_URL_PATTERN = re.compile(r"https?://[^\s\"']+", flags=re.IGNORECASE)
_CREDENTIAL_PATTERN = re.compile(
    r"\b(authorization|token|api[_ -]?key|secret|password)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
    flags=re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\bbearer\s+[^\s,;]+", flags=re.IGNORECASE)
_MAX_EXTERNAL_REF_CHARS = 2_048
_SNIPPET_FIELDS = (
    "artifact_ref",
    "artifact_id",
    "job_id",
    "scene_id",
    "status",
    "title",
    "type",
    "summary",
)

PayloadKind = Literal["tool", "artifact"]


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"无法序列化 {type(value).__name__} 类型的上下文载荷")


def _serialize(value: object, *, sort_keys: bool) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
        default=_json_default,
    ).encode("utf-8")


def estimate_prompt_bytes(payload: Mapping[str, object]) -> int:
    """按 UTF-8 序列化结果计算 Prompt 的可比较字节规模。"""

    return len(_serialize(dict(payload), sort_keys=False))


class _ExternalizerRecord(BaseModel):
    """为外置写入和证据报告提供不可变字段合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextPayloadRecord(_ExternalizerRecord):
    """交给权威载荷存储的完整 tool/artifact 记录。"""

    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    source_kind: PayloadKind
    source_ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    original_bytes: int = Field(ge=1)
    payload: JsonValue

    @property
    def storage_identity(self) -> tuple[str, str, PayloadKind, str, str]:
        """提供 Store 必须用于幂等 upsert 的稳定复合键。"""

        return (
            self.user_id,
            self.conversation_id,
            self.source_kind,
            self.source_ref,
            self.content_hash,
        )


class ExternalizedPayloadEvidence(_ExternalizerRecord):
    """描述模型输入中一个已替换载荷的稳定证据。"""

    source_kind: PayloadKind
    source_ref: str = Field(min_length=1)
    external_ref: str = Field(min_length=1, max_length=_MAX_EXTERNAL_REF_CHARS)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    original_bytes: int = Field(ge=1)


class ContextPayloadStore(Protocol):
    """按 ``storage_identity`` 幂等保存载荷并返回会话内稳定引用。"""

    async def save_context_payload(self, record: ContextPayloadRecord) -> str: ...


@dataclass(frozen=True, slots=True)
class ContextExternalizationResult:
    """返回不含大型载荷的 Prompt 副本和外置证据。"""

    payload: dict[str, object]
    externalized: tuple[ExternalizedPayloadEvidence, ...]
    prompt_bytes: int


def _content_hash(payload: JsonValue) -> tuple[str, int]:
    encoded = _serialize(payload, sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}", len(encoded)


def _truncate_snippet(value: str, max_chars: int) -> str:
    without_urls = _URL_PATTERN.sub("[已隐藏链接]", value)
    without_credentials = _CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group(1)}=[已隐藏凭据]",
        without_urls,
    )
    compact = " ".join(_BEARER_PATTERN.sub("Bearer [已隐藏凭据]", without_credentials).split())
    if len(compact) <= max_chars:
        return compact
    marker = " … "
    if max_chars <= len(marker):
        return compact[:max_chars]
    available = max_chars - len(marker)
    head_chars = (available * 2) // 3
    tail_chars = available - head_chars
    return f"{compact[:head_chars]}{marker}{compact[-tail_chars:]}"


def _safe_mapping_snippet(value: Mapping[str, object], max_chars: int) -> str | None:
    selected = {field: deepcopy(value[field]) for field in _SNIPPET_FIELDS if field in value}
    if not selected:
        return None
    try:
        normalized = _JSON_VALUE_ADAPTER.validate_python(selected)
    except ValueError:
        return None
    rendered = _serialize(normalized, sort_keys=True).decode("utf-8")
    return _truncate_snippet(rendered, max_chars)


def _snippet(
    message: Mapping[str, object],
    value: JsonValue,
    *,
    max_chars: int,
) -> str | None:
    explicit = message.get("context_snippet")
    if isinstance(explicit, str) and explicit.strip():
        return _truncate_snippet(explicit, max_chars)
    if isinstance(value, Mapping):
        return _safe_mapping_snippet(value, max_chars)
    if isinstance(value, str) and value.strip():
        return _truncate_snippet(value, max_chars)
    return None


def _is_externalized(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("context_externalized") is True


def _source_ref(
    message: Mapping[str, object],
    value: JsonValue,
    *,
    source_kind: PayloadKind,
    content_hash: str,
) -> str:
    if source_kind == "tool":
        candidates = (
            message.get("tool_call_id"),
            message.get("message_id"),
        )
    else:
        value_mapping = value if isinstance(value, Mapping) else {}
        candidates = (
            value_mapping.get("artifact_ref"),
            value_mapping.get("artifact_id"),
            message.get("artifact_ref"),
            message.get("artifact_id"),
            message.get("message_id"),
        )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return content_hash


def _candidate_fields(message: Mapping[str, object]) -> tuple[tuple[str, PayloadKind], ...]:
    candidates: list[tuple[str, PayloadKind]] = []
    message_type = message.get("role", message.get("type"))
    if message_type in _TOOL_MESSAGE_TYPES and "content" in message:
        candidates.append(("content", "tool"))
    candidates.extend((field, "tool") for field in _TOOL_FIELDS if field in message)
    candidates.extend((field, "artifact") for field in _ARTIFACT_FIELDS if field in message)
    return tuple(dict.fromkeys(candidates))


class ContextPayloadExternalizer:
    """只外置近期消息中的明确大载荷，不遍历业务权威字段。"""

    def __init__(
        self,
        *,
        store: ContextPayloadStore,
        externalize_min_bytes: int = 12_000,
        snippet_max_chars: int = 500,
    ) -> None:
        if isinstance(externalize_min_bytes, bool) or not isinstance(
            externalize_min_bytes,
            int,
        ):
            raise ValueError("externalize_min_bytes 必须是正整数")
        if externalize_min_bytes <= 0:
            raise ValueError("externalize_min_bytes 必须是正整数")
        if isinstance(snippet_max_chars, bool) or not isinstance(
            snippet_max_chars,
            int,
        ):
            raise ValueError("snippet_max_chars 必须是正整数")
        if snippet_max_chars <= 0:
            raise ValueError("snippet_max_chars 必须是正整数")
        self._store = store
        self._externalize_min_bytes = externalize_min_bytes
        self._snippet_max_chars = snippet_max_chars

    async def externalize(
        self,
        *,
        user_id: str,
        conversation_id: str,
        payload: Mapping[str, object],
    ) -> ContextExternalizationResult:
        """在深副本上外置大载荷，任一存储失败都不返回裁剪结果。"""

        if not user_id.strip() or not conversation_id.strip():
            raise ValueError("user_id 和 conversation_id 不能为空")

        result_payload = deepcopy(dict(payload))
        original_prompt_bytes = estimate_prompt_bytes(result_payload)
        recent_messages = result_payload.get("recent_messages", [])
        if not isinstance(recent_messages, list):
            raise TypeError("recent_messages 必须是列表")

        evidence: list[ExternalizedPayloadEvidence] = []
        for message in recent_messages:
            if not isinstance(message, dict):
                continue
            for field, source_kind in _candidate_fields(message):
                raw_value = message[field]
                if _is_externalized(raw_value):
                    continue
                value = _JSON_VALUE_ADAPTER.validate_python(deepcopy(raw_value))
                content_hash, original_bytes = _content_hash(value)
                if original_bytes <= self._externalize_min_bytes:
                    continue
                source_ref = _source_ref(
                    message,
                    value,
                    source_kind=source_kind,
                    content_hash=content_hash,
                )
                record = ContextPayloadRecord(
                    user_id=user_id.strip(),
                    conversation_id=conversation_id.strip(),
                    source_kind=source_kind,
                    source_ref=source_ref,
                    content_hash=content_hash,
                    original_bytes=original_bytes,
                    payload=deepcopy(value),
                )
                external_ref = await self._store.save_context_payload(record)
                if not isinstance(external_ref, str) or not external_ref.strip():
                    raise ValueError("载荷存储必须返回非空 external_ref")
                external_ref = external_ref.strip()
                if len(external_ref) > _MAX_EXTERNAL_REF_CHARS:
                    raise ValueError(f"external_ref 长度不能超过 {_MAX_EXTERNAL_REF_CHARS}")

                placeholder: dict[str, JsonValue] = {
                    "context_externalized": True,
                    "external_ref": external_ref,
                    "content_hash": content_hash,
                    "original_bytes": original_bytes,
                }
                snippet = _snippet(
                    message,
                    value,
                    max_chars=self._snippet_max_chars,
                )
                if snippet is not None:
                    placeholder["snippet"] = snippet
                if len(_serialize(placeholder, sort_keys=True)) >= original_bytes:
                    raise ValueError("外置后的引用和最小片段必须严格降低载荷规模")
                message[field] = placeholder
                evidence.append(
                    ExternalizedPayloadEvidence(
                        source_kind=source_kind,
                        source_ref=source_ref,
                        external_ref=external_ref,
                        content_hash=content_hash,
                        original_bytes=original_bytes,
                    )
                )

        prompt_bytes = estimate_prompt_bytes(result_payload)
        if evidence and prompt_bytes >= original_prompt_bytes:
            raise ValueError("外置后的 Prompt 规模必须严格下降")
        return ContextExternalizationResult(
            payload=result_payload,
            externalized=tuple(evidence),
            prompt_bytes=prompt_bytes,
        )


__all__ = [
    "ContextExternalizationResult",
    "ContextPayloadExternalizer",
    "ContextPayloadRecord",
    "ContextPayloadStore",
    "ExternalizedPayloadEvidence",
    "PayloadKind",
    "estimate_prompt_bytes",
]
