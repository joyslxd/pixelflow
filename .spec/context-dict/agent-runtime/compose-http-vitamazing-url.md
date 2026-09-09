---
topic: EC 分镜已生成但前端无预览且合并失败
module: agent-runtime
date: 2026-09-08
keywords:
  - 无法解析分镜成片
  - compose_or_export_video
  - http vitamazing
  - preview_url
  - _variant_urls_by_artifact
---
## 结论摘要

EC 成片 URL 是 `http://*.vitamazing.top/...`。`inspect_video_results` 与 digest 已认 HTTP，所以 Agent 会说 4/4 就绪；但 `compose_or_export_video` 解析分镜成片、写入 `delivery_url` 时仍只收 HTTPS，于是报「无法解析分镜成片」。上一轮 EC 未重发前端时，旧 `previewUrl` 也会丢掉 HTTP 预览。

## 关键文件

- `backend/pixelflow/capabilities/video_delivery/providers/content_app.py`
- `backend/pixelflow/agent_tools/video/delivery.py`
- `backend/pixelflow/video/workspace/digest.py`
- `web/src/features/agent-runtime/workspaceV2.ts`

## 核心逻辑

1. 分镜选版可以不检查协议（有 `approved_variant_id` 时直接选用），合并 Adapter 再按 artifact_ref 取 URL。
2. URL 一律走 `canonical_provider_media_url`：vitamazing 保留/改回 HTTP，TOS 升 HTTPS。
3. 工作台播放器只吃 digest 的 `preview_url`；前端必须放行白名单 HTTP。

## 注意事项

- 不要对已有 `provider_job_id` 再点确认生成。
- 只修 Gateway 不够：EC 前端静态包也要 `pnpm build-ec-prod` 同步到 `/var/www/pixelflow-agentfrontend/`。
- 任意 http 主机仍拒绝，不能为了合并放开非白名单地址。
