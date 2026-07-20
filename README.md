# PixelFlow

PixelFlow 是一个面向电商内容创作的 AI Agent 工作台，支持从自然语言和素材附件出发，完成图片生成、短视频生成、视频分析拆解和 PPT 制作。

当前项目仍在快速迭代中，但主流程已经从早期 LangGraph-only 任务流演进为前端工作台驱动的 v2 分段工作流：采集意图、补全表单、生成创意方向、填充 plan.md、人工审核，再分别进入图片、视频、视频分析或 PPT 制作链路。

详细 Agent/Skill 流程见：

- `docs/pixelflow-agent-skill-flow-latest-design.md`
- `AGENTS.md`

## 当前能力

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 对话工作台 | 可用 | 支持新建对话、历史对话、分页加载、恢复上下文 |
| 采集 Agent | 可用 | 使用 `deepseek-v4-pro` 识别图片/视频/PPT/视频分析意图；视频额外抽取总时长、画幅、视频模型、图片模型、用途和风格建议值 |
| 表单补全 | 可用 | 图片、视频和PPT分别有表单 schema，最多 3 轮补充；视频粗略需求必须先确认需求清洗表单，不能直接进入创意方向 |
| 垂类 Skill | 可用 | 命中预制行业画像时使用模板，未知行业用 LLM 生成通用画像 |
| 创意方向 | 可用 | 基于表单、行业画像和素材生成 3 个方向 |
| plan.md 策划 | 可用 | 图片/视频使用独立模板和 `deepseek-v4-pro` 生成 plan.md；视频先生成结构与资产分析，再用项目内 `seedance-prompt` Skill 专门写作全部分镜，最终同时发布权威分镜蓝图和角色/场景/道具 `asset_manifest`，支持版本化修订与回退 |
| 图片生成 | 可用 | 支持文生图、图片编辑、参考图生成、多图融合和多张循环生成 |
| 视频分析 | 可用 | 支持单视频拆解和多视频批量拆解 |
| 视频生成 | 可用 | 用户确认的创作合同贯穿 Plan、Seedance 分镜、场景资产和逐段视频；每镜 4-15 秒且总和精确等于目标时长 |
| 视频修改循环 | 可用 | 支持 QAAgent QC 质检、按受影响场景重生并重新合并 |
| 剪映草稿 Agent | 可用 | 最终视频可异步创建第三方剪映草稿任务，轮询多个 JSON 结果，服务端打包 ZIP 并通过 content-app 上传到 TOS |
| PPT 制作 | 可用 | 支持 PPT 表单、大纲确认/修改、页面图片生成、PPT 文件生成和重新生成附件 |
| PowerMem 语义记忆 | 可用 | 通过 HTTP sidecar 读取用户/品牌长期偏好，并记录 Agent 经验/Skill 沉淀 |
| 额度不足暂停恢复 | 可用 | content-app/Borgrise 返回额度不足时暂停，用户充值后可回同一对话继续 |
| 旧 LangGraph 任务流 | 保留 | `/agent/flows` 旧任务、SSE、资产接口仍存在，用于兼容 |

## 架构概览

```mermaid
flowchart LR
  FE["Web 前端<br/>React + Vite"] --> GW["FastAPI Gateway<br/>/agent/*"]
  GW --> PF["PixelFlow 业务层<br/>intake / creative / generate / skills"]
  PF --> LLM["DeepSeek LLM<br/>deepseek-v4-pro"]
  PF --> Store["Task / Conversation Store"]
  PF --> PM["PowerMem HTTP sidecar<br/>semantic memory"]
  PF --> Skill["Skill Protocol"]
  Skill --> Borgrise["content-app / Borgrise API"]
  FE --> Upload["content-app /api/upload"]
```

代码结构：

```text
pixelflow/
├── backend/
│   ├── app/gateway/                 # FastAPI 网关、/agent Controller、鉴权、配置加载
│   ├── pixelflow/                   # PixelFlow 业务逻辑
│   │   ├── intake/                  # 意图识别、表单、垂类画像、采集上下文
│   │   ├── creative/                # 双 Plan 模板、LLM 策划、创作合同、版本和时长分配
│   │   ├── generate/                # 图片参数准备、视频场景包、Seedance 镜头 Prompt
│   │   ├── jianying_draft/          # 剪映草稿 DTO、Skill 协议与异步幂等 Service
│   │   ├── memory/                  # PowerMemService、语义记忆上下文注入
│   │   ├── skills/                  # Skill Protocol + Borgrise/FFmpeg/剪映适配
│   │   ├── tasks/                   # 任务、会话、消息、资产持久化
│   │   └── preferences/             # 用户偏好
│   ├── packages/harness/deerflow/   # DeerFlow 基础设施
│   ├── skills/public/               # Borgrise creative assistant、图片/视频 Plan 模板与 Seedance Prompt Skill
│   └── tests/                       # 后端测试
├── web/
│   ├── src/pages/WorkspacePage.tsx  # 前端主流程编排
│   ├── src/lib/api.ts               # /agent API client
│   └── src/components/canvas/       # plan、图片结果、视频分镜、结果展示
├── docs/
│   └── pixelflow-agent-skill-flow-latest-design.md
├── AGENTS.md
└── README.md
```

## 主流程

```mermaid
flowchart TD
  A["用户输入提示词和附件"] --> B["采集 Agent 识别意图"]
  B --> C{"intent"}
  C -->|"image"| D["图片表单 + 创意方向 + plan.md"]
  C -->|"video"| E["视频需求清洗表单 + 创作合同 + 创意方向 + plan.md"]
  C -->|"video_analysis"| F["视频链接识别 + storyboard 拆解"]
  C -->|"ppt"| Q["PPT表单 + 附件"]
  D --> G["图片参数准备"]
  G --> H["调用图片 Skill"]
  H --> I["图片结果确认或重新生成"]
  E --> J["按当前 Plan 和创作合同生成可编辑视频场景包"]
  J --> K["按 Plan 指定图片模型、比例、清晰度生成角色三视图、场景图、道具图"]
  K --> L["前端编辑故事线、镜头描述、旁白和 @参考图"]
  L --> M["按 Seedance Prompt 和视频合同串行创建场景视频任务"]
  M --> N["按顺序合并视频"]
  N --> O["视频结果确认或修改循环"]
  O -. "可选生成" .-> JD["剪映草稿 Agent\n生成可下载草稿 ZIP"]
  F --> P["返回分析结果"]
  Q --> R["SmartPPT 生成/修改大纲"]
  R --> S["大纲转JSON + 生成页面图片"]
  S --> T["生成PPT附件并确认"]
```

### 前端任务看板

- 图片、视频和 PPT 主流程在输入框后方、从左上圆角结束位置开始显示限制最大宽度的可折叠任务看板；默认折叠，只显示当前业务步骤和状态，展开时向上滑出完整链路，关闭时向下收回。
- 看板状态由 conversation context 中的 `workflowProgress`、pending job 和结果消息共同恢复；`video_analysis`、未知意图和未识别意图时不展示。
- 视频场景包 job 的 `prepare_scene_packages` 与 `generate_scene_assets` 分别对应“执行规划”和“素材生成”。失败、额度暂停、取消及人工确认都会停留在当前业务步骤。
- “导出交付”只在用户点击最终产物下载入口后完成：图片下载任意一张最终图即可，视频只计算合并成品，PPT 只计算最终 PPT 文件。下载时间保存在结果消息 artifact 中，重新生成的新结果不会继承旧记录。

## 关键约束

- 新增 Python 网关接口必须以 `/agent` 开头。
- 前端上传附件直接调用 content-app `/api/upload`，上传结果作为 `materials` 交给 Agent。
- 所有 `/agent` 请求必须携带 content-app `Authorization: Bearer <token>`。
- Skill 调用 content-app/Borgrise 计费接口时必须透传入口请求的 Authorization。
- 不允许把用户 token、用户名、密码写死到配置、代码或测试脚本里。
- PowerMem 只保存业务摘要、偏好、品牌上下文和 Agent 经验，不写入用户 token、供应商密钥、原始异常堆栈或本地部署目录。
- content-app 返回额度不足、余额不足、HTTP 402 等信息时，当前生成必须立即暂停并保存可恢复上下文。
- 前端展示 Agent 进度时只能展示业务摘要，不能暴露原始 prompt、思维链、供应商密钥或完整内部堆栈。
- 视频表单确认后的 `creation_contract` 是后续创意、Plan、场景资产和视频生成的权威合同；后续阶段不能重新猜测总时长、画幅或模型。
- 视频场景资产图片的比例和清晰度不由用户在表单中选择。Plan LLM 只能从所选图片模型的实时能力范围中选择，并把最终值写入 plan.md 和合同。

## 核心 API

前端 `web/src/lib/api.ts` 使用 `AGENT_API_PREFIX="/agent"`，下表展示最终路径。

| 模块 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 采集 | POST | `/agent/flows/intake/analyze` | LLM 意图识别，兼容旧同步调用 |
| 采集 | POST | `/agent/flows/intake/analyze/start` | 启动可恢复意图识别 job |
| 采集 | GET | `/agent/flows/intake/analyze/jobs/{job_id}` | 查询意图识别 job |
| 采集 | GET | `/agent/flows/intake/forms/{intent}` | 表单 schema |
| 采集 | POST | `/agent/flows/intake/validate` | 表单完整性校验 |
| 采集 | POST | `/agent/flows/intake/directions` | 生成 3 个创意方向 |
| 策划 | POST | `/agent/flows/planning/plan` | 同步填充 plan.md，兼容旧调用 |
| 策划 | POST | `/agent/flows/planning/plan/start` | 启动可恢复 Plan 生成 job |
| 策划 | GET | `/agent/flows/planning/plan/jobs/{job_id}` | 查询 Plan 生成 job |
| 策划 | POST | `/agent/flows/planning/plan/revise` | 同步修订 plan.md，兼容旧调用 |
| 策划 | POST | `/agent/flows/planning/plan/revise/start` | 启动可恢复 Plan 修订 job |
| 策划 | GET | `/agent/flows/planning/plan/revise/jobs/{job_id}` | 查询 Plan 修订 job |
| 策划 | POST | `/agent/flows/planning/plan/restore` | 直接激活所选历史 Plan，不追加重复版本 |
| 图片 | POST | `/agent/flows/image/prepare` | 选择图片接口并生成参数 |
| 图片 | POST | `/agent/flows/image/generate` | 同步生成图片，兼容旧调用 |
| 图片 | POST | `/agent/flows/image/generate/start` | 启动可恢复图片生成 job |
| 图片 | GET | `/agent/flows/image/generate/jobs/{job_id}` | 查询图片生成 job |
| 图片 | POST | `/agent/flows/image/edit-asset` | 同步编辑视频场景包全局素材图片，兼容旧调用 |
| 图片 | POST | `/agent/flows/image/edit-asset/start` | 启动可恢复全局素材图片编辑 job |
| 图片 | GET | `/agent/flows/image/edit-asset/jobs/{job_id}` | 查询全局素材图片编辑 job |
| 视频 | POST | `/agent/flows/video/analyze-storyboards` | 视频分析拆解 |
| 视频 | POST | `/agent/flows/video/prepare-scene-packages` | 生成视频场景包 |
| 视频 | POST | `/agent/flows/video/prepare-scene-packages/start` | 启动可恢复场景包+参考图生成 job |
| 视频 | GET | `/agent/flows/video/prepare-scene-packages/jobs/{job_id}` | 查询场景包+参考图生成 job |
| 视频 | POST | `/agent/flows/video/generate-scene-assets` | 生成场景参考图 |
| 视频 | POST | `/agent/flows/video/generate-scene-assets/start` | 启动可恢复场景参考图生成 job |
| 视频 | GET | `/agent/flows/video/generate-scene-assets/jobs/{job_id}` | 查询场景参考图生成 job |
| 视频 | POST | `/agent/flows/video/generate-scenes/start` | 启动场景视频异步生成 |
| 视频 | GET | `/agent/flows/video/generate-scenes/jobs/{job_id}` | 查询场景视频生成结果 |
| 视频 | POST | `/agent/flows/video/generate-direct/start` | 启动直接视频异步生成 |
| 视频 | GET | `/agent/flows/video/generate-direct/jobs/{job_id}` | 查询直接视频生成结果 |
| 视频 | POST | `/agent/flows/video/merge/start` | 启动可恢复视频合并 job |
| 视频 | GET | `/agent/flows/video/merge/jobs/{job_id}` | 查询视频合并结果 |
| 视频 | POST | `/agent/flows/video/quality-review` | 视频 QAAgent QC 质检，覆盖画面缺陷、商品露出、Prompt 跑偏、字幕、Brief 一致性、黑屏/卡顿和约束合规 |
| 视频 | POST | `/agent/flows/video/quality-review/start` | 启动可恢复视频 QAAgent QC 质检 job |
| 视频 | GET | `/agent/flows/video/quality-review/jobs/{job_id}` | 查询视频 QAAgent QC 质检结果 |
| 剪映草稿 | GET | `/agent/flows/video/jianying-draft/capability` | 查询剪映草稿 Provider 是否可用及前端轮询间隔 |
| 剪映草稿 | POST | `/agent/flows/video/jianying-draft/start` | 校验来源对话、当前版本全部成功且 URL 为 HTTPS 的分镜，启动或复用草稿 job；未配置时不创建任务 |
| 剪映草稿 | GET | `/agent/flows/video/jianying-draft/jobs/{job_id}` | 查询来源对话拥有的剪映草稿 job，并在首次读取 `succeeded/failed/timeout/not_configured` 终态时 claim 幂等记录经验摘要 |
| PPT | POST | `/agent/flows/ppt/summary/start` | 启动 SmartPPT 大纲生成 |
| PPT | POST | `/agent/flows/ppt/summary/update/start` | 启动 SmartPPT 大纲更新 |
| PPT | POST | `/agent/flows/ppt/content-json/start` | 启动大纲转页面 JSON |
| PPT | POST | `/agent/flows/ppt/images/start` | 启动 PPT 页面图片生成 |
| PPT | POST | `/agent/flows/ppt/images/regenerate/start` | 重新生成单页 PPT 图片 |
| PPT | POST | `/agent/flows/ppt/file/start` | 启动 PPT 文件生成 |
| PPT | GET | `/agent/flows/ppt/jobs/{job_id}` | 查询 PPT 阶段异步 job |
| 对话 | POST | `/agent/conversations` | 新建对话 |
| 对话 | GET | `/agent/conversations?page_size=5` | 最近对话分页 |
| 对话 | GET | `/agent/conversations/{conversation_id}` | 对话详情 |
| 对话 | POST | `/agent/conversations/{conversation_id}/messages` | 保存对话消息，兼容旧同步调用 |
| 对话 | POST | `/agent/conversations/{conversation_id}/messages/start` | 启动可恢复消息保存 job |
| 对话 | GET | `/agent/conversations/{conversation_id}/messages/jobs/{job_id}` | 查询消息保存 job |
| 对话 | PATCH | `/agent/conversations/{conversation_id}/messages/{message_id}` | 更新对话消息内容或 payload |
| 对话 | GET | `/agent/conversations/{conversation_id}/trace` | 内部调试专用：查看该对话的 LLM/供应商调用 trace，需要 content-app `ROLE_ADMIN` |
| 用户偏好 | GET/PUT | `/agent/users/{user_id}/preferences` | 用户偏好 |

旧 LangGraph 任务流仍保留在 `/agent/flows`、`/agent/flows/{task_id}/events`、`/agent/flows/{task_id}/assets` 等接口中。

## 剪映草稿流程

剪映草稿能力位于最终视频结果确认阶段，输入只能是当前版本全部成功、按 `scene_index` 排序且 URL 为 HTTPS 的分镜视频，不能用合并视频替代。`storyboard_version_id` 由 `scene_id`、顺序、视频 URL 和视频 task ID 的规范化摘要计算；同一 `conversation_id + storyboard_version_id` 复用运行中或未过期成功 job，失败或超时必须由用户以 `retry_failed=true` 明确重试。已过期成功结果可以重新生成，历史草稿不会被新版本复用。

后端的 `pixelflow_jianying_draft.py` 是 Controller，`JianyingDraftService` 是负责校验、幂等、状态转换、30 分钟超时和容量清理的业务 Service，`JianyingDraftSkill` 是稳定的第三方 Client 接口，`HttpJianyingDraftSkill` 是真实 HTTP 实现。`JianyingDraftResult` 只暴露状态、job、版本、下载地址、文件名、过期时间和公开消息等 typed 字段，不暴露第三方 `raw` 响应或内部异常。

真实 Provider 先调用 `POST /api/jianying/draft/tasks` 创建任务，再每 2 秒调用 `POST /api/jianying/draft/tasks/result` 查询；`20201/20202` 表示继续等待，`200` 的 `data` 是多个草稿 JSON 的 HTTPS URL。PixelFlow 立即下载并校验这些 JSON，尽量保留第三方原文件名生成一个 ZIP，再复用 content-app `/api/upload` 上传到 TOS，前端只接收最终 ZIP 的 HTTPS 下载地址。Provider 域名、固定 token、连接/读取超时和重试次数均从开发/生产配置读取；配置不完整时装配 unavailable 实现并禁用按钮。

最终视频尚未结束时，结果卡片有“无意见，结束”“生成剪映草稿”“提出修改意见”三个操作。草稿生成中会锁定这三项视频操作，但不锁定对话输入；前端每 2 秒轮询，最长 30 分钟。pending job、按版本保存的结果和恢复错误通过来源对话的原子 PATCH 持久化，刷新或切换对话后只恢复轮询原 job，结果消息按 job ID 去重。用户结束视频流程后，草稿历史下载或重新生成入口仍保留，成功也不会自动下载。

当前 Gateway 以单 Uvicorn worker 运行，`JianyingDraftService` 的 job registry 是进程内状态；部署为多 worker、多容器或多副本前，必须替换为共享、持久化的 job store，不能依赖当前内存幂等索引。路由在 `GET /jobs/{job_id}` 首次读取到 `succeeded`、`failed`、`timeout` 或 `not_configured` 终态时，才通过 claim 幂等地异步记录 PowerMem `category=experience`、`infer=False` 的安全摘要；停止轮询不会自行触发写入。

## PowerMem 语义记忆

PixelFlow 第一版 PowerMem 集成同时覆盖两类能力：

| 类型 | 记录来源 | 使用位置 |
| --- | --- | --- |
| 用户/品牌长期偏好 MVP | `/agent/users/{user_id}/preferences`、偏好反馈、Brief 修订、采集到的产品/行业上下文 | 采集分析、创意方向、plan.md、图片 prepare、视频场景包、PPT 大纲 |
| Agent 经验/Skill 沉淀 | 图片、视频、视频分析、PPT、旧 LangGraph 任务流的阶段完成/失败摘要 | 后续 Agent 检索 `preference`、`brand`、`skill`、`experience` 分类记忆作为上下文 |

接入边界：

- 统一入口是 `backend/pixelflow/memory/PowerMemService`，路由侧只通过 `app.gateway.pixelflow_memory` helper 读写。
- 测试环境 `backend/config.dev.yml` 的 `pixelflow.powermem_base_url` 走 nginx：`https://test-video.borgrise.com/powermem`。
- 生产环境 `backend/config.prod.yml` 的 `pixelflow.powermem_base_url` 走本机 sidecar：`http://127.0.0.1:18848`。
- PowerMem 不替代 `pixelflow_user_preferences` 结构化偏好表；结构化默认值、负向规则仍在业务 Store，PowerMem 负责语义检索和跨 Agent 经验复用。
- `powermem_timeout_seconds` 只用于 search/health 这类同步读请求，当前默认 3 秒；record 写入统一走 `powermem_record_timeout_seconds`，当前默认 60 秒。
- PixelFlow 进程内所有 PowerMem search、record、health HTTP 请求共用同一请求闸门，避免 OceanBase `OB_SESSION_ENTRY_EXIST`。
- search/health 的锁等待和 HTTP 共用短总预算，超时直接 fail-open，不绕过闸门并发请求；record 使用独立长预算。
- 只有幂等的 search/health 对 `OB_SESSION_ENTRY_EXIST` 最多尝试 3 次，record 不自动重试。
- 该闸门不跨进程；多 worker、多容器或多副本部署仍需要 PowerMem 服务端正确管理数据库 Session。
- 网关侧 `record_power_mem()` / `record_power_mem_background()` 默认按 category 决定 infer：`preference` 默认 `infer=True`，用于用户中文偏好的服务端抽取和向量化；`brand`、`experience`、`skill` 默认 `infer=False`，避免阶段摘要被重复 LLM 抽取。
- 当前 `infer=True` 场景是用户偏好类：偏好 API 更新、偏好反馈、旧 Brief 修订反馈。若 PowerMem 返回 `success=true` 但 `data=[]`，`PowerMemService` 会自动用 `infer=False` 补写一次同一条偏好，避免服务端 LLM 额度不足或抽取空结果导致偏好完全丢失。
- 图片/视频/PPT 等 Skill 调用类经验会自动双写 `experience` 与 `skill`，便于后续流程复用接口选择和失败处理经验。
- 后续新增或修改 Agent/流程时，必须复用 `PowerMemService`：进入决策前先检索相关记忆，阶段完成/失败后写入业务摘要；只要涉及用户长期偏好、默认规则、负向要求、品牌口吻或风格偏好，就要写 `category=preference` 并走默认 `infer=True`。

## content-app/Borgrise 接口

图片：

| PixelFlow Skill | content-app/Borgrise 接口 |
| --- | --- |
| 文生图 | `/api/picture/text_to_image` |
| 图片编辑 | `/api/picture/image_edit` |
| 参考图生成 | `/api/picture/multi_reference_image_generation` |
| 多图融合 | `/api/picture/multi_image_fusion` |
| 图片模型参数配置 | `/api/modelParamConfig/listByCategory/image_generate` |

视频：

| PixelFlow Skill | content-app/Borgrise 接口 |
| --- | --- |
| 视频模型参数配置 | `/api/modelParamConfig/listByCategory/video_generate` |
| 文生视频 | `/api/video/text-to-video` |
| 首帧图生视频 | `/api/video/image-to-video` |
| 首尾帧生视频 | `/api/video/two-image-to-video` |
| 全能参考模式 | `/api/video/reference-mode-video` |
| 编辑视频 | `/api/video/edit-video` |
| 延伸视频 | `/api/video/extend-video` |
| 合并视频 | `/api/video/merge` |

五类场景视频请求体按 content-app 当前 DTO 精确映射：

| 接口 | 字段 |
| --- | --- |
| `/api/video/text-to-video` | `prompt/model/ratio/size/duration/videoCount/sound` |
| `/api/video/image-to-video` | `image_url/prompt/duration/ratio/model/size/sound/videoCount` |
| `/api/video/two-image-to-video` | `first_frame_image_url/last_frame_image_url/prompt/ratio/duration/model/size/videoCount/sound` |
| `/api/video/reference-mode-video` | `prompt/imageUrls/videoUrls/audioUrls/duration/ratio/sound/model/size/videoCount` |
| `/api/video/edit-video` | `prompt/refImage/refVideo/model/duration/size/ratio/videoCount/sound` |

视频理解：

| PixelFlow Skill | content-app/Borgrise 接口 |
| --- | --- |
| 文本抽取媒体链接 | `/api/creative/extractMediaLinks` |
| 单视频拆解 | `/api/creative/decompose_video_to_storyboard` |
| 多视频拆解 | `/api/creative/batch_decompose_video_to_storyboard` |
| 视频 QAAgent QC 质检 | `/api/creative/video_quality_review` |

SmartPPT：

| PixelFlow Skill | content-app/Borgrise 接口 |
| --- | --- |
| 生成 PPT 大纲 | `/api/picture/smart-ppt/generatePptSummary` |
| 更新 PPT 大纲 | `/api/picture/smart-ppt/updatePptSummary` |
| 大纲转页面 JSON | `/api/picture/smart-ppt/generatePptContentToJson` |
| 生成 PPT 页面图片 | `/api/picture/smart-ppt/generatePptImage` |
| 生成 PPT 文件 | `/api/picture/smart-ppt/generatePptFile` |

SmartPPT 每一步都是异步任务，PixelFlow 通过 `/api/task/{taskId}/status` 轮询，默认超时 2 小时。

## 视频需求清洗与创作合同

- 视频粗略需求经采集 LLM 预填后，必须先展示需求清洗表单。表单保留产品信息、产品品类、目标人群和转化目标，并包含总时长、视频画幅、视频模型、图片模型、视频用途和视觉风格。
- 总时长支持 30/60/90/180 秒和自定义；自定义只能是 4-300 的自然数。用户选择 180 秒后，Plan 和全部分镜总时长必须精确等于 180 秒。
- 视频模型来自 `/api/modelParamConfig/listByCategory/video_generate`，前端展示 content-app 返回的所有启用 Seedance 模型；系统推荐默认解析成 `seedance-2.0`，并向用户展示实际结果。这里的 2.0 只是推荐默认值，不是 Seedance Prompt Skill 的调用开关。
- 模型特有的画幅、清晰度、声音和参考素材能力以 content-app 实时配置与实际生成 API 为准，PixelFlow 不根据型号名称自行推断能力。
- 图片模型来自 `/api/modelParamConfig/listByCategory/image_generate`，默认 `gpt-image-2`。表单不展示图片比例和清晰度，只把所选模型及其能力范围提交给 Plan Agent。
- Plan LLM 从图片模型支持范围内选择 `scene_image_ratio` 和 `scene_image_size`，并先自主规划结构、精确时间线、资产需求和 `asset_manifest`；稳定 `asset_id` 生成后，再由 `backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/SKILL.md` 对全部 `scene_blueprints` 做一次专用分镜写作。每个蓝图包含叙事职能、精确时长、故事线、镜头描述、旁白、转场和资产需求；不再预先按 10 秒机械切分。
- Seedance Plan 写作显式接收用户确认的 `video_model`、完整创作合同、当前 Plan、全部分镜、稳定资产 ID、用户要求和附件，只允许改写标题、故事线、镜头描述、旁白与转场。每镜描述是一整段中文，内部只用连续的 `0-N秒` 整数时间码，覆盖地点、主体、动作、景别、运镜、光影、声音和收束；只引用该镜声明的 `@character-*`、`@scene-*`、`@prop-*`，每次解释用途且最多 9 张。分镜数量、顺序、时间线、模型、画幅、卖点、转化目标和资产集合均不可修改；非法响应整批拒绝并携带校验错误重试一次。
- Plan 修订（包括手工编辑重新对齐）在结构与资产清单通过校验后也执行同一个 Seedance 专用写作阶段，并携带当前版本、修订候选、用户意见、附件和上下文；最终确认版本仍是后续场景包唯一依据。
- 历史已审核 Plan 若仍使用全局镜头时间段，场景包恢复时会确定性换算为当前分镜的局部 `0-N秒`，不会因新校验阻断旧对话；新 Plan 仍按严格局部时间轴发布。
- 优先级固定为“用户确认值 > LLM 预填值 > 系统默认值”。Plan、场景包、场景资产和场景视频只读取当前激活 Plan 的最终 `creation_contract`。
- PowerMem 长期记忆只作为 LLM 的内部决策上下文，不得把“长期记忆约束”、PowerMem、Skill/Agent 运行日志或记忆原文展示在 plan.md 中。

图片和视频分别使用：

```text
backend/skills/public/borgrise-creative-assistant-v2/templates/plan_image.md
backend/skills/public/borgrise-creative-assistant-v2/templates/plan_video.md
```

Plan 默认按当前创意修订并生成 v2/v3；只有用户明确选择“重新生成新创意”才重新返回 3 个方向。当前创意内修订会先把用户意见解析为白名单 `creation_contract_patch`，再把当前 Plan、附件、垂类补充、采集上下文和 PowerMem 检索结果交给 LLM 重写；合同值优先级固定为“用户意见中的明确值 > LLM 结构化补丁 > 当前版本合同”，未提及或明确要求保持不变的字段不能变化。单分镜时长和画面中的数量不会被误判为总时长或图片数量；“延长/缩短 N 秒”按当前总时长计算增量，“把片子改成 N 秒”按绝对总时长处理。视频修订后重新校验每镜 4-15 秒、精确总时长及镜头描述八维完整度，图片修订后的目标、风格、尺寸和数量会直接进入图片 prepare，不能再被初始表单覆盖。Plan 修订没有新模型的实时能力快照，因此视频模型或图片模型变更会要求返回需求表单重新确认。候选合同或蓝图校验失败时只允许携带校验原因让 LLM 修正 1 次；仍失败则保留当前版本和历史，前端展示失败原因，不发布错误版本。

右侧编辑器发布完整 Plan 时也会调用同一个 Plan 修订 LLM，把用户编辑稿与当前表单、创意、附件、采集上下文和长期记忆一起对齐为新的 Markdown、`creation_contract` 与视频 `scene_blueprints`。后端先确定性计算当前稿与编辑稿的差异，合同白名单只允许差异中真正涉及的字段，完整稿只供 LLM 重写内容和蓝图，防止模型顺手篡改未编辑参数。三者全部校验通过才发布 `manual_edit` 新版本；失败时保留当前权威版本，避免界面文字已改但后续仍按旧合同生产。

图片最终合同会在 PowerMem 检索和 content-app 调用前做严格校验：目标、类型、用途、风格、尺寸必须为非空文本，数量为 1-10，比例必须精确匹配支持值；历史空合同 `{}` 仍按缺失合同兼容。

`/agent/flows/planning/plan/restore` 回退时直接激活所选历史版本并保持既有历史不变，不追加重复版本。回退后再次“继续修改”时，以历史最大版本号加一创建新版本，例如 v2 回退到 v1 后修订生成 v3，同时保留 v2。每个新历史条目保存 `creation_contract`、`scene_durations_sec` 和 `scene_blueprints` 快照；旧对话缺少蓝图时才按当前合同使用规则兜底。

## 视频场景包规则

视频生成主流程固定为：plan.md -> 多个视频场景片段 -> 每段生成视频 -> 按顺序合并。

- 每个场景片段最少 4 秒，最多 15 秒。
- 所有分镜整数秒时长总和必须精确等于用户确认的 `video_duration_sec`；300 秒任务允许产生超过 18 个分镜。
- 视频 Plan 的固定第四章是“全局资产清单”，结构化 `asset_manifest` 分别保存角色、场景、道具的最终名称、文字说明和生图要求；三类清单必须与全部 `scene_blueprints[].asset_requirements` 的分类并集完全相等，不能少也不能多。修订、手工编辑和历史回退都按版本保存该清单。
- 场景包直接消费当前 Plan 的权威 `scene_blueprints + asset_manifest`，保留蓝图标题、故事线、镜头描述、旁白、转场和时长，并机械映射全局素材及 `@asset_id`，不再调用第二次 LLM 分析资产。缺少清单的旧 Plan 会被阻止进入新场景包流程；前端展示和提交的 `mentions.name` 始终以清单正式名称为准。任一分镜超过 9 张引用会返回明确错误而不是静默截断。
- 全局固定资产是 `characters`、`scenes`、`props`、`visual_style`。
- `characters` 只能是人物角色，每个角色必须是同一个人物的正面、侧面、背面三视图。
- 产品、商品、包装、工具、书包、球、床垫等非人物主体放到 `props`。
- `shot_description.text` 是一整段镜头描述，不能拆成时间、地点、角色、景别等多个字段。
- `shot_description.text` 的时间范围必须使用秒级表达，例如 `0-10秒`，不要使用 `ms`、`毫秒` 或 `00:00.000`。
- 用户在前端镜头描述框输入 `@` 后，可以选择角色、场景、道具图片；前端保存 `mentions`，后端生成视频时提取对应图片 URL 作为参考图。
- 每个视频场景片段最多 9 张参考图。
- Plan 分镜写作与场景包执行提示词都应用项目内 `skills/seedance-prompt/SKILL.md`。该 Skill 对 content-app 实时启用的全部 Seedance 系列模型通用；调用层始终显式携带用户确认的 `video_model`，Skill 不改写模型，实时能力不兼容时由调用层提示。场景包继续严格保留最终 Plan 中的 `@asset_id`、正式资产名称和 mentions 图片 URL。
- `skills/seedance-prompt/THIRD_PARTY_NOTICE.md` 记录 Skill 的输入来源、哈希和授权边界，具有来源审计价值，不能当作无用文件删除。
- 角色三视图、场景图和道具图严格按 `asset_manifest` 一项生成一张并只保存一个 URL，同一 `asset_id` 跨分镜只生成一次；实际生图提示词同时包含清单的正式名称、文字说明和生图要求，避免 `image_prompt` 未重复某项外观约束时丢失 Plan 说明；统一使用当前 Plan 合同中的 `image_model/scene_image_ratio/scene_image_size`。分镜视频统一使用 `video_model/video_ratio/video_size/video_sound`。
- 场景包确认页支持点击全局素材图片预览、引用到左侧对话输入框并发送编辑指令；前端调用 `/agent/flows/image/edit-asset/start` 启动可恢复图片编辑 job，后端复用 `/api/picture/image_edit`，成功后直接替换原 `global_assets` 图片，并同步相关 `shot_description.mentions` 的 `image_url`。编辑结果卡片点击“重新生成”后，下一条用户输入继续走全局素材图片编辑，不重新进入采集 Agent。
- 全局素材预览也支持“删除素材”：点击后只预填左侧固定删除文案和素材 chip，用户发送后在当前场景包内原地删除该素材引用，清空 `global_assets` 中该素材图片 URL 作为占位符，不新增场景包确认卡片。
- 新需求入口使用可恢复 job：用户消息保存走 `/agent/conversations/{conversation_id}/messages/start` + `/messages/jobs/{job_id}`，并把 `pendingMessageJob` / `pending_message_job` 写入 conversation context；消息保存完成后采集意图识别走 `/agent/flows/intake/analyze/start` + `/analyze/jobs/{job_id}`，并写入 `pendingIntakeJob` / `pending_intake_job`。用户切到历史对话、创作页、iframe 外或刷新后只轮询已有 job，不重复追加用户消息、不重复启动采集流程；旧 `/messages` 和 `/intake/analyze` 同步接口仅做兼容。
- 普通图片流程里，如果采集 Agent 识别到用户是在编辑上传图片，前端会跳过普通图片表单、创意方向和 plan.md。缺原图时会把等待上传状态写入对话，用户上传图片后可继续；有原图时先调用 `/api/modelParamConfig/listByCategory/image_generate` 展示图片编辑模型、尺寸和清晰度确认卡，默认选 `gpt-image-2`，确认后再复用 `/agent/flows/image/prepare` + `/agent/flows/image/generate/start` 调用 `/api/picture/image_edit`。用户确认过的模型、尺寸和清晰度会写入对话 context，切换对话或刷新后仍能恢复展示。图片编辑成功后同样展示“满意，结束 / 重新生成”，60 秒未操作默认满意并结束。
- 图片 plan.md 同意、图片修改重生成、直接图片编辑和全局素材图片编辑都会先拿到 Python `job_id`，并把 `pendingImageJob` / `pending_image_job` 写入 conversation context。用户切到历史对话、创作页、iframe 外或刷新后，只继续查询 `/agent/flows/image/generate/jobs/{job_id}` 或 `/agent/flows/image/edit-asset/jobs/{job_id}`，不会重复启动生成；网关重启导致 job 404 时只提示手动重试，不自动重启，避免重复计费。
- 图片编辑分支会让 LLM 抽取用户指定的尺寸和清晰度；如果所选模型不支持这些参数，前端提示并自动落到当前模型可用参数，用户可以重新选择可用尺寸和清晰度后继续提交。如果用户没有明确指定，前端按所选模型自动选择一组可用尺寸和清晰度。模型、尺寸和清晰度的可选项以 content-app `/api/modelParamConfig/listByCategory/image_generate` 实时配置为准，Python 侧不再用硬编码模型白名单拦截用户已确认的参数。content-app 图片编辑请求里 `size` 表示比例，`imageSize` 表示清晰度，网关会保持两者分离。图片编辑失败后，重新生成会先回到模型、尺寸和清晰度确认卡，避免继续复用失败参数。
- 对话里可能保留多个历史视频场景包卡片，但只有最后一个 `video_scene_packages` 卡片显示“查看分镜”和“确认并生成视频”操作；旧卡片只作为历史预览，避免误用过期场景包生成视频。
- 场景视频和合并视频生成完成后，未结束的 `video_result` 卡片固定展示“无意见，结束 / 生成剪映草稿 / 提出修改意见”三个按钮，草稿运行中锁定三个按钮。生成结果会同步回填到原 `video_scene_packages` 卡片；用户继续点击原来的“查看分镜”时，右侧 `StoryboardPanel` 的镜头预览优先展示已生成的分镜视频。用户只修改某几个分镜后再次确认生成时，仅重生成这些已修改分镜，未修改分镜复用旧视频，再按分镜顺序重新合并并回填原场景包。
- 场景视频生成 job 内部可以并发调度多个分镜，但所有会创建 content-app 计费生成任务的 POST 都经 `run_generation.py` 串行提交；前一个创建接口返回 taskId 并完成 content-app 扣费确认后，才创建下一个图片或视频任务，后续 `/api/task/{taskId}/status` 轮询可以并行等待。所有分镜都结束后再统一判断。全部成功时按 `scene_index` 合并；前端通过 `/agent/flows/video/merge/start` 启动可恢复合并 job，再轮询 `/agent/flows/video/merge/jobs/{job_id}`，并把 `pendingVideoJob.kind="video_merge"` 写入对话上下文，用户切走或刷新后只恢复轮询已有 job；如果只有 1 个分镜，PixelFlow 直接把该分镜视频作为最终视频返回，不调用 content-app `/api/video/merge`；多个分镜合并时 content-app 会同步完成下载、ffmpeg 合并和上传，PixelFlow 使用 `BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT` 控制合并接口读等待，默认 1 小时；合并异常时 job 返回 `status=failed`，并在 `result.error/message/raw.details` 中保留 content-app 原始错误，前端据此展示“视频合并失败”而不是“合并完成”；部分异常时返回 `failed_scenes` 和每个失败原因，重试只提交失败分镜；部分额度不足时整批只提示一次额度不足，充值后同样只重试额度暂停或异常分镜。
- 视频 QAAgent QC 通过 `/agent/flows/video/quality-review/start` 启动异步 job，再轮询 `/agent/flows/video/quality-review/jobs/{job_id}`，避免浏览器或网关长连接超时。QC 失败时 job 返回 `status=failed` 并保留 content-app 原始错误；content-app 会把长视频压成完整时序的低码率质检预览再送入模型，避免 300 秒级成片直接 base64 后超过模型请求体限制。
- 视频 plan.md 同意后，前端调用 `/agent/flows/video/prepare-scene-packages/start`，后端 job 连续完成“生成可编辑场景包”和“生成角色三视图、场景图、道具图”。前端拿到 `job_id` 后立即把 `pendingScenePackageJob` / `pending_scene_package_job` 写入 conversation context；用户切到历史对话、创作页、iframe 外或刷新后，只继续查询 `/jobs/{job_id}`，不会重复启动生成。参考图失败或额度不足时，job 返回已生成场景包和 `sceneAssetFailures`，前端展示可继续的场景包卡片。
- 场景包卡片上的“继续生成参考图/重新生成参考图”调用 `/agent/flows/video/generate-scene-assets/start`，同样保存 `pendingScenePackageJob` 并恢复轮询；网关重启导致 job 404 时只提示手动重试，不自动重启，避免重复计费。
- 场景视频生成、视频修改重生成和视频合并都会先调用对应 `/start` 取得 `job_id`，并把 `pendingVideoJob` / `pending_video_job` 写入当前 conversation context；用户离开再返回同一对话时，前端只继续查询 `/jobs/{job_id}`，不会重复启动生成或合并。
- 图片、视频、PPT 的需求表单弹出后，用户点击右上角 `X` 视为取消当前流程，前端会清空 pending 表单上下文并保存 `form_cancelled`。
- PPT 表单的 `PPT风格` 支持“自定义”，选中后显示文本框，最终把用户输入的风格词作为 `ppt_style` 提交给 SmartPPT。
- 当前对话中任意阶段正在生成或处理时，历史 artifact 按钮统一禁用；阶段结束后只保留最新可操作 artifact 的按钮，失败或额度暂停时只保留当前可恢复点的重试入口。

### PPT 页面图片交互

- 页面图片生成阶段先展示每一页小格子为“图片生成中...”，省略号动态循环。
- 小格子处于生成中时不显示重新生成按钮；已生成或失败后才显示。
- 点击单页重新生成时，只把当前小格子原位切回生成中并更新该页结果，不追加新的整组 PPT 图片卡片。
- 只要还有页面处于生成中或失败，隐藏“开始生成PPT附件”；所有页面都有图片且无失败时才展示该按钮。

## 本地启动

### 后端

要求：Python 3.12、uv。

```bash
cd backend
uv sync
make dev
```

默认读取 `backend/config.dev.yml`，监听 `0.0.0.0:8001`。

常用命令：

```bash
cd backend
make gateway                              # 非 reload 模式
PIXELFLOW_CONFIG_ENV=prod make gateway    # 使用 config.prod.yml
make test
make lint
```

### 前端

要求：Node.js。仓库有 `pnpm-lock.yaml`，推荐用 corepack 调 pnpm，避免依赖版本漂移。

```bash
cd web
corepack enable
corepack pnpm install
corepack pnpm dev
```

本地联调 content_frontend + PixelFlow 本地后端时，使用 test 模式：

```bash
cd backend
make dev

cd web
corepack pnpm dev:test -- --host 0.0.0.0 --port 5273

cd ../../content_frontend
yarn test -- --host 0.0.0.0 --port 5174
```

这条链路是：content_frontend test 环境 `http://localhost:5174/home/agent` 嵌入 PixelFlow `http://localhost:5273/agentfrontend/`，PixelFlow 前端再把 `/agent` 代理到本地后端 `http://127.0.0.1:8001`。

打包：

```bash
cd web
corepack pnpm build-prod   # 使用 web/.env.production，产物到 web/dist/
corepack pnpm build-dev    # 使用 web/.env.development，联调测试环境时使用
corepack pnpm build-test   # 使用 web/.env.test，本地后端联调构建
```

如果本机没有 corepack 或 pnpm，也可以临时使用：

```bash
cd web
npm install
npm run build-prod
```

不要直接运行裸 `tsc -b && vite build`，本机没有全局 `tsc` 或 `vite` 时会报 `command not found`。应通过 `pnpm build-prod`、`corepack pnpm build-prod` 或 `npm run build-prod` 触发 `package.json` 脚本。

前端环境变量文件在 `web/` 目录下，`web/vite.config.ts` 会从该目录读取：

| 文件 | 当前目标 | 用途 |
| --- | --- | --- |
| `web/.env.development` | `https://test-video.borgrise.com` | `pnpm dev`、`pnpm build-dev`，测试环境联调 |
| `web/.env.test` | `http://127.0.0.1:8001` | `pnpm dev:test`、`pnpm build-test`，content_frontend test + PixelFlow 本地后端联调 |
| `web/.env.production` | `https://video.borgrise.com` | `pnpm prod`、`pnpm build-prod`，生产环境 |

变量含义：

- `VITE_API_TARGET`：Vite dev server 将 `/agent` 代理到的目标。development/production 分别指向测试/正式 content-app 域名；test 固定指向本机后端 `http://127.0.0.1:8001`。
- `VITE_CONTENT_APP_TARGET`：Vite dev server 将所有 `/api/...` 请求代理到的 content-app 目标，通常应与当前环境的 content-app 域名一致。

## 鉴权与调试

PixelFlow 不提供自己的登录体系。登录统一由 content-app 完成，前端或第三方调用 PixelFlow 时必须携带：

```http
Authorization: Bearer <content-app 登录 token>
```

后端处理：

1. `AuthMiddleware` 读取 `Authorization`。
2. `content_app_auth.py` 从 JWT payload 读取 `sub` 作为用户名。
3. 后端调用 content-app `/api/auth/verify` 做实时校验。
4. 当前用户名用于任务、会话、资产、偏好隔离。
5. Borgrise Skill 调用生成接口时透传同一个 Authorization。

本地单独调试前端时，可以打开：

```text
http://localhost:5273/auth-token
```

把 content-app token 保存到 `localStorage.Authorization`。正式集成优先由 content-app 宿主注入 `window.__CONTENT_APP_AUTHORIZATION__`。

### 对话 Trace（内部调试专用）

当前主用的 v2 对话工作流（intake/creative/image/video/ppt）会把每次 LLM 调用（`pixelflow/intake/llm.py`）和每次 content-app/Borgrise 供应商调用（`run_generation.make_request`）记一条 trace 事件，按 `conversation_id` 关联，存在 `pixelflow_conversation_trace_events` 表：

- 前端在调用生成类接口时，通过 `web/src/lib/api.ts` 的 `setActiveConversationId` 让后续请求自动带上 `X-Conversation-Id` 请求头；`AuthMiddleware` 读取该请求头写入 ContextVar，业务代码只在这个 ContextVar 存在时才记录 trace，不影响没有 conversation_id 的旧流程。
- 查看入口是 `GET /agent/conversations/{conversation_id}/trace`，前端页面在 `http://localhost:5273/agentfrontend/#/trace/<对话ID>`（hash 路由，`web/src/pages/TracePage.tsx`）；对话 ID 可以从工作台地址栏 `#/c/<对话ID>` 里复制。
- 这个接口和页面只服务内部排查，会展示原始 prompt、供应商请求/响应；调用方必须是 content-app `ROLE_ADMIN`（`content_app_auth.is_admin_user` 实时调用 content-app `GET /api/user/me` 校验角色），非管理员会收到 403。旧 LangGraph 任务流的 `pixelflow_task_events`/`run_events` 是完全独立的另一套 trace，不受这个功能影响。

## 配置

后端主配置：

| 文件 | 说明 |
| --- | --- |
| `backend/config.dev.yml` | 开发环境，Swagger/OpenAPI 默认开启 |
| `backend/config.prod.yml` | 生产环境，Swagger/OpenAPI 默认关闭 |

关键配置：

| 配置段 | 说明 |
| --- | --- |
| `gateway.*` | FastAPI host、port、docs、CORS |
| `pixelflow.*` | MySQL、media_skill、edit_skill、输出目录 |
| `pixelflow.semantic_memory_*` / `pixelflow.powermem_*` | PowerMem 语义记忆开关、base_url、API key、search 超时（`powermem_timeout_seconds`）、record 专用超时（`powermem_record_timeout_seconds`）、检索数量、失败开放策略 |
| `borgrise.*` | content-app/Borgrise base_url、auth verify、轮询超时、重试次数 |
| `models` | LLM 配置，当前主模型是 `deepseek-v4-pro` |
| `database` | DeerFlow checkpointer 和平台持久化 |
| `skills` | DeerFlow skills 路径 |

轮询默认值：

- 图片：10 分钟。
- 视频：1 小时。
- 视频分析：15 分钟。
- content-app `/api/auth/verify`：10 秒。

`/api/task/{taskId}/status` 状态查询如果出现短暂 SSL EOF、握手超时等可恢复网络错误，会在单次请求重试后继续轮询最多 3 次；401、402、额度不足和非重试业务错误仍会立即返回。

## 测试与验证

后端核心测试：

```bash
cd backend
uv run pytest tests/test_intake_llm.py tests/test_intake_forms.py tests/test_industry_profile.py -q
uv run pytest tests/test_creative_plan_markdown.py tests/test_image_prepare.py -q
uv run pytest tests/test_pixelflow_image_router.py tests/test_video_scene_packages.py tests/test_pixelflow_video_router.py -q
uv run pytest tests/test_powermem_service.py tests/test_pixelflow_preferences.py -q
uv run pytest tests/test_borgrise_poll.py tests/test_borgrise_authorization_passthrough.py tests/test_borgrise_quota_detection.py -q
uv run ruff check .
```

前端核心测试：

```bash
cd web
corepack pnpm test:scene-packages
corepack pnpm test:scene-mentions
corepack pnpm test:conversation-routing
corepack pnpm build-prod
```

文档变更至少跑：

```bash
git diff --check
```

## 文档维护

以下变更必须同步更新 `docs/pixelflow-agent-skill-flow-latest-design.md`、`AGENTS.md` 和本 README：

- Agent 流程变化。
- Skill Protocol 或 Borgrise/content-app 接口变化。
- 前端确认、倒计时、重试、恢复逻辑变化。
- 对话隔离、历史记录、上下文恢复变化。
- 鉴权、额度不足、错误处理策略变化。
- 核心运行命令或配置变化。

## License

见 `LICENSE` 与 `NOTICE`。
