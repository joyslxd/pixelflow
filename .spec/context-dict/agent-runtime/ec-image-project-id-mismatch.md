---
topic: EC 生图失败因写死 projectId=1
module: agent-runtime
date: 2026-09-06
keywords:
  - PIXELFLOW_M06_IMAGE_PROJECT_ID
  - projectId
  - provider_business_failed
  - ConstraintViolationException
  - text_to_image
---
## 结论摘要

PixelFlow 调 Content-App 文生图时查询参数固定 `projectId`，默认 `"1"`（`PIXELFLOW_M06_IMAGE_PROJECT_ID`）。博观测试 admin 项目就是 1，所以 115 → test-video 能成功。EC 创作页实际用 `projectId=148`；同一套 Adapter 仍传 1，Content-App 写库触发 Hibernate `ConstraintViolationException`，轮询映射成 `provider_business_failed`。浏览器直连 EC `text_to_image?projectId=148` 返回 200，不能证明 PixelFlow 请求等价。

## 关键文件

- `backend/pixelflow/capabilities/image_generation/providers/content_app.py`（未配置时 `GET /projects` 解析第一项）
- `backend/.env.example`（`PIXELFLOW_M06_IMAGE_PROJECT_ID` 留空表示自动解析）
- `web/src/api/contentAppAssets.ts`（上传走 `/api/projects` 取当前用户项目）

## 核心逻辑

1. Adapter `POST {base}/picture/text_to_image?projectId={resolved}`。未设置 `PIXELFLOW_M06_IMAGE_PROJECT_ID` 时先 `GET /projects`。
2. 解析 `projects` 或 `data.projects` 第一项 `id`，按 Authorization SHA-256 缓存，不保存明文 token。
3. Content-App 按项目外键落任务表；项目不存在或不属于当前用户即约束失败。HTTP 仍可能 200，status=failed，Gateway 收成 `provider_business_failed`。
4. 文档曾写「不再传 projectId」；EC 前端与图片 Adapter 仍需要真实项目 ID。

## 注意事项

- 不要把 EC 的 148 写进仓库当默认值；那是租户数据。
- 应急仍可在部署注入 `PIXELFLOW_M06_IMAGE_PROJECT_ID`，不要把租户项目号提交进仓库。
- 不要在日志或交接中写入 JWT、prompt 或 Hibernate 绑定参数。
