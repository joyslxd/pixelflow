---
topic: 脚本时长推断与分镜切分偏好
module: video-agent
date: 2026-08-08
keywords:
  - video_duration_sec
  - inferVideoDurationSecFromScript
  - PREFERRED_SCENE_DURATION_SEC
  - split_video_duration
  - 60秒
  - 10秒
---
## 结论摘要
脚本直出资产包出现「3 镜 ×10s=30s」有两层原因：  
1) FE `inferVideoDurationSecFromScript` 旧正则吃不进 `**时长**：60秒`（`时长` 与 `：` 之间夹了 `*`），回落默认 30s；  
2) 后端 `PREFERRED_SCENE_DURATION_SEC` 曾为 10，等分偏好约 10s/镜（Seedance 硬上限其实是 15s，不是 10s）。  
已修：时长正则兼容 Markdown 加粗 + 时间轴末尾推断；切分偏好改为 15s。

## 关键文件
- `web/.../LegacyWorkspace.tsx`（`inferVideoDurationSecFromScript`）
- `backend/pixelflow/creative/duration.py`
- `backend/pixelflow/creative/scene_blueprint.py`（`_fallback_scene_count`）

## 核心逻辑
1. 60s + preferred 15 → `[15,15,15,15]`（4 镜）
2. 旧 30s + preferred 10 → `[10,10,10]`（用户看到的三镜）
3. 单镜硬上限仍是 Seedance 的 4–15 秒

## 注意事项
- 已生成的场景包不会自动变长，需用修好后的合同重新「继续生成视频」
- Plan LLM 路径仍可按故事密度非等分；无 blueprint 的脚本直出走 `split_video_duration`
