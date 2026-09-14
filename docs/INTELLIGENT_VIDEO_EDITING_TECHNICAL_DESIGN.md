# PixelFlow 智能剪辑技术方案

> 状态：技术设计，未开始实现
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
- `MediaLicense`：来源、作者、许可证、署名、商业使用和平台限制。
- `EditRenderJob`：稳定 Job ID、输入 revision、渲染 Profile、Provider job 身份、状态、幂等键和脱敏错误码。
- `EditArtifact`：成片及派生版本，记录输入、质量报告和授权快照。

每次 Timeline 写入均由 Repository 创建新 revision。前端请求必须带 `expected_revision` 与 `client_command_id`；Harness Session 绝不保存权威 Timeline。

## 5. Tool 合同

| Tool | 费用/副作用 | 职责 |
| --- | --- | --- |
| `inspect_edit_workspace` | 只读 | 读取素材、Timeline、版本和输出状态 |
| `analyze_edit_material` | 外部读取 | 媒体探测、场景/动作/ASR/节拍分析 |
| `propose_edit_timeline` | Workspace 写入 | 依据意图生成可审阅的草案 |
| `patch_edit_timeline` | Workspace 写入 | 修改轨道、片段、字幕、音频和包装 |
| `validate_edit_timeline` | 只读 | 时间轴、平台、授权与质量前置校验 |
| `render_edit_video` | 异步/可能计费 | 确认后创建 `EditRenderJob` |
| `inspect_edit_result` | 只读 | Job、质量报告和 Artifact 回读 |
| `export_edit_draft` | 异步交付 | 可选导出剪映草稿或项目包 |

每次 Tool 调用必须校验 user、conversation、workspace、Run binding、Manifest digest、revision 和幂等键。可能计费、渲染或外部导出操作必须经过唯一确认记录。

## 6. Provider 技术栈

### 6.1 P0：FFmpeg 渲染 Provider

- `ffprobe`：媒体元数据、音轨、帧率、时长与编码读取。
- FFmpeg：裁剪、拼接、缩放、补边、基础转场、混音、响度、编码和质量抽样。
- 生产镜像必须固定完整 FFmpeg 构建：`libfreetype`、`fontconfig`、`libass`、必要的编码器和中文字体资源。
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

## 7. Job、恢复与事件

`render_edit_video` 只创建 `EditRenderJob`；Worker 负责 start、poll、取消、超时、Artifact 回写和终态事件。Job 状态经 Gateway 投影为 Snapshot/SSE，浏览器不轮询 Provider。

所有 `user_turn`、`confirmation_resume`、`form_resume`、`authorization_resume` 和 `run_recovery` 必须使用同一通用系统指令，只叠加最小剪辑上下文。恢复时重读权威 Workspace revision，不假设旧 Sidecar Session 有效。

## 8. 安全与合规

- Artifact 先登记 owner、来源、媒体类型、用途和授权声明，未登记素材不可进入 Timeline。
- Secret、Token、Cookie、完整用户正文和 Provider 原始异常不得进入仓库、日志、事件或测试夹具。
- 音乐、音效、字体和贴纸必须保存 License 快照；不符合商业使用条件时拒绝渲染或要求用户替换。
- Sidecar 不持有 Authorization、Provider Secret、数据库连接或宿主文件系统权限。
- 输出 Artifact 与授权清单一一关联，支持审计和复查。

## 9. 实施顺序与门禁

1. M0：冻结 DTO、Repository、Projection、错误码、Tool Manifest，补充纯 Timeline/RenderPlan 单测。
2. M1：实现分析、草案、patch、revision 冲突、预览 Projection 与用户/恢复 Run 回归测试。
3. M2：实现 Linux FFmpeg Worker、`EditRenderJob`、质量检查、Artifact 回写和端到端 Golden Journey。
4. M3：接入 HyperFrames、ASR、节拍、授权 Catalog 和剪映草稿导出。

必须覆盖：缺少 Artifact/授权/revision/确认/Run binding 的拒绝；幂等重试；并发 revision 冲突；Job 超时与 Provider 失败；所有恢复入口；媒体授权缺失；质量检查失败。默认测试不得发起真实计费 Provider 请求。
