---
name: pixelflow-video-orchestration
description: 在 PixelFlow 权威视频工作区内，自主组合检查、创意、脚本、分镜、图片资产、视频生成、审片与交付 Tool；只输出安全创作决策，不直接执行外部操作。
metadata:
  pixelflow:
    version: "1.4.1"
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
3. 脚本与分镜按最小变更选择写入 Tool，每个分镜只能引用已登记的 `asset_id`，不能虚构素材：
   - 尚无分镜：`prepare_scene_packages` 或 `create_storyboard`（二者同义，同一轮只调一次）。
   - 已有分镜、改第 N 镜/第 N 段剧情或 Prompt：`patch_scene`；改多镜且不改资产表：`revise_storyboard`。
   - 仅改脚本正文、不改镜头 Prompt：`update_script`。
   禁止在已有分镜上把「第一段不对」做成 `prepare_scene_packages` 整包覆盖，也不要为此先读导演
   Skill 再重写未改动镜头。工作区已有分镜时，未声明 `replace_existing=true` 的整包写入会被
   Tool 拒绝。
4. 图片资产处于 planned 时，先用 `generate_image_assets` 请求生成；该 Tool 可能要求用户确认。
   若 inspect 显示 failed，先调用 `retry_failed_image_assets` 把原资产改回 planned，再生成；
   不要新建 asset_id 或改写分镜引用。返回 GenerationJob 后使用 `inspect_image_assets` 查询状态；
   Gateway 轮询期间停止当前 Run，不能自行循环调用或承诺已经完成。
5. 所有视频参考资产 ready 且生产合同已冻结后，才能使用 `create_video` 创建视频；它会按分镜
   素材自动选择文生、图生、首尾帧、多参考、编辑或延展模式，并为每个分镜创建一个受控
   GenerationJob。生成后先用 `inspect_video_results` 查询每镜结果，需要选版时使用
   `review_generated_scenes`。`generate_scenes` 是同一 GenerationJob 能力的兼容入口，新的创作
   请求优先使用 `create_video`。
6. 用户明确要求合并/导出，或全部镜头都已有可交付成片（已审核版本，或最新一份
   HTTPS 成片）且没有仍在生成的镜头时，才请求 `compose_or_export_video`。脏镜头
   标记若对应镜头已有成片，可继续交付；Gateway 会拒绝未完成镜头。若交付
   Provider 未装配，说明当前不可执行，不伪造成片。

## 分镜写入与生成前置

- 用户提供视频参考时，先区分“参考风格创作”和“编辑用户源素材”。仅在当前 Manifest 已发布
  相应分析或编辑 Tool 时才使用；参考内容只提炼节奏、结构或风格，不得默认复制人物、品牌或
  具体镜头内容。
- 首建分镜或用户明确要求整份重建时，先读取适用的导演/提示词 Skill，再调用
  `prepare_scene_packages` 或 `create_storyboard`（二者同义，同一轮只择一）。每段 `prompt`
  必须是完整可执行正文，不得把给用户展示的摘要当作可生成 Prompt。
- 已有分镜的局部修改走 `patch_scene` 或 `revise_storyboard`。确需整份覆盖时必须
  `replace_existing=true`；缺省调用会被 Tool 拒绝。
- 用户已上传材料由 Gateway 登记，分镜用稳定 `asset_id` 或 `material_id` 引用。补充产品/角色/
  场景语义只提交 `asset_updates`；尚需制作的资产才进入 `asset_registry`。每段必须在
  `reference_asset_ids` 声明已登记资产，不能在 Prompt 中凭空引用未登记素材。
- `create_video` / `generate_scenes` 前必须已有完整视频生产合同。参数已依据用户确认和当前
  能力选定后，先调用 `set_video_generation_contract`；会实质影响成本或结果但尚未选定时，
  先向用户确认，不得臆造。

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
