"""使用官方 Python SDK 驱动固定 DeepSeek Harness Runtime 的 Engine Adapter。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import jwt

from .config import SidecarSettings
from .contracts import HarnessRunRequest
from .skill_snapshot import SkillCatalogSnapshot, snapshot_skill_root


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HarnessExecutionDiagnostic:
    """保存可审计但不含请求正文、响应或凭据的 Runtime 失败摘要。"""

    exception_type: str
    failure_phase: str
    failure_reason: str | None
    timeout_phase: str | None


class HarnessExecutionError(RuntimeError):
    """将底层 Runtime 异常收敛为可安全公开的诊断合同。"""

    def __init__(self, diagnostic: HarnessExecutionDiagnostic) -> None:
        super().__init__("Harness Runtime 执行失败")
        self.diagnostic = diagnostic


class HarnessProjectionError(RuntimeError):
    """标识公开结果投影的固定失败原因，禁止携带 SDK 原始正文。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__("Harness 结果投影失败")
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class DeepSeekEngineResult:
    """保存已经过滤为 PixelFlow 可公开消费的真实模型执行结果。"""

    final_response: str
    finish_reason: str | None
    tool_names: tuple[str, ...]
    suspension_kind: str | None = None
    suspension_interrupt_id: str | None = None
    response_deltas: tuple[str, ...] = field(default_factory=tuple)
    public_summaries: tuple[str, ...] = field(default_factory=tuple)
    notification_events_emitted: bool = False


class DeepSeekHarnessEngine:
    """将一次 PixelFlow Run 映射为一次隔离的官方 Harness Session。"""

    engine_id = "deepseek-harness"
    engine_version = "0.1.1rc1"

    def __init__(self, settings: SidecarSettings, cordis_path: Path) -> None:
        """保存只读启动配置；每次 Run 都启动独立 Runtime，避免跨 Run 共享业务上下文。"""

        self._settings = settings
        self._cordis_path = cordis_path

    def snapshot_skills(self) -> SkillCatalogSnapshot:
        """在 Run 接受边界读取管理员 Skill；执行阶段不得再次读取该目录。"""

        return snapshot_skill_root(self._settings.agent_home / "skills")

    def validate_request(self, request: HarnessRunRequest) -> None:
        """在持久化 accepted Run 前验证模型与限制快照，拒绝漂移请求。"""

        if request.model.profile_name != self._settings.model_profile_name:
            raise ValueError("模型档案与 Sidecar 启动配置不匹配")
        if request.model.profile_digest != self._settings.model_profile_digest:
            raise ValueError("模型档案摘要与 Sidecar 启动配置不匹配")
        self._settings.validate_run_limits(request.limits)

    async def execute(
        self,
        run_id: str,
        request: HarnessRunRequest,
        skill_snapshot: SkillCatalogSnapshot,
        on_public_event: Callable[[str, dict[str, str]], None] | None = None,
    ) -> DeepSeekEngineResult:
        """在工作线程运行阻塞 SDK，模型调用只使用进程注入的测试或生产 Secret。"""

        self.validate_request(request)
        return await asyncio.to_thread(
            self._execute_blocking, run_id, request, skill_snapshot, on_public_event,
        )

    def _execute_blocking(
        self,
        run_id: str,
        request: HarnessRunRequest,
        skill_snapshot: SkillCatalogSnapshot,
        on_public_event: Callable[[str, dict[str, str]], None] | None,
    ) -> DeepSeekEngineResult:
        """执行一个真实 Harness Run，不持久化 API key、用户正文或原始 Session 事件。"""

        from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

        phase = "runtime_initialization"
        harness: DeepSeekHarness | None = None
        try:
            # 活动 Skill 根由部署编排只读挂载；Runtime 的会话与 Run 快照必须写入独立持久卷。
            runtime_root = self._settings.run_store_path.parent / "runtime"
            session_root = runtime_root / "sessions"
            session_root.mkdir(parents=True, exist_ok=True)
            run_home = runtime_root / "run-skill-snapshots" / run_id
            skill_snapshot.materialize(run_home)
            # 该文本只在本次模型请求中存在；Sidecar Repository 只保存 request_digest。
            model_input = (
                f"【PixelFlow 安全指令】\n{request.context.system_instruction}\n\n"
                f"【用户请求】\n{request.context.user_input}\n\n"
                "【输出要求】\n完成内部推理后，必须输出一条简洁、可公开的最终文本回复；"
                "不得只产生 reasoning 或工具过程。"
            )
            manifest_json = self._load_frozen_tool_manifest(request)
            harness = DeepSeekHarness(
                build_deepseek_harness_config(
                    DeepSeekHarnessConfig,
                    settings=self._settings,
                    request=request,
                    run_id=run_id,
                    run_home=run_home,
                    session_root=session_root,
                    cordis_path=self._cordis_path,
                    manifest_json=manifest_json,
                )
            )
            phase = "model_execution"
            emitted_notification_event = False
            notification_events: list[object] = []

            def on_notification(notification: object) -> None:
                nonlocal emitted_notification_event
                payload = getattr(notification, "payload", None)
                source_event = payload.get("event") if isinstance(payload, dict) else None
                if isinstance(source_event, dict):
                    notification_events.append(source_event)
                for event_type, payload in _public_events_from_notification(notification):
                    emitted_notification_event = True
                    if on_public_event is not None:
                        on_public_event(event_type, payload)

            result = harness.run(model_input, session_id=request.session_id, on_notification=on_notification)
            phase = "runtime_cleanup"
            harness.close()
            harness = None
            phase = "result_projection"
            return _project_harness_result(
                result,
                notification_events_emitted=emitted_notification_event,
                notification_events=notification_events,
            )
        except Exception as error:
            diagnostic = _execution_diagnostic(error, phase)
            logger.warning(
                "harness_execution_failed run_id=%s exception_type=%s failure_phase=%s failure_reason=%s timeout_phase=%s",
                run_id,
                diagnostic.exception_type,
                diagnostic.failure_phase,
                diagnostic.failure_reason or "none",
                diagnostic.timeout_phase or "none",
            )
            raise HarnessExecutionError(diagnostic) from error
        finally:
            if harness is not None:
                try:
                    harness.close()
                except Exception:
                    # 已有主异常时清理失败不覆盖原始阶段；日志只保留固定类型，不输出底层文本。
                    logger.warning("harness_cleanup_failed run_id=%s", run_id)

    def _load_frozen_tool_manifest(self, request: HarnessRunRequest) -> str:
        """从受服务 JWT 保护的 Broker 获取 Manifest，并在启动 Session 前校验冻结摘要。"""

        now = int(time.time())
        token = jwt.encode(
            {
                "sub": "pixelflow-harness-sidecar",
                "iss": self._settings.tool_broker_jwt_issuer,
                "aud": self._settings.tool_broker_jwt_audience,
                "service_instance_id": self._settings.sidecar_instance_id,
                "iat": now,
                "exp": now + 60,
            },
            self._settings.tool_broker_jwt_signing_key,
            algorithm="HS256",
        )
        request_url = f"{self._settings.tool_broker_base_url}/agent/internal/agent-tools/manifest"
        try:
            with urlopen(  # noqa: S310 - URL 已由 Sidecar 启动配置的内部地址校验。
                Request(request_url, headers={"Authorization": f"Bearer {token}"}),
                timeout=10,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError, UnicodeDecodeError) as error:
            raise HarnessProjectionError("tool_manifest_unavailable") from error
        if (
            not isinstance(payload, dict)
            or payload.get("protocol_version") != "v1"
            or payload.get("version") != request.toolset.version
            or payload.get("digest") != request.toolset.manifest_digest
            or not isinstance(payload.get("tools"), list)
        ):
            raise HarnessProjectionError("tool_manifest_drift")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_deepseek_harness_config(
    config_factory: Callable[..., object],
    *,
    settings: SidecarSettings,
    request: HarnessRunRequest,
    run_id: str,
    run_home: Path,
    session_root: Path,
    cordis_path: Path,
    manifest_json: str,
) -> object:
    """集中组装官方 SDK 配置，确保 Sidecar 与真实验收使用同一模型路由合同。"""

    environment = {
        "DSH_HOME": str(run_home),
        "DSH_AGENTS_HOME": str(run_home / "agents-home"),
        "DSH_SESSION_ROOT": str(session_root),
        "DEEPSEEK_API_KEY": os.environ["DEEPSEEK_API_KEY"],
        "DEEPSEEK_BASE_URL": os.environ["DEEPSEEK_BASE_URL"],
        "PIXELFLOW_TOOL_BROKER_BASE_URL": settings.tool_broker_base_url,
        "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": settings.tool_broker_jwt_signing_key,
        "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": settings.tool_broker_jwt_issuer,
        "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": settings.tool_broker_jwt_audience,
        "PIXELFLOW_SIDECAR_INSTANCE_ID": settings.sidecar_instance_id,
        "PIXELFLOW_HARNESS_RUN_ID": run_id,
        "PIXELFLOW_HARNESS_SESSION_ID": request.session_id,
        "PIXELFLOW_HARNESS_CONTEXT_DIGEST": request.binding.context_digest,
        "PIXELFLOW_HARNESS_TOOLSET_VERSION": request.toolset.version,
        "PIXELFLOW_HARNESS_TOOL_MANIFEST_JSON": manifest_json,
        "PIXELFLOW_HARNESS_WORKSPACE_REVISION": str(request.binding.workspace_revision),
        "PIXELFLOW_HARNESS_MAX_MODEL_STEPS": str(request.limits.max_model_steps),
        "PIXELFLOW_HARNESS_MAX_BUSINESS_TOOLS": str(request.limits.max_business_tools),
        # 用途：把 Gateway 冻结的计费批次上限传给 Sidecar Policy；影响：模型无法在单个 Run 内绕过 M5 费用边界。
        "PIXELFLOW_HARNESS_MAX_BILLABLE_BATCH_STARTS": str(request.limits.max_billable_batch_starts),
        "PIXELFLOW_HARNESS_DEADLINE_SECONDS": str(request.limits.deadline_seconds),
    }
    return config_factory(
        provider="deepseek-official",
        model=settings.model_id,
        max_tokens=request.model.max_output_tokens,
        cwd=str(run_home),
        session_root=str(session_root),
        cordis=str(cordis_path),
        env=environment,
        request_timeout_seconds=settings.request_timeout_seconds,
    )


def _execution_diagnostic(error: Exception, phase: str) -> HarnessExecutionDiagnostic:
    """把 Runtime 失败压缩为固定字段，禁止把异常正文或链路细节发送到事件流。"""

    exception_type = type(error).__name__
    timeout_names = {
        "TimeoutError",
        "TimeoutExpired",
        "ConnectTimeout",
        "ReadTimeout",
        "ReadTimeoutError",
        "WriteTimeout",
    }
    is_timeout = isinstance(error, TimeoutError) or exception_type in timeout_names
    return HarnessExecutionDiagnostic(
        exception_type=exception_type if exception_type.isidentifier() else "RuntimeError",
        failure_phase=phase,
        failure_reason=(error.reason_code if isinstance(error, HarnessProjectionError) else None),
        timeout_phase=phase if is_timeout else None,
    )


def _project_harness_result(
    result: object,
    *,
    notification_events_emitted: bool = False,
    notification_events: list[object] | None = None,
) -> DeepSeekEngineResult:
    """投影 SDK 公开结果，不依赖不会暴露给 PixelFlow 的 Session 内部序号。"""

    runtime_events = getattr(result, "events", None)
    if not isinstance(runtime_events, list):
        raise HarnessProjectionError("events_invalid")
    # 某些已部署 Runtime 只经 Session notification 发送最终 message；与 run() 返回
    # 的同一 Run 事件合并后再按既有白名单投影，不把 notification 私有字段公开。
    events = [*runtime_events, *(notification_events or [])]
    tool_names = tuple(
        str(data.get("name"))
        for event in events
        if isinstance(event, dict)
        and event.get("type") == "tool/call"
        and isinstance((data := event.get("data")), dict)
        and isinstance(data.get("name"), str)
    )
    suspension = _suspension_from_tool_events(events)
    if suspension is not None:
        return DeepSeekEngineResult(
            final_response="",
            finish_reason="suspended",
            tool_names=tool_names,
            suspension_kind=suspension[0],
            suspension_interrupt_id=suspension[1],
            notification_events_emitted=notification_events_emitted,
        )
    response = _public_text_from_result(result)
    if not response:
        response = _public_text_from_chunks(events).strip()
    if not response:
        response = _public_text_from_final_messages(events).strip()
    if not response:
        logger.warning(
            "harness_final_response_missing result_event_types=%s notification_event_types=%s finish_reason=%s",
            _safe_event_types(runtime_events),
            _safe_event_types(notification_events or []),
            getattr(result, "finish_reason", None),
        )
        raise HarnessProjectionError("final_response_missing")
    finish_reason = getattr(result, "finish_reason", None)
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise HarnessProjectionError("finish_reason_invalid")
    # Runtime 直接透传 ``turn/end.reason.kind``；不同 Provider 对正常文本收束分别
    # 使用 completed、complete、stop 或 end_turn。统一为 PixelFlow 的 completed，
    # 未知结束值仍由 RunService fail-closed，不能被静默当作成功。
    normal_finish_reasons = {"completed", "complete", "stop", "end_turn"}
    if finish_reason in normal_finish_reasons:
        finish_reason = "completed"
    return DeepSeekEngineResult(
        final_response=response[:8_000],
        finish_reason=finish_reason,
        tool_names=tool_names,
        response_deltas=_safe_response_chunks(response[:8_000]),
        # 公开摘要只能由可审计的 Tool 名称派生，绝不转发模型 reasoning。
        public_summaries=tuple(
            f"正在完成工具：{tool_name}" for tool_name in tool_names[:3]
        ),
        notification_events_emitted=notification_events_emitted,
    )


def _public_text_from_result(result: object) -> str:
    """兼容 Harness 版本的最终公开文本字段，绝不从 reasoning 或任意对象字符串化。"""

    for field in ("final_response", "response", "output", "final_output", "text"):
        value = getattr(result, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _safe_event_types(events: list[object]) -> tuple[str, ...]:
    """诊断仅保留有限事件类型，不把模型内容或 Tool 参数写入日志。"""

    return tuple(
        event_type
        for event in events[:128]
        if isinstance(event, dict)
        and isinstance((event_type := event.get("type")), str)
        and 0 < len(event_type) <= 128
    )


def _suspension_from_tool_events(events: list[object]) -> tuple[str, str | None] | None:
    """只识别 Tool Runtime 返回的结构化挂起结果，绝不从模型文本推断业务状态。"""

    allowed = {"pending_operation", "awaiting_confirmation", "authorization_required"}
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in {"tool/result", "tool_result"}:
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        for key in ("result", "output", "value", "observation"):
            candidate = data.get(key)
            if not isinstance(candidate, dict):
                continue
            status = candidate.get("status")
            suspension = candidate.get("suspension")
            if status in allowed and isinstance(suspension, dict) and suspension.get("kind") == status:
                interrupt_id = suspension.get("interrupt_id")
                if status in {"awaiting_confirmation", "authorization_required"}:
                    if not isinstance(interrupt_id, str) or not interrupt_id.strip() or len(interrupt_id) > 128:
                        raise HarnessProjectionError("suspension_interrupt_id_invalid")
                    return status, interrupt_id
                return status, None
    return None


def _public_events_from_notification(notification: object) -> tuple[tuple[str, dict[str, str]], ...]:
    """仅把公开文本与 Tool 名称映射为实时事件，推理和原始参数一律丢弃。"""

    if getattr(notification, "method", None) != "session.event":
        return ()
    payload = getattr(notification, "payload", None)
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return ()
    event_type = event.get("type")
    data = event.get("data")
    if event_type == "assistant/chunk" and isinstance(data, dict):
        return tuple(
            ("response.delta", {"delta": value})
            for chunk in _nested_text_delta_chunks(data)
            for value in _text_delta_values(chunk)
            if value
        )
    if event_type == "tool/call" and isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str) and name and len(name) <= 128:
            return (("public_summary.delta", {"delta": f"正在调用工具：{name}"}),)
    return ()


def _safe_response_chunks(response: str, *, max_chunk_chars: int = 320) -> tuple[str, ...]:
    """把最终公开回复切成有界增量，禁止把 Runtime 私有块带入事件流。"""

    text = response.strip()
    if not text:
        return ()
    return tuple(text[index:index + max_chunk_chars] for index in range(0, len(text), max_chunk_chars))


def _public_text_from_chunks(events: list[object]) -> str:
    """仅聚合 Runtime 明确标记的公开文本块，忽略 reasoning、usage 和工具私有块。"""

    parts: list[str] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "assistant/chunk":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        for chunk in _nested_text_delta_chunks(data):
            parts.extend(_text_delta_values(chunk))
    return "".join(parts)


def _public_text_from_final_messages(events: list[object]) -> str:
    """读取 Runtime 明确标记的最终公开消息，避免遗漏流式结果的最终文本。"""

    parts: list[str] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "assistant/message":
            continue
        data = event.get("data")
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in {"text", "text-delta"}:
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _nested_text_delta_chunks(value: object) -> tuple[dict[str, object], ...]:
    """遍历 SDK chunk 包装层，只返回明确标记的公开文本字典。"""

    if isinstance(value, dict):
        # Runtime 0.1 同时使用历史 text-delta 与稳定 text-chunks；reasoning-chunks
        # 必须留在白名单之外，防止推理文本进入 Gateway Event/Snapshot。
        current = (value,) if value.get("type") in {"text-delta", "text-chunks"} else ()
        nested = tuple(
            chunk
            for child in value.values()
            for chunk in _nested_text_delta_chunks(child)
        )
        return current + nested
    if isinstance(value, list):
        return tuple(chunk for child in value for chunk in _nested_text_delta_chunks(child))
    return ()


def _text_delta_values(value: object) -> tuple[str, ...]:
    """从已确认的 text-delta 节点内递归提取公开文本字段。"""

    if isinstance(value, dict):
        values = tuple(
            child
            for key, child in value.items()
            if key in {"delta", "text", "content"} and isinstance(child, str)
        )
        chunk_values = tuple(
            item
            for key, child in value.items()
            if key == "chunks" and isinstance(child, list)
            for item in child
            if isinstance(item, str)
        )
        nested = tuple(
            item
            for child in value.values()
            for item in _text_delta_values(child)
        )
        return values + chunk_values + nested
    if isinstance(value, list):
        return tuple(item for child in value for item in _text_delta_values(child))
    return ()
