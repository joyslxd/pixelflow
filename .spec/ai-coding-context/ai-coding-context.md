# ai-coding-context

## 项目

- 名称: pixelflow
- 路径: `/Users/williaman/Documents/joyce文档/code/ec-agent/pixelflow`
- 定位: 电商内容创作 AI Agent 工作台（图片 / 短视频 / 视频分析 / PPT）
- 当前主线: 统一 VideoAgent V2（P0 本地开发候选）；`LegacyWorkspace` 仍为兼容承载层
- 初始化: 2026-08-10（`/vibe-init`）

## 技术栈

- 后端: Python ≥3.12、FastAPI、uvicorn、uv、DeerFlow harness、LangGraph SDK
- 前端: React 19、Vite、TanStack Query、React Router 7（`web/`）
- LLM: DeepSeek `deepseek-v4-pro`（计划 / 脚本 Skill / intake）
- 外部: content-app / Borgrise（生成与额度）、PowerMem sidecar、剪映草稿
- 持久化: Task/Conversation Store；VideoAgent Workspace + Plan/Step + Outbox

## 启动

- 后端: `cd backend && make dev`（reload；`PIXELFLOW_CONFIG_ENV=dev`）
- 前端: `cd web && npm run dev`（开发默认；嵌入链路常见 `localhost:5273`）
- 代理关系（常见本地）: 前端 `/agent` → 后端 `http://127.0.0.1:8001`
- 非 reload: `cd backend && make gateway`

## 测试

- 后端全量: `cd backend && make test`（`uv run pytest tests/ -v`）
- VideoAgent 相关: `cd backend && uv run pytest tests/test_video_agent_*.py -q`
- 前端: `cd web && npm test`
- 投影/通知关键: `web/tests/videoAgentWorkspaceProjection.test.mjs`、`web/tests/supervisorRuntimeNotice.test.mjs`
- Lint: `cd backend && make lint`；`cd web && npm run lint`

## 常改目录

| 区域 | 路径 |
|------|------|
| VideoAgent | `backend/pixelflow/video_agent/*` |
| Agent Runtime | `backend/pixelflow/agent_runtime/*` |
| Gateway | `backend/app/gateway/*` |
| FE VideoAgent | `web/src/features/video-agent/*` |
| FE 兼容工作台 | `web/src/features/legacy-workspace/LegacyWorkspace.tsx` |
| 设计/计划 | `docs/superpowers/specs|plans/*unified-video-agent*` |

## 知识库

调查前先检索：

```bash
rg "<关键词>" .spec/context-dict/ -l
```

当前域目录: `.spec/context-dict/video-agent/`（脚本路径、确认闸门、资产包、计划卡等）。

## AI 硬约束

- 不提交 `.env` / 凭证；不删现有测试；未经讨论不改 CI/CD / DB schema / bootstrap 生产配置
- 确认类动作只走确认/取消 API，不伪造自然语言 Turn
- 前端以 Snapshot/SSE 投影驱动；公开事件不泄漏内部参数/推理
- 优先复用能力，不主动重写整份 `LegacyWorkspace.tsx`
- 分支: `{stage}_{reqId}_{developer}`；提交: `{type}: {subject}-{reqId}`，末尾 `AI-Assisted: yes`

## 风险门禁（实现前自检）

命中任一项视为 L3（需人工确认）: 资金/额度/结算、权限鉴权审批、DB schema、对外 API 契约、幂等/事务边界、敏感数据/生产配置。

安全清单目录: `.spec/security-checklists/`（按域对照）。
