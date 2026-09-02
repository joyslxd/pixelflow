---
topic: 生成图缩略图要代理 image_url
module: agent-runtime
date: 2026-09-02
keywords:
  - thumbnail
  - image_url
  - planned_generation
  - WorkspaceV2Panel
  - existing_material
---
## 结论摘要

工作台缩略图接口原先只认 `origin=existing_material` 并读 `materials.url`。厨房/女主这类 `planned_generation` 生成图的地址在资产 `image_url` 上，接口会 404，前端只能显示占位「图片」。现在 ready 的生成图也走同一 Gateway 代理，浏览器仍不拿 TOS 原地址。

## 关键文件

- `backend/app/gateway/routers/pixelflow_conversations.py`
- `web/src/features/agent-runtime/WorkspaceV2Panel.tsx`
- `web/src/features/agent-runtime/WorkspaceAssetThumbnail.tsx`

## 核心逻辑

1. 上传素材：`source_material_id` → `materials.url`。
2. 已生成图：`origin in {planned_generation, provider_output}` 且 `state=ready` → `image_url`。
3. 仅允许 `.tos-cn-beijing.volces.com` / `.vitamazing.top` 的 HTTPS。

## 注意事项

- 公开 digest 仍然不投影 URL；缩略图必须走 Gateway。
- 改完需重启 Gateway；前端按 workspace revision 重新拉图。
