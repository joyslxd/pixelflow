---
name: image-generation
description: 为 PixelFlow 视频项目规划角色、产品、道具与场景参考图，并安全编排图片资产生成和状态核查；不直接调用图片 Provider。
metadata:
  pixelflow:
    version: "1.0.0"
  invocation_policy: agent_only
disable-model-invocation: false
user-invocable: false
---

# 图片资产生成

图片资产是视频一致性的输入，不是独立的“随手出图”步骤。先读取当前 Workspace、已上传素材和
资产注册表，区分已有素材与 planned 资产；不得把用户未提供的图片内容当作事实。

## 资产规划

- 角色：需要时规划大头照锁骨相、全身图锁服装体态；同一角色不能被拆成多个新人物。
- 产品：锁定已确认的瓶型、比例、包装色、关键部件与品牌事实；不补写可读包装文字或功效承诺。
- 场景：用空场景锚定空间、家具、主光和机位；不从场景参考中继承未经确认的人物。
- 道具：只为镜头叙事和可见操作所需的关键道具创建资产，避免为凑参考数重复生成。

每个 planned 资产使用稳定 `asset_id`、可读 `slot`、`kind`、`role` 和完整 `generation_prompt`。
Prompt 至少包含主体、材质或外观、背景、光线、视角、不可改变的品牌事实和连续性约束。

## 生成与核查

1. 资产计划写入后，先通过 `inspect_image_assets` 核查 ready、planned、running 与 failed 状态。
2. 只选择 `state=planned` 且包含 `generation_prompt` 的资产调用 `generate_image_assets`。计费确认、
   瞬时授权和幂等由 Tool Broker 强制处理；每个资产对应一个 Gateway GenerationJob。
3. Tool 返回 GenerationJob 后，调用 `inspect_image_assets` 读取安全进度。异步任务未完成时结束当前
   Run，不能自行轮询 Provider 或重新创建 GenerationJob。
4. 全部视频依赖资产 ready 后，才建议调用 `generate_scenes`；任何 failed 资产先说明失败数量和
   可恢复的重新规划条件，不回显 Provider 原始异常。

## 边界

本 Skill 不生成图片、不访问 URL、文件系统、数据库或 Provider。它只提供资产设计和 Tool 选择
规则；真实图片、Artifact 绑定与 Workspace revision 由 PixelFlow Gateway、GenerationJob Worker
和受控 Tool 完成。
