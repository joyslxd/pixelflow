---
topic: 脚本设定集缺场景道具 + 资产包进度卡消失
module: video-agent
date: 2026-08-08
keywords:
  - /characters
  - 场景设定
  - 道具与产品设定
  - AgentPipelineProgress
  - orphanActivityBlocks
---
## 结论摘要
1. 原 `/characters` 阶段 prompt 只要求角色，导出终稿也未强制场景/道具，导致脚本草稿设定集残缺。已扩展为角色+场景+道具三块，export/review 同步校验。
2. 资产包进度卡依赖 `afterMessageId` 命中消息列表；回执消息被 Snapshot 冲掉或同会话重入 `setActiveConversationId` 清空 steps 时卡片会消失。修复：同 id 不清空进度、锚点回落最近用户消息、ChatPanel 渲染孤儿 activity blocks。

## 关键文件
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `web/src/features/video-agent/scriptSkillStages.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/ChatPanel.tsx`

## 核心逻辑
- characters 阶段强制输出 `## 角色设定` / `## 场景设定` / `## 道具与产品设定`
- export 章节顺序固定包含上述三块
- 进度卡：`steps.length > 0` 即可展示；锚点优先回执消息，找不到则回落用户消息；孤儿块仍渲染

## 注意事项
- 改 prompt 后需新开对话或重新跑脚本流水线，旧终稿不会自动补全
- 重启后端后新会话才会吃到新 STAGE_PROMPTS
