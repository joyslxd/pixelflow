"""使用官方 Python SDK 驱动固定 DeepSeek Harness Runtime 的 Engine Adapter。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import SidecarSettings
from .contracts import HarnessRunRequest
from .skill_snapshot import SkillCatalogSnapshot, snapshot_skill_root


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HarnessExecutionDiagnostic:
    """保存可审计但不含请求正文、响应或凭据的 Runtime 失败摘要。"""

    exception_type: str
    failure_phase: str
    timeout_phase: str | None


class HarnessExecutionError(RuntimeError):
    """将底层 Runtime 异常收敛为可安全公开的诊断合同。"""

    def __init__(self, diagnostic: HarnessExecutionDiagnostic) -> None:
        super().__init__("Harness Runtime 执行失败")
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class DeepSeekEngineResult:
    """保存已经过滤为 PixelFlow 可公开消费的真实模型执行结果。"""

    final_response: str
    finish_reason: str | None
    session_event_sequences: tuple[int, ...]
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
            sequences = tuple(
                event["seq"]
                for event in result.events
                if isinstance(event.get("seq"), int)
            )
            if not sequences or sequences != tuple(sorted(sequences)):
                raise RuntimeError("Harness Session 事件序号不连续或缺失")
            tool_names = tuple(
                str(data.get("name"))
                for event in result.events
                if event.get("type") == "tool/call"
                and isinstance((data := event.get("data")), dict)
                and isinstance(data.get("name"), str)
            )
            response = result.final_response.strip()
            if not response:
                raise RuntimeError("Harness 未返回可公开的最终回复")
            return DeepSeekEngineResult(
                final_response=response[:8_000],
                finish_reason=result.finish_reason,
                session_event_sequences=sequences,
                tool_names=tool_names,
            )
        except Exception as error:
            diagnostic = _execution_diagnostic(error, phase)
            logger.warning(
                "harness_execution_failed run_id=%s exception_type=%s failure_phase=%s timeout_phase=%s",
                run_id,
                diagnostic.exception_type,
                diagnostic.failure_phase,
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
        timeout_phase=phase if is_timeout else None,
    )
