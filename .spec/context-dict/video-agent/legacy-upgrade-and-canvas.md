---
topic: frontend_v2 历史升级与 Canvas 壳
module: video-agent
date: 2026-08-12
keywords:
  - frontend_v2
  - legacy_upgrade
  - VideoCanvasShell
  - dirty_scene_ids
  - ArtifactCanvasRouter
---

## 结论摘要

历史 `frontend_v2` 会话打开仍只读；首次视频 Turn 或脚本保存时，
`FrontendV2LegacyUpgrader` 映射 context/消息产物到 Workspace，再切
`orchestration_mode=video_agent_v2`。新建 Workspace 后若模式切换失败，
调用 `discard_workspace` 补偿删除，避免部分升级。

右侧编辑经 `native-video-agent/canvas`：`ArtifactCanvasRouter` 按产物 kind
路由；`VideoCanvasShell` 统一头部（含脏镜头数 /「重新生成完成」）；
脚本与场景包复用既有 `AgentScriptPreviewPanel` / `VideoAgentStoryboardSurface`。

## 关键文件

- `backend/pixelflow/video_agent/legacy_upgrade.py`
- `backend/pixelflow/agent_runtime/service.py`（`start_turn` / `save_video_agent_script`）
- `web/src/features/native-video-agent/canvas/*`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑

1. `map_frontend_v2_payload` 收集 script/scene_packages/dirty/merged/artifact_refs。
2. SQL 同库：`commit_legacy_upgrade` 同一事务写 Workspace + 切 mode + 补丁 `__agent_runtime`。
3. Memory：写 Workspace → 切模式；失败 discard 新建 Workspace。
4. 原生模式右侧走 Router；单镜编辑仍经 Turn，不直调 Job API。

## 注意事项

- 幂等：已是 `video_agent_v2` 只回读 Workspace，不再改 mode。
- Runtime 键是 `__agent_runtime`；客户端 context 里的 `agent_runtime` 不是权威命名空间。
- Protocol 新增 `discard_workspace`，Memory/SQL 均已实现。
- QC/Delivery Canvas 目前为摘要占位，完整编辑仍可回场景包面。
