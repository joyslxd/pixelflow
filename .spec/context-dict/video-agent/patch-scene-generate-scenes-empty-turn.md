---
topic: 改分镜/生成视频空转「已完成本轮处理」
module: video-agent
date: 2026-08-13
keywords:
  - 已完成本轮处理
  - patch_scene
  - generate_scenes
  - _bootstrap_patch_scene_if_needed
  - resolveVideoScenePackagesForRestore
  - shot_description
---

## 结论摘要

1. **历史恢复崩溃**：`legacyWorkspaceHelpers` 调用 `resolveVideoScenePackagesForRestore` 但未从 `@/lib/scenePackageAssetUi` 导入 → `is not defined`。已补导入。

2. **改分镜 /「生成视频吧」空转**：FE 分镜编辑与确认成片发结构化 Turn，却指望模型自发选 Tool；模型常无 Tool、无正文，`choose_public_response_text` 落到「已完成本轮处理」。与生图确认同类问题。

修复：native_invoke 对「修改分镜 scene-X。…」bootstrap `patch_scene`；对「确认并生成分镜视频 / 生成视频吧」bootstrap `generate_scenes`（无参考图则明确提示）；成功后短接不再进模型。`SceneMutablePatch` 增加 `shot_description`，写入嵌套 text + prompt。

## 相关文件

- `web/src/features/legacy-workspace/legacyWorkspaceHelpers.ts`
- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/tools/scene.py`
- `backend/pixelflow/video_agent/prompts.py`

## 核心逻辑

1. `_parse_structured_scene_patch` / `_parse_generate_scenes_intent`
2. `_bootstrap_patch_scene_if_needed` / `_bootstrap_generate_scenes_if_needed`
3. 短接集合含 `("patch_scene",)` / `("generate_scenes",)`

## 注意事项

- FE 确认按钮即计费闸门；bootstrap 直执 Executor，不经 Gateway 二次确认
- 无参考图时「生成视频吧」返回明确引导，不是空完成语
- 重启后端后生效；前端需刷新以加载 helpers 导入修复
