# PixelFlow 智能剪辑需求与技术设计

> 状态：需求设计，未开始实现
>
> 依据：`DEEPSEEK_HARNESS_SIDECAR_IMPLEMENTATION_PLAN.md`、`skills/agent-extension-governance/SKILL.md` 和当前 `backend/pixelflow/edit/` 雏形。

## 1. 结论

智能剪辑应作为新的 `edit` 领域能力接入现有 Harness，而不是新增一个“剪辑 Agent”或固定剪辑工作流。模型只负责识别意图、提出剪辑方案和选择受控 Tool；Gateway 负责 Workspace、权限、revision、确认、幂等和最终状态；渲染由 Gateway 管理的 FFmpeg/剪映草稿 Provider 完成。

第一版定位为“可审计的时间线编辑器”：用户上传的视频或 PixelFlow 生成的视频都先成为 Workspace Artifact，Agent 将自然语言意图转换为工具无关的 Timeline IR，用户确认后再执行渲染。不能承诺任意视频的一键专业剪辑，也不自动复制第三方作品的素材、音乐、字幕、人物或品牌元素。

## 1.1 对用户流程总结的适配评审

你总结的十步流程适用于 PixelFlow，建议按下面的边界落地：

| 用户流程 | PixelFlow 落点 | 边界调整 |
| --- | --- | --- |
| 素材分析 | `analyze_edit_material` + `VideoUnderstandingPort` + `ffprobe` | 关键帧提取和质量检测由 Gateway Worker 做；视觉模型只输出带置信度的分析，不直接改时间线 |
| 镜头筛选 | `propose_edit_timeline` | “删除”只表示从草案 Timeline 排除，永不删除原始 Artifact；重复/冗余判断可被用户恢复 |
| 故事结构 | 领域 Skill + Timeline 草案 | 抒情、快节奏、宣传片、纪录片是风格参数，不创建四套固定 Workflow；最终顺序须可预览和修改 |
| 自动剪辑/转场 | `patch_edit_timeline` + `FfmpegEditProvider` | 转场、调色、画幅统一由 Provider 执行，模型不能生成任意 FFmpeg 参数 |
| 配乐/素材管理 | `MediaAsset`、授权清单、音乐 Capability Provider | Suno 属于外部生成 Provider；剪映库和开源库必须保存许可证、来源、署名和商业使用状态 |
| 音画混合 | `AudioTrack` + 音频 Service | ducking、响度、峰值和淡入淡出应由可测试的音频规则执行，模型只提出目标 |
| 音效增强 | `AudioOverlay` + 受控素材 Catalog | 自动匹配只能引用已登记、授权状态明确的音效 Artifact |
| 字幕包装 | ASR Capability + `OverlayTrack` | ASR 结果先作为可编辑草案；片名、字幕、贴纸和字体均需安全区及白名单校验 |
| 质量检查 | `validate_edit_timeline` + `inspect_edit_result` | 黑帧、花帧、音画同步、响度和越界检查必须是代码规则，不只依赖视觉模型 |
| 输出/版本 | `EditRenderJob` + Artifact/Projection | 母版、竖屏、横屏、无音乐、带字幕是同一 Timeline 的派生版本；每个版本保存输入 revision 和授权快照 |

其中“叙事价值”“情绪贡献”“动作完整度”属于模型辅助评分，不应作为不可解释的硬删除条件。建议 P0 先实现确定性媒体处理、可逆草案和质量门禁，再将节拍、ASR、镜头语义和自动音效逐步加入 P1。

## 2. 需求边界

### 2.1 P0（必须支持）

- 片段选择、裁剪、排序、拼接、删除和静音。
- 原声保留、替换或混音；用户提供的音乐/音效 Artifact 作为输入。
- 基础转场：`cut`、`fade`、`dissolve`，时长受时间线合同限制。
- 花字/标题/字幕叠加：文本、时间区间、位置、字号、颜色和字体白名单。
- 单轨贴纸/图片叠加：位置、缩放、透明度和时间区间。
- 输出平台、画幅、分辨率、帧率、总时长和编码格式。
- 预览 Timeline、差异、预计时长与失败原因；确认后异步渲染并回写 Artifact。

### 2.2 P1（完成 P0 后再做）

- 自动节奏点：依据音频节拍或语音停顿推荐切点，用户确认后写入 Timeline。
- 自动字幕：接入现有视频理解/ASR Port，结果必须可编辑且标注来源。
- 多轨音频 ducking、响度归一化、降噪和淡入淡出。
- 画面内目标跟踪贴纸、简单关键帧和模板化字幕样式。
- 剪映草稿导出作为可选交付，不作为 Gateway 的权威状态。

### 2.3 明确不做

- 不在 Harness/Plugin 内直接执行 FFmpeg、剪映、浏览器自动化或文件系统读写。
- 不开放任意 JavaScript/Python、Shell、MCP 或用户自定义命令作为剪辑能力。
- 不自动抓取或复用第三方视频、音乐、字体、Logo、人物肖像和完整文案。
- 不承诺“输入一个链接即可复刻原片”；参考片只能生成抽象的节奏、结构和镜头语言分析。
- 不在第一版实现专业 NLE 的全部能力（复杂遮罩、3D、调色节点、运动跟踪、插件生态）。

## 3. 用户意图到 Tool 的边界

模型先从用户话术中提取四类事实：目标（例如“做成 15 秒商品短视频”）、素材范围、编辑动作和交付约束。缺少素材、时间区间、授权或输出约束时，只追问最少必要问题。

建议的稳定 Tool：

| Tool | 类型 | 作用 |
| --- | --- | --- |
| `inspect_edit_workspace` | 只读 | 返回 Artifact、Timeline、版本、媒体元数据和可用轨道 |
| `analyze_edit_material` | 外部读取 | 调用视频理解/ffprobe/ASR，返回镜头、节拍、字幕和质量摘要 |
| `propose_edit_timeline` | 业务写入 | 将用户意图转换为 Timeline 草案，必须带 expected revision |
| `patch_edit_timeline` | 业务写入 | 增删片段、调整顺序、转场、花字、贴纸和音频参数 |
| `validate_edit_timeline` | 只读 | 校验素材授权、时间范围、轨道冲突、编码和平台约束 |
| `render_edit_video` | 计费/异步 | 用户确认后创建 GenerationJob/渲染 Job，返回 job 身份，不直接返回伪造成片 |
| `export_edit_draft` | 异步交付 | 可选生成剪映草稿或通用导出包；不改变权威 Timeline |
| `inspect_edit_result` | 只读 | 读取渲染 Job 和最终 Artifact 的公开状态 |

Tool DTO 必须与 FFmpeg、剪映或具体供应商无关。Provider 差异仅存在于 `capabilities/video_editing/providers/` 的 Adapter 和能力档案中。

## 4. 领域模型与 Workspace

新增 `backend/pixelflow/edit/` 领域合同（现有 `Timeline`、`Clip`、`DraftPlan` 可演进但不应直接作为浏览器 DTO）：

- `EditWorkspace`：`workspace_id`、`owner`、`conversation_id`、`revision`、`timeline_id`、`artifact_ids`、`status`。
- `EditTimeline`：有序 `VideoTrack`、`AudioTrack`、`OverlayTrack`、输出合同和总时长。
- `EditClip`：`artifact_id`、源区间、目标区间、速度、音量、转场和裁剪方式。
- `EditOverlay`：花字、字幕、贴纸或图片；只引用已登记 Artifact/字体白名单。
- `EditAnalysis`：分析摘要、来源、置信度、版本和过期时间；不是编辑结果的事实来源。
- `EditRenderJob`：渲染 Job 与 Provider Job 的映射、状态、幂等键和公开错误码。

Workspace Repository 是唯一权威写入方。每次 Timeline 变更都产生新 revision；前端只能提交带 `expected_revision` 和 `client_command_id` 的 Workspace Command。Harness Session 只保存决策轨迹，不保存权威时间线。

## 5. 运行时与技术栈

```text
浏览器 -> /agent Controller -> AgentRuntimeService
       -> AgentHarnessPort -> DeepSeek Harness Sidecar
       -> Capability Tool Plugin -> Gateway Tool Broker
       -> Edit Service/Repository -> Timeline Workspace
       -> RenderJob Worker -> FFmpeg Provider / Jianying Draft Provider
       -> Artifact + Snapshot/SSE
```

- Controller/Service/Repository：沿用现有 FastAPI、Pydantic、PixelFlow Repository 和 `/agent` 路由。
- Agent 编排：沿用通用 DeepSeek Harness；新增领域规则放在 `skills/.../SKILL.md`，不改通用系统指令。
- 媒体探测：`ffprobe` 作为只读元数据能力；不得让 Sidecar 调用。
- 渲染：Linux Worker 上的 FFmpeg 作为 P0 主 Provider；HyperFrames 可作为 P1 的 HTML 动效/多轨合成 Provider；剪映仅做 P1 草稿导出 Adapter。渲染进程使用固定参数白名单、资源目录和超时/资源上限。
- 分析：复用 `VideoUnderstandingPort`；ASR、节拍分析、目标跟踪分别定义 Capability Port，可替换 Provider。
- 状态与可靠性：P0 剪辑渲染使用 `EditRenderJob -> worker -> Workspace 回写`，不冒充图片/视频 `GenerationJob`，也不恢复旧 Batch/Child/M06 图片视频生成编排。若未来某个剪辑 Provider 明确计费，再为该能力定义独立的计费 Job 合同，并由 Gateway 管理 lease/recovery。
- 前端：只消费 `EditWorkspaceProjection` 与有序 Snapshot/SSE，不读取 FFmpeg 命令、Provider 响应或 Sidecar 私有事件。

## 6. 剪辑 Skill 的职责

新增领域 Skill，例如 `video-editing/SKILL.md`，只写：

- 如何把自然语言意图拆成目标、素材、节奏、音画和交付约束。
- 各平台常见时长、画幅、字幕可读性和节奏建议。
- 何时先分析素材、何时先提出 Timeline 草案、何时请求用户确认。
- 转场、配乐、花字和贴纸的质量检查清单。
- 第三方参考作品只能提炼抽象结构，必须输出差异化创作点。

Skill 不写 Token、Provider 地址、数据库表、FFmpeg 命令、授权判断、费用判断或 revision 规则。上述内容由 Gateway Tool Broker/Service 强制执行。

## 6.1 HyperFrames 的接入定位

HyperFrames 官方公开定位是把 HTML、CSS、JavaScript、媒体和可 seek 的动画渲染为确定性 MP4；其工程包含 CLI、Core/Engine/Producer、Catalog、Agent Skill 和 Studio，渲染链路使用无头 Chrome/Puppeteer 逐帧采集并由 FFmpeg 编码/混音。它特别适合：

- 踩点后的动态图形、扫描/闪白/遮罩转场、阶段标题、花字、字幕、贴纸和品牌动效。
- 需要精确到帧、可重复渲染、可做回归测试的广告片合成。
- 将一条已确认的 `EditTimeline` 翻译成 HTML composition，再输出 MP4 或透明 Overlay。

它不应成为 PixelFlow 的权威时间线格式，也不应由 Harness Plugin 直接调用。推荐增加：

```text
capabilities/video_editing/port.py
  ├─ FfmpegEditProvider      # P0，基础剪辑与合成
  └─ HyperFramesEditProvider # P1，HTML 动效、复杂 Overlay、可复现合成
```

`HyperFramesEditProvider` 只接收经 Gateway 校验后的 Provider 无关 `EditTimeline`/`RenderPlan`，在隔离 Worker 中生成临时 HTML 项目并执行 HyperFrames CLI；HTML/JS 不得来自用户原文直接执行，必须经过模板白名单、资源白名单、网络禁用、CPU/内存/时长限制和产物清理。Node.js、无头 Chrome、FFmpeg 版本应固定在渲染镜像中，不能依赖宿主机安装。

采用条件路由：基础裁剪/拼接/音频处理走 FFmpeg；包含复杂动效、字幕动画、WebGL/Canvas 或透明 Overlay 时才选择 HyperFrames。Provider 失败、超时或版本不兼容时，返回脱敏错误码，不由模型静默改用另一 Provider；是否改用替代 Provider 必须经过用户确认或明确的非实质性降级策略。

## 7. 安全、授权与确认

1. 上传素材先登记为 Artifact，记录 owner、来源、媒体类型、授权声明和过期时间；未登记 Artifact 不可进入 Timeline。
2. `analyze_edit_material` 可按用户确认的素材范围读取；不把原始视频、Cookie、Token、完整评论或供应商异常写入日志。
3. `patch_edit_timeline` 是可回滚的业务写入，必须校验 user/session/run binding、manifest、revision 和幂等键。
4. `render_edit_video`、`export_edit_draft` 属于异步或可能计费动作，必须在 Gateway 产生唯一确认记录后执行；确认只适用于对应 Tool。
5. Provider 只收到短期授权票据或受控 Artifact URL；Sidecar 永远拿不到用户 Authorization、Provider Secret、数据库连接或宿主文件系统权限。
6. 所有恢复入口（`user_turn`、`confirmation_resume`、`form_resume`、`authorization_resume`、`run_recovery`）继续注入同一通用系统指令，并只附加最小的剪辑上下文。

## 8. 分阶段交付

### M0：合同与纯逻辑

冻结 `EditWorkspace`、Timeline IR、Projection、Tool Manifest、错误码和幂等规则；补齐 `timeline.py`、`draft_plan.py` 的 Pydantic 校验与单测。此阶段不执行真实渲染。

### M1：草案编辑

实现 inspect/propose/patch/validate Tool、Repository、revision 冲突和前端 Timeline 预览。只允许用户确认后的本地测试 Artifact，验证用户 Turn 与恢复 Run。

### M2：FFmpeg 渲染

实现 `render_edit_video`、RenderJob Worker、FFmpeg Provider、start/poll/失败回写和 Snapshot/SSE。先支持拼接、裁剪、基础转场、配乐、花字和贴纸；真实 Provider 请求必须单独获得确认。

### M3：分析增强与草稿导出

接入 ASR/节拍/目标跟踪等可替换 Capability Provider；增加剪映草稿 Adapter、差异预览和 Golden Journey。不得把剪映工程文件当作权威 Workspace。

## 9. 必测场景与验收门禁

- Tool 缺少 Artifact、授权、revision、确认或 Run binding 时失败；补齐前置条件后成功。
- 同一 `client_command_id`、Tool Call 和 RenderJob 重试只产生一个业务结果。
- 两个并发 patch 使用同一 revision 时，一个成功、一个得到安全冲突摘要；不得丢失用户修改。
- 用户 Turn、confirmation_resume、authorization_resume、form_resume、run_recovery 都能读取同一 EditWorkspace 事实来源。
- FFmpeg 超时、输入损坏、缺少音轨、转场超时和 Provider 失败均映射为脱敏错误码，并能恢复或安全终止。
- 成片 Artifact 只在渲染终态回写；前端不通过浏览器轮询 Provider，也不读取内部命令。
- 测试执行：目标 Python/TypeScript 合同测试、Ruff、前端类型检查、`git diff --check`、中文工程门禁；默认不执行真实计费 Provider。

## 10. 最终产品承诺

对用户应表述为：“你可以上传素材或使用已生成视频，用自然语言描述剪辑目标；PixelFlow 会先生成可预览、可修改的剪辑方案，确认后渲染成片。”

不要表述为：“输入任意链接即可一键复刻原视频。”这既超出当前技术边界，也会引入素材版权、平台条款、肖像权和音乐授权风险。
