"""Run 生命周期服务层。

这里集中处理创建 run、组装 SSE 帧、消费 StreamBridge 事件等通用逻辑。Router
模块（``thread_runs``、``runs``）只保留薄 HTTP 处理，把真正的 run 生命周期
逻辑委托到这里。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import convert_to_messages

from app.gateway.deps import get_run_context, get_run_manager, get_stream_bridge
from app.gateway.utils import sanitize_log_param
from deerflow.config.app_config import get_app_config
from deerflow.runtime import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    ConflictError,
    DisconnectMode,
    RunManager,
    RunRecord,
    RunStatus,
    StreamBridge,
    UnsupportedStrategyError,
    run_agent,
)
from deerflow.runtime.runs.naming import resolve_root_run_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE 格式化
# ---------------------------------------------------------------------------


def format_sse(event: str, data: Any, *, event_id: str | None = None) -> str:
    """格式化单条 SSE 帧。

    字段顺序是 ``event:`` -> ``data:`` -> 可选 ``id:`` -> 空行。这个格式要和
    LangGraph Platform 的 wire format 保持一致，前端 ``useStream`` hook 和
    Python ``langgraph-sdk`` 的 SSE decoder 都按这个格式解析。
    """
    payload = json.dumps(data, default=str, ensure_ascii=False)
    parts = [f"event: {event}", f"data: {payload}"]
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append("")
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 输入和配置辅助函数
# ---------------------------------------------------------------------------


def normalize_stream_modes(raw: list[str] | str | None) -> list[str]:
    """把 stream_mode 参数归一为列表。

    默认值按当前网关约定返回 ``values``，满足前端消费状态更新的主路径。
    """
    if raw is None:
        return ["values"]
    if isinstance(raw, str):
        return [raw]
    return raw if raw else ["values"]


def normalize_input(raw_input: dict[str, Any] | None) -> dict[str, Any]:
    """把 LangGraph Platform 输入格式转换成 LangChain 状态 dict。

    dict -> message 的转换委托给 ``convert_to_messages``，以保留
    ``additional_kwargs``（如上传文件元数据）、``id``、``name`` 以及 ai/system/tool
    等非 human 角色。旧的手写转换只保留 ``content``，并把所有角色压成
    ``HumanMessage``，会静默丢掉前端上传附件。

    格式错误的 message dict（缺 ``role``/``type``/``content``、角色不支持等）
    会抛 ``HTTPException(400)`` 并带上出错下标，而不是冒泡成 500。网关是系统
    边界，客户端需要可重试、可定位的 400 错误。
    """
    if raw_input is None:
        return {}
    messages = raw_input.get("messages")
    if messages and isinstance(messages, list):
        converted: list[Any] = []
        for index, msg in enumerate(messages):
            if isinstance(msg, BaseMessage):
                converted.append(msg)
            elif isinstance(msg, dict):
                try:
                    converted.extend(convert_to_messages([msg]))
                except (ValueError, TypeError, NotImplementedError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid message at input.messages[{index}]: {exc}",
                    ) from exc
            else:
                converted.append(msg)
        return {**raw_input, "messages": converted}
    return raw_input


_DEFAULT_ASSISTANT_ID = "lead_agent"


# run-context 白名单：langgraph-compat 层只会把这些 key 从 ``body.context`` 转进
# run config。LangGraph >=0.6 引入了 ``config["context"]``，但为了兼容旧的
# ``_get_runtime_config`` 消费方，以及 LangGraph >=1.1.9 中不再从 configurable
# 回退读取的 ``ToolRuntime.context``，这些值需要同时写入 configurable 和 context。
_CONTEXT_CONFIGURABLE_KEYS: frozenset[str] = frozenset(
    {
        "model_name",
        "mode",
        "thinking_enabled",
        "reasoning_effort",
        "is_plan_mode",
        "subagent_enabled",
        "max_concurrent_subagents",
        "agent_name",
        "is_bootstrap",
    }
)


def merge_run_context_overrides(config: dict[str, Any], context: Mapping[str, Any] | None) -> None:
    """把 ``body.context`` 的白名单字段合并到 run config。

    字段会同时写入 ``config['configurable']`` 和 ``config['context']``，让旧版
    configurable 读取方和新版 ``ToolRuntime.context`` 消费方都能看到。

    ``user_id`` 也会被传播到 ``context``，用于 IM 等非 Web 调用方传身份；这里用
    ``setdefault``，保证服务器鉴权写入的 user_id 永远优先于客户端传入值。
    """
    if not context:
        return
    configurable = config.setdefault("configurable", {})
    runtime_context = config.setdefault("context", {})
    for key in _CONTEXT_CONFIGURABLE_KEYS:
        if key in context:
            if isinstance(configurable, dict):
                configurable.setdefault(key, context[key])
            if isinstance(runtime_context, dict):
                runtime_context.setdefault(key, context[key])
    if "user_id" in context and isinstance(runtime_context, dict):
        runtime_context.setdefault("user_id", context["user_id"])


def inject_authenticated_user_context(config: dict[str, Any], request: Request) -> None:
    """把服务端鉴权用户写入 run context，供后台工具使用。

    工具执行可能发生在 HTTP handler 返回之后，所以写用户隔离文件的工具不能只依赖
    当前请求的 ContextVars。这里的值来自服务端鉴权状态，不信任客户端 context。
    """

    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return

    runtime_context = config.setdefault("context", {})
    if isinstance(runtime_context, dict):
        runtime_context["user_id"] = str(user_id)


def resolve_agent_factory(assistant_id: str | None):
    """根据 assistant_id 解析 agent factory。

    自定义 agent 的实现方式是 ``lead_agent`` + 注入 ``configurable`` 或 ``context``
    的 ``agent_name``。因此所有 ``assistant_id`` 都映射到同一个 factory，真正路由
    发生在 ``make_lead_agent`` 读取 ``cfg["agent_name"]`` 时。
    """
    from deerflow.agents.lead_agent.agent import make_lead_agent

    return make_lead_agent


def build_run_config(
    thread_id: str,
    request_config: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    *,
    assistant_id: str | None = None,
) -> dict[str, Any]:
    """构建 agent 运行所需的 RunnableConfig。

    当 ``assistant_id`` 指向自定义 agent（非 ``"lead_agent"`` / ``None``）时，
    会把它作为 ``agent_name`` 写入当前启用的运行时参数容器：LangGraph >=0.6.0
    请求使用 ``context``，否则使用 ``configurable``。``make_lead_agent`` 会读取
    这个 key 加载对应的 ``agents/<name>/SOUL.md`` 和配置；缺失时会静默退回默认
    lead agent。

    这和 channel manager 的 ``_resolve_run_params`` 保持一致，保证兼容 LangGraph
    Platform 的 HTTP API 和 IM 通道路由行为一致。
    """
    config: dict[str, Any] = {"recursion_limit": 100}
    if request_config:
        # LangGraph >=0.6.0 推荐用 ``context`` 传线程级数据，并拒绝同时带
        # ``configurable`` 和 ``context`` 的请求。如果调用方已传 context，则尊重它，
        # 不再生成自己的 configurable。
        if "context" in request_config:
            if "configurable" in request_config:
                logger.warning(
                    "build_run_config: client sent both 'context' and 'configurable'; preferring 'context' (LangGraph >= 0.6.0). thread_id=%s, caller_configurable keys=%s",
                    thread_id,
                    list(request_config.get("configurable", {}).keys()),
                )
            context_value = request_config["context"]
            if context_value is None:
                context = {}
            elif isinstance(context_value, Mapping):
                context = dict(context_value)
            else:
                raise ValueError("request config 'context' must be a mapping or null.")
            config["context"] = context
        else:
            configurable = {"thread_id": thread_id}
            configurable.update(request_config.get("configurable", {}))
            config["configurable"] = configurable
        for k, v in request_config.items():
            if k not in ("configurable", "context"):
                config[k] = v
    else:
        config["configurable"] = {"thread_id": thread_id}

    # 调用方指定非默认 assistant 时注入自定义 agent 名；如果运行时容器里已有
    # agent_name，则尊重显式值。
    if assistant_id and assistant_id != _DEFAULT_ASSISTANT_ID:
        normalized = assistant_id.strip().lower().replace("_", "-")
        if not normalized or not re.fullmatch(r"[a-z0-9-]+", normalized):
            raise ValueError(f"Invalid assistant_id {assistant_id!r}: must contain only letters, digits, and hyphens after normalization.")
        if "configurable" in config:
            target = config["configurable"]
        elif "context" in config:
            target = config["context"]
        else:
            target = config.setdefault("configurable", {})
        if target is not None and "agent_name" not in target:
            target["agent_name"] = normalized
        config.setdefault("run_name", resolve_root_run_name(config, normalized))
    if metadata:
        config.setdefault("metadata", {}).update(metadata)
    return config


# ---------------------------------------------------------------------------
# Run 生命周期
# ---------------------------------------------------------------------------


async def start_run(
    body: Any,
    thread_id: str,
    request: Request,
) -> RunRecord:
    """创建 ``RunRecord`` 并启动后台 agent 任务。

    参数：
        body: 已校验的 ``RunCreateRequest``。这里标成 Any 是为了避免和定义
            Pydantic model 的 router 模块循环 import。
        thread_id: 目标 thread。
        request: FastAPI request，用于从 ``app.state`` 读取运行时单例。
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    run_ctx = get_run_context(request)

    disconnect = DisconnectMode.cancel if body.on_disconnect == "cancel" else DisconnectMode.continue_

    body_context = getattr(body, "context", None) or {}
    model_name = body_context.get("model_name")

    # model_name 可能来自 JSON；不是字符串时先转字符串再校验。
    if model_name is not None and not isinstance(model_name, str):
        model_name = str(model_name)

    # 提供 model_name 时必须命中 allowlist，避免客户端任意指定未配置模型。
    if model_name:
        app_config = get_app_config()
        resolved = app_config.get_model_config(model_name)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail=f"Model {model_name!r} is not in the configured model allowlist",
            )

    try:
        record = await run_mgr.create_or_reject(
            thread_id,
            body.assistant_id,
            on_disconnect=disconnect,
            metadata=body.metadata or {},
            kwargs={"input": body.input, "config": body.config},
            multitask_strategy=body.multitask_strategy,
            model_name=model_name,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedStrategyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    # upsert thread 元数据，确保即使没有显式 POST /threads 创建过的线程
    # （例如 stateless runs）也能出现在 /threads/search 中。
    try:
        existing = await run_ctx.thread_store.get(thread_id)
        if existing is None:
            await run_ctx.thread_store.create(
                thread_id,
                assistant_id=body.assistant_id,
                metadata=body.metadata,
            )
        else:
            await run_ctx.thread_store.update_status(thread_id, "running")
    except Exception:
        logger.warning("Failed to upsert thread_meta for %s (non-fatal)", sanitize_log_param(thread_id))

    agent_factory = resolve_agent_factory(body.assistant_id)
    graph_input = normalize_input(body.input)
    config = build_run_config(thread_id, body.config, body.metadata, assistant_id=body.assistant_id)

    # 把 DeerFlow 专用 context 覆盖项写入 configurable 和 context。context 是
    # langgraph-compat 层的扩展字段，用于携带模型、thinking_enabled 等 agent 配置。
    # 这里只转发和 agent 相关的白名单 key，忽略 thread_id 等未知 key。
    merge_run_context_overrides(config, getattr(body, "context", None))
    inject_authenticated_user_context(config, request)

    stream_modes = normalize_stream_modes(body.stream_mode)

    task = asyncio.create_task(
        run_agent(
            bridge,
            run_mgr,
            record,
            ctx=run_ctx,
            agent_factory=agent_factory,
            graph_input=graph_input,
            config=config,
            stream_modes=stream_modes,
            stream_subgraphs=body.stream_subgraphs,
            interrupt_before=body.interrupt_before,
            interrupt_after=body.interrupt_after,
        )
    )
    record.task = task

    # 标题同步由 worker.py 的 finally 块处理：run 完成后从 checkpoint 读取标题，
    # 再调用 thread_store.update_display_name。

    return record


async def sse_consumer(
    bridge: StreamBridge,
    record: RunRecord,
    request: Request,
    run_mgr: RunManager,
):
    """从 StreamBridge 读取事件并产出 SSE 帧的异步生成器。

    ``finally`` 块实现 ``on_disconnect`` 语义：
    - ``cancel``：客户端断开时取消后台任务。
    - ``continue``：后台任务继续跑，只是丢弃事件。
    """
    last_event_id = request.headers.get("Last-Event-ID")
    try:
        async for entry in bridge.subscribe(record.run_id, last_event_id=last_event_id):
            if await request.is_disconnected():
                break

            if entry is HEARTBEAT_SENTINEL:
                yield ": heartbeat\n\n"
                continue

            if entry is END_SENTINEL:
                yield format_sse("end", None, event_id=entry.id or None)
                return

            yield format_sse(entry.event, entry.data, event_id=entry.id or None)

    finally:
        if record.status in (RunStatus.pending, RunStatus.running):
            if record.on_disconnect == DisconnectMode.cancel:
                await run_mgr.cancel(record.run_id)


async def wait_for_run_completion(
    bridge: StreamBridge,
    record: RunRecord,
    request: Request,
    run_mgr: RunManager,
) -> bool:
    """等待 run 发布 ``END_SENTINEL``，并遵守 on_disconnect 语义。

    非流式 ``/wait`` 端点曾经直接 ``await record.task``，没有处理断连。长工具调用
    （如 ``pip install``）期间，如果客户端或中间 HTTP 代理超时，handler 可能吞掉
    ``CancelledError`` 并序列化当时存在的 checkpoint，把半完成 run 伪装成正常完成。

    这个辅助函数消费和 ``sse_consumer`` 相同的 bridge，让 wait 路径共享同一套断连
    语义：每次唤醒都检查 ``request.is_disconnected()``；真实断连且
    ``record.on_disconnect`` 为 ``cancel`` 时取消后台 run。bridge 的 heartbeat
    sentinel 保证即使 agent 一段时间没有事件，也会按 heartbeat_interval 唤醒。

    返回：
        观察到 ``END_SENTINEL`` 时返回 True，表示 run 到达终态；因为客户端断开而
        退出时返回 False。调用方在 False 时必须跳过 checkpoint 序列化，避免把
        半成品 checkpoint 当正常响应返回。
    """
    completed = False
    try:
        async for entry in bridge.subscribe(record.run_id):
            # END_SENTINEL 表示 run 已到终态；即使客户端刚断开也要尊重它，让调用方
            # 仍然可以序列化真实最终 checkpoint。
            if entry is END_SENTINEL:
                completed = True
                return True
            if await request.is_disconnected():
                break
            # heartbeat 和普通事件只用于唤醒循环，继续等待 END_SENTINEL。
        return completed
    finally:
        if not completed and record.status in (RunStatus.pending, RunStatus.running):
            if record.on_disconnect == DisconnectMode.cancel:
                await run_mgr.cancel(record.run_id)
