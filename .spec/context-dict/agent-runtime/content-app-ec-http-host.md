---
topic: 本地联调切到 creator.vitamazing.top
module: agent-runtime
date: 2026-09-03
keywords:
  - BORGRISE_BASE_URL
  - content-app
  - vitamazing
  - VITE_CONTENT_APP_TARGET
---
## 结论摘要

ec 环境 content-app 在 `http://creator.vitamazing.top`。Gateway 用 `BORGRISE_BASE_URL=http://creator.vitamazing.top/api`；浏览器 `/api` 代理到站点根（不含 `/api`）。公网 HTTP 只放行 `*.vitamazing.top`，公网 IP 仍拒绝。

## 关键文件

- `backend/pixelflow/platform/content_app_url.py`
- `backend/config.dev.yml`
- `backend/.env.example`
- `web/.env.development`
- `web/src/lib/authStorage.ts`

## 核心逻辑

1. 启动脚本先读 `.env.example` 再读 `.env`；进程已有 `BORGRISE_BASE_URL` 时 YAML 不覆盖。
2. 上传走浏览器 `/api/upload`，不经 Gateway；502 是 content-app/Nginx 问题。
3. 改完需重启 Gateway 与 Vite。

## 注意事项

- 不要把行内注释写进 env 值。
- `VITE_API_TARGET` 本地仍指向 Gateway `:8001`，不要改成 content-app。
