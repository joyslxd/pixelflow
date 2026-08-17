"""VideoAgent 真 LLM token 思考流：批量写入公开 SSE 事件。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
)

logger = logging.getLogger(__name__)

THINKING_FLUSH_INTERVAL_SEC = 0.08
THINKING_FLUSH_MIN_CHARS = 1
# 入场上下文理解需产出完整事实摘要；DeepSeek thinking 首包可能很慢。
# 必须走 stream=true：非流式会等整段返回，易在首包前触发整请求超时。
# HTTP request_timeout 不得短于本 wait_for，否则会先被客户端掐断。
THINKING_PREAMBLE_TIMEOUT_SEC = 90.0
THINKING_REQUEST_TIMEOUT_SEC = 90.0
THINKING_MAX_TOKENS = 2_400
THINKING_LLM_INPUT_MAX_CHARS = 2_400
DURATION_EXTRACT_TIMEOUT_SEC = 12.0
DURATION_EXTRACT_INPUT_MAX_CHARS = 6_000
# AgentEvent.event_id 上限 64；用短前缀 + 摘要保证稳定落库。
_EVENT_ID_MAX = 64
_DURATION_JSON_RE = re.compile(r"\{[^{}]*\"duration_sec\"[^{}]*\}", re.DOTALL)
_INTAKE_CONTEXT_MARKERS = ("INTAKE_CONTEXT", "INTAKE_PLAN", "INTAKE_VERDICT")
IntakeEntryPath = Literal["create", "polish", "continue", "inspect"]
IntakeIntent = Literal[
    "create",
    "polish",
    "continue_assets",
    "continue_images",
    "continue_video",
    "patch_scene",
    "inspect",
    "clarify",
]
IntakeTargetCapability = Literal[
    "clarify_brief",
    "develop_script",
    "import_script",
    "confirm_script",
    "prepare_scene_packages",
    "inspect_storyboard",
    "generate_scene_assets",
    "inspect_scene_results",
    "patch_scene",
    "generate_scenes",
    "review_generated_scenes",
    "compose_video",
    "inspect_workspace",
]
IntakeReadiness = Literal[
    "ready",
    "blocked",
    "waiting_confirmation",
    "inspect_required",
]
_ALLOWED_TARGET_CAPABILITIES = {
    "clarify_brief",
    "develop_script",
    "import_script",
    "confirm_script",
    "prepare_scene_packages",
    "inspect_storyboard",
    "generate_scene_assets",
    "inspect_scene_results",
    "patch_scene",
    "generate_scenes",
    "review_generated_scenes",
    "compose_video",
    "inspect_workspace",
}
_ALLOWED_READINESS = {
    "ready",
    "blocked",
    "waiting_confirmation",
    "inspect_required",
}
_INTAKE_STATE_KEYS = (
    "script_available",
    "script_confirmed",
    "storyboard_available",
    "scene_packages_available",
    "scene_assets_available",
    "scene_videos_available",
    "final_video_available",
)
_INTAKE_CONSTRAINT_KEYS = (
    "dirty_scene_only",
    "requires_visual_inspection",
)
_MAX_MODEL_PROGRESS_MESSAGES = 3
_MAX_MODEL_PROGRESS_CHARS = 80
_UNSAFE_PROGRESS_FRAGMENTS = (
    "系统提示词",
    "提示词规定",
    "内部推理",
    "思维链",
    "我们被要求",
    "我被要求",
    "规则要求我",
    "reasoning_content",
    "import_script",
    "run_script_skill_stage",
    "confirm_script_creative",
)
ALLOWED_INTAKE_MISSING = (
    "视频画幅",
    "结尾行动引导",
    "整片时长",
    "脚本方案确认",
    "角色设定",
    "产品信息",
    "创意方向",
)
_INTENT_TO_ENTRY_PATH: dict[str, IntakeEntryPath] = {
    "create": "create",
    "polish": "polish",
    "continue_assets": "continue",
    "continue_images": "continue",
    "continue_video": "continue",
    "patch_scene": "inspect",
    "inspect": "inspect",
    "clarify": "inspect",
}


@dataclass(frozen=True, slots=True)
class IntakeThinkingResult:
    """入场思考结论：用户可见文案与规划所需上下文事实。"""

    user_message: str
    entry_path: IntakeEntryPath | None = None
    intent: IntakeIntent | None = None
    missing_requirements: tuple[str, ...] = ()
    duration_sec: int | None = None
    aspect_ratio: str | None = None
    ending_cta: str | None = None
    needs_user_reply: bool = False
    target_capability: IntakeTargetCapability | None = None
    readiness: IntakeReadiness | None = None
    current_state: dict[str, bool] = field(default_factory=dict)
    scene_ids: tuple[str, ...] = ()
    constraints: dict[str, bool] = field(default_factory=dict)

    def as_planner_digest(self) -> dict[str, Any]:
        return {
            "user_message": self.user_message,
            "public_goal": self.user_message,
            "entry_path": self.entry_path,
            "intent": self.intent,
            "missing_requirements": list(self.missing_requirements),
            "duration_sec": self.duration_sec,
            "aspect_ratio": self.aspect_ratio,
            "ending_cta": self.ending_cta,
            "needs_user_reply": self.needs_user_reply,
            "target_capability": self.target_capability,
            "readiness": self.readiness,
            "current_state": dict(self.current_state),
            "scene_ids": list(self.scene_ids),
            "constraints": dict(self.constraints),
        }


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _thinking_event_id(turn_id: str, event_key: str) -> str:
    digest = hashlib.sha256(
        f"{turn_id}:{event_key}:{uuid4().hex}".encode()
    ).hexdigest()
    # thk_ + 40 hex = 44 ≤ 64
    return f"thk_{digest[:40]}"


def _chunk_text(chunk: Any) -> tuple[str, str]:
    """从 LangChain chunk 拆出 reasoning / content 增量。"""

    reasoning = ""
    content = ""
    kwargs = getattr(chunk, "additional_kwargs", None)
    if isinstance(kwargs, Mapping):
        raw = kwargs.get("reasoning_content")
        if isinstance(raw, str) and raw:
            reasoning = raw
    raw_content = getattr(chunk, "content", None)
    if isinstance(raw_content, str):
        content = raw_content
    elif isinstance(raw_content, list):
        parts: list[str] = []
        for part in raw_content:
            if isinstance(part, Mapping):
                kind = str(part.get("type") or "")
                if kind in {"thinking", "reasoning"}:
                    text = part.get("thinking") or part.get("text") or ""
                    if isinstance(text, str) and text:
                        reasoning += text
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            else:
                parts.append(str(part))
        content = "".join(parts)
    return reasoning, content


def _truncate_for_thinking(text: str, *, max_chars: int = THINKING_LLM_INPUT_MAX_CHARS) -> str:
    """截断长输入；若含「本轮指令」必须保留尾部指令，否则 LLM 看不到补字段跟进。"""

    raw = text.strip()
    if len(raw) <= max_chars:
        return raw
    marker = "【本轮指令】"
    if marker in raw:
        head, _, instruction = raw.partition(marker)
        tail = f"{marker}{instruction}".strip()
        # 指令本身超长时优先保留指令。
        if len(tail) >= max_chars - 40:
            return tail[:max_chars].rstrip() + "\n…（截断）"
        budget = max_chars - len(tail) - 24
        if budget < 120:
            return tail
        return (
            head[:budget].rstrip()
            + "\n…（中间已截断，仅用于思考预热）\n"
            + tail
        )
    return raw[:max_chars].rstrip() + "\n…（后文已截断，仅用于思考预热）"


def _parse_duration_sec_payload(raw: str) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    candidate = text
    match = _DURATION_JSON_RE.search(text)
    if match is not None:
        candidate = match.group(0)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("duration_sec")
    if value is None:
        return None
    try:
        duration = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= duration <= 3600:
        return duration
    return None



class ThinkingStreamPublisher:
    """按 turn 推送 thinking started/delta/completed，delta 做短缓冲合并。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._user_id = user_id.strip()
        self._conversation_id = conversation_id.strip()
        self._turn_id = turn_id.strip()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._buffer = ""
        self._buffer_channel = "reasoning"
        self._last_flush = 0.0
        self._delta_index = 0
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        title: str,
        subtitle: str = "",
    ) -> None:
        now = self._clock()
        self._last_flush = time.monotonic()
        await self._emit(
            AgentEventType.AGENT_THINKING_STARTED,
            {
                "turn_id": self._turn_id,
                "title": title.strip() or "思考中",
                "subtitle": subtitle.strip(),
                "started_at": _iso(now),
            },
            event_key="started",
        )

    async def push_delta(self, text: str, *, channel: str = "reasoning") -> None:
        piece = text.strip("\x00")
        if not piece:
            return
        # 不做微切段：事件洪水会拖死前端 SSE；平滑感交给前端打字机。
        async with self._lock:
            # 切换 channel 时先冲掉旧缓冲，避免 reasoning/answer 串台。
            if self._buffer and self._buffer_channel != channel:
                await self._flush_locked()
            self._buffer_channel = channel
            self._buffer += piece
            now = time.monotonic()
            due = (
                self._last_flush > 0
                and now - self._last_flush >= THINKING_FLUSH_INTERVAL_SEC
            )
            if len(self._buffer) >= THINKING_FLUSH_MIN_CHARS or due:
                await self._flush_locked()

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def complete(self) -> None:
        await self.flush()
        await self._emit(
            AgentEventType.AGENT_THINKING_COMPLETED,
            {"turn_id": self._turn_id},
            event_key="completed",
        )

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        delta = self._buffer
        channel = self._buffer_channel
        self._buffer = ""
        self._last_flush = time.monotonic()
        # 大段一次到达时切成小包写出，让 SSE 轮询能边写边推，前端打字机有增量可跟。
        chunk_size = 16
        for offset in range(0, len(delta), chunk_size):
            piece = delta[offset : offset + chunk_size]
            self._delta_index += 1
            await self._emit(
                AgentEventType.AGENT_THINKING_DELTA,
                {
                    "turn_id": self._turn_id,
                    "delta": piece,
                    "channel": channel,
                    "index": self._delta_index,
                },
                event_key=f"d{self._delta_index}",
            )
            if offset + chunk_size < len(delta):
                await asyncio.sleep(0)

    async def _emit(
        self,
        event_type: AgentEventType,
        payload: dict[str, Any],
        *,
        event_key: str,
    ) -> None:
        for attempt in range(4):
            now = self._clock()
            events = await self._repository.list_events(
                self._user_id,
                self._conversation_id,
            )
            sequence = 1 if not events else events[-1].sequence + 1
            event_id = _thinking_event_id(self._turn_id, event_key)
            if len(event_id) > _EVENT_ID_MAX:
                event_id = event_id[:_EVENT_ID_MAX]
            event = AgentEvent(
                event_id=event_id,
                sequence=sequence,
                cursor=f"c_{event_id}",
                conversation_id=self._conversation_id,
                run_id=self._turn_id[:64],
                occurred_at=now,
                type=event_type,
                payload=payload,
            )
            try:
                await self._repository.create_event(self._user_id, event)
                return
            except AgentRuntimeRecordConflictError:
                if attempt >= 3:
                    logger.warning(
                        "thinking 事件写入冲突 turn_id=%s type=%s",
                        self._turn_id,
                        event_type.value,
                    )
                    return
                await asyncio.sleep(0.02 * (attempt + 1))
            except ValueError as exc:
                logger.warning(
                    "thinking 事件写入非法 turn_id=%s type=%s error=%s",
                    self._turn_id,
                    event_type.value,
                    str(exc)[:200],
                )
                return


async def stream_chat_tokens(
    *,
    model: Any,
    messages: Sequence[Any],
    on_reasoning: Callable[[str], Awaitable[None]] | None = None,
    on_content: Callable[[str], Awaitable[None]] | None = None,
    timeout_sec: float,
) -> tuple[str, str]:
    """消费模型流式输出；返回 (reasoning, content) 全文。

    强制 `astream(..., stream=True)`，避免 LangChain 在实例
    ``streaming=False`` 时把 astream 退化成 ``ainvoke``（HTTP stream=false）。
    仅当模型根本没有 astream 时才退回一次性 invoke。
    """

    reasoning_parts: list[str] = []
    content_parts: list[str] = []

    async def _consume() -> None:
        astream = getattr(model, "astream", None)
        if astream is None:
            logger.warning("模型缺少 astream，思考流退化为一次性输出")
            message = await asyncio.to_thread(model.invoke, list(messages))
            reasoning, content = _chunk_text(message)
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning is not None:
                    await on_reasoning(reasoning)
            if content:
                content_parts.append(content)
                if on_content is not None:
                    await on_content(content)
            return
        # 显式 stream=True：覆盖实例 streaming=False，保证上游收到流式请求。
        try:
            stream_iter = astream(list(messages), stream=True)
        except TypeError:
            stream_iter = astream(list(messages))
        async for chunk in stream_iter:
            reasoning, content = _chunk_text(chunk)
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning is not None:
                    await on_reasoning(reasoning)
            if content:
                content_parts.append(content)
                if on_content is not None:
                    await on_content(content)

    await asyncio.wait_for(_consume(), timeout=timeout_sec)
    return "".join(reasoning_parts).strip(), "".join(content_parts).strip()


def _create_streaming_chat_model(
    factory: Callable[..., Any],
    *,
    thinking_enabled: bool = False,
    model_name: str = "deepseek-v4-pro",
) -> Any:
    """创建强制开启 OpenAI 兼容 streaming 的聊天模型。

    DeepSeek V4 思考模式需在请求里带 extra_body.thinking；真正把
    reasoning_content 解析进 chunk 依赖配置里的 PatchedChatOpenAIReasoning。
    入场预热用 low；HTTP 超时与 wait_for 对齐，避免非流式整段等待被掐断。
    """

    thinking_kwargs: dict[str, Any] = {}
    if thinking_enabled:
        thinking_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        thinking_kwargs["reasoning_effort"] = "low"
        thinking_kwargs["max_tokens"] = THINKING_MAX_TOKENS
        thinking_kwargs["request_timeout"] = THINKING_REQUEST_TIMEOUT_SEC
    try:
        model = factory(
            name=model_name,
            thinking_enabled=thinking_enabled,
            streaming=True,
            **thinking_kwargs,
        )
    except TypeError:
        try:
            model = factory(
                name=model_name,
                thinking_enabled=thinking_enabled,
                **thinking_kwargs,
            )
        except TypeError:
            try:
                model = factory(
                    name=model_name,
                    thinking_enabled=thinking_enabled,
                    streaming=True,
                )
            except TypeError:
                model = factory(thinking_enabled=False)
    # 配置里 when_thinking_enabled.reasoning_effort=high 会覆盖 kwargs；入场强制改回。
    # LangChain：实例 streaming=False 会让 astream 退化成 ainvoke（HTTP stream=false）。
    force_attrs: list[tuple[str, Any]] = [("streaming", True)]
    if thinking_enabled:
        force_attrs.extend(
            (
                ("reasoning_effort", "low"),
                ("max_tokens", THINKING_MAX_TOKENS),
                ("request_timeout", THINKING_REQUEST_TIMEOUT_SEC),
            )
        )
    for attr, value in force_attrs:
        try:
            setattr(model, attr, value)
        except Exception:  # noqa: BLE001
            pass
    return model


def _extract_json_object(raw: str) -> Mapping[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, Mapping) else None
    return None


def _split_answer_and_machine_block(raw_answer: str) -> tuple[str, Mapping[str, Any]]:
    """拆分用户可见结论与上下文机器块；兼容历史标记。"""

    text = (raw_answer or "").strip()
    payload: Mapping[str, Any] = {}
    user_message = text
    for marker in _INTAKE_CONTEXT_MARKERS:
        token = f"<<<{marker}>>>"
        start = text.find(token)
        if start < 0:
            continue
        body_start = start + len(token)
        end = text.find("<<<END>>>", body_start)
        block = text[body_start:end if end >= 0 else None]
        parsed = _extract_json_object(block)
        if parsed is None:
            continue
        user_message = text[:start].strip()
        if end >= 0:
            tail = text[end + len("<<<END>>>") :].strip()
            if tail and not user_message:
                user_message = tail
        payload = parsed
        break
    if not payload:
        parsed = _extract_json_object(text)
        if parsed is not None and text.startswith("{"):
            payload = parsed
            user_message = ""
    return user_message, payload


def _parse_bool_map(raw: Any, *, allowed_keys: Sequence[str]) -> dict[str, bool]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        key: value
        for key in allowed_keys
        if isinstance((value := raw.get(key)), bool)
    }


def _parse_scene_ids(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    scene_ids: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        scene_id = item.strip()[:128]
        if scene_id and scene_id not in scene_ids:
            scene_ids.append(scene_id)
        if len(scene_ids) >= 100:
            break
    return tuple(scene_ids)


def _safe_public_progress(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    message = " ".join(raw.split()).strip()
    if not 4 <= len(message) <= _MAX_MODEL_PROGRESS_CHARS:
        return None
    folded = message.casefold()
    if any(fragment.casefold() in folded for fragment in _UNSAFE_PROGRESS_FRAGMENTS):
        return None
    if "{" in message or "}" in message or "<<<" in message:
        return None
    return message


class _IntakeNdjsonStream:
    """从流式正文提取安全公开进度和终态诊断，同时保留旧格式原文。"""

    def __init__(self, publisher: ThinkingStreamPublisher) -> None:
        self._publisher = publisher
        self._buffer = ""
        self._raw_parts: list[str] = []
        self._progress_messages: list[str] = []
        self._result: Mapping[str, Any] | None = None

    async def feed(self, delta: str) -> None:
        if not delta:
            return
        self._raw_parts.append(delta)
        self._buffer += delta
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            await self._consume_line(line)

    async def finish(self) -> None:
        if self._buffer.strip():
            await self._consume_line(self._buffer)
        self._buffer = ""

    def final_content(self) -> str:
        if self._result is not None:
            return json.dumps(dict(self._result), ensure_ascii=False)
        return "".join(self._raw_parts)

    async def _consume_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line.startswith("{"):
            return
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(record, Mapping):
            return
        record_type = record.get("type")
        if record_type == "result" and isinstance(record.get("data"), Mapping):
            self._result = record["data"]
            return
        if record_type != "progress":
            return
        message = _safe_public_progress(record.get("message"))
        if (
            message is None
            or message in self._progress_messages
            or len(self._progress_messages) >= _MAX_MODEL_PROGRESS_MESSAGES
        ):
            return
        self._progress_messages.append(message)
        await self._publisher.push_delta(f"{message}\n", channel="reasoning")


def _parse_intake_answer_and_verdict(
    raw_answer: str,
) -> IntakeThinkingResult:
    """拆分用户可见结论与上下文机器块；失败时降级为仅文案。"""

    user_message, payload = _split_answer_and_machine_block(raw_answer)
    answer_from_payload = payload.get("answer") or payload.get("public_goal")
    if isinstance(answer_from_payload, str) and answer_from_payload.strip():
        # 机器块内 answer 优先，保证与 public_goal 一致。
        user_message = answer_from_payload.strip()
    if not user_message:
        user_message = "已完成初步判断，继续生成执行方案。"

    intent: IntakeIntent | None = None
    facts = payload.get("facts") if isinstance(payload.get("facts"), Mapping) else {}
    raw_intent = payload.get("intent")
    if isinstance(facts, Mapping) and facts.get("intent") is not None:
        raw_intent = facts.get("intent")
    if isinstance(raw_intent, str) and raw_intent.strip() in _INTENT_TO_ENTRY_PATH:
        intent = raw_intent.strip()  # type: ignore[assignment]

    entry_path: IntakeEntryPath | None = None
    raw_path = payload.get("entry_path")
    if isinstance(raw_path, str) and raw_path.strip() in {"create", "polish", "continue", "inspect"}:
        entry_path = raw_path.strip()  # type: ignore[assignment]
    elif intent is not None:
        entry_path = _INTENT_TO_ENTRY_PATH[intent]

    missing: list[str] = []
    raw_missing = payload.get("missing")
    if isinstance(raw_missing, list):
        for item in raw_missing:
            if isinstance(item, str) and item.strip() in ALLOWED_INTAKE_MISSING:
                if item.strip() not in missing:
                    missing.append(item.strip())

    duration_sec: int | None = None
    raw_duration = payload.get("duration_sec")
    if isinstance(facts, Mapping) and facts.get("duration_sec") is not None:
        raw_duration = facts.get("duration_sec")
    if isinstance(raw_duration, int) and not isinstance(raw_duration, bool) and 4 <= raw_duration <= 300:
        duration_sec = raw_duration

    aspect_ratio: str | None = None
    raw_ratio = payload.get("aspect_ratio")
    if isinstance(facts, Mapping) and facts.get("aspect_ratio") is not None:
        raw_ratio = facts.get("aspect_ratio")
    if isinstance(raw_ratio, str) and raw_ratio.strip() in {"9:16", "16:9", "1:1"}:
        aspect_ratio = raw_ratio.strip()

    ending_cta: str | None = None
    raw_cta = payload.get("ending_cta")
    if isinstance(facts, Mapping) and facts.get("ending_cta") is not None:
        raw_cta = facts.get("ending_cta")
    if isinstance(raw_cta, str) and raw_cta.strip() in {"keep", "none", "present", "unknown"}:
        ending_cta = raw_cta.strip()

    target_capability: IntakeTargetCapability | None = None
    raw_capability = payload.get("target_capability")
    if isinstance(raw_capability, str) and raw_capability.strip() in _ALLOWED_TARGET_CAPABILITIES:
        target_capability = raw_capability.strip()  # type: ignore[assignment]

    readiness: IntakeReadiness | None = None
    raw_readiness = payload.get("readiness")
    if isinstance(raw_readiness, str) and raw_readiness.strip() in _ALLOWED_READINESS:
        readiness = raw_readiness.strip()  # type: ignore[assignment]

    current_state = _parse_bool_map(
        payload.get("current_state"),
        allowed_keys=_INTAKE_STATE_KEYS,
    )
    scene_ids = _parse_scene_ids(facts.get("scene_ids"))
    constraints = _parse_bool_map(
        payload.get("constraints"),
        allowed_keys=_INTAKE_CONSTRAINT_KEYS,
    )

    needs_user_reply = payload.get("needs_user_reply") is True or bool(missing)
    return IntakeThinkingResult(
        user_message=user_message[:2_000],
        entry_path=entry_path,
        intent=intent,
        missing_requirements=tuple(missing),
        duration_sec=duration_sec,
        aspect_ratio=aspect_ratio,
        ending_cta=ending_cta,
        needs_user_reply=needs_user_reply,
        target_capability=target_capability,
        readiness=readiness,
        current_state=current_state,
        scene_ids=scene_ids,
        constraints=constraints,
    )


_INTAKE_SYSTEM_PROMPT = """
你是 PixelFlow VideoAgent 的状态理解器。

你的职责是根据用户本轮输入、附件、workspace_digest 和
blocking_confirmation，判断：

1. 用户希望达成的业务目标；
2. 工作区当前已经完成和缺失的产物；
3. 目标能力的前置条件是否满足；
4. 当前应该进入哪一种业务能力；
5. 是否需要用户补充信息、确认，或先检查现有结果。

你不选择 Tool，不生成执行步骤，不按照固定工作流机械推进。
Planner 会根据你的状态诊断和服务端 Tool Registry 决定具体执行方案。

【允许的目标能力】
- clarify_brief：补充创意方向或产品信息
- develop_script：从想法创作或继续完善脚本
- import_script：导入并结构化已有成熟脚本
- confirm_script：等待用户确认脚本方案
- prepare_scene_packages：把脚本预览分阶段产物（角色/场景/道具设定与分镜提示词）投影进视频场景包；已有 characters/outline 时不要当作未拆解；用户说「重新生成分镜包/场景包/资产包」时目标仍是 prepare_scene_packages，不要改成确认脚本或空聊
- inspect_storyboard：检查分镜结构及资产引用
- generate_scene_assets：生成分镜参考图或资产
- inspect_scene_results：检查已生成的分镜结果
- patch_scene：修改指定镜头或替换局部素材
- generate_scenes：生成或重新生成指定视频镜头

【场景包与参考图】
- workspace_digest.has_scene_packages=true 且尚无参考图时，
  用户说「没有参考图 / 直接生成 / 生成参考图」→ target_capability=generate_scene_assets，
  intent=continue_images；不要写成 generate_scenes 或「直接生成视频」。
- workspace_digest.scene_asset_status=partial/failed 时，说明参考图仅完成
  scene_asset_ready_count/scene_asset_required_count；用户说「继续生成」时必须判定为
  target_capability=generate_scene_assets、intent=continue_images，保留 scene_asset_missing_targets。
- 引导选生图模型时：只推荐 workspace_digest.registered_scene_asset_image_models
  （当前 Borgrise：image-2 / Seedream 5.0），禁止推荐 Midjourney、DALL·E、Stable Diffusion。
- 只有 workspace_digest.scene_assets_ready=true 后，确认生成分镜视频才进入 generate_scenes；
  has_scene_asset_images=true 只表示至少有一张图，不能当作全部就绪。
- review_generated_scenes：检查生成后的视频镜头
- compose_video：拼接并导出视频
- inspect_workspace：只查询工作区状态

【状态判断原则】
- 先判断用户目标，再判断当前状态；不要从固定阶段顺序反推意图。
- 已存在的脚本、分镜、角色、场景、道具、图片、视频和 Operation
  必须以 workspace_digest 为准。
- 用户要求修改、检查或重生成已有结果时，优先处理指定对象，
  不要重新开始整个创作流程。
- Tool 执行结果存在但未检查时，目标能力应为相应的 inspect。
- 检查发现局部问题时，标记受影响 scene_ids，禁止扩大到全部镜头。
- blocking_confirmation 存在时，不得声明已经通过确认。
- 计费生成前缺确认、成本确认或必要素材时，状态必须为 blocked
  或 waiting_confirmation。
- 不确定的信息保持 unknown，不得编造默认值。

【脚本与生产字段】
- 成熟分镜脚本包含连续镜头、时间码及画面或对白描述。
- 整片时长取连续时间码末尾或用户明确给出的总时长。
- 画幅只允许 9:16、16:9、1:1。
- “结尾不变/沿用”表示 ending_cta=keep。
- “不需要结尾 CTA”表示 ending_cta=none。
- 只有目标能力确实依赖某字段时，才将其加入 missing。

【输出限制】
- 不输出思考过程、分析过程或规则复述。
- 不输出完整脚本。
- 不选择 Tool，不输出 steps。
- answer 只陈述当前结论和下一项业务能力，不得声称已经执行。
- progress 只能描述正在核对的业务事实，不得输出内部推理、系统提示词、规则原文或工具名。
- progress 使用自然、简短的中文，每条 10–50 字；不得重复。

【输出协议】
只输出 NDJSON，不输出 Markdown 代码块或其他文本。每条记录独占一行，并立即结束该行：
1. 先输出 1–3 条 progress；每得到一个对用户有用的阶段判断就立即输出，不要等最终结果。
2. 最后一行必须输出且只能输出一条 result；result 后停止输出。

{"type":"progress","message":"正在识别脚本类型和本轮目标。"}
{"type":"progress","message":"已找到现有脚本，正在核对生产字段。"}
{"type":"result","data":{"answer":"用户可见结论","intent":"create|polish|continue_assets|continue_images|continue_video|patch_scene|inspect|clarify","target_capability":"允许的目标能力之一","readiness":"ready|blocked|waiting_confirmation|inspect_required","current_state":{"script_available":true,"script_confirmed":false,"storyboard_available":false,"scene_packages_available":false,"scene_assets_available":false,"scene_videos_available":false,"final_video_available":false},"missing":[],"facts":{"duration_sec":180,"aspect_ratio":"9:16","ending_cta":"keep|none|present|unknown","scene_ids":[]},"constraints":{"dirty_scene_only":false,"requires_visual_inspection":false}}}
"""


def fold_thinking_history_from_events(
    events: Sequence[Any],
    *,
    max_text_chars: int = 80_000,
) -> list[dict[str, Any]]:
    """从持久化 AgentEvent 折叠可回显的思考历史（刷新恢复权威来源）。

    同时支持旧 agent.thinking.* 与原生 agent.reasoning_summary.* / agent.response.*。
    """

    by_turn: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def ensure_turn(turn_id: str, *, title: str = "思考中") -> dict[str, Any]:
        current = by_turn.get(turn_id)
        if current is not None:
            return current
        order.append(turn_id)
        created = {
            "turn_id": turn_id,
            "title": title,
            "subtitle": "",
            "text": "",
            "answer": "",
            "started_at": None,
            "status": "streaming",
        }
        by_turn[turn_id] = created
        return created

    for event in events:
        event_type = getattr(event, "type", None)
        type_value = getattr(event_type, "value", event_type)
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            continue
        turn_id = str(payload.get("turn_id") or "").strip()
        if not turn_id:
            continue
        if type_value == "agent.thinking.started":
            current = ensure_turn(turn_id)
            current["title"] = str(payload.get("title") or "").strip() or "思考中"
            current["subtitle"] = str(payload.get("subtitle") or "").strip()
            current["started_at"] = payload.get("started_at")
            current["status"] = "streaming"
            continue
        if type_value == "agent.thinking.delta":
            current = by_turn.get(turn_id)
            if current is None:
                continue
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                continue
            channel = str(payload.get("channel") or "reasoning").strip() or "reasoning"
            key = "answer" if channel == "answer" else "text"
            merged = f"{current[key]}{delta}"
            if len(merged) > max_text_chars:
                merged = merged[:max_text_chars]
            current[key] = merged
            continue
        if type_value == "agent.thinking.completed":
            current = by_turn.get(turn_id)
            if current is None:
                continue
            current["status"] = "completed"
            continue
        # 原生 VideoAgent：思考摘要与公开回答。
        if type_value == "agent.reasoning_summary.delta":
            current = ensure_turn(turn_id, title="思考中")
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                continue
            if current["started_at"] is None:
                current["started_at"] = getattr(event, "occurred_at", None)
            merged = f"{current['text']}{delta}"
            if len(merged) > max_text_chars:
                merged = merged[:max_text_chars]
            current["text"] = merged
            continue
        if type_value == "agent.reasoning_summary.completed":
            current = ensure_turn(turn_id, title="思考中")
            summary = payload.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                summary = payload.get("text")
            if isinstance(summary, str) and summary.strip():
                current["text"] = summary.strip()[:max_text_chars]
            if current["started_at"] is None:
                current["started_at"] = getattr(event, "occurred_at", None)
            continue
        if type_value == "agent.response.delta":
            current = ensure_turn(turn_id, title="回复中")
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                continue
            merged = f"{current['answer']}{delta}"
            if len(merged) > max_text_chars:
                merged = merged[:max_text_chars]
            current["answer"] = merged
            continue
        if type_value == "agent.response.completed":
            current = ensure_turn(turn_id, title="回复完成")
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                current["answer"] = text.strip()[:max_text_chars]
            current["status"] = "completed"
            continue
    return [by_turn[turn_id] for turn_id in order if turn_id in by_turn]


__all__ = [
    "IntakeThinkingResult",
    "ThinkingStreamPublisher",
    "fold_thinking_history_from_events",
    "stream_chat_tokens",
    "_parse_intake_answer_and_verdict",
    "_thinking_event_id",
]
