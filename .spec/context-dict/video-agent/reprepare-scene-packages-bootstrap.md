---
topic: 重新生成分镜包空转「已完成本轮处理」
module: video-agent
date: 2026-08-14
keywords:
  - 重新生成视频分镜包
  - 已完成本轮处理
  - prepare_scene_packages
  - failsafe
  - 口头答应
  - AgentPipelineProgress
---

## 结论摘要

「重新生成视频分镜包」若模型长思考后只回复「好的，立刻…」却不发原生 Tool Call，旧逻辑会把非空正文当成功结束，**不跑 failsafe**，UI 卡住且无新分镜包。

修复：首轮 LLM 只要 `tool_names` 不含 `prepare_scene_packages`，立刻 `_failsafe_prepare_scene_packages`；不再二次 astream（避免数分钟空转 + response_delta event_id 冲突）。

执行规划卡：默认收在输入框上方底栏（collapsed），点击展开；`prepare` running 时无论旧包是否存在都 `createAssetPackageProgressSteps` 重置，按最新重拆流程推进。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `web/src/features/video-agent/AgentPipelineProgress.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/tests/test_video_agent_native_invoke.py`

## 核心逻辑

1. reprepare 判定 → 入模提示强制 Tool Call → 若无 prepare → failsafe 直执。
2. 进度卡固定 `composerTopSlot`；`defaultCollapsed`。
3. `nativePrepareToolSignal=running` → 重置四步进度；completed → awaiting_image_model / completed。

## 注意事项

- 禁止把口头文案当重拆成功。
- 「重新生成分镜视频」仍是 generate_scenes。
- snapshot 恢复不得在 packagesRunning 时把进度盖回旧「待看分镜」。
