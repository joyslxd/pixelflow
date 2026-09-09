---
topic: EC 合并 500 因 Content-App 容器没有 ffprobe
module: agent-runtime
date: 2026-09-08
keywords:
  - 视频交付合并失败
  - /api/video/merge
  - ffprobe
  - content_app_dev
  - compose_or_export_video
---
## 结论摘要

分镜成片已就绪、PixelFlow 已把 4 个 HTTP URL 交到 `POST /api/video/merge`。Content-App 在约 3 秒内返回 HTTP 500。Java `VideoMergeService.probeVideoMediaInfo` 已把分镜下到 `/tmp/*.mp4`，但容器内没有 `ffprobe`：`Cannot run program "ffprobe": error=2`。Agent 文案「视频交付合并失败」是这层 500 的安全包装。同轮确认恢复 `max_business_tools=5`，inspect + 两次合并后触顶，所以不能再在本轮重试。

## 关键文件

- `backend/pixelflow/capabilities/video_delivery/providers/content_app.py`
- EC：`content_app_dev`（Amazon Linux 2023，镜像 `content_app_dev:latest`）
- `com.volcengine.contentapp.shared.service.impl.VideoMergeService`

## 核心逻辑

1. PixelFlow 只负责提交 `videoUrls`；真正拼接在 Java Content-App 同步完成。
2. 无 ffmpeg/ffprobe 时探测阶段即失败，不会产出成片。
3. 盲着重试合并在装好 ffprobe 前会同样 500。

## 注意事项

- 不要为了合并再点「确认生成」分镜视频。
- 不要停 Nginx / Portainer / Content-App 进程；装 ffprobe 属于 Content-App 镜像/容器运维，重建镜像会丢容器内临时安装。
- 2026-09-08 已把 johnvansickle 静态 `ffmpeg`/`ffprobe` 7.0.2 拷进运行中的 `content_app_dev`（`/usr/local/bin` 并链到 `/usr/bin`），主机副本在 `/opt/ffmpeg-static/`。未重启 Java。重建容器后需再 `docker cp`。
- 工具上限是确认恢复 Run 的限额，开新一轮对话即可再调合并。
