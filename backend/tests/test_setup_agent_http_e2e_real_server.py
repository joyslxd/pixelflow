"""验证 ``setup_agent`` 真实 HTTP 链路的端到端合同。

测试通过 ``TestClient`` 驱动完整 FastAPI 网关，仅替换外部 LLM 与 content-app
鉴权 Client。认证中间件、ContextVar、运行时、工具分发与文件写入均使用生产代码，
任何一层丢失 ``user_id`` 都会使测试失败。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from _agent_e2e_helpers import FakeToolCallingModel, build_single_tool_call_model

_TEST_AUTHORIZATION = "Bearer setup-agent-e2e-token"
_TEST_USER_ID = "setup-agent-e2e-user"


def _build_fake_create_chat_model(agent_name: str):
    """Return a callable matching the real ``create_chat_model`` signature.

    Whenever the lead agent constructs a chat model during the bootstrap flow,
    we hand it a fake that emits a single setup_agent tool_call on its first
    turn, then a benign final answer on its second turn.
    """

    def fake_create_chat_model(*args: Any, **kwargs: Any) -> FakeToolCallingModel:
        return build_single_tool_call_model(
            tool_name="setup_agent",
            tool_args={
                "soul": f"# Real HTTP E2E SOUL for {agent_name}",
                "description": "real-http-e2e agent",
            },
            tool_call_id="call_real_http_1",
            final_text=f"Agent {agent_name} created via real HTTP e2e.",
        )

    return fake_create_chat_model


@pytest.fixture
def isolated_deer_flow_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stand up an isolated DeerFlow data root + config under tmp_path.

    - Sets ``DEER_FLOW_HOME`` so paths land under tmp_path, not the real
      ``.deer-flow`` directory.
    - Stages a copy of the project's ``config.yaml`` (or ``config.example.yaml``
      on a fresh CI checkout where ``config.yaml`` is gitignored) and pins
      ``DEER_FLOW_CONFIG_PATH`` to it, so lifespan boot doesn't depend on the
      developer's local config layout.
    - Sets a placeholder OPENAI_API_KEY because the config has
      ``$OPENAI_API_KEY`` that gets resolved at parse time; the LLM itself is
      mocked, so any non-empty value works.
    """
    home = tmp_path / "deer-flow-home"
    home.mkdir()
    monkeypatch.setenv("DEER_FLOW_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-not-used-because-llm-is-mocked")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid")

    # Hermetic config: do not depend on whether the dev machine has a real
    # ``config.yaml`` at the repo root. CI's ``actions/checkout`` only ships
    # ``config.example.yaml`` (and its ``models:`` list is commented out, so
    # AppConfig validation would reject it). Write a minimal, self-sufficient
    # config to tmp_path and pin ``DEER_FLOW_CONFIG_PATH`` to it.
    staged_config = tmp_path / "config.yaml"
    staged_config.write_text(_MINIMAL_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(staged_config))

    return home


# Minimal config that satisfies AppConfig + LeadAgent's _resolve_model_name.
# The model `use` path must resolve to a real class for config parsing to
# succeed; the test patches ``create_chat_model`` on the lead agent module,
# so the model is never actually instantiated. SandboxConfig.use is required
# at schema level; LocalSandboxProvider is the only sandbox that runs without
# Docker.
_MINIMAL_CONFIG_YAML = """\
log_level: info
models:
  - name: fake-test-model
    display_name: Fake Test Model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
    base_url: $OPENAI_API_BASE
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
agents_api:
  enabled: true
database:
  backend: sqlite
"""


def _reset_process_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset every process-wide cache that would survive across tests.

    This fixture stands up a full FastAPI app + sqlite DB + LangGraph runtime
    inside ``tmp_path``. To get true per-test isolation we have to invalidate
    a handful of module-level caches that production normally never resets,
    so they pick up our test-only ``DEER_FLOW_HOME`` and sqlite path:

    - ``deerflow.config.app_config`` caches the parsed ``config.yaml``.
    - ``deerflow.config.paths`` caches the ``Paths`` singleton derived from
      ``DEER_FLOW_HOME`` at first access.
    - ``deerflow.persistence.engine`` caches the SQLAlchemy engine and
      session factory after the first call to ``init_engine_from_config``.

    ``raising=False`` keeps the fixture resilient if upstream renames or
    drops one of these attributes — the test will simply skip that reset
    instead of failing with a confusing AttributeError, and the next test
    to call ``get_app_config()``/``get_paths()`` will surface the real
    incompatibility loudly.
    """
    from deerflow.config import app_config as app_config_module
    from deerflow.config import paths as paths_module
    from deerflow.persistence import engine as engine_module

    for module, attr in (
        (app_config_module, "_app_config"),
        (app_config_module, "_app_config_path"),
        (app_config_module, "_app_config_mtime"),
        (paths_module, "_paths_singleton"),
        (engine_module, "_engine"),
        (engine_module, "_session_factory"),
    ):
        monkeypatch.setattr(module, attr, None, raising=False)


@pytest.fixture
def isolated_app(isolated_deer_flow_home: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a fresh FastAPI app inside a clean DEER_FLOW_HOME.

    Each test gets its own sqlite DB and checkpoint store under ``tmp_path``,
    with no cross-test contamination.
    """
    _reset_process_singletons(monkeypatch)

    # Re-resolve the config from the test-only DEER_FLOW_HOME and pin its
    # sqlite path into tmp_path so the lifespan-time engine init lands there.
    from app.gateway import deps as deps_module
    from app.gateway.content_app_auth import ContentAppUser
    from deerflow.config import app_config as app_config_module

    cfg = app_config_module.get_app_config()
    cfg.database.sqlite_dir = str(isolated_deer_flow_home / "db")

    async def authenticate_test_authorization(authorization: str | None) -> ContentAppUser:
        """在 content-app Client 边界返回固定用户，保留真实认证传播链路。"""
        assert authorization == _TEST_AUTHORIZATION
        return ContentAppUser(id=_TEST_USER_ID, username=_TEST_USER_ID)

    monkeypatch.setattr(deps_module, "authenticate_authorization_header", authenticate_test_authorization)

    from app.gateway.app import create_app

    return create_app()


def _drain_stream(response, *, timeout: float = 30.0, max_bytes: int = 4 * 1024 * 1024) -> str:
    """Consume an SSE response body until the run terminates and return the text.

    Bounded to keep the test fail-fast:
      - Stops as soon as an ``event: end`` SSE frame is observed (the gateway
        sends this when the background run finishes — see ``services.format_sse``
        and ``StreamBridge.publish_end``).
      - Stops at ``timeout`` seconds wall-clock so a stuck run / runaway heartbeat
        loop surfaces a real failure instead of hanging pytest.
      - Stops at ``max_bytes`` so a runaway producer can't OOM the test process.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    body = b""
    for chunk in response.iter_bytes():
        body += chunk
        if b"event: end" in body:
            break
        if len(body) >= max_bytes:
            break
        if _time.monotonic() >= deadline:
            break
    return body.decode("utf-8", errors="replace")


def _wait_for_file(path: Path, *, timeout: float = 10.0) -> bool:
    """Block until *path* exists or *timeout* elapses.

    The run completes inside ``asyncio.create_task`` after start_run returns,
    so the test must wait for the background task to flush its writes.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if path.exists():
            return True
        _time.sleep(0.05)
    return False


@pytest.mark.no_auto_user
def test_real_http_create_agent_lands_in_authenticated_user_dir(
    isolated_app: Any,
    isolated_deer_flow_home: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """验证 content-app 用户身份能传到 ``setup_agent`` 的目标目录。

    1. 为 TestClient 配置 content-app Authorization。
    2. 按前端线协议创建线程并提交 ``/runs/stream``。
    3. 等待后台运行完成。
    4. 断言 SOUL.md 位于已认证用户目录。
    5. 断言默认用户目录没有同名 Agent。
    """
    # ``deerflow.agents.lead_agent.agent`` imports ``create_chat_model`` with
    # ``from deerflow.models import create_chat_model`` at module load time,
    # rebinding the symbol into its own namespace. So the only patch that
    # intercepts the call is the bound name on ``lead_agent.agent`` — patching
    # ``deerflow.models.create_chat_model`` would be too late.
    agent_name = "real-http-agent"

    from starlette.testclient import TestClient

    with (
        patch(
            "deerflow.agents.lead_agent.agent.create_chat_model",
            new=_build_fake_create_chat_model(agent_name),
        ),
        TestClient(isolated_app) as client,
    ):
        # 前端对所有网关请求都透传 content-app Authorization。
        client.headers.update({"Authorization": _TEST_AUTHORIZATION})
        current_user = client.get("/agent/auth/me")
        assert current_user.status_code == 200, current_user.text
        auth_uid = current_user.json()["id"]

        # ``/runs/stream`` 要求线程已存在，与前端 LangGraph SDK 行为一致。
        import uuid as _uuid

        thread_id = str(_uuid.uuid4())
        created = client.post(
            "/agent/threads",
            json={"thread_id": thread_id, "metadata": {}},
        )
        assert created.status_code == 200, created.text

        # 使用前端 bootstrap 请求体：
        #   thread.submit(input, {config, context}) ->
        #   POST /agent/threads/{id}/runs/stream body =
        #     {assistant_id, input, config, context}
        body = {
            "assistant_id": "lead_agent",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": (f"The new custom agent name is {agent_name}. Help me design its SOUL.md before saving it."),
                    }
                ]
            },
            "config": {"recursion_limit": 50},
            "context": {
                "agent_name": agent_name,
                "is_bootstrap": True,
                "mode": "flash",
                "thinking_enabled": False,
                "is_plan_mode": False,
                "subagent_enabled": False,
            },
            "stream_mode": ["values"],
        }
        # 消费 SSE，等待服务端后台任务完成后再检查磁盘。
        with client.stream(
            "POST",
            f"/agent/threads/{thread_id}/runs/stream",
            json=body,
        ) as resp:
            assert resp.status_code == 200, resp.read().decode()
            transcript = _drain_stream(resp)

        # 至少应产生一个 SSE 事件。
        assert "event:" in transcript, f"no SSE events in response: {transcript[:500]!r}"

        # 检查已认证用户目录，不允许回退到 default。
        expected_dir = isolated_deer_flow_home / "users" / auth_uid / "agents" / agent_name
        default_dir = isolated_deer_flow_home / "users" / "default" / "agents" / agent_name

        # 后台任务可能存在少量调度延迟，因此使用有界轮询。
        assert _wait_for_file(expected_dir / "SOUL.md", timeout=15.0), (
            "SOUL.md did not appear under users/<auth_uid>/agents/. "
            f"Expected: {expected_dir / 'SOUL.md'}. "
            f"tmp tree: {sorted(str(p.relative_to(isolated_deer_flow_home)) for p in isolated_deer_flow_home.rglob('SOUL.md'))}. "
            f"SSE transcript tail: {transcript[-1000:]!r}"
        )

        soul_text = (expected_dir / "SOUL.md").read_text()
        assert agent_name in soul_text, f"unexpected SOUL content: {soul_text!r}"

        # 关键回归断言：Agent 不能写入默认用户目录。
        assert not default_dir.exists(), f"REGRESSION: agent landed under users/default/{agent_name} instead of the authenticated user. Default-dir contents: {list(default_dir.rglob('*')) if default_dir.exists() else 'n/a'}"
