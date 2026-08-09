---
topic: 脚本入口三路径 A/B/C
module: video-agent
date: 2026-08-08
keywords:
  - script_entry_path
  - polish
  - review
  - compliance
  - export
  - continue_generation
  - 成稿润色
  - script_plan_confirmed
---
## 结论摘要
VideoAgent `submit_turn` 按优先级路由：C 直接成片 > B 成稿润色 > A 全创作 > inspect。  
A 种子 8 步 Skill；B 只种子 `review→compliance→export`，并把用户原文注入 `script_pipeline.episode`（`source=user_complete_script`）；C 仅在脚本已就绪、**已确认**且「继续生成视频」时只做 `inspect` + 前端资产包。多人戏角色设定不清时强制 A。

## 关键文件
- `backend/pixelflow/video_agent/entrypoint.py`（路径判定与 Plan 种子）
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`（B 路径 review/export Prompt）
- `web/src/features/video-agent/scriptSkillStages.ts`（C 前端拦截 + 确认门禁）

## 核心逻辑
1. C：`_workspace_has_generatable_script` ∧ `_is_continue_video_generation` ∧ `script_plan_confirmed`
2. 未确认的 continue → inspect（前端提示确认）
3. B：`_is_complete_script_polish`，但 `script_needs_full_character_plan` 时改 A
4. A：`_should_seed_script_draft` 或角色补全强制

## 注意事项
- 裸「生成视频」不再算 continue
- 旧会话脚本不会自动补场景/道具；B 的 export Prompt 会在缺失时按故事补全设定集
- 详见 `script-plan-confirm-before-assets.md`
