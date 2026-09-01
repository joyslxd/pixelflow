---
name: pixelflow-video-orchestration
description: 在 PixelFlow 权威视频工作区内，自主组合检查、创意、脚本、分镜、图片资产、视频生成、审片与交付 Tool；只输出安全创作决策，不直接执行外部操作。
metadata:
  pixelflow:
    version: "1.1.0"
  invocation_policy: agent_only
disable-model-invocation: false
user-invocable: false
---

# PixelFlow 视频编排

本 Skill 是视频工作流的应用编排说明，类似 Service 的调用准则，而不是固定 Workflow。
当前 Workspace、Tool Observation 和用户本轮明确输入才是事实来源。不得访问数据库、Provider、
宿主文件或凭据；任何写入、生成、确认和恢复都必须调用受控 Tool。

## 选择 Tool 的顺序

1. 事实不清时先调用 `inspect_video_workspace`；创意、脚本、计划、镜头或图片资产需要细节时，
   分别调用对应 `inspect_*` Tool，不能猜测已有资产或生成状态。
2. 创作目标尚未确认时，先使用 `inspect_creative_brief`，再用 `update_creative_brief` 或
   `select_creative_option`；用户已经确认的品牌事实、角色、时长、画幅和结局不得改写。
3. 脚本与场景包按需使用 `update_script`、`update_video_plan`、`prepare_scene_packages`、
   `create_storyboard` 或 `revise_storyboard`。每个分镜引用已登记的 `asset_id`，不能虚构素材。
4. 图片资产处于 planned 时，先用 `generate_image_assets` 请求生成；该 Tool 可能要求用户确认。
   返回 GenerationJob 后使用 `inspect_image_assets` 查询状态；Gateway 轮询期间停止当前 Run，
   不能自行循环调用或承诺已经完成。
5. 所有视频参考资产 ready 且生产合同已冻结后，才能使用 `create_video` 创建视频；它会按分镜
   素材自动选择文生、图生、首尾帧、多参考、编辑或延展模式，并为每个分镜创建一个受控
   GenerationJob。生成后先用 `inspect_video_results` 查询每镜结果，需要选版时使用
   `review_generated_scenes`。`generate_scenes` 是同一 GenerationJob 能力的兼容入口，新的创作
   请求优先使用 `create_video`。
6. 仅当全部镜头均有已审核版本、没有脏镜头和未解决质检问题时，才请求
   `compose_or_export_video`；若交付 Provider 未装配，说明当前不可执行，不伪造成片。

## 异步、确认与失败

- `pending_operation`、`awaiting_confirmation` 或 `authorization_required` 是本 Run 的终点。
  告知用户下一步或等待 M06 恢复，不继续模型规划或重复发起计费调用。
- Tool 返回 failed/rejected 时，先重新检查 Workspace，再解释可恢复条件；不得把 Provider 原文、
  GenerationJob ID、授权或内部错误回显给用户。
- 所有修改依赖 Gateway 的 revision、Run binding 与幂等校验。不要把“重试”表达为重新创建资产或
  批次；应由相同 Tool 身份回读权威结果。

## 交付准则

最终回复只说明已完成事项、等待中的异步状态，或用户需要确认的少量事实。不要输出 Tool 名称、
内部批次身份、Provider 参数、隐藏推理或未经 Tool 证实的生成结果。
