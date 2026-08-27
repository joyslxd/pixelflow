"""使用官方 Python SDK 驱动固定 DeepSeek Harness Runtime 的 Engine Adapter。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
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

    async def execute(
        self,
        run_id: str,
        request: HarnessRunRequest,
        skill_snapshot: SkillCatalogSnapshot,
    ) -> DeepSeekEngineResult:
        """在工作线程运行阻塞 SDK，模型调用只使用进程注入的测试或生产 Secret。"""

        if request.model.profile_name != self._settings.model_profile_name:
            raise ValueError("模型档案与 Sidecar 启动配置不匹配")
        return await asyncio.to_thread(self._execute_blocking, run_id, request, skill_snapshot)

    def _execute_blocking(
        self,
        run_id: str,
        request: HarnessRunRequest,
        skill_snapshot: SkillCatalogSnapshot,
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
                f"【用户请求】\n{request.context.user_input}"
            )
            manifest_json = self._load_frozen_tool_manifest(request)
            harness = DeepSeekHarness(
                DeepSeekHarnessConfig(
                    provider="deepseek-official",
                    model=self._settings.model_id,
                    max_tokens=request.model.max_output_tokens,
                    cwd=str(run_home),
                    session_root=str(session_root),
                    cordis=str(self._cordis_path),
                    env={
                        "DSH_HOME": str(run_home),
                        "DSH_AGENTS_HOME": str(run_home / "agents-home"),
                        "DSH_SESSION_ROOT": str(session_root),
                        "PIXELFLOW_TOOL_BROKER_BASE_URL": self._settings.tool_broker_base_url,
                        "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": self._settings.tool_broker_jwt_signing_key,
                        "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": self._settings.tool_broker_jwt_issuer,
                        "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": self._settings.tool_broker_jwt_audience,
                        "PIXELFLOW_SIDECAR_INSTANCE_ID": self._settings.sidecar_instance_id,
                        "PIXELFLOW_HARNESS_RUN_ID": run_id,
                        "PIXELFLOW_HARNESS_SESSION_ID": request.session_id,
                        "PIXELFLOW_HARNESS_CONTEXT_DIGEST": request.binding.context_digest,
                        "PIXELFLOW_HARNESS_TOOLSET_VERSION": request.toolset.version,
                        "PIXELFLOW_HARNESS_TOOL_MANIFEST_JSON": manifest_json,
                        "PIXELFLOW_HARNESS_WORKSPACE_REVISION": str(request.binding.workspace_revision),
                    },
                    request_timeout_seconds=self._settings.request_timeout_seconds,
                )
            )
            phase = "model_execution"
            result = harness.run(model_input, session_id=request.session_id)
            phase = "runtime_cleanup"
            harness.close()
            harness = None
            phase = "result_projection"
            return _project_harness_result(result)
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


def _project_harness_result(result: object) -> DeepSeekEngineResult:
    """投影 SDK 公开结果，不依赖不会暴露给 PixelFlow 的 Session 内部序号。"""

    events = getattr(result, "events", None)
    if not isinstance(events, list):
        raise HarnessProjectionError("events_invalid")
    tool_names = tuple(
        str(data.get("name"))
        for event in events
        if isinstance(event, dict)
        and event.get("type") == "tool/call"
        and isinstance((data := event.get("data")), dict)
        and isinstance(data.get("name"), str)
    )
    final_response = getattr(result, "final_response", None)
    response = final_response.strip() if isinstance(final_response, str) else ""
    if not response:
        response = _public_text_from_chunks(events).strip()
    if not response:
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
    )


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
