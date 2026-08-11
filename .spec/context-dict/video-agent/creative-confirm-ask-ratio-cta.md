---
topic: 创意确认前追问画幅与结尾引导
module: video-agent
date: 2026-08-11
keywords:
  - production_fields
  - 画幅
  - 结尾行动引导
  - 还需要你确认
  - confirm_script_creative
  - agent.confirmation.requested
  - 180秒
---
## 结论摘要
思考流或 /start 已能识别时长（如 180s）时，若用户未给画幅/结尾引导行动，应在选题创意确认卡追问。探测只看 `latest_input`。确认卡必须靠 `agent.confirmation.requested` 事件携带 `cost_summary` 即时投影，不能只等 Snapshot 刷新（否则常错过追问文案）。

## 关键文件
- `backend/pixelflow/video_agent/production_fields.py`
- `backend/pixelflow/video_agent/executor/service.py`（确认时 emit confirmation.requested）
- `backend/pixelflow/video_agent/executor/events.py`
- `backend/pixelflow/agent_runtime/service.py`（Snapshot 同源摘要）
- `web/src/lib/supervisor/reducer.ts`（消费 confirmation.requested）
- `web/src/features/video-agent/scriptSkillStages.ts`（`creativeConfirmNeedsClarification`）

## 核心逻辑
1. `missing_creative_production_fields(user_text)` → 缺「视频画幅」「结尾行动引导」
2. `creative_confirm_cost_summary` 追加 `还需要你确认` + 已识别时长
3. Executor 进入确认闸门时写 `agent.confirmation.requested`（含 confirmation_id / cost_summary）
4. FE/工具双闸门挡住未补齐时的「同意」

## 注意事项
- 竖屏/横屏也算已给画幅
- `/start` prompt 要求缺字段写「待用户确认」，禁止默认填画幅
- 确认 ID 必须与 Snapshot 的 `video_confirmation_{uuid5}` 一致
