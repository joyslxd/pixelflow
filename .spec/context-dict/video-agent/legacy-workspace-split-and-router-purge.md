---
topic: LegacyWorkspace 拆分与旧 Job 路由物理删除
module: video-agent
date: 2026-08-12
keywords:
  - LegacyWorkspace
  - legacyWorkspaceTypes
  - legacyWorkspaceHelpers
  - pixelflow_video
  - pixelflow_jianying_draft
  - hard-delete
---

## 结论摘要

`LegacyWorkspace.tsx` 从约 13.6k 行拆到约 9.9k 行：类型、纯 helper、旧视频 Job
常量分别迁入同目录 sibling 模块；旧 `/flows/video*` poll/start 死代码改为 early-return stub。  
Gateway 侧 `pixelflow_video.py` / `pixelflow_jianying_draft.py` 路由模块已物理删除；
领域 Service（`generate/`、`jianying_draft/`）仍供原生 Tool 适配器使用。

## 关键文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/legacy-workspace/legacyWorkspaceTypes.ts`
- `web/src/features/legacy-workspace/legacyWorkspaceHelpers.ts`
- `web/src/features/legacy-workspace/legacyWorkspaceLegacyVideoJobs.ts`
- （已删）`backend/app/gateway/routers/pixelflow_video.py`
- （已删）`backend/app/gateway/routers/pixelflow_jianying_draft.py`
- `backend/app/gateway/app.py` / `routers/__init__.py`

## 核心逻辑

1. 页面仍由 `VideoAgentWorkspace` → `LegacyWorkspace` 承载；Canvas 走 `native-video-agent/canvas`。
2. 视频执行只走 Turn / Tool；前端 Job HTTP 客户端继续 throw。
3. 剪映草稿 Service 仍可在 Gateway lifespan 装配，供 Tool；HTTP Job 入口不存在。

## 注意事项

- 勿再 `import app.gateway.routers.pixelflow_video`；表征测试用本地 DTO 快照替代
  `PrepareScenePackagesResponse`。
- 继续拆 `WorkspacePage` 内部 state/handler 时，优先迁出 intake/plan/image/PPT 活路径，
  不要把原生 Turn / Canvas 再塞回巨型文件。
