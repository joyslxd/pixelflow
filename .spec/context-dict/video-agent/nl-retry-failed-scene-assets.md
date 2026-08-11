---
topic: 自然语言续跑失败参考图，不重开 Skill 执行方案
module: video-agent
date: 2026-08-10
keywords:
  - 继续生成失败的参考图
  - isRetryFailedSceneAssetsRequest
  - retry_failed_images
  - handleRetrySceneAssets
  - 登录已过期
  - resolveWorkflowResumeIntent
---
## 结论摘要
场景参考图部分成功、部分失败（如 token/登录过期）后，用户说「继续生成失败的参考图」必须只调用 `handleRetrySceneAssets`（`target_assets`=失败项），禁止当成「开始生图」提示已完成，也禁止落入 VideoAgent 新开「执行方案 · 成稿自检与导出」。

## 关键文件
- `web/src/features/video-agent/scriptSkillStages.ts`（`isRetryFailedSceneAssetsRequest`、`resolveWorkflowResumeIntent`）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`handleSend` resumeIntent 分支）
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑
1. 「继续生成失败的参考图」含「生成参考图」，旧逻辑先命中 `start_images`；若已有图则 early return「已经生成完成」，未拦住则落到 Skill turn。
2. 新意图 `retry_failed_images` 优先于 `start_images`；`isStartImageGenerationRequest` 排除失败重试话术。
3. Workspace：找最近带 `sceneAssetFailures` 的 `video_scene_packages` → `handleRetrySceneAssets`。
4. `start_images` / `generic_resume` 若仍有可重试失败项，同样走失败重试，而不是「已完成」或重建资产包。

## 注意事项
- 按钮「重新生成参考图」路径本来就对；本修复补齐自然语言。
- 无失败记录时应提示打开结果卡，不要假重试、也不要开新计划。
- token 过期本身仍需用户重新登录后重试才会成功；路由正确只是不跑错流程。
