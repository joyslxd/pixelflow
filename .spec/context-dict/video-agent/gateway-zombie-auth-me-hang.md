---
topic: 网关 worker 僵尸导致「验证中」一直转圈
module: gateway / auth
date: 2026-08-10
keywords: zombie, defunct, auth/me, AuthTokenPage, 验证中, BaseHTTPMiddleware, --reload
---
## 结论摘要
AuthTokenPage「验证中」只是在等 `GET /agent/auth/me`。若网关 `--reload` 子进程变成 `Z <defunct>`、父进程仍 `LISTEN :8001`，TCP 能连上但请求永不返回，前端会一直停在「验证中」，看起来像 token 问题，其实是**后端挂了**。

## 关键文件
- `web/src/pages/AuthTokenPage.tsx`（`checking` → `api.getCurrentUser()`）
- `backend/app/gateway/auth_middleware.py`
- 本地 `uv run python -m app.gateway.run --reload`

## 核心逻辑
1. 排障先 `curl -m 3 http://127.0.0.1:8001/health`：超时则不是 token 错，是进程假死
2. `ps` 看 worker 是否 `Z`；有则杀 reload 树并重启 `make dev`
3. 内存 Job（参考图等）会随重启丢失；需重新生成或等用户重新触发

## 注意事项
- 长任务（串行 seeddream）期间若触发 WatchFiles reload，更容易留下僵尸/半死 reload 状态
- token 真过期应快速 401/403，不会无限「验证中」
