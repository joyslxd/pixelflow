---
topic: generate_scenes 报「工具结果无效」
module: video-agent
date: 2026-08-14
keywords:
  - generate_scenes
  - 工具结果无效
  - workspace_mutations
  - quota_interrupt
  - VideoToolRegistry
---

## 结论摘要

Provider 已能启动后，bootstrap `generate_scenes` 仍报「工具结果无效」。与 `prepare_scene_packages` 同类：`GenerateScenesTool` 成功路径总会写 `quota_interrupt`（含显式 `None`），但 `workspace_mutations` 未声明该根键，`VideoToolRegistry.execute` 白名单校验失败并抛 `VideoToolExecutionError`。

修复：mutations 增加 `quota_interrupt`；Registry 校验失败时打 undeclared 根键 warning。

## 相关文件

- `backend/pixelflow/video_agent/tools/scene.py`
- `backend/pixelflow/video_agent/tools/registry.py`
- `backend/tests/test_video_agent_scene_tools.py`
- `.spec/context-dict/video-agent/scene-package-tool-result-invalid.md`（同类先例）

## 核心逻辑

1. Tool 返回 `workspace_patch` → Registry：`set(patch) ⊆ allowed_roots`
2. 未声明根键 → 「工具结果无效」→ bootstrap 表面给用户
3. 直接 `tool.execute` 测不出；必须 `registry.execute`

## 注意事项

- `delivery` / `reference` 若也写未声明 `quota_interrupt`，同样会中招
- 与「缺 creation_contract 参数」是前后两道闸：合同先过，mutations 再拦
