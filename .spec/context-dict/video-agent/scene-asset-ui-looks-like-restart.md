---
topic: 参考图「看起来重新开始」实为同一次 Job + UI 误导
module: video-agent / legacy-workspace
date: 2026-08-10
keywords: generate-scene-assets, 待生成, upsertEarlyScenePackageCard, previewAssets, stopIfHidden, 任务不存在或已过期
---
## 结论摘要
网关重启后旧内存 Job 404 → 前端提示「不存在或已过期、没有自动重启」（正确）。用户手动再选 Seedream 会开**新** Job。同一次 Job 后端只会 `POST .../start` 一次并串行跑完；聊天里再次出现「场景包结构已就绪，参考图生成中 + 五个待生成」**不等于**重新开跑：`upsertEarlyScenePackageCard` 在进度 tick 会重写同卡 tip，而 `previewAssets` 在 `global_assets` 尚无图时固定渲染 5 格「待生成」。切走会话会 `stopIfHidden` 停轮询，终态可能未 apply。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（early card / poll / stopIfHidden）
- `web/src/components/chat/MessageBubble.tsx`（`previewAssets` → 待生成）
- `backend/app/gateway/routers/pixelflow_video.py`（内存 `_SCENE_ASSET_JOBS`）

## 核心逻辑
1. 排障先数网关日志里本会话的 `generate-scene-assets/start` 次数；=1 就不是重跑
2. 再数 `Model: seeddream` / `Task …: completed` 是否继续涨
3. DB 里 tip 固定 id，可能只看到较早的 `x/8` 文案

## 注意事项
- 生成中不要切会话/刷新；否则结果在内存、卡片仍「待生成」
- 重启网关会丢未取走的 Job，只能手动再生成
