---
topic: 自然语言续跑失败参考图，不重开 Skill 执行方案
module: video-agent
date: 2026-08-11
keywords:
  - 继续生成失败的参考图
  - handleRetrySceneAssets
  - turns/start
  - 登录已过期
---
## 结论摘要
场景参考图部分失败后，「继续生成失败的参考图」等自然语言在 V2.1 **不再由前端关键词路由**；应交 VideoAgent 思考流选 Tool。工作台「重新生成参考图」按钮仍走 `handleRetrySceneAssets`（V2 发 Turn / `frontend_v2` 开 Job）。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（按钮 `handleRetrySceneAssets`；NL 走 turns/start）
- `backend/pixelflow/video_agent/*`（思考流 / Planner 选 generate_scene_assets）
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑
1. 已删除 `isRetryFailedSceneAssetsRequest` / `resolveWorkflowResumeIntent` 前端编排
2. 按钮路径：`onRetrySceneAssets` → `handleRetrySceneAssets` → V2 Turn 或 Job
3. NL 路径：turns/start → thinking → Plan，禁止前端假「已完成」或新开无关 Skill 计划

## 注意事项
- 无失败记录时由 Agent/气泡说明，不要假重试
- token 过期仍需用户重新登录后重试
---
