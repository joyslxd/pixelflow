---
topic: 失败原因去重与分镜面板单镜生成
module: video-agent
date: 2026-08-14
keywords:
  - formatSceneVideoFailureReason
  - enrichFailedSceneForDisplay
  - 确认并生成分镜视频
  - StoryboardPanel
  - _parse_scene_ids_from_paren
---

## 结论摘要

1. 失败原因套娃：`enrichFailedSceneForDisplay` 已格式化后，`MessageBubble` 再次 `formatSceneVideoFailureReason`，把整段文案塞进「详情」并重复「可点击重试」。修复：已含重试提示或「第 N 镜」开头的文案直接原样返回。
2. 场景包右下角「确认并生成视频」原先走全量 `确认并生成分镜视频`。选中某镜时应只生成该镜：FE 发 `确认并生成分镜视频（scene-x）`，BE 在 mode=all 时优先取括号内 scene_id。

## 相关文件

- `web/src/lib/sceneVideoFailures.ts`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/video_agent/native_invoke.py`

## 注意事项

- 卡片级「确认并生成视频 / 重新生成失败分镜」不带括号，仍全量或失败集合
- 单镜生成不走「无脏镜则拦截」校验，允许对已成功镜单独重跑
- 对话卡片上的失败文案若已 enrich，二次 format 必须幂等
