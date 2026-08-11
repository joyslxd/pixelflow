---
topic: 脚本确认卡无 workflow 身份导致 Snapshot fatal
module: video-agent
date: 2026-08-10
keywords:
  - 没有可确认的脚本工作区
  - 会话 Agent 状态恢复失败
  - 脚本方案待确认
  - projectMessage
  - Snapshot fatal
  - scriptPlanConfirmForAssets
  - 121653e87eaf43b88a05a10aaf093f00
---
## 结论摘要
重登后脚本工作区消失、提示「恢复失败 / 没有可确认的脚本工作区」，根因不是脚本丢了（DB 里 script 仍在），而是 Snapshot 里一条「脚本方案待确认」助手消息带 `plan` artifact、却没有 `run_id/workflow_id/artifact_ref`。前端 `projectMessage` 把整份 Snapshot 判非法 → connection fatal → `videoAgentWorkspace` 不 hydration。资产包「任务不存在或已过期」是热重载后内存 job 404，与脚本丢失无关。

## 关键文件
- `web/src/lib/supervisor/workspaceProjection.ts`（`projectMessage`）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`ensureDurableScriptPlanMessage`）
- `web/tests/supervisorWorkspaceProjection.test.mjs`

## 核心逻辑
1. 本地脚本确认卡允许无 workflow 身份，仍投影消息+artifact
2. 残缺/冲突身份（给了但对不上）仍 fail
3. Snapshot 成功后右侧脚本面板可恢复；资产包需从确认入口重新发起

## 注意事项
- 该会话 `last_phase=scene_package_job_resume_failed`，旧 job 已不可续，需重新「确认脚本并生成资产包」
- 后端 workspace.revision=12、`script_plan_confirmed=true`，数据在
- 修后需硬刷新前端（Vite 热更或刷新页面）清掉 fatal 连接态
