---
topic: compose 约 300 秒失败但 content-app 已上传成片（网关掐断）
module: video-agent
date: 2026-08-17
keywords:
  - compose_or_export_video
  - 视频交付Operation执行失败
  - ProviderJobCallError
  - proxy_read_timeout
  - /api/video/merge
  - RemoteProtocolError
  - /etc/nginx/conf.d/content-app.conf
---
## 结论摘要

15:10:46 content-app 收到 `POST /api/video/merge`（14 镜）；15:14:53 TOS 已上传
`videos/20260817_1514537069023_merged_video_20260817_151453706.mp4`；PixelFlow 在
15:15:47（约 **301 秒**）报「视频交付Operation执行失败」。

**已定位配置：** `/etc/nginx/conf.d/content-app.conf` 中
`server_name test-video.borgrise.com`（80/443）的 `location /api/`：

```
proxy_send_timeout 300s;
proxy_read_timeout 300s;
```

同文件里 `location /agent/` 已是 `3600s`，唯独 content-app `/api/` 仍是 300s。
生产 `video.borgrise.com` 的 `/api/` 同样是 300s。

根因是 nginx 掐断长连接；httpx 抛 `TransportError` → 旧逻辑 `ProviderJobCallError` →
被吞成笼统「执行失败」；服务端异步合并仍成功。

## 关键文件

- **运维（根因）：** `/etc/nginx/conf.d/content-app.conf` → `location /api/`
- `backend/config.dev.yml`：`borgrise.base_url=https://test-video.borgrise.com/api`
- `backend/pixelflow/skills/borgrise/provider_jobs.py`（`ContentAppMergeJobService`）
- content-app：`VideoMergeService`（8082）

## 核心逻辑

1. PixelFlow → `https://test-video.borgrise.com/api/video/merge` → nginx `/api/` → `127.0.0.1:8082`
2. 合并对客户端是同步长 HTTP；nginx 读超时 300s 后断开，upstream 异步任务可继续写 TOS。
3. 修复建议：将 `/api/`（至少 `/api/video/merge`）的 `proxy_read_timeout` / `proxy_send_timeout` 调到 **3600s**，与 `/agent/` 对齐；改完 `nginx -t && reload`。
4. 仅加长 PixelFlow `request_timeout` 挡不住前面的 nginx 300s。

## 注意事项

- 同秒对照：agent「执行失败」≠ content-app 业务失败；先查 TOS 是否已有 `merged_video_*`。
- 与「start 租约 30s」是另一类故障；租约已改为 1h，本次是 **HTTP 连接被掐**。
- 未授权不要直接改测试机 nginx；改配置属运维变更。
