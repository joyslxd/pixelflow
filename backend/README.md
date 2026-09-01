# PixelFlow Backend

## 当前架构

Gateway 是唯一面向浏览器的 Controller 层，公开接口统一以 `/agent` 开头。它负责鉴权、会话归属、Workspace revision、Snapshot/SSE 投影与长期记忆上下文；它不直接执行模型推理或媒体 Provider。

`services/pixelflow-agent-harness` 是独立 Sidecar，只执行 DeepSeek Harness 模型循环、Skill 加载和受控 Tool 调用。Sidecar 不保存 PixelFlow 业务真相，不直接访问数据库、用户 Authorization 或 Provider。

视频 Workspace、GenerationJob、Outbox、用户偏好和对话消息由 `pixelflow/` 中的 Repository / Application Service 持久化。浏览器只能消费 Gateway 的 Snapshot、公开 SSE 事件和 Workspace 安全摘要。

## 本地开发

```bash
cd backend
uv sync
PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
```

前端位于 `../web`，通过 `npm run dev` 启动。完整 Gateway + Sidecar 联调使用 `services/pixelflow-agent-harness/deploy/docker-compose.linux.yml`；生产或云服务器必须经 Compose 注入敏感环境变量，禁止把密钥写入 YAML 或仓库。

## 验证

```bash
cd backend
uv run ruff check app pixelflow tests
uv run pytest tests -q
cd ../web
npm run lint
npm test
```

Mem0 真实验收需要部署环境提供 `PIXELFLOW_VOLCENGINE_MEM0_BASE_URL`、`PIXELFLOW_VOLCENGINE_MEM0_API_KEY` 和 `PIXELFLOW_LONG_TERM_MEMORY_USER_SALT`，再显式执行 `PIXELFLOW_RUN_REAL_MEM0=1 uv run pytest -m mem0_real`。测试只使用匿名主体并在结束时清理测试记忆。
