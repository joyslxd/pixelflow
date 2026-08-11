---
topic: 改创意跟进合并上文并重跑 Path A
module: video-agent
date: 2026-08-10
keywords:
  - creative followup
  - merge_video_turn_content_with_history
  - _looks_like_creative_followup
  - inspect
  - 拍立得
  - 确认选题创意
  - Supervisor context
---
## 结论摘要
Supervisor 路由层：明确 intent 粘住，不每轮重跑 LLM；unknown 才重路由。VideoAgent **不是**再做一次 LLM 意图识别选 Skill，而是规则判 A/B/C/inspect。改创意短句若不含「视频」会误落 inspect；已修为：有工作区 brief + 跟进语义 → Path A，并合并 `latest_input`/`start` 与本轮指令。

## 关键文件
- `backend/pixelflow/agent_runtime/conversation_router.py`（跨业务 intent）
- `backend/pixelflow/agent_runtime/service.py`（`start_turn` 合并聊天上文）
- `backend/pixelflow/video_agent/entrypoint.py`（入口路径 + 工作区 brief 合并）

## 核心逻辑
1. 路由：video 粘住后进 VideoAgent；阶段由 `_resolve_script_entry_path` 决定
2. 合并：短「生成带货视频」拼成稿；改创意跟进拼模糊 brief（含「视频」即可，不必 ≥400 字）
3. 工作区兜底：`_workspace_creative_brief` + `_looks_like_creative_followup` → create

## 注意事项
- 当前**没有**「Supervisor 每轮带完整记忆 → VideoAgent 再 LLM 选 stage/skill」链路
- Skill 阶段顺序由种子 Plan 固定；用户自然语言改创意靠重开 Path A，不是跳到某一 stage
- 取消确认后再发跟进，依赖本修复；纯「谢谢」等无跟进语义仍可能 inspect
