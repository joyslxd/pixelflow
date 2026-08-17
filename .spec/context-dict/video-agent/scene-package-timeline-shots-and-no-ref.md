---
topic: 时间线分镜镜数与没有参考图续步
module: video-agent
date: 2026-08-13
keywords:
  - 0—10秒｜
  - extract_script_shot_entries
  - prepare_scene_packages
  - 没有参考图
  - scene_asset_model_options
  - turnOffersScriptPreview
---

## 结论摘要

1. 用户粘贴 `0—10秒｜标题` 时间线成稿时，旧抽取只认 `镜头N「00:00-00:05」`，镜数掉到默认 30s→2 镜；须走 `_TIMELINE_SHOT_PATTERN`。
2. 「没有参考图」不是生产字段补丁，也不是成片；FE 应弹出 `scene_asset_model_options`，选模型后再 Turn → `generate_scene_assets`。
3. `prepare_scene_packages` 完成后结论气泡应提供「查看分镜」打开分镜资产包（不再用「在右侧查看脚本」）。

## 相关文件

- `backend/pixelflow/creative/script_shots.py`
- `backend/pixelflow/video_agent/tools/scene_packages.py`（`_infer_timeline_end_sec`）
- `backend/pixelflow/video_agent/production_fields.py`（`looks_like_scene_asset_continue`）
- `backend/pixelflow/video_agent/native_invoke.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx` / `legacyWorkspaceHelpers.ts`
- `web/src/features/native-video-agent/state/selectors.ts`

## 核心逻辑

1. 无「镜头N+时码」时回退 `N—M秒｜标题` 抽取蓝图
2. V2 结构就绪投影：`sceneAssetsAwaitingModel = !hasImages`，并自动弹出 `scene_asset_model_options`
3. `handleSend`：`isNoRefImageContinueRequest` 且包 ≥4 镜无图 → 直接选模型卡；≤2 镜交给 Turn，服务端按脚本重拆
4. `native_invoke`：`_scene_packages_need_script_refresh` 时确认脚本 /「没有参考图」都会重跑 prepare

## 注意事项

- 「确认并生成视频」不得命中无参考图续步
- 选模型卡是确认闸门，不是前端私自开旧 Job
- 当前对话若仍是错误 2 镜：再发「没有参考图」会触发重拆后再选模型