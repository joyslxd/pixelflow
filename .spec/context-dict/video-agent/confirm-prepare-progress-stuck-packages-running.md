---
topic: 确认脚本后资产包进度卡在第 2 步
module: video-agent
date: 2026-08-15
keywords:
  - confirm-script-plan
  - prepare_scene_packages
  - packagesRunning
  - nativePrepareToolSignal
  - awaiting_image_model
  - AgentPipelineProgress
---
## 结论摘要
服务端该会话已有 20 个 `scene_packages` 且 `scene_package_job=succeeded`；卡住的是前端进度卡。

按钮确认先把「分镜包」置 `running`，命令路径不发 native prepare 完成事件。更致命的是：会话里若残留 **native `prepare_scene_packages` = running**（或 Plan step 未收口），`nativePrepareToolSignal` effect 会**反复**把进度重置回第 2 步，盖掉 hydrate/confirm 解卡。

## 相关文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/AgentPipelineProgress.tsx`
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑
1. hydrate：`packagesRunning` 仅在 `jobActive || packages.length === 0` 时保留
2. upsert early-return：结构未变但 packages 仍 running → 仍推进
3. confirm 成功：无「无包且 job 活跃」时立刻 `awaiting_image_model`
4. native prepare running：若 Workspace 已有包且 job 非活跃 → **解卡**，禁止重置
5. 兜底 effect：有包 + job 非活跃 + packages=running → 强制 awaiting/completed

## 注意事项
- 查 DB 可快速区分「真 prepare 挂死」vs「假进度」：`scene_packages` 长度与 `scene_package_job.status`
- 重拆进行中靠 `jobActive` 保留 running，避免旧包盖回选模
- 若仍卡住：硬刷新页面让 HMR/状态重载；本条修的是包已就绪仍显示「正在生成」
