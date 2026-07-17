# 剪映 ZIP 结果与 Plan 场景资产合同修复设计

## 1. 目标

本次改造同时解决两个独立问题：

1. 剪映草稿 Provider 的查询成功结果已由“多个 JSON URL”改为“单个 ZIP URL”，PixelFlow 需要下载该 ZIP、校验后原样上传至 content-app `/api/upload`，继续向前端返回自有 TOS 下载地址。
2. 视频 Plan 初次生成或 Agent 修改后，不能再把“三秒钩子、转场、运镜、声音、全局设定、@图片1”等策划元信息写入 `scene_blueprints[].asset_requirements` 并生成无意义参考图。

本次不改变剪映草稿的异步 job、对话归属、版本幂等、恢复轮询和 PowerMem 终态记录，也不改变视频 Plan 是后续场景包权威执行合同的原则。

## 2. 已确认根因

### 2.1 剪映草稿立即失败

使用本机当前分支和 `backend/config.dev.yml` 真实调用第三方创建接口、结果查询接口，两个接口都返回 HTTP 401、业务码 `40101`、消息“token 缺失或无效”。这说明当前真实请求在第三方鉴权入口被拒绝，尚未创建异步任务。

代码不能伪造鉴权成功。实现完成后的完整成功联调仍依赖第三方重新启用当前固定 token，或提供新的有效 token。

### 2.2 剪映结果合同已经过期

`HttpJianyingDraftSkill` 当前要求查询响应 `data` 为非空 `list[str]`，逐个下载 JSON 后再压缩。如果第三方返回单个 ZIP URL，当前实现会把成功响应判定为“第三方剪映草稿结果为空”。

### 2.3 Plan 资产字段缺少语义校验

Plan LLM 的 `scene_blueprints[].asset_requirements` 当前只做字符串去重。场景包随后无条件把 `characters/scenes/props` 中每个字符串转成 `global_assets`，并为每一项生成图片。因此只要 LLM 把叙事结构或镜头指令放错字段，错误内容就会进入真实生图阶段。

## 3. 总体方案

采用“合同前置校验 + 定向修复 + 执行前防线”：

- 剪映 HTTP Skill 改为单 ZIP 合同，只负责创建任务、轮询、下载 ZIP、校验 ZIP、上传 TOS。
- Plan 初次生成和 Agent 修改都在发布新版本前校验资产语义。
- 发现非法资产时，只让 LLM 修复 `asset_requirements`，不允许改动分镜时长、时间线、故事线、镜头描述、旁白、转场和结构职能。
- 场景包只消费通过校验的权威蓝图；遇到旧历史 Plan 的非法资产时返回可读错误，不在用户审核之后静默删改 Plan。

## 4. 剪映 ZIP 新合同

### 4.1 Provider 响应

创建接口保持不变：

- `POST /api/jianying/draft/tasks`
- 请求体按 `videoOrder` 传有序分镜视频。
- `code=200` 时 `data` 是 Provider 任务 ID。

查询接口保持路径不变，但成功数据改为：

- `POST /api/jianying/draft/tasks/result`
- `code=20201/20202`：继续轮询。
- `code=200`：`data` 必须是单个公开 HTTPS ZIP URL。
- 其他业务码：立即进入失败终态，不因业务失败重复创建任务。

不再兼容旧的 JSON URL 数组合同，避免一套代码同时维护两种含义不同的成功数据。

### 4.2 ZIP 下载与上传

成功结果按以下顺序处理：

1. 校验 Provider ZIP URL 是公开 HTTPS URL。
2. 流式下载到 `TemporaryDirectory` 中的 `.zip` 文件，不把整个压缩包常驻内存。
3. 沿用现有总下载大小上限 200 MiB；网络异常和 HTTP 5xx 按配置最多重试 2 次，4xx 不重试。
4. 使用 `zipfile.is_zipfile()` 和中央目录检查确认文件是非空 ZIP；不解压、不修改、不重新压缩，避免破坏第三方草稿结构。
5. 在线程中复用 `run_generation.upload_file()` 调用 content-app `/api/upload`，携带当前用户 Authorization 上传到 TOS。
6. 只接受 content-app 返回的公开 HTTPS 地址，并生成现有 `JianyingDraftResult` 成功终态。

### 4.3 错误信息

第三方业务失败时，从响应 `message` 中提取长度受限的纯文本原因，拼接到公开错误中。例如：

`第三方剪映草稿任务创建失败：token 缺失或无效`

不得返回第三方 token、Authorization、完整响应对象、堆栈或下载 URL 查询参数。前端仍复用现有失败卡和“重新生成剪映草稿”按钮。

## 5. Plan 场景资产合同

### 5.1 合法资产定义

`asset_requirements` 只允许三类可稳定生成参考图的实体：

- `characters`：具有明确身份或角色名称的人物，不允许商品、产品、场景或镜头术语。
- `scenes`：可以作为画面空间的物理环境或地点，例如“G500头等舱”“万米高空金色云海”“白色产品展示台”。
- `props`：可见、可持有或可操作的商品、包装、工具、家具和物件，例如“蓝妹啤酒瓶”“玻璃杯”“开瓶器”。

下列内容不能成为资产：

- 时间范围、时长和顺序，如“0-3秒”“三秒钩子”“段A”“第一轮误判”。
- 叙事职能和流程词，如“开场”“高潮”“转场”“收束”“答案揭晓”“关键差异指令”。
- 摄影和声音指令，如“穿透运镜”“背景音乐”“旁白”“ASMR”“画面无字幕”。
- 风格和规格，如“8K真人质感”“9:16竖屏”“黄金时刻光影”。
- 未绑定真实附件的占位符，如“@图片1”“@视频3”“参考图集合”。

这些内容可以继续出现在 `title/structure_role/storyline/shot_description/narration/transition` 中，只是不能触发生图。

### 5.2 校验与定向修复

新增独立的资产质量检查函数，输出精确到分镜、字段和非法值的问题列表。Plan 初次生成和 Agent 修改流程执行顺序为：

1. 按现有规则生成并规范化完整 `scene_blueprints`。
2. 运行现有时长、时间线、八维镜头描述校验。
3. 运行新增资产语义校验。
4. 若资产不合法，调用一次专用 LLM 修复：输入当前蓝图、问题列表和用户内容，只返回 `scene_index + asset_requirements`。
5. 应用修复时只覆盖对应分镜的三个资产数组，其他权威字段保持原值。
6. 重新执行完整蓝图规范化和资产校验；仍不合法则不发布新 Plan 版本，并保留当前有效版本。

LLM 提示词同时明确：用户提供的 Seedance 段落、时间标记、镜头指令和参考占位符属于创作内容，不是图片资产名称；实际人物、地点和有形物件才进入资产字段。

### 5.3 场景包执行前防线

`prepare_video_scene_packages_with_llm()` 在把权威蓝图映射成 `global_assets` 前再次运行同一校验：

- 校验通过：按现有逻辑生成角色三视图、场景图和道具图。
- 校验失败：返回明确错误并停止参考图生成，不静默过滤、不修改已审核 Plan。

这样可以保护升级前保存的历史对话，又不会让场景包阶段擅自改变用户确认的创作合同。

## 6. 测试与验收

### 6.1 自动化测试

- Provider 查询返回单个 ZIP URL时，下载一次、原样上传一次并成功返回 TOS URL。
- 查询返回旧数组、空字符串、HTTP URL、非 ZIP、空 ZIP、超限 ZIP、下载 4xx/5xx、上传失败时进入正确失败终态。
- 第三方 40101 等业务失败公开显示安全原因，且不重试业务失败。
- Plan 蓝图中的人物、物理场景、有形道具通过校验。
- “三秒钩子、段A、穿透运镜、背景音乐、9:16、@图片1”等被准确判为非法资产。
- 定向修复只能修改 `asset_requirements`，不能篡改时长、时间线、故事线、镜头描述、旁白和转场。
- 场景包遇到非法历史蓝图时停止，不产生对应图片任务。

### 6.2 真实联调

使用本机当前分支和 `PIXELFLOW_CONFIG_ENV=dev`：

1. 用真实分镜视频调用创建接口并拿到任务 ID。
2. 轮询查询接口直至返回 ZIP URL。
3. 下载 ZIP、校验并调用测试环境 content-app `/api/upload`。
4. 查询本地 `/agent/flows/video/jianying-draft/jobs/{job_id}`，确认终态为 `succeeded` 且下载地址属于自有 TOS。

如果第三方仍返回 `40101`，记录为外部凭据阻塞，自动化合同测试和本地失败原因展示仍需全部通过；获得有效 token 后继续真实成功验收，不以 Mock 冒充真实成功。

场景包回归使用用户提供的完整 Seedance 修改意见，确认最终 `global_assets` 只包含周衡、林悦、G500相关物理环境和真实道具，不包含时间段、钩子、运镜、声音、风格或 `@图片N/@视频N` 占位符。

## 7. 文档同步

实现完成后同步更新：

- `docs/pixelflow-agent-skill-flow-latest-design.md`
- `CONTENT_APP_API_CALLS.md`
- `AGENTS.md` 中剪映 Provider 成功合同和 Plan 资产校验规则

其中外部 Provider 结果应改为“单 ZIP URL”；content-app 调用仍只有最终 `/api/upload`。
