# PixelFlow 工作说明

用户主要使用 Java 后端思维。说明代码时优先用 Controller、Service、Repository、Client、DTO、Filter 和工作流编排类比。

## 中文工程规范

- 提交标题/正文、PR、状态、交接和测试结论必须使用中文。
- push 前必须运行中文工程门禁；人工注释、docstring 和配置说明必须使用中文。
- 每个新增或修改的 YAML/TOML 配置叶子项必须紧邻说明用途和影响；JSON 必须由 schema 或同目录中文说明覆盖。
- Secret、账号、token、用户正文和供应商原始异常不得写入仓库、日志或测试夹具。

## 当前架构

PixelFlow 只保留新 Harness 架构：

| 层 | 主要路径 | 职责 |
| --- | --- | --- |
| Gateway | `backend/app/gateway/` | `/agent` Controller、认证、Sidecar Client、Tool Broker HTTP Adapter。 |
| 控制面 | `backend/pixelflow/agent_control_plane/` | Run/Event DTO、持久化与投影。 |
| Harness | `backend/pixelflow/agent_harness/`、`services/pixelflow-agent-harness/` | Run Port、Sidecar HTTP/SSE、模型决策和 Skill 快照。 |
| Tool | `backend/pixelflow/agent_tools/` | Manifest、策略、幂等 Tool Broker。 |
| 视频工作区 | `backend/pixelflow/video/` | 权威 Workspace、Repository 与非计费编辑能力。 |
| 记忆 | `backend/pixelflow/long_term_memory/` | Mem0 Port、Adapter、WriteOutbox。 |
| 操作恢复 | `backend/pixelflow/operations/` | M06 幂等、租约和恢复语义。 |

旧 DeerFlow、LangGraph、PowerMem、`agent_runtime`、`agent_workflows`、`video_agent` 和旧 `/agent/flows` 不得重新引入。图片/视频/PPT 旧 Provider 已下线；恢复业务能力必须以新的 Harness Tool、M06 Provider Adapter 和权威 Workspace 合同实现。

## 进入仓库先读

1. `README.md`
2. `DEEPSEEK_HARNESS_SIDECAR_IMPLEMENTATION_PLAN.md`
3. `backend/app/gateway/routers/pixelflow_conversations.py`
4. `backend/pixelflow/agent_harness/`
5. `backend/pixelflow/agent_tools/`
6. `backend/pixelflow/long_term_memory/`
7. `services/pixelflow-agent-harness/README.md`

## 关键规则

- 所有新网关接口必须以 `/agent` 开头。
- Gateway 是权威状态写入方；Sidecar 只能通过 Tool Broker 请求业务动作，不能直连数据库、Provider 或宿主文件系统。
- Tool 修改工作区前必须校验用户、会话、revision、Run binding 与幂等键。
- Sidecar、Gateway 与 Mem0 的 Secret 仅从部署环境注入。Linux 容器编排见 `services/pixelflow-agent-harness/deploy/docker-compose.linux.yml`。
