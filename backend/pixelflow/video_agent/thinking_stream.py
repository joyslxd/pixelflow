"""VideoAgent 真 LLM token 思考流：批量写入公开 SSE 事件。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
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
THINKING_PREAMBLE_TIMEOUT_SEC = 45.0
THINKING_REQUEST_TIMEOUT_SEC = 40.0
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
ALLOWED_INTAKE_MISSING = ("视频画幅", "结尾行动引导")
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
        subtitle: str = "AI 编剧思考中…",
    ) -> None:
        now = self._clock()
        self._last_flush = time.monotonic()
        await self._emit(
            AgentEventType.AGENT_THINKING_STARTED,
            {
                "turn_id": self._turn_id,
                "title": title.strip() or "正在分析素材，提炼电商属性并构思方向…",
                "subtitle": subtitle.strip() or "AI 编剧思考中…",
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

    优先 `astream`；若模型未暴露 astream，才退回一次性 invoke。
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
) -> Any:
    """创建强制开启 OpenAI 兼容 streaming 的聊天模型。

    DeepSeek V4 思考模式需在请求里带 extra_body.thinking；真正把
    reasoning_content 解析进 chunk 依赖配置里的 PatchedChatOpenAIReasoning。
    入场预热用 low + 短超时，避免 high 导致首包数分钟无输出。
    """

    thinking_kwargs: dict[str, Any] = {}
    if thinking_enabled:
        thinking_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        thinking_kwargs["reasoning_effort"] = "low"
        thinking_kwargs["max_tokens"] = THINKING_MAX_TOKENS
        thinking_kwargs["request_timeout"] = THINKING_REQUEST_TIMEOUT_SEC
    try:
        model = factory(
            thinking_enabled=thinking_enabled,
            streaming=True,
            **thinking_kwargs,
        )
    except TypeError:
        try:
            model = factory(thinking_enabled=thinking_enabled, **thinking_kwargs)
        except TypeError:
            try:
                model = factory(thinking_enabled=thinking_enabled, streaming=True)
            except TypeError:
                model = factory(thinking_enabled=False)
    # 配置里 when_thinking_enabled.reasoning_effort=high 会覆盖 kwargs；入场强制改回 low。
    if thinking_enabled:
        for attr, value in (
            ("reasoning_effort", "low"),
            ("max_tokens", THINKING_MAX_TOKENS),
            ("request_timeout", THINKING_REQUEST_TIMEOUT_SEC),
        ):
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
    return user_message, payload


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
    )


_INTAKE_SYSTEM_PROMPT = (
    "你是 PixelFlow 电商短视频工作台的上下文理解器（VideoAgent Intake）。\n"
    "你只负责：理解本轮全部上下文、提取事实和缺失项，并给出用户可见的当前判断。\n"
    "\n"
    "【判断要求】\n"
    "1) 先概括用户本轮真正想干什么（创作/导入润色/补字段/改第N镜/生资产/生图/成片/探查）。\n"
    "2) 对照 workspace：缺什么、卡在哪、能不能直接推进。\n"
    "3) 分镜「0—10秒」「170—180秒」是局部时间码；整片时长取连续时间码末尾或本轮「180s」；"
    "画幅看 9:16/16:9/1:1；「结尾不变」=沿用；「不需要结尾CTA」=已确认无CTA。\n"
    "4) 若缺画幅或 CTA，needs_user_reply=true，并明确列出 missing。\n"
    "5) 不要选择工具、不要生成执行步骤、不要承诺已执行或绕过确认、额度、权限。\n"
    "6) 不要输出最终完整脚本正文；不要编造未出现的产品/素材。\n"
    "\n"
    "【最终正文格式】\n"
    "A) 先写 1–3 句中文结论（直接给用户看）\n"
    "B) 紧接着输出机器块（供 Planner 读取，前端会剥离）：\n"
    "<<<INTAKE_CONTEXT>>>\n"
    "{\n"
    '  "answer": "<与上面中文结论一字不差>",\n'
    '  "needs_user_reply": false,\n'
    '  "missing": [],\n'
    '  "facts": {\n'
    '    "duration_sec": 180,\n'
    '    "aspect_ratio": "9:16",\n'
    '    "ending_cta": "keep|none|present|unknown",\n'
    '    "intent": "create|polish|continue_assets|continue_images|continue_video|patch_scene|inspect|clarify"\n'
    "  },\n"
    "}\n"
    "<<<END>>>\n"
    "needs_user_reply=true 时必须列出 missing。\n"
)


async def stream_intake_thinking(
    *,
    publisher: ThinkingStreamPublisher,
    content: str,
    materials: Sequence[Mapping[str, Any]] | None = None,
    workspace_digest: Mapping[str, Any] | None = None,
    blocking_confirmation: Mapping[str, Any] | None = None,
    model_factory: Callable[..., Any] | None = None,
) -> IntakeThinkingResult:
    """入场思考流：流式判断 → answer + context；失败不阻断 Turn。"""

    from deerflow.models import create_chat_model

    factory = model_factory or create_chat_model
    material_hint = ""
    if materials:
        names = [
            str(item.get("name") or item.get("filename") or "").strip()
            for item in materials
            if isinstance(item, Mapping)
        ]
        names = [name for name in names if name][:8]
        if names:
            material_hint = "素材文件：" + "、".join(names)

    raw = content.strip()
    fallback = IntakeThinkingResult(
        user_message="已完成初步判断，继续生成执行方案。",
        entry_path=None,
        needs_user_reply=False,
    )

    try:
        await publisher.start(
            title="正在结合上下文判断本轮意图与下一步…",
            subtitle="AI 编剧思考中…",
        )
        await publisher.flush()

        model = _create_streaming_chat_model(factory, thinking_enabled=True)
        human_parts = [f"【本轮用户输入】\n{_truncate_for_thinking(raw)}\n"]
        if material_hint:
            human_parts.append(f"【{material_hint}】\n")
        if workspace_digest:
            human_parts.append(
                "【workspace_digest】\n"
                f"{json.dumps(dict(workspace_digest), ensure_ascii=False)[:1_200]}\n"
            )
        if blocking_confirmation:
            human_parts.append(
                "【blocking_confirmation】\n"
                f"{json.dumps(dict(blocking_confirmation), ensure_ascii=False)[:600]}\n"
            )
        else:
            human_parts.append("【blocking_confirmation】\nnull\n")

        async def on_reasoning(delta: str) -> None:
            await publisher.push_delta(delta, channel="reasoning")

        answer_parts: list[str] = []

        async def on_content(delta: str) -> None:
            answer_parts.append(delta)

        reasoning, answer = await stream_chat_tokens(
            model=model,
            messages=[
                ("system", _INTAKE_SYSTEM_PROMPT),
                ("human", "\n".join(human_parts)),
            ],
            on_reasoning=on_reasoning,
            on_content=on_content,
            timeout_sec=THINKING_PREAMBLE_TIMEOUT_SEC,
        )
        combined_answer = "".join(answer_parts) or (answer or "")
        result = _parse_intake_answer_and_verdict(combined_answer)
        if not combined_answer.strip() and not reasoning:
            result = fallback
        await publisher.push_delta(result.user_message, channel="answer")
        logger.info(
            "入场思考流完成 turn_id=%s reasoning_chars=%s answer_chars=%s "
            "entry_path=%s intent=%s missing=%s",
            publisher._turn_id,
            len(reasoning or ""),
            len(result.user_message),
            result.entry_path,
            result.intent,
            list(result.missing_requirements),
        )
        return result
    except TimeoutError:
        logger.warning(
            "入场思考流超时 turn_id=%s，保留已推送片段并继续",
            publisher._turn_id,
        )
        try:
            await publisher.push_delta(
                "\n思考预热超时，先按现有判断继续。",
                channel="answer",
            )
        except Exception:  # noqa: BLE001
            pass
        return IntakeThinkingResult(
            user_message="思考预热超时，先按现有判断继续。",
            entry_path=None,
            needs_user_reply=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "入场思考流失败 turn_id=%s error_type=%s",
            publisher._turn_id,
            type(exc).__name__,
            exc_info=True,
        )
        try:
            await publisher.push_delta(
                "思考流暂时中断，继续生成执行方案。",
                channel="answer",
            )
        except Exception:  # noqa: BLE001
            pass
        return IntakeThinkingResult(
            user_message="思考流暂时中断，继续生成执行方案。",
            entry_path=None,
            needs_user_reply=False,
        )
    finally:
        try:
            await publisher.complete()
        except Exception:  # noqa: BLE001
            try:
                await publisher.flush()
            except Exception:  # noqa: BLE001
                pass


def fold_thinking_history_from_events(
    events: Sequence[Any],
    *,
    max_text_chars: int = 80_000,
) -> list[dict[str, Any]]:
    """从持久化 AgentEvent 折叠可回显的思考历史（刷新恢复权威来源）。"""

    by_turn: dict[str, dict[str, Any]] = {}
    order: list[str] = []
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
            if turn_id not in by_turn:
                order.append(turn_id)
            by_turn[turn_id] = {
                "turn_id": turn_id,
                "title": str(payload.get("title") or "").strip()
                or "正在分析素材，提炼电商属性并构思方向…",
                "subtitle": str(payload.get("subtitle") or "").strip() or "AI 编剧思考中…",
                "text": "",
                "answer": "",
                "started_at": payload.get("started_at"),
                "status": "streaming",
            }
            continue
        current = by_turn.get(turn_id)
        if current is None:
            continue
        if type_value == "agent.thinking.delta":
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
            current["status"] = "completed"
    return [by_turn[turn_id] for turn_id in order if turn_id in by_turn]


__all__ = [
    "IntakeThinkingResult",
    "ThinkingStreamPublisher",
    "fold_thinking_history_from_events",
    "stream_chat_tokens",
    "stream_intake_thinking",
    "_parse_intake_answer_and_verdict",
    "_thinking_event_id",
]
