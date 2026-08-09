---
topic: 脚本预览可编辑保存
module: video-agent
date: 2026-08-08
keywords:
  - AgentScriptPreviewPanel
  - save_video_agent_script
  - video-agent/script
---
## 结论摘要
右侧脚本预览支持编辑并保存。前端 `AgentScriptPreviewPanel` 进入编辑态后用 textarea 改 Markdown；保存调用 `PUT /agent/conversations/{id}/video-agent/script`，服务端按 `expected_revision` CAS 写入 `workspace.script` 并追加 `script_versions`，随后 refreshSnapshot。

## 关键文件
- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/lib/supervisor/api.ts`
- `backend/pixelflow/agent_runtime/service.py`（`save_video_agent_script`）
- `backend/app/gateway/routers/pixelflow_conversations.py`

## 核心逻辑
1. 保存请求：`{ markdown, expected_revision }`
2. revision 不匹配返回 409 `video_agent_script_conflict`
3. 新版本 `source=user_edit`，`status=ready`，`review_required=false`

## 注意事项
- 不改 DB schema；只 patch workspace payload。
- 保存后依赖 snapshot 刷新右侧预览；若用户同时开着多标签可能撞 revision。
- 保存成功后会自动启动视频资产包生成，详见 `script-save-asset-package.md`。
- 标题里的 `vN` = `workspace.script.version`（episode/export/用户保存每次 +1）；`工作区 rN` = `workspace.revision`（任意 workspace patch CAS +1，与脚本版本无关）。
- 非编辑态用内置轻量 Markdown 渲染（标题/列表/表格/加粗等）；不依赖 `@uiw/react-markdown-preview`（本地常未装齐会炸 Vite）。
- 编辑态仍是纯文本 textarea。
