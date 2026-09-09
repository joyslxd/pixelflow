---
topic: EC 生产机 Gateway/Sidecar 发布布局
module: deploy
date: 2026-09-08
keywords:
  - creator.vitamazing.top
  - 115.190.210.199
  - ec-prod
  - build-and-start-linux
---
## 结论摘要

EC 生产跑在 `creator.vitamazing.top`（`115.190.210.199`，主机 `iv-ye9d6q2g3kqc6inebz7f`）。Gateway + Sidecar 用 `PIXELFLOW_CONFIG_ENV=ec-prod`，Content-App 仍是同机 `http://creator.vitamazing.top`。公网 22 现可用本机 `id_ed25519` 登录 root。

## 关键文件

- 当前源码：`/opt/pixelflow-ec-0908m/`（含 HTTP 成片回显与合并修复；上一版 `/opt/pixelflow-bdee7b9w` 与 `/opt/pixelflow-90b1a76` 保留回滚）
- 镜像：`pixelflow-gateway:ec-0908m`、`pixelflow-harness:ec-0908m`
- 前端静态：`/home/devops/apps/prod/agentfrontend/dist` → `releases/ec-0908m`（不是 `/var/www/pixelflow-agentfrontend`）
- Secret：`services/pixelflow-agent-harness/deploy/.env.gateway`、`.env.sidecar`（从上一版复制，禁止打印）
- 发布身份：同目录 `.env.harness-release`
- 数据卷：`/var/lib/pixelflow-ec/gateway`、`/var/lib/pixelflow-ec/harness`
- Skill 根：`/var/lib/pixelflow-ec/agent-home`（Sidecar 只读挂载）

## 核心逻辑

1. rsync 本机当前工作区到新的 `/opt/pixelflow-<id>`，排除 `.env` / Secret / `.venv`。
2. 从上一版复制 `.env.gateway` / `.env.sidecar`，只改 `.env.harness-release` 的镜像名；`PIXELFLOW_CONFIG_ENV` 保持 `ec-prod`。
3. 在该目录跑 `bash services/pixelflow-agent-harness/deploy/build-and-start-linux.sh`。只重建 `pixelflow-gateway` 与 `pixelflow-harness-sidecar`。
4. 领域 Skill 需同步进 `/var/lib/pixelflow-ec/agent-home/skills/`，因为 Compose 用该目录覆盖容器内 Skill 根。
5. 健康检查只打 loopback `:8001/live` `:8001/ready` `:8090/live` `:8090/ready`。Nginx `/agent/ready` 用 GET，不要 HEAD（会 405）。

## 注意事项

- 不要停 Nginx、Portainer、`content_app_dev`。
- 不要把本机 `backend/.env` 同步到 EC。
- 回滚：Compose 工作目录切回 `/opt/pixelflow-bdee7b9w` 再 `up -d`，数据卷不要删。前端 symlink 切回 `releases/90b1a76`。
- 前端发布：本机 `pnpm build-ec-prod` 后 rsync 到 `/home/devops/apps/prod/agentfrontend/releases/<id>/`，再 `ln -sfn releases/<id> dist`。
- 这台不是博观测试机 `115.191.36.147`；不要把 `PIXELFLOW_CONFIG_ENV` 改成 `borgrise-test`。
