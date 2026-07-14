# PixelFlow 视频 Plan 分镜蓝图设计

## 目标

视频策划 Agent 在生成 `plan.md` 时就完成整片叙事调度和逐分镜规划，不再先按 10 秒均分、再由场景包 Agent 重新创作另一套分镜。Plan LLM 必须应用项目内 `seedance-prompt/SKILL.md`，输出可校验、可版本化、可直接供场景包继续生产的结构化分镜蓝图。

## 根因

当前链路在调用 Plan LLM 前先执行 `split_video_duration(total, preferred=10)`，并把固定时间线作为不可修改输入交给 LLM。Plan 响应只保存 Markdown 与时长数组；场景包阶段只收到总时长，随后再次按 10 秒切分并重新调用 LLM。结果是：

- LLM 没有真正的分镜数量和时长调度权。
- `plan.md` 里的镜头描述与最终场景包不是同一份生产蓝图。
- Plan 版本历史不能保存每个分镜的故事线、镜头描述、旁白和转场。
- 修改或回退 Plan 后，场景包仍可能重新猜测时长和内容。

## 采用方案

采用“单次结构化 Plan LLM + 后端强校验 + 场景包严格消费”方案。

不采用仅修改 Prompt 的方案，因为 Markdown 无法稳定传递到后续阶段；也不采用每次都执行两次 LLM 的方案，因为正常链路会增加不必要的延迟和成本。LLM 输出违反时长约束时，后端保留标题、故事线、镜头描述、旁白、转场和资产语义，只重新调度非法时间线；结构无法修复时才使用确定性蓝图兜底。

## 数据合同

视频 Plan 响应新增 `scene_blueprints`，每项包含：

| 字段 | 说明 |
| --- | --- |
| `scene_id` / `scene_index` | 稳定分镜标识和顺序 |
| `title` | 分镜标题 |
| `structure_role` | `opening`、`development`、`climax`、`conclusion` |
| `start_sec` / `end_sec` / `duration_sec` | 全局连续整数秒时间线 |
| `storyline` | 当前分镜叙事目标和因果 |
| `shot_description` | 符合 Seedance Skill 的整段中文镜头描述，内部使用当前片段的局部秒段 |
| `narration` | 旁白或明确无旁白 |
| `transition` | 与下一镜头的衔接方式 |
| `asset_requirements` | 计划使用的人物、场景、商品/道具语义名称，不包含尚未生成的 URL |

`scene_blueprints` 必须与 `scene_durations_sec` 一一对应，并同时保存到每个 `plan_history` 版本。历史对话缺少该字段时按旧合同兼容，但新生成或新修订的视频 Plan 必须返回完整蓝图。

## LLM 规划规则

Plan LLM 输入包括视频模板、用户确认合同、创意方向、行业画像、素材摘要和完整 Seedance Skill 指导。它不再接收预先固定的 10 秒时间线，而是接收以下可行域：

- 总时长必须精确等于 `creation_contract.video_duration_sec`。
- 每个分镜为 4-15 个整数秒。
- 叙事必须形成开场、展开、高潮/证明、收束的总分总结构。
- 分镜数量和时长由内容密度、旁白长度、动作复杂度和转场决定，禁止机械等分。
- 每个镜头只承担一个主要叙事目标，镜头描述遵守 Seedance Skill 的秒级、动作、运镜、光影、声音与连续性规则。
- PowerMem 长期记忆只能影响 LLM 的内部决策，禁止把“长期记忆约束”、记忆原文或 Agent/Skill 运行日志写入面向用户的 `plan.md`。

LLM 输出 `plan_markdown`、`scene_blueprints`、`scene_image_ratio` 和 `scene_image_size`。`plan.md` 的“镜头列表”必须完整呈现结构角色、全局时间、时长、故事线、镜头描述、旁白和转场。

## 校验与修复

后端统一校验：

1. 分镜数量处于当前总时长的可行范围。
2. 每段时长是 4-15 的整数，且总和精确等于用户时长。
3. `start_sec/end_sec` 从 0 开始连续、无重叠、无空洞。
4. 镜头描述使用秒，不含 `ms`、毫秒或小数时间码。
5. 首镜为 opening，末镜为 conclusion，中间至少覆盖 development；三段以上应包含 climax 或证明节点。
6. `storyline`、`shot_description` 和 `transition` 非空；`narration` 可明确为空。

第一次输出不合法时，优先对原始蓝图做语义保留式时间线修复：按照原始时长权重重新分配整数秒，同时缩放镜头描述中的局部秒段。若分镜数量、内容字段或结构无法修复，使用按叙事权重分配的确定性兜底；兜底也必须产生完整蓝图，并满足全部硬约束。

## 场景包消费规则

`prepare-scene-packages` 请求新增 `scene_blueprints`。场景包 Agent：

- 直接采用蓝图的分镜数量、顺序和时长，不再调用 `split_video_duration()` 重新切分。
- 以蓝图的故事线、镜头描述、旁白和转场为权威内容。
- 只负责把 `asset_requirements` 解析成全局人物、场景、道具资产，生成稳定 `asset_id`，并在镜头描述中补齐合法的 `@asset_id` 与 mentions。
- 可以根据实际资产名称润色表达，但不得改变叙事目标、时间线、模型、画幅或转化目标。
- 历史请求没有蓝图时继续走旧兼容路径。

## 版本、恢复与前端

- `PlanMarkdownResponse`、对话快照、Plan 恢复结构和场景包请求都携带 `scene_blueprints`。
- Plan 修订必须重新返回并校验蓝图；历史版本保存对应蓝图快照。
- 回退 Plan 时同时恢复 Markdown、创作合同、时长数组和蓝图。
- 前端展示仍以 `plan.md` 为主，不新增额外用户步骤。

## 验证

- 单元测试证明 LLM 可为 26 秒返回如 `6/12/8` 的内容驱动时长，而不是固定 10 秒。
- 单元测试证明 Plan Prompt 包含 Seedance Skill、总分总和自主时长调度要求。
- 场景包测试证明它严格继承蓝图时长和故事内容。
- 版本历史、回退、旧对话兼容、前端类型和构建测试通过。
- 使用测试环境 content-app 从采集、表单、创意、Plan、场景资产、分镜视频到合并视频跑通一条真实链路，并记录最终 URL、实际分镜时长和模型参数；Token 不写入仓库或日志。
