---
topic: 参考图 Operation 冲突与分镜 patch @ 校验
module: video-agent
date: 2026-08-14
keywords:
  - generate_scene_assets
  - Operation同步终态或完成事件持久化冲突
  - scene_asset_job attempt
  - patch_scene
  - 镜头补丁参数无效
  - 旁白（对白）
---

## 结论摘要

1. 确认 Seedream 后 `generate_scene_assets` 报「Operation同步终态或完成事件持久化冲突」：同步终态落库与僵尸完成事件/并发重试冲突；同指纹 attempt=1 易撞车。修复：`record_start_terminal` 幂等回读已终态；抬高 `scene_asset_job.attempt`；start lease 提到 2h。
2. 分镜保存「镜头补丁参数无效」：模型可能传 `shot_description` 对象；FE 用 `。` 拼接且文案含「旁白（对白）」。修复：对象 coerce 取 text；识别「旁白（对白）」；FE 字段用换行拼接。

## 关键文件

- `backend/pixelflow/agent_runtime/jobs/completion.py`
- `backend/pixelflow/video_agent/adapters/scene_package_operation.py`
- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `backend/pixelflow/video_agent/tools/scene.py`
- `backend/pixelflow/video_agent/native_invoke.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 注意事项

- `@` 引用全局素材应在编辑器里选 chip（`@安然`），不要手打 `安然@盯着`。
- 若会话仍有大量 completion_dispatch 冲突日志，可新开对话或清僵尸 Operation 后再生图。
