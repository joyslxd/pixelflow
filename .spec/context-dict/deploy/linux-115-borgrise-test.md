---
topic: 115.191.36.147 博观测试机发布布局
module: deploy
date: 2026-09-04
keywords:
  - 115.191.36.147
  - borgrise-test
  - test-video.borgrise.com
  - agentfrontend
  - build-and-start-linux
---
## 结论摘要

这台机跑 PixelFlow Gateway + Harness Sidecar，浏览器入口是 `http://115.191.36.147/agentfrontend/`。Content-App 生图/生视频走博观测试 `https://test-video.borgrise.com`，不要改成 EC `creator.vitamazing.top`。

## 关键文件

- 运行源码：`/opt/pixelflow-<shortsha>/`（当前 `1d90fa9`）
- Compose：`services/pixelflow-agent-harness/deploy/docker-compose.linux.yml`
- Secret：同目录 `.env.gateway`、`.env.sidecar`（禁止覆盖、禁止打印）
- 发布身份：`.env.harness-release`（`PIXELFLOW_CONFIG_ENV=borgrise-test`）
- Nginx：`/etc/nginx/conf.d/frontend.conf`
- 前端静态：`/data/ecom/test/agentfrontend/dist/`（不是 `/var/www/pixelflow-agentfrontend`）
- 数据卷：`/var/lib/pixelflow-gateway`、`/var/lib/pixelflow-harness`

## 核心逻辑

1. 用本机当前提交 rsync 到新的 `/opt/pixelflow-<sha>`，从上一版 `/opt/pixelflow-*` 复制 `.env.gateway` / `.env.sidecar`，只改 `.env.harness-release` 的镜像名、Skill 根和 `PIXELFLOW_CONFIG_ENV`。
2. 在该目录跑 `bash services/pixelflow-agent-harness/deploy/build-and-start-linux.sh`。脚本只重建 `pixelflow-gateway` 与 `pixelflow-harness-sidecar`，不碰 Nginx / 8082 / 数据库。
3. `.env.gateway` 里已有 `BORGRISE_BASE_URL=https://test-video.borgrise.com/api`。Profile Loader 不覆盖已存在的 env，因此即使旧文件仍写着 `PIXELFLOW_CONFIG_ENV=prod`，Compose 后加载的 `.env.harness-release` 会把 Profile 改成 `borgrise-test`，Content-App 地址仍保持 test-video。
4. 浏览器在 `115.191.36.147` 上与 `VITE_CONTENT_APP_ORIGIN` 不同源，上传走同域 `/api/`。本机 `content_app_dev:8082` 已于 2026-09-01 左右 OOM 退出，继续指 8082 会 502。2026-09-04 已把 Nginx `/api/`、`/api/video/`、`/ws` 反代到 `https://test-video.borgrise.com`，与 Gateway `BORGRISE_BASE_URL` 同一套 Content-App。无 Token 时应返回 401 `NO_TOKEN`，不应再 502。
5. Nginx `location /agent/` 的 `proxy_pass` 没有剥路径，所以对外 `/agent/live` 会打到 Gateway `/agent/live` 并 401。健康检查只打 loopback `:8001/live` `:8001/ready` `:8090/live` `:8090/ready`。
6. `/etc/nginx/nginx.conf` 里不要再放同名 `server_name 115.191.36.147`，否则会 `conflicting server name` 并让人误判生效配置。实际 vhost 只留 `conf.d/frontend.conf`。

## 注意事项

- 不要把 `/api/` 指回已退出的 `127.0.0.1:8082`；不要为修 502 去重启 `content_app_dev`，那会让上传落到本机 EC Java、生图仍走 test-video。
- 不要停 Nginx、Portainer。
- 不要把本机 `backend/.env`（可能指向 EC）同步到服务器。
- 磁盘很容易被旧 `pixelflow-gateway:m5-*` 镜像占满；发布前可删未运行标签，保留当前正在跑的镜像以便回滚。
- 回滚：把 Compose 工作目录切回上一版 `/opt/pixelflow-<旧sha>` 再 `up -d`，数据卷不要删。
