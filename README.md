# PixelFlow

PixelFlow 是面向电商内容创作的 Agent 工作台。当前主架构是 **Gateway + DeepSeek Harness Sidecar**：Gateway 负责用户身份、会话、权威工作区、长期记忆和 Tool Broker；Sidecar 负责模型决策、Skill 发现、公开进度和受控 Tool 调用。

## 当前 M1 能力

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| Harness Sidecar | 已实现 | 独立 HTTP/SSE Run、SQLite 回放、Skill 快照和服务 JWT。 |
| Gateway 控制面 | 已实现 | 对话、Harness Run 准入、Snapshot/SSE、Interrupt 与 Tool Broker。 |
| 视频 Tool | 已实现 | 发布 `inspect_video_workspace`、分镜查询/修改和素材替换等非计费动作。 |
| 长期记忆 | 已实现 | `LongTermMemoryPort`、WriteOutbox 和 Volcengine Mem0 Adapter；失败开放不阻断会话。 |
| 旧架构 | 已下线 | DeerFlow、LangGraph、PowerMem、旧 Task Router、旧 VideoAgent Runtime 已删除。 |
| 计费生成能力 | 已下线 | 图片、视频生成、PPT 和旧 Provider 只能在后续 M2 以新 Harness Tool + M06 Provider 重建。 |

## 架构

```text
浏览器
  -> Gateway (/agent)
     -> PixelFlow Repository / Workspace / Mem0 WriteOutbox
     -> Tool Broker
  -> Harness Sidecar
     -> DeepSeek 模型、活动 Skill 根、公开 Run/SSE
```

Gateway 与 Sidecar 都只接受内部服务 JWT；模型密钥、Mem0 密钥和服务签名材料只能放在部署环境的 `.env` 或 Secret Manager，禁止提交。

## 本地启动

```bash
cd backend
uv sync --locked --all-groups
PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
```

Linux 部署见 [Sidecar 部署说明](services/pixelflow-agent-harness/deploy/README.md)。Sidecar 活动 Skill 根由 `PIXELFLOW_AGENT_HOME` 指向 `backend/skills`；每个 Run 在接受时冻结 Skill 快照。

## 验证

```bash
cd backend
uv run pytest -q -m "not m0_real and not mem0_real"
uv run ruff check app pixelflow tests
```

真实 Sidecar 与 Mem0 验证只在 Linux 环境显式注入受保护 Secret 后运行；它们会访问外部服务，不属于默认测试。
