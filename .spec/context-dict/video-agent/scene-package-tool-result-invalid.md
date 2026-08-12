---
topic: prepare_scene_packages 成功后报「工具结果无效」
module: video-agent
date: 2026-08-12
keywords:
  - prepare_scene_packages
  - 工具结果无效
  - workspace_mutations
  - scene_package_job
  - VideoToolRegistry
---

## 结论摘要

「工具结果无效，请稍后重试」不是 LLM/Operation 失败，而是 `VideoToolRegistry.execute` 在 Tool 返回后做根键白名单校验失败：`workspace_patch` 的根键必须是 `tool.spec.workspace_mutations` 的子集。

`PrepareScenePackagesTool` 成功路径会写 `script_plan_confirmed` / `scene_package_job` / `quota_interrupt`，但原先 `workspace_mutations` 只声明了 `global_assets/scenes/scene_packages/creation_contract`。租约修好后 LLM ~55s 成功返回，立刻撞上这道闸门。

## 关键文件

- `backend/pixelflow/video_agent/tools/registry.py`（抛错点）
- `backend/pixelflow/video_agent/tools/scene_packages.py`（mutations 声明与 patch 内容）
- `backend/tests/test_video_agent_scene_package_tools.py`

## 核心逻辑

1. Tool `execute` 成功 → Registry 比较 `set(result.workspace_patch) ⊆ allowed_roots`
2. 不一致 → `VideoToolExecutionError("工具结果无效，请稍后重试")` → Executor `fail_step` → UI 步骤失败
3. 修复：把成功/轮询路径实际写入的根键全部加入 `workspace_mutations`（generate 同理：`scene_asset_job` / `scene_asset_failures` / `quota_interrupt`）

## 注意事项

- 直接测 Tool 不会发现此 bug；必须经 `VideoToolRegistry.execute`
- `delivery` / `reference` / `generate_scenes` 等 Tool 也可能在 patch 里写了未声明的 `quota_interrupt`，属同类潜伏问题，改那些路径时一并核对
- 与「Operation start租约无效」是前后两道闸：租约先过，mutations 再拦
