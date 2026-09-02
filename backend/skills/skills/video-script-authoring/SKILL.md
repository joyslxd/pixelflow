---
name: video-script-authoring
description: 将已确认的品牌、故事或脚本整理为 PixelFlow 可审阅的脚本、场景包、资产计划和 Seedance 分镜 Prompt；不直接生成媒体或写入工作区。
metadata:
  pixelflow:
    version: "1.1.0"
  invocation_policy: agent_only
disable-model-invocation: false
user-invocable: false
---

# 视频脚本与场景包

本 Skill 负责“写什么”，不负责“何时调用 Provider”。它必须保留用户已确认的角色数量、商品事实、
道具归属、事件因果、空间关系、画幅、时长和结局；不编造价格、功效、认证或已有素材细节。

## 输出链路

1. 从创意合同提取故事钩子、人物动机、产品自然落点、冲突、转折和收束。
2. 按叙事密度切分镜头，不平均分段。每镜写明地点、主体、连续动作、景别、单一主运镜、光影、
   声音与结束状态；对白放在所属镜头内，保留动作前摇、停顿和非说话者反应。
3. 先创建资产计划：已有素材只绑定稳定 `asset_id`；待生成素材必须具备完整
   `generation_prompt`、角色/产品/空间职责和与后续镜头的依赖关系。
4. 场景包和最终 Prompt 的每个参考都声明 `reference_asset_ids`。planned 资产只能标记为等待就绪，
   不能声称可提交视频生成。
5. 多段内容写明上一段尾帧如何承接、下一段尾帧如何交付。参考素材只继承被明确分配的维度，
   不因引用而继承错误人物、品牌或场景。

## Prompt 质量

- 先用可观察的摄影、构图、光线和动作描述，再写风格或情绪；抽象词必须翻译成镜头语言。
- 一个镜头只安排一个主动作和一个主运镜；复杂手部交接、反常规形变或高密度动作拆镜，或改写为
  可观察结果态。
- 画面默认无字幕；复杂可读文字、价格、UI 与合规文案交给既有后期流程。
- 给用户的说明可以给完整可审阅稿；写回工作区时只提交变更镜头。已有分镜的局部修改走
  `patch_scene` 或 `revise_storyboard`，不要为改第 N 段再输出整包 `prepare_scene_packages`。

## 边界

本 Skill 不读取未提供文件，不观察无法读取的图片，不输出 URL、Token、模型参数或生成结果。
Workspace 写回：首建分镜用 `prepare_scene_packages` 或 `create_storyboard`；已有分镜的局部
修改用 `patch_scene` 或 `revise_storyboard`；仅改脚本正文用 `update_script`。导演 Skill 只在
首建或用户明确要求整份重建时加载，不能作为局部改镜的前置步骤。整份覆盖必须
`replace_existing=true`，否则 Tool 会拒绝。
