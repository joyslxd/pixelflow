# PixelFlow 智能剪辑技术方案

> 状态：技术设计，未开始实现
>
> 更新日期：2026-09-14。已补充首期单 Docker Worker、任务队列、TOS 存储、音乐来源及恢复方案；服务器部署、镜像验证、容量压测和完整剪辑服务尚未执行。
>
> 架构依据：`DEEPSEEK_HARNESS_SIDECAR_IMPLEMENTATION_PLAN.md` 与 `skills/agent-extension-governance/SKILL.md`。

## 1. 设计原则

- 不新建“剪辑 Agent”或固定 Workflow；使用同一个通用 Harness Agent。
- Gateway 是 Workspace、权限、确认、渲染 Job、Artifact 和业务终态的唯一权威写入方。
- Sidecar 只做模型决策、Skill 发现和受控 Tool 调用，不访问数据库、Provider、文件系统或用户凭据。
- Timeline 是 Provider 无关的领域合同；FFmpeg、HyperFrames、剪映草稿、ASR、音乐库都只是可替换 Adapter。
- 剪辑计划可逆、渲染异步、结果可审计；不恢复旧图片/视频 Batch、Child 或 M06 生成编排。

## 2. 总体架构

```text
浏览器
  → /agent Controller
  → AgentRuntimeService / Snapshot / SSE
  → AgentHarnessPort
  → DeepSeek Harness Sidecar
  → Capability Tool Plugin
  → /agent/internal/agent-tools/calls
  → Gateway Tool Broker
  → Edit Service / EditWorkspace Repository
  → EditRenderJob Worker
  → FFmpeg / HyperFrames / ASR / 音乐 Provider
  → Artifact Repository / EditWorkspace Projection
```

### 2.1 首期运行形态

首期采用 **单个 Docker Worker + 任务队列 + TOS 对象存储**。优先完成稳定可用的分析、时间线编辑、渲染、质检和交付，暂不引入 Kubernetes 或函数服务。

```text
用户上传素材到受控 TOS 空间并完成 Artifact 登记，提交剪辑要求
  → /agent Controller 接收请求并返回稳定任务身份
  → Harness Agent 依据 Skill 自主选择分析、规划、编辑和渲染 Tool
  → Gateway Service 保存 Workspace / Job，并可靠投递队列通知
  → 单个 Docker Worker，每次领取 1 个可执行任务
      ├─ 分析任务：媒体探测、抽帧、音频分析与受控 Provider 请求
      └─ 渲染任务：读取冻结 RenderPlan → FFmpeg 渲染与混音 → 质量检查
  → 输出上传 TOS，Gateway 发布 Artifact、任务终态和 Snapshot/SSE
```

单 Worker 是执行资源的部署选择，不代表新建一个自动决定所有业务步骤的剪辑 Agent。素材选择、故事组织、时间线规划和是否调用音乐 Provider 仍由通用 Harness Agent 通过 Tool 决策；Worker 仅执行已授权任务中的确定性处理及明确指定的 Provider 请求。

第 6.3.1 节的 Probe、Frame、Audio Worker 是逻辑执行模块，首期可运行在同一个 Worker 容器内，不要求分别部署。分析结果先由 Gateway 领域 Service 持久化，再供 Agent 决定后续动作，不在 Worker 中维护另一套 Agent loop。

API 不等待完整剪辑完成。用户请求的任务身份用于关联会话与剪辑 Workspace，具体分析和渲染 Job 各有稳定 ID；提交剪辑要求不等于已经创建或确认渲染 Job。渲染 Tool 被授权调用后立即返回 `render_job_id`，前端通过 Gateway Snapshot/SSE 展示排队、分析、待确认、渲染、质检、上传与完成进度，不直接消费队列或轮询 Provider。

## 3. 分层职责

| 层 | 组件 | 职责 |
| --- | --- | --- |
| Controller | `/agent` Router | 用户身份、请求 DTO、Snapshot/SSE 与中断 API |
| Harness | Sidecar + 通用系统指令 | 理解意图、加载 Skill、选择 Tool，不持有业务状态 |
| Skill | `video-editing/SKILL.md` | 剪辑方法、故事/节奏建议、质量清单和 Tool 选择建议 |
| Tool Broker | `agent_tools` | Run binding、Manifest、owner、revision、确认、幂等和安全摘要 |
| Domain | `pixelflow/edit` | Timeline、版本、分析、渲染 Job、业务规则和 Projection |
| Provider | `capabilities/video_editing` | FFmpeg、HyperFrames、ASR、音乐/音效 Catalog 与外部 Adapter |

## 4. 领域模型

- `EditWorkspace`：owner、conversation、revision、状态、Timeline 与 Artifact 引用。
- `EditTimeline`：`VideoTrack`、`AudioTrack`、`OverlayTrack` 与输出合同。
- `EditClip`：Artifact 引用、源/目标时间范围、速度、音量、裁剪和转场。
- `EditOverlay`：字幕、标题、贴纸、Logo；仅能引用已登记资源和字体白名单。
- `EditAnalysis`：镜头/音频分析摘要、时间范围、置信度、来源与过期信息。
- `EditAnalysisJob`：异步分析任务，记录输入 Artifact 版本、分析 Profile、状态和租约；长耗时分析不占用单次 Tool 请求。
- `MediaLicense`：来源、作者、许可证、署名、商业使用和平台限制。
- `EditRenderJob`：稳定 Job ID、输入 revision、冻结 Timeline/RenderPlan 引用、渲染 Profile、镜像摘要、Provider job 身份、状态、幂等键、执行尝试、租约版本和脱敏错误码。
- `EditArtifact`：成片及派生版本，记录输入、质量报告和授权快照。

每次 Timeline 写入均由 Repository 创建新 revision。前端请求必须带 `expected_revision` 与 `client_command_id`；Harness Session 绝不保存权威 Timeline。

## 5. Tool 合同

| Tool | 费用/副作用 | 职责 |
| --- | --- | --- |
| `inspect_edit_workspace` | 只读 | 读取素材、Timeline、版本和输出状态 |
| `analyze_edit_material` | 异步分析/可能计费 | 创建或回读分析 Job，执行媒体探测、场景/动作/ASR/节拍分析，持久化受控分析结果 |
| `propose_edit_timeline` | Workspace 写入 | 依据意图生成可审阅的草案 |
| `patch_edit_timeline` | Workspace 写入 | 修改轨道、片段、字幕、音频和包装 |
| `validate_edit_timeline` | 只读 | 时间轴、平台、授权与质量前置校验 |
| `render_edit_video` | 异步/可能计费 | 确认后创建 `EditRenderJob` |
| `inspect_edit_result` | 只读 | Job、质量报告和 Artifact 回读 |
| `export_edit_draft` | 异步交付 | 可选导出剪映草稿或项目包 |

每次 Tool 调用必须校验 user、conversation、workspace、Run binding、Manifest digest、revision 和幂等键。可能计费、渲染或外部导出操作必须经过唯一确认记录。

确认记录绑定具体动作、输入 revision、参数摘要和费用范围；只在这些条件仍有效时复用，重试不重复确认，输入或费用范围变化时重新确认。分析及音乐调用是否计费必须由 Provider 能力和 Service 判断，不能因 Tool 被标为“读取”而跳过费用约束。

## 6. Provider 技术栈

### 6.1 P0：FFmpeg 渲染 Provider

- `ffprobe`：媒体元数据、音轨、帧率、时长与编码读取。
- FFmpeg：裁剪、拼接、缩放、补边、基础转场、混音、响度、编码和质量抽样。
- 生产镜像必须固定完整 FFmpeg 构建：`libfreetype`、`fontconfig`、`libass`、`HarfBuzz`、必要的编码器和中文字体资源；依赖存在不代表相关滤镜可用，必须执行构建能力检查和实际媒体样例验证。
- FFmpeg 参数只能由 `RenderPlan` 模板生成；不得拼接用户输入形成可执行命令。

### 6.2 P1：HyperFrames 包装 Provider

HyperFrames 适合字幕动画、花字、扫描/遮罩转场、贴纸、Logo、WebGL/Canvas 动效和透明 Overlay。它把受控 `RenderPlan` 翻译为 HTML Composition，再由无头 Chrome/Puppeteer 与 FFmpeg 确定性渲染。

- 使用隔离 Worker；固定 Node.js、Chrome、FFmpeg 与 HyperFrames 版本。
- HTML/JS 必须来自模板/组件白名单，禁止直接执行用户正文或模型输出。
- 仅允许受控 Artifact URL、网络禁用、CPU/内存/时长限制和临时目录清理。
- 基础剪辑走 FFmpeg；复杂包装才选择 HyperFrames。Provider 不得静默互相降级。

### 6.3 分析与媒体 Provider

- 视频理解：`VideoUnderstandingPort`，提供镜头、动作、场景、质量与叙事建议。
- ASR：独立 `SpeechRecognitionPort`，生成可编辑字幕草案。
- 节拍/响度：`AudioAnalysisPort`，输出 beat、VAD、LUFS 与峰值。
- 音乐/音效：`MediaCatalogPort` 与可选 `MusicGenerationPort`；必须返回 `MediaLicense`。

### 6.3.1 分级素材分析流水线

参考“先用本机媒体工具检查，再让模型审阅画面”的实现方式，推荐把分析拆为低成本确定性层与高价值语义层。这样不会把每个完整视频都直接提交给视觉模型。

```text
已登记 Video Artifact
  → Probe Worker：ffprobe 元数据
  → Frame Worker：FFmpeg 低清关键帧/联系表 + 技术异常检测
  → Audio Worker：静音、VAD、响度、峰值、节拍
  → Candidate Service：按规则选出候选片段
  → VideoUnderstandingPort：仅审阅候选片段/联系表
  → EditAnalysis Projection：证据、时间区间、置信度、建议
  → propose_edit_timeline：生成可编辑草案
```

#### A. Probe Worker：零模型成本的媒体事实

使用 `ffprobe` 读取时长、分辨率、帧率、编码、色彩信息、视频/音频流和章节信息。这些是技术事实，写入 `EditAnalysis`，供后续渲染约束、平台适配和质量检查使用。

`ffprobe` 不负责人物动作、情绪、叙事价值、镜头重复语义或字幕识别；不要将它的元数据结果误当作内容理解。

#### B. Frame Worker：低成本候选镜头生成

使用 FFmpeg 以自适应间隔抽取低分辨率关键帧，并生成带时间戳的联系表（contact sheet），但只保存在 Worker 临时目录或登记为受控分析 Artifact。结合 `blackdetect`、`freezedetect`、`blurdetect`、场景变化阈值和可选的曝光统计，生成：

- 黑帧、冻结帧、明显模糊和技术异常区间；
- 初步场景边界和每个候选镜头的时间范围；
- 供视觉模型或人工审阅的低成本缩略图证据。

抽帧频率不是固定 `fps=1`：静态素材可 1–2 秒一帧，动作/快切素材先做场景检测再在候选边界附近加密抽帧。原始视频不复制、不删除，临时帧在 Job 结束后按保留策略清理。

#### C. Audio Worker：不使用 FFprobe 的音频理解

`ffprobe` 只能报告音频流信息，不能计算 beat、VAD、LUFS 或峰值。应使用 FFmpeg 的 `ebur128`、`astats`、`silencedetect` 做响度/峰值/静音检测，并使用独立音频算法库或 Provider 计算 VAD 与 beat。输出仅是时间点与测量值，避免保存用户原始音频内容。

#### D. 语义理解层：Gemini 或其他 `VideoUnderstandingPort` Provider

仅把候选片段、低清联系表或经用户确认的完整素材送入模型，以判断人物、动作完整度、表情、场景含义、镜头重复语义、情绪曲线和故事贡献。Gemini 可以作为一个 Adapter，但领域 Service 只能依赖 `VideoUnderstandingPort`；Provider 更换不改变 Tool、Skill 或 Timeline DTO。

模型输出必须包含 `source_artifact_id`、时间范围、置信度和可解释摘要。模型只能提出“建议保留/排除/排序”，不能删除原始 Artifact、直接启动渲染或绕过用户确认。

#### E. 联系表的正确使用方式

其他 Agent 通过 Python/PIL 将抽帧拼成联系表的方法适合原型验证；生产实现中，PIL/FFmpeg/Python 只属于 Gateway 管理的 Analysis Worker，不属于 Harness Agent。模型只通过 `analyze_edit_material` 收到脱敏、受控的分析投影或短期 Artifact 引用，不直接扫描宿主 `Downloads` 目录。

#### F. 音乐选择边界

“默认配温柔、无歌词的抒情纯音乐”只能是风格建议，不能默认下载或使用任意外部曲目。`MediaCatalogPort` 必须先按用途、时长、节奏和许可证筛选已登记素材；若没有适用且可商用的音乐，应请求用户上传、选择授权库素材或确认调用音乐生成 Provider。

### 6.4 首期能力范围与质量判断

| 环节 | 首期方案 | 结果与边界 |
| --- | --- | --- |
| 素材检查 | FFprobe 读取时长、分辨率、帧率、音轨、旋转和色彩元数据 | 形成技术事实，不代替内容理解 |
| 镜头分析 | 镜头切分、关键帧抽取及视觉 Provider 理解候选内容 | 保存时间范围、来源、置信度和受控证据 |
| 质量评估 | 检查模糊、曝光异常、黑帧、重复内容和明显生成瑕疵 | 技术检测和语义建议分开记录，不承诺检测所有生成瑕疵 |
| 镜头选择 | 综合画面质量、动作完整度、人物情绪和叙事价值 | Agent 根据 Skill 提出保留与排序建议，不删除原始素材 |
| 时间线规划 | 按主题组织故事，控制镜头长度与整体节奏 | 形成可编辑、可审阅、可冻结的 Timeline |
| 画面处理 | 裁剪、拼接、适度转场，按需统一画幅、曝光与色彩 | 操作写入 RenderPlan，不盲目对所有镜头自动调色 |
| 字幕包装 | 按需 ASR、编辑字幕、片名和署名 | 基础静态包装走 FFmpeg；复杂动效仍属于 P1 |
| 导出检查 | 可解码性、时长、音轨、音画同步、异常黑帧及字幕显示 | 硬性错误阻止发布；美学疑点进入可审阅质量报告 |

质量评分只作为筛选依据，不能机械删除暗场、空镜、静止镜头或有意留黑。检测到的异常必须对照 Timeline 的创作意图；对白、人物动作和音乐衔接不能仅按画面分数裁断。

Timeline 内部统一使用整数时间单位并明确源时间与目标时间的映射，禁止依靠浮点秒反复累计。RenderPlan 解析源媒体时间基、可变帧率、旋转、声道、采样率及 SDR/HDR 色彩差异；不支持的组合在渲染前明确拒绝或要求选择已验证的转换 Profile，不静默改变画面或音频语义。

### 6.5 音乐与音效来源、授权和混音

| 来源 | 接入方式 | 首期范围 |
| --- | --- | --- |
| 授权音乐库、音效包 | `MediaCatalogPort` 按风格、情绪、节奏、时长和授权条件检索，再经受控 Client 导入 | 至少支持已登记的授权音频素材；外部目录按需接入 |
| 用户上传 | 上传 TOS 并登记 Artifact、来源和用途授权声明 | 支持用户指定配乐、音效，不以“已上传”推定任意用途均获许可 |
| Suno 生成音乐 | 可选 `MusicGenerationPort` Adapter | 仅保留候选接入点；官方接口可用性、自动化方式和使用权须在接入前核实，不作为首期依赖 |

剪映音乐库不能默认按开源素材处理，也不能默认具有自动下载权限。具备允许的获取方式及适用授权后再接入；合法取得的音频可通过统一导入流程使用。这里规定的是接入门槛，不对某一平台当前授权条款作结论。

每份音乐/音效记录来源、作者、许可证或授权声明、证据引用、署名要求及适用平台、商业用途等限制。License 快照与实际使用的音频 Artifact 版本绑定，生成音乐同样需要记录适用的来源与权利依据。

音乐选择、生成或更换在渲染请求冻结前完成；待音乐 Provider 返回并登记 Artifact 后再继续，不让 FFmpeg 渲染阶段临时发起新的付费音乐生成。外部生成需幂等请求、Provider Job ID 回读和明确的预算边界，不恢复旧媒体 Batch/Child Operation 编排。

混音按 RenderPlan 完成时长适配、允许的裁切或循环、淡入淡出、目标响度和峰值控制。对白区间来自 VAD/ASR 等分析，在对白出现时压低背景音乐，并按创作需求保留环境声。目标 LUFS、峰值、采样率及声道由已验证输出 Profile 指定并冻结，不能套用未确认的平台标准。

首期默认交付一份标准 MP4，编码、分辨率、帧率、音频和字幕方式由输出 Profile 固定。字幕版、无音乐版及其他画幅作为显式派生输出按需创建，不默认重复渲染。基础 ASR 按需启用，无可用 Provider 时允许用户提供字幕或确认不添加字幕。

## 7. Job、恢复与事件

`render_edit_video` 只创建或回读 `EditRenderJob`；Worker 执行已冻结 RenderPlan。长耗时分析使用 `EditAnalysisJob`，不能把分析、Agent 规划和最终渲染混成一个无检查点的进程。两类 Job 共用队列投递和租约机制的基础设施，但保留各自领域语义。

Worker 属于 Gateway 管理的后端执行面，可用受限服务身份访问其必需的任务 Repository、TOS 和 Provider。所有业务写入经领域 Service/Repository 执行；这项权限不授予 Sidecar，也不允许 Worker 持有无限范围的宿主访问权。Job 状态经 Gateway 投影为 Snapshot/SSE，浏览器不轮询 Provider。

### 7.1 数据库与队列一致性

- 数据库 Job 是任务状态的权威来源；队列只承载 Job ID 和必要的非敏感路由元数据，不携带用户正文、完整 Timeline、签名 URL 或凭据。
- 创建 Job 与投递意图在同一数据库事务中保存，以 Outbox 可靠投递；队列重复通知是允许的。实现应复用已有可靠投递组件，但必须验证其覆盖这些语义，不能假设现有队列天然满足。
- Job 表周期扫描补投漏通知及到期任务。队列丢失不等于任务丢失，队列为空也不能证明数据库不存在待执行 Job。
- 至少一次投递配合数据库原子领取；终态或已被有效租约领取的 Job 收到重复通知时不再执行。成功、失败、延迟重试或恢复责任持久化后再确认消息，不能在落库前确认且依赖 Worker 内存继续执行。
- `analyze_edit_material` 和 `render_edit_video` 的调用幂等绑定现有 Broker 身份。相同身份和摘要返回原 Job；相同身份但输入改变返回冲突，不能重复创建任务。

### 7.2 状态、租约与重试

建议执行状态为 `queued`、`running`、`retry_wait`、`succeeded`、`failed`、`cancelled`。`phase` 单独表达探测、分析、渲染、质检和上传；待用户确认属于 Workspace/Run 中断状态，不伪装成正在执行的 Job。

每次领取原子写入 `lease_owner`、租约截止时间和递增的 fencing token（执行权版本）。心跳定期续租；进度、检查点、终态和发布必须携带当前执行权版本做条件更新。旧 Worker 即使继续运行，也不能覆盖新 Worker 的结果。租约失效或取消时终止 FFmpeg 进程组，独立监督进程保证渲染阻塞不会阻断心跳。

瞬时网络或资源故障有限重试并退避，记录次数与下次执行时间；输入损坏、授权不足、不支持的 Profile 或明确质量失败不能无条件重复执行。超过上限进入可诊断失败状态，保留受控原因码。外部请求已提交但结果不明时先核对 Provider Job 或幂等结果，不盲目再次产生费用。

Worker 重启后按数据库和不可变输入重新领取过期任务，不依赖上次容器的本地目录。完整且校验通过的分析结果、音频 Artifact 和已上传输出可以复用；不承诺从任意 FFmpeg 帧位置继续，缺少有效渲染检查点时从冻结 RenderPlan 重新渲染。

用户取消先持久化取消请求，Worker 在阶段边界及运行中检查并停止子进程。取消与发布竞争由数据库条件更新裁决；若已经发布成功，返回真实终态，不能把已交付成片宣称为已撤销。

### 7.3 TOS 上传与原子发布

1. 每个任务和执行尝试使用独立临时目录及 TOS 暂存对象键；输入通过已登记 Artifact 解析，不接受任意宿主路径或 URL。
2. 渲染后执行约定的质量门禁，通过后上传输出并校验对象存在、大小和内容校验信息。TOS 对象保持私有，通过 Gateway 权限检查获取受控访问方式。
3. 在一个数据库事务中登记输出 Artifact、质量报告和授权快照，绑定 Job 结果并更新终态，同时保存事件投递意图；只有当前有效执行权可提交。
4. 上传成功但事务未提交时，由重试核对同一尝试对象后恢复发布，或把它作为孤立对象清理。数据库成功但事件未送达由 Outbox 重放，不重新渲染或重新发布 Artifact。
5. 任务绑定冻结的输入 revision。若 Workspace 已被用户编辑，结果仍归属原输入版本，不覆盖更新后的 Timeline 或最新结果指针；前端明确标识该结果基于旧版本。
6. 成功发布后清理临时文件；清理失败记录并由回收任务处理，不把已成功任务重新置为失败。失败/取消尝试也需按有限保留期限清理，禁止无限占用本地或 TOS 空间。

TOS 与数据库没有共同事务，以上暂存、条件提交与孤立对象回收是必须实现的恢复路径，不能用“上传后标记完成”省略失败窗口。

### 7.4 Run 恢复与进度

所有 `user_turn`、`confirmation_resume`、`form_resume`、`authorization_resume` 和 `run_recovery` 必须使用同一通用系统指令，只叠加最小剪辑上下文。恢复时重读权威 Workspace revision，不假设旧 Sidecar Session 有效。

分析或渲染完成事件通过现有受控恢复入口供 Agent 观察，按事件身份去重，不新建固定阶段调度器。进度按 Job、执行尝试和事件序号持久化、合并发送；重试展示新的尝试，不伪造连续百分比。用户刷新后可从 Snapshot 重建状态。

## 8. 安全与合规

- Artifact 先登记 owner、来源、媒体类型、用途和授权声明，未登记素材不可进入 Timeline。
- Secret、Token、Cookie、完整用户正文和 Provider 原始异常不得进入仓库、日志、事件或测试夹具。
- 音乐、音效、字体和贴纸必须保存 License 快照；不符合本次用途、平台及商业使用要求时拒绝渲染或要求用户替换。
- Sidecar 不持有 Authorization、Provider Secret、数据库连接或宿主文件系统权限。
- 输出 Artifact 与授权清单一一关联，支持审计和复查。
- 外部媒体先经白名单 Client 下载至隔离目录并验证类型、大小和校验信息，渲染阶段使用本地受控资源；避免 FFmpeg 隐式访问任意网络协议。
- 复现资料包括冻结 Timeline/RenderPlan、素材版本与摘要、Profile、镜像摘要和随机种子（如适用），存于受控业务存储。错误诊断只保存白名单错误码和脱敏信息，不能原样保留包含路径、签名 URL、字幕正文或凭据的 FFmpeg stderr。

## 9. 实施顺序与门禁

1. M0：冻结 DTO、Repository、Projection、错误码、Tool Manifest，补充纯 Timeline/RenderPlan 单测。
2. M1：实现异步分析、草案、patch、revision 冲突、预览 Projection 与用户/恢复 Run 回归测试；完成上传音频及已登记授权素材的引用、基本混音和静态字幕合同，ASR 按需接入。
3. M2：实现单 Docker Linux FFmpeg Worker、队列可靠投递、数据库租约、`EditRenderJob`、TOS 发布、质量检查、资源压测和端到端 Golden Journey，交付首期标准 MP4。
4. M3：按业务需求接入 HyperFrames、增强 ASR/节拍、外部授权 Catalog 和剪映草稿导出；生成音乐待接口与授权核实后独立接入，不阻塞首期。

必须覆盖：缺少 Artifact/授权/revision/确认/Run binding 的拒绝；幂等重试；并发 revision 冲突；Job 超时与 Provider 失败；所有恢复入口；媒体授权缺失；质量检查失败。默认测试不得发起真实计费 Provider 请求。

补充故障验收：数据库提交后投递失败、队列重复/丢消息、领取后崩溃、心跳中断后双 Worker 竞争、旧租约迟到发布、磁盘不足、OOM、FFmpeg 超时、取消与发布竞争、TOS 上传成功但数据库提交失败、终态成功但 SSE 投递失败、Workspace 已更新时旧 Job 完成。必须证明不会重复发布、覆盖新 revision 或在外部结果不明时重复计费。

## 10. 首期部署基线与资源门禁

| 项目 | 首期拟实施方案 | 验证要求 |
| --- | --- | --- |
| 服务器 | 火山引擎 ECS，先以 4 vCPU、8 GiB 内存压测 | 作为起始测试规格，不承诺可承载任意分辨率、时长或特效 |
| 运行方式 | 单个 Docker Worker，一次处理 1 个任务 | API、Sidecar 与 Worker 分离职责；若共享主机，为 API/Sidecar 留出资源 |
| 基础环境 | Ubuntu 24.04 LTS | 用户此前选定的基线，实际镜像与补丁在构建时锁定并验证 |
| 媒体引擎 | 发行版 FFmpeg 6.1.x 作为候选基线 | 核对实际包版本、构建选项、滤镜和编码器；不将此表视为已验证兼容组合 |
| 字体依赖 | FreeType、Fontconfig、libass、HarfBuzz 和已授权中文字体 | 验证中文、标点、换行、字体回退、字幕/文字渲染及字体许可证 |
| 队列 | 优先复用满足第 7.1 节语义的现有队列；无合适组件时选 Redis 队列 | Redis 实现需具备确认、重投与持久化能力；数据库仍负责权威状态和补投 |
| 任务状态 | 数据库持久化，原子领取与心跳续租 | 不能只存 Worker 内存或 Redis 临时键 |
| 文件存储 | TOS 保存输入、可复用中间 Artifact 与成片；本地磁盘为临时工作区 | 限制输入下载、临时数据与输出规模，校验上传和访问隔离 |
| 版本固定 | 构建后按固定镜像摘要部署 | 保存依赖清单与渲染 Profile，不在容器启动时安装依赖 |

本节记录部署决策，不新增另一套部署脚本。实际容器编排、Secret 注入和启动流程应接入仓库现有部署体系，部署前再按对应技能和文档执行。

### 10.1 构建与压测验收

构建时检查 `ffmpeg -version`、构建配置、滤镜与编码器列表，并执行实际样例：中文烧录字幕、文字标题、裁剪拼接、转场、对白压低配乐、音轨缺失处理及目标 MP4 编码。所有输出重新探测并解码验证；只检查包已安装不能算通过。

压测素材覆盖不同分辨率、时长、素材数量、音轨、字幕量和并发外部请求。记录渲染实时比、峰值内存、CPU、临时磁盘高水位和各阶段耗时，再冻结首期准入 Profile。4 vCPU、8 GiB 下不预先承诺 4K、长视频或复杂 Overlay 性能。

### 10.2 必须落地的资源限制

- 设置容器 CPU/内存限制、FFmpeg 编解码及滤镜线程数、子进程数量、单阶段和总任务超时；超时终止整个进程组。
- 准入限制包括文件数、单文件与总输入字节、总素材时长、分辨率、轨道数量、参考资源和预估输出/中间文件规模；数值由上述压测确定并在上线前固定。
- 领取前检查磁盘预算并预留空间，运行中监测高水位；磁盘不足时停止领取，不依靠任务失败后的清理维持容量。
- 临时目录按 Job ID 和尝试 ID 隔离，限制路径访问；挂载专用数据卷，不依赖镜像可写层保存恢复数据。
- 停机时先停止领取并给予有界退出时间；未完成任务保留数据库检查点和恢复责任。已确认成功的产物与临时文件回收分开处理。

## 11. 监控与后续扩容

首期记录排队时长、各阶段处理耗时、端到端耗时、失败率、重试率、租约丢失率、CPU/内存/磁盘使用率和队列中最老任务年龄。分析与渲染分别统计，区分本机资源瓶颈、外部 Provider 限流及用户待确认时间；用户等待确认不计入 Worker 排队。

上线验收时约定可接受排队时长及持续观察窗口。只有持续超过该目标且确认执行资源为瓶颈时才扩容；单纯增加 Worker 不能解决 Provider 限流或目录/授权故障。

```text
单 Worker
  → 同一服务器增加 Worker（压测证明 CPU、内存和磁盘允许）
  → 多服务器 Worker，共享队列、数据库和 TOS
  → 需要弹性调度时评估 VKE / VCI，按队列压力扩容
```

首期即实现多 Worker 安全领取和 fencing token，部署时仅启用一个实例。跨主机任务只依赖不可变 Artifact 与冻结计划，不能依赖另一台机器的临时路径；扩容沿用同一镜像和 Provider 合同。后续引入 VKE/VCI 时再核实运行时限制、费用和终止行为，不把未来平台选型作为本期前提。
