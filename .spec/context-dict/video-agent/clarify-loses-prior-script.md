---
topic: 澄清短句丢失上文成稿
module: video-agent
date: 2026-08-08
keywords:
  - latest_input
  - merge_video_turn_content_with_history
  - complete_script_payload
  - 生成带货视频
  - unknown
  - context
---
## 结论摘要
不是「context compaction 把历史压没了」，而是 **VideoAgent 热路径只吃本轮 `body.content`**。用户先贴完整 `/episode` 被判 unknown 澄清后，再发「生成带货视频」时，`submit_turn` 把短句写成 `latest_input`，8 步 Skill 只看见短句。修复：短视频跟进指令合并最近成稿/长 brief；成稿粘贴直接规则路由为 video。

## 关键文件
- `backend/pixelflow/agent_runtime/service.py`（`start_turn` 合并上文）
- `backend/pixelflow/video_agent/entrypoint.py`（`merge_video_turn_content_with_history`）
- `backend/pixelflow/agent_runtime/conversation_router.py`（`complete_script_payload` / 带货澄清）

## 核心逻辑
1. 登记 Turn 后、调用 `submit_turn` 前：读会话 user 消息，排除本轮，合并进 `video_content`
2. 合并条件：本轮是短跟进（≤48 或继续生成类），上文结构分 ≥3 或篇幅 ≥400
3. 成稿首轮：`looks_like_complete_shooting_script` → `RouteIntent.VIDEO`，避免先澄清

## 注意事项
- 合并后的成稿 +「生成带货视频」通常走路径 B（polish），不是空创意 8 步
- 可见聊天消息仍是短句；工作区 `latest_input` 才是 Skill 权威输入
- compaction 仍可能摘要旧消息，但本问题发生在进 VideoAgent 之前的丢字段，与压缩无关
