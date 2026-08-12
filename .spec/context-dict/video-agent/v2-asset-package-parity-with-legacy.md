---
topic: V2 资产包应对齐旧工作流结构与卡片
module: video-agent
date: 2026-08-12
keywords:
  - prepare_scene_packages
  - target_duration_ms
  - buildAssetPackagePlanMarkdown
  - video_scene_packages
  - global_assets
---

## 结论摘要

V2 `prepare_scene_packages` 曾默认 `target_duration_ms=30000`（拆成恰好 2 镜），且只用 raw `script.content`、未合并 `creation_contract`/设定集 markdown；结果再经 `projectVideoWorkspaceSnapshot` 丢掉 `global_assets` 与提示词，对话里也没有旧工作流的 `video_scene_packages` 卡片。

修复：Tool 侧对齐旧 FE——推断时长、拼接 characters+export markdown、`build_video_creation_contract` 写入 form；投影保留完整 `scenePackages/globalAssets`；LegacyWorkspace 在 V2 下 upsert 同款资产包卡片。

## 关键文件

- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `web/src/features/video-agent/state/workspace.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/scriptSkillStages.ts`（`buildAssetPackagePlanMarkdown` 对照）

## 核心逻辑

1. 时长优先级：workspace `creation_contract` → `form_values.video_duration_sec` → 脚本时长/时间轴推断 → 请求参数
2. markdown：export/终稿 + 缺设定标题时前置 characters 阶段
3. 成功 patch 含 `global_assets/scene_packages/creation_contract/target_duration_ms`
4. FE 固定 message id `video-agent-workspace-scene-packages:{workspaceId}` 投影卡片

## 注意事项

- 仅修输入与展示；结构 LLM 仍走 `prepare_video_scene_packages_with_llm`
- 有 Plan `scene_blueprints`+`asset_manifest` 时应继续走机械快路径（本轮未接，脚本直出仍走 LLM）
- 重启后端后再确认脚本，旧会话里已生成的 2 镜薄包不会自动变厚
