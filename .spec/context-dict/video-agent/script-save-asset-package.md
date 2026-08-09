---
topic: 脚本保存后自动生成视频资产包
module: video-agent
date: 2026-08-08
keywords:
  - save_video_agent_script
  - prepare-scene-packages
  - refreshSnapshot
  - mergeProjectedMessagesWithLocalCards
  - confirm_for_generation
---
## 结论摘要
右侧脚本**仅保存**不再自动生成资产包。用户需点击「确认脚本并生成资产包」（或回复「确认脚本」），经 `confirm_for_generation=true` 写入 `script_plan_confirmed` 后，才启动 `prepare-scene-packages`。Snapshot 刷新不得抹掉聊天回执与执行步骤时间线。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`onConfirmScript`、`startVideoAgentAssetPackageFromScript`）
- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/lib/supervisor/reducer.ts`
- `backend/.../service.py`（`confirm_for_generation`）

## 核心逻辑
1. `onSave` → 只保存 + 提示确认
2. `onConfirmScript` → `saveVideoAgentScript(confirm_for_generation=true)` → `startVideoAgentAssetPackageFromScript`
3. Snapshot hydrate 保留步骤更完整的本地 plan

## 注意事项
- 制作合同默认 seedance-2.0 / gpt-image-2 / 9:16；时长从脚本推断
- 多人戏角色设定不全时确认会被拒绝，需先走全流程补角色
