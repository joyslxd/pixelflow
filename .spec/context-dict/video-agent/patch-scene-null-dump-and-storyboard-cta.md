---
topic: 改镜补丁误失败与缺少查看分镜
module: video-agent
date: 2026-08-14
keywords:
  - 镜头补丁参数无效
  - exclude_unset
  - patch_scene
  - 旁白（对白）
  - reference_asset_ids
  - 查看分镜
  - turnOffersScenePackageStoryboard
---

## 结论摘要

1. **「镜头补丁参数无效」假失败**：`VideoToolRegistry.execute` 校验后 `model_dump()` 把未设置的 Optional 填成 `null`，`PatchSceneTool` 二次校验触发「不能把字段写为 null」。活动区显示失败摘要，但 bootstrap 仍回「已更新分镜」假成功。修复：dump 使用 `exclude_unset=True`；bootstrap 对空 `workspace_patch` 按失败处理。
2. **镜头正文被截断**：解析把正文里的「旁白（对白）：」当成独立字段。改为只认 FE 的「旁白：」，并支持「参考素材：」→ `reference_asset_ids`。
3. **缺「查看分镜」**：`turnOffersScenePackageStoryboard` 仅认 `prepare_scene_packages`。补上 `patch_scene` 与「已更新分镜」文案。

## 相关文件

- `backend/pixelflow/video_agent/tools/registry.py`
- `backend/pixelflow/video_agent/tools/scene.py`
- `backend/pixelflow/video_agent/native_invoke.py`
- `web/src/features/native-video-agent/state/selectors.ts`

## 注意事项

- 保存分镜后应看到「查看分镜」可重新打开资产包
- 重启后端后 registry 修复才生效
