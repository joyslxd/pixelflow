# DeepSeek Harness M0 兼容性报告

## 固定来源

- 官方仓库：`https://github.com/deepseek-ai/deepseek-harness.git`
- 固定 SDK：`deepseek-harness-sdk==0.1.1rc1`
- 固定运行时：`deepseek-harness-runtime-bin==0.1.1rc1`（由 SDK 自动安装完全匹配版本）
- 官方源码核对提交：`b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`
- 官方 npm 源码版本：`0.1.1-rc.2`（只用于源代码合同核对，不替代 Python 发布版本）
- Python 下限：`>=3.10`
- 本地开发 wheel：`macosx_14_0_arm64`
- 生产部署 wheel：使用相同版本的 Linux x64 或 Linux ARM64 官方 wheel；不得在 Linux 上复制 macOS Runtime 二进制。
- 核对日期：2026-08-23

## 已确认的合同

- Python SDK 的导入模块为 `deepseek_harness`，高层入口为 `DeepSeekHarness`；运行时通过 stdio JSON-RPC 运行。
- 已在 macOS ARM64 Python 3.13 venv 安装 `deepseek-harness-sdk==0.1.1rc1`；SDK 自动安装完全匹配的 `deepseek-harness-runtime-bin==0.1.1rc1`，实际 wheel 标签为 `macosx_14_0_arm64`。
- `engines/deepseek/cordis/m0-safe.cordis.yml` 在无 API key、无模型请求条件下成功执行 `HarnessClient.start()` 与 `initialize()`，返回 `deepseek-harness-sdk-runtime`；该 Composition 不装载 Bash、PTY、文件读写、Web、MCP、Subagent 或通用任务工具。
- `engines/deepseek/packages/dsh-plugin-m0-probe/` 使用与 Python Runtime 对齐的 `@deepseek-ai/dsh-tools@0.1.1-rc.1` 成功加载到安全 Composition；其 `inspect_video_workspace` Tool 已验证未知参数拒绝、opaque 引用校验和固定 canonical JSON 输出。
- M0 Runtime 测试在隔离 `DSH_HOME/skills/m0-probe-skill/SKILL.md` 中写入受控 Skill，并在外部 Plugin 生命周期内依次调用官方 `ctx.skills.list()`、`ctx.skills.get()` 和 Tool Registry 查询；成功发现并读取该 Skill、确认 `skill` Tool 已装配，同时拒绝工作目录中未授权的 `host-skill`。该结果只证明启动期 Provider/loader 装配与隔离根发现，不代表模型 Turn 已验证正文进入 Session 的顺序。
- 已显式开启真实 M0 用例，经官方 Python SDK、固定 JSON-RPC Runtime 与安全 Composition 完成一个最小模型 Turn；结果为 `completed` 且有非空回复，返回的 Session 事件序号严格递增。用例最多请求 64 个输出 tokens，不记录 API key、输入正文、模型正文或原始 Provider 响应。模型端点与模型 ID 必须以当前部署环境配置和当次真实验证为准，不在本报告指定供应商入口。该用例证明禁用 Bash、PTY、文件、Web、MCP 和 Subagent 后 Agent 仍可调用真实模型，但不替代 Gateway、Tool Broker、Repository 与 SSE 的纵向验收。
- 已显式开启真实 Sidecar HTTP 用例：独立 Uvicorn 进程通过 `AgentHarnessSidecarClient` 的 Bearer 服务身份接收 `POST /internal/v1/runs`，将 Run/Event 写入隔离 SQLite，调用同一官方 Harness Composition 与 Ark 模型，并从 `GET /internal/v1/runs/{run_id}/events` 回放严格递增的公开 SSE 事件。Client 用例通过耗时 20.77 秒；它尚未被 PixelFlow Gateway Controller 注入，也未覆盖 Tool Broker、Workspace Repository 或浏览器 SSE。
- 服务身份已从本地固定 Bearer token 改为短期 JWT：Gateway→Sidecar 校验 `iss=pixelflow-gateway`、`aud=pixelflow-harness-sidecar`、`exp`、`iat` 和 `service_instance_id`；Sidecar→Tool Broker 使用独立的 `iss=pixelflow-harness-sidecar` 与 `aud=pixelflow-tool-broker`。两段鉴权均由真实 Sidecar HTTP 与 loopback Gateway HTTP/SQLite Tool Broker 用例验证；生产验证材料必须由 Secret Manager 注入。
- 已显式开启真实纵向用例：独立 Gateway 与 Sidecar Uvicorn 进程通过 `GatewayHarnessRunBridge` 创建 Run；Sidecar 只持久化 `accepted` 状态，Gateway 依照返回的 `run_id` 持久化 Run binding 后才以服务 JWT 激活模型。官方 ARM64 Runtime 随后发现隔离 `$DSH_HOME/skills` 的真实 Skill，真实模型自主调用 `inspect_video_workspace`。Capability Plugin 为该调用签发短期 Sidecar→Tool Broker JWT，Gateway Broker 按 binding 执行 owner、会话、Manifest 与 revision 校验，并从隔离 SQLite `VideoWorkspace` 返回 Observation；`pixelflow_agent_harness_tool_calls` 留存同一 Tool Call 的安全结果。该用例不预先固定 Tool 调用顺序，输出上限为 192 tokens；不记录密钥、输入/输出正文或下游原始响应。
- 已显式开启真实公共入口用例：经 content-app `/api/auth/verify` 验证的真实用户 Authorization 请求 `POST /agent/conversations/{conversation_id}/harness-turns/start`。Gateway 先持久化用户消息、回查该用户的真实 SQL Workspace，再创建/绑定/激活 Sidecar Run；`GET /agent/conversations/{conversation_id}/harness-runs/{run_id}/events` 只按 binding 的用户和会话回放过滤后的公开 SSE。该 Case 同时确认模型 Tool Call 已落库，耗时 26.65 秒；不调用媒体 Provider。
- 公共入口已进一步改为由后台投影消费 Sidecar 事件：source event ID 幂等写入既有 Gateway Event Outbox，最终回复写入权威助手消息；SSE 与 `GET /agent/conversations/{conversation_id}/harness-runs/{run_id}/snapshot` 均只读取 Gateway 投影，不再把 Sidecar HTTP 直传给浏览器。真实 Case 确认 Outbox sequence、最终回复消息和 Snapshot 一致，耗时 20.56 秒；不调用媒体 Provider。
- 已显式执行真实 Sidecar `kill`/restart：重启进程读取同一 SQLite Run/Event Store，将所有遗留非终态 Run 收口为 `failed / engine_error` 并追加 `run.failed` 的固定代码 `harness_run_recovery_required`，耗时 2.60 秒且不激活模型；该 Case 只验证 Sidecar 收口，完整 Gateway 恢复旅程见下一条。
- 已显式执行完整真实恢复旅程：认证用户启动公开 Turn 后立即 `kill` Sidecar，重启进程复用同一 SQLite Run/Event Store；Gateway 按 binding 重新投影失败事件，持久化唯一 `recovery_event_id`，再由 `/recover` 从权威用户消息和当前 SQL Workspace 创建新的 `run_recovery`。恢复 Run 使用新的 Harness Session 并经真实模型、动态 Skill、Capability Plugin、短期 JWT、Tool Broker 和只读 Workspace Tool 完成，耗时 21.97 秒。原 Run 已存在 Tool Ledger 而尚无完整结果投影时会进入 `manual_review`，禁止自动重放。
- 已显式执行真实 SSE 断线重连与 Gateway restart：认证客户端在收到首个 Gateway SSE 事件后主动关闭 HTTP 流，再按已收到的 sequence 重连；事件连续且不重复。随后停止 Gateway 并以新 FastAPI/Uvicorn 实例、同一 SQLite Outbox/消息表和持续运行的 Sidecar 启动，再从公开 Snapshot 与 SSE 回放原 Run 的完整事件和助手回复，耗时 58.09 秒。该 Case 不创建第二个模型 Run、不调用媒体 Provider。
- `RunResult.finish_reason` 可以返回 `completed`、`max-tokens` 或 `error` 等结束原因，Sidecar 必须映射为自身的 `status + termination_reason`。
- 自定义 Cordis composition 必须保留 `@deepseek-ai/dsh-sdk-jsonrpc-server`，并显式设置 `DSH_CORDIS_CONFIG` 或 SDK 的 `cordis` 参数。
- 官方 Session Persistence 在发现中断 Turn 时会将其收口为中断终态；不支持从 checkpoint、JSONL 或 Session 原位续跑未完成 Turn。PixelFlow 必须创建新的 `run_recovery`。

## 尚未通过的验证

- 尚未验证模型 Turn 中目录与正文的可见顺序、Run 快照对 watcher 变更的隔离、挂起策略，以及跨 Sidecar/PixelFlow 公共 Turn/Outbox/Snapshot/SSE 边界的有序通知。
- 官方 SDK 默认 Composition 会加载 `dsh-subprocess-local` 与 `node-pty`；当前 macOS ARM64 wheel 缺少 `prebuilds/darwin-arm64/pty.node`，默认 Composition 初始化失败。该默认配置本身也违反 PixelFlow 的禁用 Bash/PTY 约束，不能用于开发或生产。
- 本机 `uv` 为 x86_64 Rosetta 可执行程序，会拒绝 ARM64-only Runtime wheel；这不影响 ARM64 Python venv 的 SDK 验证。Linux 发布镜像必须在原生目标架构执行 `uv sync --locked`。
- Linux 原生镜像验证尚未完成；完整浏览器 Snapshot/Outbox/SSE 和进程重启演练分别归属 M2/M4，不作为 M0 完成条件，生产接线仍禁止开始。

## 下一步

1. 按 M1 接入 PixelFlow 自有基础设施与火山 Mem0，避免 Sidecar 直接访问记忆服务。
2. 按 M2 完成稳定 Run 协议、浏览器 Snapshot/SSE 与真实故障演练。
3. 在原生 Linux x64/ARM64 CI 或测试服务器执行 `uv sync --locked`，确认同版本 SDK/Runtime wheel 与安全 Composition 可以启动。
