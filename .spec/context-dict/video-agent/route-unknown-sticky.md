---
topic: 路由 unknown 粘住导致无法进入视频
module: video-agent
date: 2026-08-08
keywords:
  - route_decision
  - unknown
  - requires_clarification
  - 帮我生成一分钟广告
  - ConversationRouteService
---
## 结论摘要
会话首轮若路由为 `unknown`（短句/LLM 不可用），`route_decision` 会持久化；旧逻辑后续 Turn 直接复用该决定，即使用户补充「帮我生成一分钟广告」仍提示意图不明。修复：unknown/待澄清必须按新输入重新路由，并允许升级到 `video` + `video_agent_v2`。

## 关键文件
- `backend/pixelflow/agent_runtime/service.py`（`_route_decision_needs_resolution`）
- `backend/pixelflow/agent_runtime/persistence/turn_registration.py`（`_should_persist_route_assignment`）
- `backend/pixelflow/agent_runtime/conversation_router.py`（澄清短句与一分钟广告规则）

## 核心逻辑
1. 已有明确 intent（video/image/ppt/…）→ 粘住，不重算
2. 已有 unknown / requires_clarification → 每轮重新 `route(content)`
3. 新决定明确时覆盖 `route_decision`，并可切换 `orchestration_mode`

## 注意事项
- 澄清短句「创建视频」「视频」等 ≤24 字走规则，避免再依赖 LLM
- 「帮我生成一分钟广告」本身规则本可命中；被粘住的 unknown 才是主因
- 用户侧需新开对话或在本会话再发一次明确视频请求才能升级（已修后端后同会话即可）
