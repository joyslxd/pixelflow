# AGENTS.md

本文件是后续 agent 进入 PixelFlow 仓库时必须先读的工作说明。用户主要是 Java 后端开发，对 Python、React 和 Agent 编排不熟；解释实现时请尽量用 Controller / Service / Repository / Client / DTO / Filter / 工作流编排类比说明。

## 中文工程交付规范（硬性要求）

以下规则适用于本要求确认后的所有新增或修改，不得以“Codex 自动生成”“只是临时分支”或“后续再补”为由跳过：

- Git commit 的标题和正文、PR/合并说明、模块状态、交接记录、测试结论和发布记录必须使用中文表达；允许保留 `Agent`、`API`、类名、配置键、模块号和命令等必要技术标识，但不能使用只有英文语义的提交信息。自动集成产生的 commit 也必须使用中文模板。
- 分支 push 前必须完成中文规范检查；不符合时不得 push，不得写 `ready_for_phase_integration`、`ready_for_integration` 或完成状态。
- 新增或修改的人工代码注释、docstring、JSDoc、脚本说明必须使用中文，必要英文术语可以保留并在中文句子中解释。变量、类、函数、文件名、DTO字段和第三方协议字段仍按语言、框架与外部合同使用英文，禁止强行改成中文标识符。
- `noqa`、`type: ignore`、ESLint/TypeScript 指令、SPDX、正则、命令、URL、供应商原始字段名等机器可识别或外部协议要求的注释可以保留原文；如果旁边需要解释原因，另加中文说明。
- 新增或修改配置文件时，每一个新增或修改的叶子配置项都必须有紧邻的中文注释。注释至少说明“用途”和“开启/取值后的影响”；适用时还要说明类型、单位、默认值、可选范围、是否需要重启、只影响新对话还是影响运行中任务、回滚方式以及敏感值获取方式。注释不得包含真实 token、密钥或账号。
- 对 JSON 等语法不允许注释的配置格式，必须在同一变更中为每个配置键补充对应 schema `description` 或同目录中文配置说明文档，并建立可追踪的一一映射；不能因为格式不支持注释而省略说明。
- M00 必须把上述规则纳入仓库脚本：检查提交信息含中文语义、检查新增/修改注释的中文说明、检查配置键的逐项说明，并为允许的机器指令建立最小例外清单。当前没有 Jenkins 或其他远端 CI，状态保持 `automation_local_ready`，所有 push、阶段集成和最终集成前由人工触发同一套本地门禁；以后实际部署远端 CI 时再复用这些脚本配置远端门禁并提升为 `automation_active`。自动检查无法准确判断时，由独立 reviewer 逐项确认，不能默认放行。

## 项目定位

PixelFlow 是面向电商内容创作的图片、视频、视频分析、PPT制作 Agent 工作台。当前源码里同时存在两条能力线：

| 能力线 | 主要入口 | Java 类比 | 当前用途 |
| --- | --- | --- | --- |
| v2 分段工作流 | `backend/app/gateway/routers/pixelflow_intake.py`、`pixelflow_planning.py`、`pixelflow_image.py`、`pixelflow_video.py`、`pixelflow_ppt.py` | 一组面向前端步骤的 Controller + Service | 当前前端工作台主流程 |
| R1 统一会话 Runtime 候选 | `backend/pixelflow/agent_runtime/service.py`、`runtime_compaction.py`、`pixelflow_conversations.py` | Filter + 会话编排 Service + Inbox/Outbox Repository | 测试 profile 的全部新对话先经 Turn、Snapshot/SSE、上下文压缩和队列；`assist` 下业务推进权仍属于 v2，生产默认关闭 |
| R2 视频 Workflow Adapter 开发候选 | `backend/pixelflow/agent_workflows/video/planning.py`、`scene_packages.py` | 视频领域 Application Service + 权威 DTO | M11.1–M11.2 已冻结 intake/方向/Plan 及严格继承 Plan 的场景包与全局资产图；尚未接 Supervisor、供应商 Operation 或生产路由 |
| 旧 LangGraph 任务流 | `backend/app/gateway/routers/pixelflow_tasks.py`、`backend/pixelflow/graph.py`、`backend/pixelflow/nodes.py` | 固定状态机编排 Service | 仍保留任务 API、SSE、资产 API |
| DeerFlow harness | `backend/packages/harness/deerflow/` | 平台基础设施 | run/thread、checkpointer、skills、sandbox、memory |
| Web 前端 | `web/` | React 工作台 | 对话、表单、分镜编辑、产物确认 |

优先以源码和 `docs/pixelflow-agent-skill-flow-latest-design.md` 为准。旧 README 或旧设计文档可能描述的是 LangGraph-only 流程，不能直接作为当前实现依据。

## 进入项目先读

按这个顺序读，避免一上来被 harness 框架淹没：

1. `README.md`
2. `docs/pixelflow-agent-skill-flow-latest-design.md`
3. `CONTENT_APP_API_CALLS.md`
4. `backend/app/gateway/routers/pixelflow_intake.py`
5. `backend/app/gateway/routers/pixelflow_planning.py`
6. `backend/app/gateway/routers/pixelflow_image.py`
7. `backend/app/gateway/routers/pixelflow_video.py`
8. `backend/app/gateway/routers/pixelflow_ppt.py`
9. `backend/app/gateway/routers/pixelflow_jianying_draft.py`
10. `backend/pixelflow/jianying_draft/models.py`
11. `backend/pixelflow/jianying_draft/skill.py`
12. `backend/pixelflow/jianying_draft/http_skill.py`
13. `backend/pixelflow/jianying_draft/service.py`
14. `backend/app/gateway/routers/pixelflow_conversations.py`
15. `backend/pixelflow/intake/llm.py`
16. `backend/pixelflow/intake/forms.py`
17. `backend/pixelflow/intake/industry_profile.py`
18. `backend/pixelflow/creative/plan_markdown.py`
19. `backend/pixelflow/creative/plan_llm.py`
20. `backend/pixelflow/creative/contract.py`
21. `backend/pixelflow/creative/duration.py`
22. `backend/pixelflow/generate/image_prepare.py`
23. `backend/pixelflow/agent_workflows/video/planning.py`
24. `backend/pixelflow/agent_workflows/video/scene_packages.py`
25. `backend/pixelflow/generate/scene_packages.py`
26. `backend/pixelflow/generate/seedance_prompt.py`
27. `backend/pixelflow/skills/base.py`
28. `backend/pixelflow/skills/borgrise/skill.py`
29. `backend/pixelflow/skills/borgrise/run_generation.py`
30. `web/src/pages/WorkspacePage.tsx`
31. `web/src/lib/api.ts`
32. `web/src/components/chat/MessageBubble.tsx`
33. `web/src/components/composer/GenParamsDialog.tsx`
34. `web/src/components/canvas/StoryboardPanel.tsx`
35. `web/src/components/canvas/SceneMentionEditor.tsx`

模板和垂类资料在：

| 文件 | 用途 |
| --- | --- |
| `CONTENT_APP_API_CALLS.md` | PixelFlow 调用 content-app/Borgrise 接口的清单和合同记录 |
| `backend/skills/public/borgrise-creative-assistant-v2/templates/plan_video.md` | 视频策划 Agent 的 plan.md 结构模板 |
| `backend/skills/public/borgrise-creative-assistant-v2/templates/plan_image.md` | 图片策划 Agent 的 plan.md 结构模板 |
| `backend/skills/public/borgrise-creative-assistant-v2/templates/industry_profile.md` | 垂类 Skill 的预制行业画像 |
| `backend/skills/public/borgrise-creative-assistant-v2/skills/seedance-prompt/SKILL.md` | Seedance 分镜镜头描述规则；运行时由 `generate/seedance_prompt.py` 适配 |
| `backend/skills/public/borgrise-creative-assistant-v2/references/` | Borgrise/content-app 能力调用说明 |

## 当前主流程

前端工作台主流程不是一次性自由聊天，而是阶段化编排：

M13.1/R1 测试候选在主流程入口前增加统一会话层：测试 profile 对全部新对话冻结
`assist / enabled_intents=[] / 100% / context_compaction=true`，先原子保存可见消息和
Turn，再通过 Snapshot/SSE 投影压缩 Notice 与输入队列；旧 v2 只有在服务端 Turn
进入可执行状态后才继续，并复用同一个 `client_input_id`。刷新只查询权威 Snapshot
和原 job，不自动重发。历史对话、运行中任务及生产 profile 均不迁移或自动启用。

R1 修复后的上下文预算是跨 R2–R4 的强制合同：dev/prod profile 都配置
`context_budget.effective_context_k=896`、`output_reserve_k=32`、
`safety_reserve_k=32`，其中 `K=1024 tokens`；DeepSeek V4 Pro 的已验证
`max_context_tokens=1000000`。全部当前和未来 Agent/节点必须通过共享
`ContextBudgetPolicyProvider` 读取同一预算，不得增加节点级窗口常量。
`require_verified_model_profile=true` 时缺失、未验证或过期档案必须 fail-closed，
实际流程不得走底层 128K 兼容兜底。压缩失败持久化 30 秒 `retry_not_before`，
Snapshot/SSE/Run 在边界前不得重复调度；恢复专用 Plan 修订快照保留在 Store，
但不得重复进入模型上下文。新增或修改流程必须验证附件完整、自动压缩、压缩期输入
排队继续和失败受控重试。

```text
用户输入 + 附件
  -> 采集 Agent 识别 intent
  -> 表单补全与垂类画像；视频需求必须确认时长、画幅、视频模型、图片模型、用途和风格
  -> 生成 3 个创意方向
  -> 策划 Agent 按图片/视频独立模板调用 LLM 生成 plan.md 与创作合同
  -> 人工审核 plan.md；支持当前创意内版本化修订、历史回退或明确重生成新创意
  -> 图片生成 / 视频生成 / 视频分析 / PPT制作
  -> 用户确认、修改、重试或结束
```

四类 intent：

| intent | 判定入口 | 后续流程 |
| --- | --- | --- |
| `image` | 图片生成、图片编辑、参考图生成、融合等需求 | 表单 -> 创意方向 -> plan.md -> 图片参数准备 -> Borgrise 图片接口 |
| `video` | 文生视频、图生视频、参考生成视频、编辑视频、延伸视频等需求 | 表单 -> 创意方向 -> plan.md -> 视频场景包 -> 场景资产图 -> 场景视频 -> 合并 -> 修改循环 |
| `ppt` | PPT、演示文稿、汇报、路演材料等需求 | PPT表单 -> 垂类画像 -> SmartPPT大纲 -> 页面JSON -> 页面图片 -> PPT文件 |
| `video_analysis` | 分析视频、拆解视频、视频拆解等需求 | 媒体链接识别 -> 单视频/多视频 storyboard 拆解 |

默认人工确认规则：

- 创意方向选择：用户可点击“重新生成”换一组 3 个方向；必须手动选择方向，不再做 60 秒默认推荐。
- plan.md 审核：需要用户手动点击同意方案，不做倒计时自动确认。
- 图片生成结果：60 秒未反馈默认满意并结束；视频生成结果不再自动确认，必须用户手动点击“无意见，结束”才结束。
- 视频场景包确认：当前代码返回 `review_timeout_sec=None`，不做倒计时自动确认。
- 图片、视频、PPT 的需求表单弹出后，如果用户点击右上角 `X` 关闭，视为取消并终止当前流程；前端需要清空 pending 表单上下文并记录 `form_cancelled`。

前端任务看板规则：

- 图片、视频、PPT 主流程在输入框后方、从左上圆角结束位置开始显示有最大宽度的任务看板；默认折叠，只展示当前步骤和状态，折叠态由前景输入框覆盖看板底边和下方圆角，展开时向上滑出完整链路、关闭时向下收回。`video_analysis`、未知意图和意图尚未识别时不展示。
- 视频步骤固定为“需求收集 / 创意规划 / 创作规划 / 执行规划 / 素材生成 / 视频生成 / 导出交付”；PPT 为“需求收集 / 内容规划 / 大纲规划 / 页面生成 / PPT生成 / 导出交付”；图片为“需求收集 / 创意规划 / 执行规划 / 图片生成 / 导出交付”。直接图片编辑的“创意规划、执行规划”显示“已跳过”。
- 看板使用 conversation context 的 `workflowProgress`、pending job 和结果 artifact 恢复；视频场景包 `stage=prepare_scene_packages` 对应“执行规划”，`stage=generate_scene_assets` 对应“素材生成”。
- “导出交付”只在明确点击最终产物下载入口后完成：多图下载任意一张即可，视频只计算合并成品，PPT 只计算最终 PPT 文件；预览、分镜视频和 PPT 页面图不计入。下载记录写入对应消息 artifact，新结果不能继承旧结果记录。

## 核心 API

所有 Python 网关对前端或第三方暴露的新接口必须以 `/agent` 开头。当前前端 API client 里 `AGENT_API_PREFIX = "/agent"`，所以 `api.ts` 里的 `FLOW_BASE="/flows"` 最终会拼成 `/agent/flows/...`。

| 模块 | 路径 | 说明 |
| --- | --- | --- |
| 采集 | `POST /agent/flows/intake/analyze` | LLM 识别 intent、主体、行业、目标、数量 |
| 采集 | `POST /agent/flows/intake/analyze/start` | 启动可恢复意图识别 job |
| 采集 | `GET /agent/flows/intake/analyze/jobs/{job_id}` | 查询意图识别 job |
| 采集 | `GET /agent/flows/intake/forms/{intent}` | 获取图片、视频或PPT表单 schema |
| 采集 | `POST /agent/flows/intake/validate` | 表单完整性校验，最多 3 轮 |
| 采集 | `POST /agent/flows/intake/directions` | 生成 3 个创意方向 |
| 策划 | `POST /agent/flows/planning/plan` | 同步生成 plan.md，仅保留兼容旧调用 |
| 策划 | `POST /agent/flows/planning/plan/start` | 启动可恢复 Plan 生成 job |
| 策划 | `GET /agent/flows/planning/plan/jobs/{job_id}` | 查询 Plan 生成 job |
| 策划 | `POST /agent/flows/planning/plan/revise` | 同步修订 plan.md，仅保留兼容旧调用 |
| 策划 | `POST /agent/flows/planning/plan/revise/start` | 启动可恢复 Plan 修订 job |
| 策划 | `GET /agent/flows/planning/plan/revise/jobs/{job_id}` | 查询 Plan 修订 job |
| 策划 | `POST /agent/flows/planning/plan/restore` | 直接激活所选历史 Plan，不追加重复版本 |
| 图片 | `POST /agent/flows/image/prepare` | 判断图片接口并生成参数 |
| 图片 | `POST /agent/flows/image/generate` | 调用图片 skill 生成，支持多张循环生成 |
| 图片 | `POST /agent/flows/image/generate/start` | 启动可恢复图片生成异步任务 |
| 图片 | `GET /agent/flows/image/generate/jobs/{job_id}` | 轮询图片生成结果 |
| 图片 | `POST /agent/flows/image/edit-asset` | 编辑视频场景包全局素材图片，复用图片编辑 skill |
| 图片 | `POST /agent/flows/image/edit-asset/start` | 启动可恢复全局素材图片编辑任务 |
| 图片 | `GET /agent/flows/image/edit-asset/jobs/{job_id}` | 轮询全局素材图片编辑结果 |
| 视频 | `POST /agent/flows/video/analyze-storyboards` | 视频分析，自动单个/批量拆解 |
| 视频 | `POST /agent/flows/video/prepare-scene-packages` | 生成可编辑视频场景包 |
| 视频 | `POST /agent/flows/video/prepare-scene-packages/start` | 启动可恢复场景包+参考图生成 job |
| 视频 | `GET /agent/flows/video/prepare-scene-packages/jobs/{job_id}` | 查询场景包+参考图生成 job |
| 视频 | `POST /agent/flows/video/generate-scene-assets` | 生成角色三视图、场景图、道具图 |
| 视频 | `POST /agent/flows/video/generate-scene-assets/start` | 启动可恢复场景参考图生成 job |
| 视频 | `GET /agent/flows/video/generate-scene-assets/jobs/{job_id}` | 查询场景参考图生成 job |
| 视频 | `POST /agent/flows/video/generate-scenes/start` | 启动场景视频生成异步任务 |
| 视频 | `GET /agent/flows/video/generate-scenes/jobs/{job_id}` | 轮询场景视频结果 |
| 视频 | `POST /agent/flows/video/generate-direct/start` | 启动直接视频生成异步任务 |
| 视频 | `GET /agent/flows/video/generate-direct/jobs/{job_id}` | 轮询直接视频生成结果 |
| 视频 | `POST /agent/flows/video/merge/start` | 启动可恢复视频合并异步任务 |
| 视频 | `GET /agent/flows/video/merge/jobs/{job_id}` | 轮询视频合并结果 |
| 视频 | `POST /agent/flows/video/quality-review` | 视频 QAAgent QC 质检 |
| 视频 | `POST /agent/flows/video/quality-review/start` | 启动可恢复视频 QAAgent QC 质检 job |
| 视频 | `GET /agent/flows/video/quality-review/jobs/{job_id}` | 轮询视频 QAAgent QC 质检结果 |
| 剪映草稿 | `GET /agent/flows/video/jianying-draft/capability` | 查询 Provider 可用性、不可用原因和轮询间隔 |
| 剪映草稿 | `POST /agent/flows/video/jianying-draft/start` | 校验对话归属、当前版本全部成功且 URL 为 HTTPS 的分镜，启动或复用草稿 job |
| 剪映草稿 | `GET /agent/flows/video/jianying-draft/jobs/{job_id}` | 校验来源对话归属后查询草稿 job；首次读取到 `succeeded/failed/timeout/not_configured` 终态时按 job claim 幂等写经验 |
| PPT | `POST /agent/flows/ppt/summary/start` | 启动 SmartPPT 大纲生成 |
| PPT | `POST /agent/flows/ppt/summary/update/start` | 启动 SmartPPT 大纲更新 |
| PPT | `POST /agent/flows/ppt/content-json/start` | 启动大纲转页面 JSON |
| PPT | `POST /agent/flows/ppt/images/start` | 启动 PPT 页面图片生成 |
| PPT | `POST /agent/flows/ppt/images/regenerate/start` | 重新生成单页 PPT 图片 |
| PPT | `POST /agent/flows/ppt/file/start` | 启动 PPT 文件生成 |
| PPT | `GET /agent/flows/ppt/jobs/{job_id}` | 查询 PPT 阶段异步 job |
| 对话 | `POST /agent/conversations` | 新建独立对话 |
| 对话 | `GET /agent/conversations?page_size=5` | 最近对话列表，按创建时间倒序分页 |
| 对话 | `GET /agent/conversations/{conversation_id}` | 进入历史对话并恢复消息 |
| 对话 | `POST /agent/conversations/{conversation_id}/messages` | 保存用户/助手消息 |
| 对话 | `POST /agent/conversations/{conversation_id}/messages/start` | 启动可恢复消息保存 job |
| 对话 | `GET /agent/conversations/{conversation_id}/messages/jobs/{job_id}` | 查询消息保存 job |
| 偏好 | `GET/PUT /agent/users/{user_id}/preferences` | 用户偏好 |
| 旧任务流 | `/agent/flows`、`/agent/flows/{task_id}/events` 等 | LangGraph 任务、SSE、资产查询 |

附件上传是例外：前端文件上传直接调用 content-app 的 `/api/upload`，不是 Python `/agent` 接口。输入框支持文件选择，并支持从剪贴板粘贴或拖拽加入图片素材；三种入口复用同一上传 Client，上传结果作为 `materials` 随用户输入传给 Agent。

## Agent 与 Skill

这里的 Skill 可以理解成 Java 里的第三方 Client / 策略 Service / 纯逻辑能力接口。主流程应只依赖稳定 DTO，不直接把供应商细节写进前端或 Controller。

| Agent | 主要文件 | 调用的 Skill / Service | 职责 |
| --- | --- | --- | --- |
| 采集 Agent | `pixelflow_intake.py`、`intake/llm.py`、`intake/forms.py`、`intake/industry_profile.py` | IntentRecognitionSkill、FormValidationSkill、IndustryProfileSkill、CreativeDirectionSkill、动态模型配置查询 | 识别图片/视频/PPT/视频分析；视频先确认需求清洗表单和创作合同，再生成创意方向 |
| 策划 Agent | `pixelflow_planning.py`、`creative/plan_markdown.py`、`creative/plan_llm.py`、`creative/contract.py`、`creative/duration.py` | PlanTemplateFillSkill、PlanConsistencyCheckSkill、PlanRevisionSkill、PlanRestoreSkill | 图片/视频按独立模板调用 LLM 生成 Plan，校验模型能力与精确时长，维护版本历史 |
| 人工审核 Agent | `WorkspacePage.tsx` | 前端状态与对话存储 | plan.md、图片结果、视频结果的确认/修改循环；当前创意内修订不得重新生成方向，历史版本支持回退 |
| 图片生成 Agent | `pixelflow_image.py`、`generate/image_prepare.py` | ImageEndpointDecisionSkill、ImagePromptBuildSkill、ImageGenerationSkill | 选择文生图/图片编辑/参考图/多图融合，支持多图生成 |
| 视频生成 Agent | `agent_workflows/video/planning.py`、`agent_workflows/video/scene_packages.py`、`pixelflow_video.py`、`generate/scene_packages.py`、`generate/seedance_prompt.py`、`qc/video_review.py` | VideoPlanningWorkflowService、VideoScenePackageWorkflowService、ScenePackageSkill、SeedanceShotPromptSkill、SceneAssetImageSkill、SceneVideoGenerationSkill、VideoMergeSkill、VideoQualityReviewSkill | M11.1–M11.2 已以确定性 Service 固化 intake/方向/Plan 权威快照，以及严格继承 Plan 的场景包和全局资产图；后续切片接入供应商 Operation、场景视频、合并、QAAgent QC 质检和修改循环 |
| 视频分析 Agent | `pixelflow_video.py` | MediaLinkExtractionSkill、VideoDecomposeSkill | 抽取媒体链接，按单个或多个视频调用 storyboard 拆解 |
| 剪映草稿 Agent | `pixelflow_jianying_draft.py`、`jianying_draft/service.py`、`jianying_draft/http_skill.py` | JianyingDraftService、JianyingDraftSkill、HttpJianyingDraftSkill | 只接收当前版本全部成功的分镜视频，异步创建并轮询第三方任务，下载第三方 ZIP、校验后原样通过 content-app 上传 TOS；同时管理对话归属、版本幂等、超时和安全终态摘要 |
| PPT制作 Agent | `pixelflow_ppt.py`、`intake/forms.py`、`skills/borgrise/run_generation.py` | PptFormSchemaSkill、PptIndustryProfileSkill、SmartPptSummarySkill、SmartPptImageSkill、SmartPptFileSkill | 表单收集、行业补充、大纲确认/修改、页面图片生成、PPT文件生成 |
| 对话持久化 | `pixelflow_conversations.py`、`tasks/store.py` | PixelFlowTaskStore | 保存对话、消息、上下文，避免切换对话串流程 |
| 语义记忆 | `pixelflow/memory/service.py`、`app/gateway/pixelflow_memory.py` | PowerMemService | 读取用户/品牌长期偏好，记录 Agent 经验/Skill 沉淀 |

## PowerMem 语义记忆

当前第一版已经同时纳入“用户/品牌长期偏好 MVP”和“Agent 经验/Skill 沉淀”。

配置：

- 测试环境 `backend/config.dev.yml`：`pixelflow.powermem_base_url=https://test-video.borgrise.com/powermem`，经 nginx 转发到测试服务器本机 PowerMem。
- 生产环境 `backend/config.prod.yml`：`pixelflow.powermem_base_url=http://127.0.0.1:18848`，走同机 sidecar。
- `pixelflow.powermem_api_key` 必须与 PowerMem 服务端 API key 一致；不要把 content-app 用户 `Authorization` 当成 PowerMem key。
- `pixelflow.powermem_fail_open=true` 时，PowerMem 不可用只记录 warning，主流程继续。
- `pixelflow.powermem_timeout_seconds`（默认 3s）只用于 search/health 等「同步在用户请求路径上」的调用，必须短且 fail-open；record 写入走独立的 `pixelflow.powermem_record_timeout_seconds`（默认 60s），因为 record 由 `PowerMemService` 纳管为后台任务，不在请求路径上，且 PowerMem 服务端 `infer=true` 要做 DeepSeek LLM 抽取（实测约 36s），不能和 search 共用 3s 否则会被静默打断。
- PixelFlow 进程内所有 PowerMem search、record、health HTTP 请求共用同一请求闸门，避免 OceanBase `OB_SESSION_ENTRY_EXIST`。
- search/health 的锁等待和 HTTP 共用短总预算；多 category search 也只共享一次公开调用预算，超时保留已完成分类的部分结果且不再执行后续分类；record 使用独立长预算。
- 只有幂等的 search/health 在实际 5xx 响应中精确识别到 `OB_SESSION_ENTRY_EXIST` 时最多尝试 3 次；401/403 等非 5xx 和 record 都不自动重试。
- `record_power_mem_background()` 创建的任务必须交给 `PowerMemService` 追踪；服务关闭时会拒绝新请求、取消并等待后台任务、等待活动请求退出闸门后再关闭自有 HTTP client，注入的测试/外部 client 不由服务关闭。
- PowerMem fail-open 和后台错误日志只记录操作、异常类型、HTTP status 等安全元数据，不记录响应体、用户内容、异常字符串或完整 traceback。
- 该闸门不跨进程；多 worker、多容器或多副本部署仍需要 PowerMem 服务端正确管理数据库 Session。
- 网关侧 `record_power_mem` / `record_power_mem_background` 默认按 category 决定 infer：`preference` 默认 `infer=True`，让用户中文偏好进入 PowerMem 服务端语义抽取和向量化；`brand`、`experience`、`skill` 默认 `infer=False`，这些业务摘要已经由 Agent 分类，不再让 PowerMem 做二次 LLM 抽取。调用方显式传 `infer=True/False` 时以显式值为准。
- `preference` 且 `infer=True` 时，如果 PowerMem 服务端返回 `success=true` 但 `data=[]`（常见于 LLM 抽取失败、额度不足被服务端吞成空结果，或未抽出 facts），`PowerMemService.record()` 会自动用同一内容再写一次 `infer=False`，metadata 增加 `infer_fallback=true` 和 `infer_fallback_reason=empty_infer_result`，保证用户偏好至少可以直接入库并被检索。
- record 的 `memory_type` 必须和 `category` 一致：PowerMem 服务端会用 `memory_type` 覆写 `metadata.category`，若两者不一致（例如 `category="brand"` 配 `memory_type="fact"`），记忆会落到错误的 category，后续按 `filters.category` 检索时永远搜不到。

统一接入规则：

- 只能通过 `PowerMemService` 和 `app.gateway.pixelflow_memory` helper 读写 PowerMem，不要在业务路由里直接拼 HTTP。
- 进入关键决策前先检索：采集、创意方向、plan.md、图片 prepare、视频场景包、PPT 大纲、旧任务创建。
- 阶段完成或失败后写摘要：图片、视频、视频分析、PPT、旧 LangGraph run 都要记录 `experience`。
- 图片/视频/PPT 等 Skill 调用类 `experience` 会由 `record_power_mem_background()` 自动再沉淀一条 `skill` 记忆；新增流程要继续复用这个 helper。
- 用户明确偏好、偏好反馈、Brief 修订写 `preference`，默认 `infer=True`；采集出的产品/行业上下文写 `brand`，默认 `infer=False`；可复用 Skill 经验写 `skill`，默认 `infer=False`。
- 当前 `infer=True` 写入场景只有用户偏好类：`PUT /agent/users/{user_id}/preferences` 的结构化偏好更新、`POST /agent/users/{user_id}/preferences/feedback` 的用户反馈、旧 LangGraph `/agent/flows/{task_id}/brief/revise` 的 Brief 修改意见。以后新增流程、修改现有流程或新增功能时，只要用户明确表达了长期偏好、默认生成规则、负向要求、品牌口吻偏好、风格偏好或可跨对话复用的个人选择，就必须写 `category=preference` 并使用默认 `infer=True`；是否应该调用 PowerMem 由后续 agent 在需求实现时主动判断。
- PowerMem 只写业务摘要、偏好和经验，不写用户 token、供应商 key、完整异常堆栈、本地部署目录或原始大段 prompt。
- `pixelflow_user_preferences` 仍是结构化业务偏好 Store，PowerMem 不替代它，只提供语义检索和跨 Agent 经验复用。
- 以后新增或修改 Agent、流程、Skill 时，必须按同一套逻辑：读 `PowerMemService` 上下文，写阶段摘要，并同步更新 `docs/pixelflow-agent-skill-flow-latest-design.md`。

## 剪映草稿 Agent

剪映草稿位于最终视频确认阶段，输入是当前版本全部成功且 `video_url` 为 HTTPS 的场景视频，绝不能用合并视频或本地 Blob 地址替代。前后端用相同 FNV-1a 64 位规范从有序 `scene_id/scene_index/task_id/video_url` 计算 `storyboard_version_id`。`conversation_id + storyboard_version_id` 是幂等键：运行中和未过期成功任务复用，`failed/timeout` 只有用户显式传 `retry_failed=true` 才会新建 job；成功结果过期后可重新生成，旧版本只能保留为历史下载入口。

真实实现是 `HttpJianyingDraftSkill`。它按 `scene_index` 排序后的分镜顺序向第三方 `POST /api/jianying/draft/tasks` 提交 `[{videoUrl, videoOrder}]`，`videoOrder` 从 1 连续递增；绝不能提交合并视频。随后通过 `POST /api/jianying/draft/tasks/result` 轮询：`20201/20202` 继续等待，`200` 的 `data` 接受单个公开 HTTPS ZIP URL，兼容第三方真实响应使用单元素数组包装该 ZIP URL，多个 URL、非 ZIP 或空 ZIP 均失败；`50002/41001` 等业务失败立即结束。ZIP 最大 200 MiB，下载后只校验格式和非空，不解压、不重新打包，再复用 content-app `/api/upload` 携带当前用户 Authorization 上传 TOS，最终只把自有 TOS ZIP HTTPS 地址返回前端。上传在线程中执行，协程取消时仍等待上传线程结束后再清理临时 ZIP，避免文件被提前删除或已上传结果成为孤儿；第三方错误文案还必须过滤 URL、token、Authorization、API key、secret、密钥和凭据。第三方域名、固定 token、连接/读取超时和重试次数都从 `config.dev.yml/config.prod.yml` 的 `pixelflow.jianying_draft_*` 读取；配置不完整时装配 `UnavailableJianyingDraftSkill`。

未结束最终视频卡片有“无意见，结束”“生成剪映草稿”“提出修改意见”三个按钮。草稿生成期间锁定三项视频操作，但不锁对话输入；前端按 capability 返回的间隔（默认 2 秒）轮询，客户端和服务端最大等待 30 分钟。`pendingJianyingDraftJob`、按版本的 `jianyingDraftRecords` 和恢复错误必须通过 `/agent/conversations/{conversation_id}/jianying-draft-context` 原子 PATCH 写回来源对话；刷新、离开或切换对话后只能恢复查询原 job，结果消息按 job ID 去重。视频点击“无意见，结束”后，草稿下载/重生历史入口仍保留，成功不自动下载。

路由在 `GET /agent/flows/video/jianying-draft/jobs/{job_id}` 首次读取到 `succeeded/failed/timeout/not_configured` 终态时，才通过 claim 调用 `record_power_mem_background()` 写 `category=experience`、`memory_type=experience`、`infer=False` 的安全摘要，`source_agent=jianying_draft_agent`；停止轮询不会自行写入。不得写入 Authorization、第三方密钥、完整下载 URL 查询参数、ZIP 内容或异常堆栈。第三方剪映接口不是 content-app 接口，但最终 ZIP 使用 `/api/upload`，因此相关调用变化仍要同步记录到 `CONTENT_APP_API_CALLS.md`。

当前 `JianyingDraftService` 的 job registry、幂等索引和后台 task 都在单 Uvicorn worker 的进程内。未来部署到多 worker、多容器或多副本前，必须改为共享且持久化的 job store，否则不同进程无法共同保证 job 查询、幂等和终态去重。

## Borgrise/content-app 能力

`backend/pixelflow/skills/base.py` 定义 Protocol，`backend/pixelflow/skills/borgrise/skill.py` 是实现层，阻塞 HTTP 和轮询集中在 `run_generation.py`。

图片接口：

| Skill 方法 | content-app/Borgrise 接口 |
| --- | --- |
| `text_to_image` | `/api/picture/text_to_image` |
| `reference_image` | `/api/picture/multi_reference_image_generation` |
| `image_edit` | `/api/picture/image_edit` |
| `multi_image_fusion` | `/api/picture/multi_image_fusion` |
| 图片模型参数配置 | `/api/modelParamConfig/listByCategory/image_generate` |

视频接口：

| Skill 方法 | content-app/Borgrise 接口 |
| --- | --- |
| 视频模型参数配置 | `/api/modelParamConfig/listByCategory/video_generate` |
| `text_to_video` | `/api/video/text-to-video` |
| `image_to_video` | `/api/video/image-to-video` |
| `two_image_to_video` | `/api/video/two-image-to-video` |
| `reference_mode_video` | `/api/video/reference-mode-video` |
| `edit_video` | `/api/video/edit-video` |
| `extend_video` | `/api/video/extend-video` |
| `merge_videos` | `/api/video/merge` |

五类场景视频请求体必须按 content-app 当前 DTO 精确映射：

- `text-to-video`：`prompt/model/ratio/size/duration/videoCount/sound`
- `image-to-video`：`image_url/prompt/duration/ratio/model/size/sound/videoCount`
- `two-image-to-video`：`first_frame_image_url/last_frame_image_url/prompt/ratio/duration/model/size/videoCount/sound`
- `reference-mode-video`：`prompt/imageUrls/videoUrls/audioUrls/duration/ratio/sound/model/size/videoCount`
- `edit-video`：`prompt/refImage/refVideo/model/duration/size/ratio/videoCount/sound`

不要重新加回 content-app DTO 未声明的旧字段。Seedance 单分镜时长允许 4-15 秒，必须透传真实整数秒。

视频理解接口：

| Skill 方法 | content-app/Borgrise 接口 |
| --- | --- |
| `extract_media_links` | `/api/creative/extractMediaLinks` |
| `decompose_video_to_storyboard` | `/api/creative/decompose_video_to_storyboard` |
| `batch_decompose_video_to_storyboard` | `/api/creative/batch_decompose_video_to_storyboard` |
| `review_video_quality` | `/api/creative/video_quality_review` |

SmartPPT接口：

| Skill 方法 | content-app/Borgrise 接口 |
| --- | --- |
| `generate_ppt_summary` | `/api/picture/smart-ppt/generatePptSummary` |
| `update_ppt_summary` | `/api/picture/smart-ppt/updatePptSummary` |
| `generate_ppt_content_json` | `/api/picture/smart-ppt/generatePptContentToJson` |
| `generate_ppt_image` | `/api/picture/smart-ppt/generatePptImage` |
| `generate_ppt_file` | `/api/picture/smart-ppt/generatePptFile` |

调用约束：

- 必须透传入口请求的 content-app `Authorization`。
- 不要把用户 token、账号、密码写入配置或代码。
- content-app 返回 HTTP 402 或“额度不足/余额不足/没有有效额度/充值”等文案时，必须暂停当前流程并返回可恢复提示。
- 图片轮询超时按配置默认 10 分钟，视频轮询默认 1 小时，视频分析默认 15 分钟，SmartPPT 每一步默认 2 小时。
- 第三方异常可按 `borgrise.max_retries` 重试；业务失败不要无意义重试。
- `/api/task/{taskId}/status` 状态轮询如果遇到 SSL EOF、握手超时等可恢复网络错误，`run_generation.poll_task()` 会在单次请求重试后继续轮询最多 3 次；401、402、额度不足和非重试业务错误仍立即返回。

## 图片流程要点

图片采集表单在 `intake/forms.py`：

- `image_goal` 必须保留真实目标，如“书包宣传图”，不能退化成“宣传”。
- `image_type`、`image_usage`、`image_style`、`image_size` 一起进入创意方向和 plan.md。
- 前端图片尺寸只展示 `1:1`、`16:9`、`9:16`、`自动适配`。
- `自动适配` 会由 `image_prepare.py` 根据用途和目标映射到供应商支持比例。
- 用户明确要求多张图片时，`requested_output_count` 会进入 `intake_context`，最终 `image/generate` 按数量循环调用，默认 1 张，最多 10 张。
- 图片 plan.md 同意、图片修改重生成、图片编辑参数确认后，前端必须调用 `/agent/flows/image/generate/start` 获取 `job_id`，并立即把 `pendingImageJob` / `pending_image_job` 写入 conversation context。用户离开 iframe、刷新或切回同一历史对话时，只继续查询 `/agent/flows/image/generate/jobs/{job_id}`，不能重新调用 `/start`，避免重复计费；job 404 或过期时只提示用户从最新图片卡片手动重试，不自动重启。
- `pendingImageJob.kind` 固定为 `image_generation`、`image_regeneration`、`direct_image_edit` 或 `scene_global_asset_edit`；`job_api` 固定为 `generate` 或 `edit_asset`；字段必须包含 `job_id`、`conversation_id`、`source_message_id`、`started_at`、`request`、`artifact`。
- 采集阶段如果 LLM 或 fallback 识别到 `image_operation=image_edit`，前端不再弹普通图片表单，不再进入创意方向和 plan.md；有原图时先调用 content-app `/api/modelParamConfig/listByCategory/image_generate` 展示图片编辑模型、尺寸和清晰度确认卡，默认选 `gpt-image-2`，确认后再调用 `/agent/flows/image/prepare` + `/agent/flows/image/generate/start`，最终走 `/api/picture/image_edit`。用户确认的模型、尺寸和清晰度必须写入对话 context 的 `imageEditConfirmedSelections`，切换对话或刷新恢复后继续展示确认过的参数。图片编辑结果成功后保留“满意，结束 / 重新生成”，60 秒未操作默认满意并结束。
- 图片编辑分支会让 LLM 抽取用户指定的 `image_size` 和 `image_quality`；如果所选模型不支持该尺寸或清晰度，前端必须提示，并自动落到当前模型可用参数，用户可以重新选择可用尺寸和清晰度后继续提交。如果用户没有指定，则按所选模型自动选择一组可用参数。图片编辑失败后，“重新生成图片”必须重新打开模型/尺寸/清晰度确认卡，不能直接复用失败参数盲重试。
- 图片编辑模型、尺寸和清晰度的可选项以 content-app `/api/modelParamConfig/listByCategory/image_generate` 实时响应为准；Python 侧只做通用清晰度格式校验和缺省值兜底，不再用硬编码模型白名单拦截用户已确认的参数。模型级参数是否合法由前端实时配置和 content-app 生成接口共同兜底。
- content-app 图片编辑请求体里 `size` 表示比例，如 `9:16`；`imageSize` 表示清晰度，如 `2K/4K/1080p`。PixelFlow 网关需要保持两者分离，不能把 `size` 当清晰度覆盖用户选择。
- 图片编辑必须有原图；如果用户没有上传图片，前端会提示“请上传需要编辑的图片”，并把 `pendingImageEditRequest` 存入对话 context，用户上传后可从同一对话继续执行。
- 有附件时，附件 URL 会进入 `materials`，图片编辑/参考图/多图融合会根据素材数量和用户语义选择接口。

## 视频流程要点

视频主流程仍是：plan.md -> 多个视频场景片段 -> 每段生成视频 -> 按顺序合并。

需求清洗与创作合同：

- 视频粗略需求必须先展示表单。必填项除原产品信息、产品品类、目标人群、转化目标外，还包含 `video_duration_sec`、`video_ratio`、`video_model`、`image_model`、`video_usage`；`visual_style` 可由 LLM 预填并允许用户修改。
- `video_duration_sec` 预设 30/60/90/180；选择自定义后只能提交 4-300 的自然数。前端和 Pydantic 都必须校验。
- 视频模型来自 `/api/modelParamConfig/listByCategory/video_generate`，前端展示 content-app 返回的所有启用 Seedance 模型；系统推荐默认 `seedance-2.0` 并展示解析结果。2.0 只是推荐默认值，不是 `seedance-prompt` 的调用开关；画幅下拉只展示当前视频模型支持值。
- 模型特有的画幅、清晰度、声音、单分镜时长和参考素材能力以 content-app 实时配置与实际生成 API 为准，不得按型号名称在 PixelFlow 中自行假设。`video_model_capabilities` 必须完整保存 `aspect_ratios/sizes/sound_options/durations_sec/generation_types/upload_file_types`；新的采集表单缺少完整实时快照时必须阻止提交，只有历史对话恢复可按旧合同兼容。切换模型后必须把不支持的旧清晰度改成当前模型支持值，不能让 `seedance-2.0-mini/fast` 携带 `1080p`。
- 图片模型来自 `/api/modelParamConfig/listByCategory/image_generate`，默认 `gpt-image-2`。用户不选择场景资产图片比例和清晰度；前端把所选模型支持的 `aspect_ratios/sizes` 作为 `image_model_capabilities` 提交。
- 用户确认表单后生成 `creation_contract`。优先级固定为用户确认值 > LLM 预填 > 默认值。后续创意、Plan、场景包、场景资产和视频生成不得覆盖它。
- Plan LLM 只能从 `image_model_capabilities` 中选择 `scene_image_ratio/scene_image_size`；非法值按规则修正。最终生产合同必须随 Plan artifact、conversation context 和 pending jobs 保存。
- 图片和视频 Plan 模板分别是 `plan_image.md` 与 `plan_video.md`。视频 Plan 优先调用 `deepseek-v4-pro` 并加载 `skills/seedance-prompt/SKILL.md`，由 LLM 自主生成总分总 `scene_blueprints`，不得预先按 10 秒机械切分；失败时才使用同合同的叙事职能加权兜底。
- `scene_blueprints` 是 Plan 阶段的权威脚本合同，包含叙事职能、连续起止秒、时长、故事线、Seedance 镜头描述、旁白、转场和资产需求。结构 Plan 先确定时间线与资产需求并生成稳定 `asset_id`，随后必须调用 `creative/seedance_plan.py` + vendored `skills/seedance-prompt/SKILL.md` 对全部分镜做专用写作；它必须随 Plan artifact、每个历史版本、conversation context 和 `pendingScenePackageJob.request` 持久化。
- Seedance Plan 写作必须传入用户已确认的 `video_model`、完整创作合同、当前 Plan、全部蓝图、稳定资产 ID、用户要求和附件，只允许合并 `title/storyline/shot_description/narration/transition`。每镜描述是一整段中文，内部使用从 0 秒连续覆盖到当前镜时长的整数秒级时间码，禁止 ms、毫秒和小数；必须覆盖地点、主体、动作、景别、运镜、光影、声音和收束，只引用本镜声明的 `@character-*`、`@scene-*`、`@prop-*`，每次说明用途且最多 9 张。分镜数量、顺序、时间线、模型、画幅、卖点、转化目标和资产集合不可修改；非法响应整批拒绝并携带错误重试一次。Plan 修订和手工编辑重新对齐同样执行该阶段，并携带当前版本、候选版本、修改意见、附件和上下文。
- 场景包恢复历史已审核 Plan 时允许兼容旧的全局镜头时间段，并确定性转换为当前分镜的 `0-N秒` 局部时间段；新 Plan 候选仍必须通过严格局部时间轴校验。
- PowerMem 长期记忆只允许作为 LLM 内部决策上下文，不得在面向用户的 plan.md 中输出“长期记忆约束”、PowerMem、Skill/Agent 运行日志或记忆原文；场景包阶段也不得改写已审核 Plan 来追加记忆文本。
- Plan 初次生成和当前创意内修改分别调用 `/planning/plan/start`、`/planning/plan/revise/start`，轮询对应 `/jobs/{job_id}`；前端必须保存 `pendingPlanJob`，恢复时只查询原 job。当前创意内修改产生 v2/v3；只有明确选择“重新生成新创意”才返回 3 个方向。
- 用户在右侧编辑器直接修改完整 plan.md 后，`/planning/plan/save-edit` 也必须调用 Plan 修订 LLM，把编辑稿重新对齐 `creation_contract` 与视频 `scene_blueprints`；合同字段白名单只能来自当前稿与编辑稿的确定性文本差异，完整稿仅供 LLM 重写内容和蓝图，禁止借机修改用户未编辑字段。三者同时校验通过才发布 `manual_edit` 新版本，失败时保留当前版本，禁止只保存 Markdown 并沿用旧合同。
- 当前创意内修订必须先合并结构化合同补丁，再调用 LLM 重写 Plan。优先级是“用户意见中的明确值 > LLM `creation_contract_patch` > 当前版本合同”；未提及字段不得变化。修订 LLM 的上下文必须包含当前 Plan、表单、选中创意、垂类补充、附件、采集上下文和 PowerMem 检索结果。
- “视频总时长延长/缩短 N 秒”按当前合同计算增量，“把片子改成 N 秒”等自然说法按新的绝对总时长处理。Plan 修订阶段没有新模型的实时能力快照，因此不能直接修改视频模型或图片模型；用户需要返回需求表单重新选择模型并确认能力。
- 修订候选合同或分镜蓝图第一次校验失败时，只允许把原因反馈给 LLM 再修正 1 次；第二次仍失败必须保留当前 Plan、合同、蓝图和历史，前端显示失败原因，不能发布带错误合同的新版本。
- “用户明确修改”只允许从指向合同字段的语句提取：单分镜时长、画面中的数量或否定式“不要改/保持不变”不能误改总时长、图片数量或风格。LLM 生成的候选合同与蓝图校验失败时，只携带具体校验反馈再修正 1 次；第二次仍失败必须保留当前版本、历史、合同和蓝图，不得发布脏版本。
- 视频修订后的 `video_duration_sec` 必须重新约束 `scene_blueprints`，每镜 4-15 秒且总和精确相等；图片修订后的 `image_goal/image_type/image_usage/image_style/image_size/image_count` 是后续 prepare 的权威输入，即使最终数量为 1 也必须覆盖旧采集上下文中的多图数量。
- 图片最终合同必须先于 PowerMem 检索和 content-app 调用完成严格校验；目标、类型、用途、风格、尺寸必须是非空字符串，数量为 1-10，比例只能精确匹配 `1:1/16:9/9:16/自动适配`。历史对话的空合同 `{}` 按缺失合同兼容。
- `/agent/flows/planning/plan/restore` 直接激活所选历史版本并保持既有历史不变，不追加重复版本；回退后的激活版本会持久化到 conversation context，刷新或重新进入对话后继续展示该版本。
- 回退后再次“继续修改”时，以历史最大版本号加一创建新版本，例如 v2 回退到 v1 后修订生成 v3，同时保留 v2。
- 每个新历史条目保存 `creation_contract` 与 `scene_durations_sec` 快照；旧对话的历史条目缺少快照时，沿用当前权威创作合同与分镜时长。

场景包结构：

- 每个片段最少 4 秒，最多 15 秒。
- 所有片段的整数秒时长总和必须精确等于当前 Plan 合同的 `video_duration_sec`；300 秒可以超过旧 18 分镜上限。
- 视频 Plan 必须同时发布结构化 `asset_manifest` 和固定“全局资产清单”第四章。角色项包含最终名称、文字说明、`three_view_prompt`；场景/道具项包含最终名称、文字说明、`image_prompt`。三类名称全局唯一，并分别与所有蓝图 `asset_requirements` 的同类并集完全一致；初次生成、Agent 修订、手工编辑和历史回退都必须版本化保存。
- 场景包必须消费当前激活 Plan 的 `scene_blueprints + asset_manifest`，不得再按总时长重新切分、重写故事内容或调用第二次 LLM 分析资产；只机械映射全局资产、`@asset_id` 和 mentions。缺少清单的旧 Plan 必须先重新生成或修订，不能继续生成场景包。
- M11.2 的 `VideoScenePackageWorkflowService` 要求 `plan_review` 先经用户显式同意并持久化为 `plan_approved`，再进入 `generate_scene_assets` 和 `scene_package_review`；边界重新校验当前 Plan 与完整历史并冻结场景包 SHA-256 权威快照。场景 ID/顺序/时长、叙事字段、执行提示词和允许字段集合都必须等于权威蓝图或确定性机械结果；供应商追加字段、追加故事或改写提示词一律失败关闭。
- 权威蓝图中的人物、场景和道具需求必须逐项进入全局资产，四类全局 ID（含 `visual_style.asset_id`）必须唯一；已有 `@asset_id` 不得被名称规范化再次替换。任一分镜引用超过 9 张时直接返回包含分镜编号和引用数的明确错误，不得静默截断。
- 全局资产名称、场景包名称和前端 `shot_description.mentions[].name` 必须等于最终 Plan 名称；旧缓存名称不能覆盖全局正式名称。每个清单资产只创建一个图片任务并只绑定一个图片 URL，同一资产跨分镜复用时不得重复生图。
- 场景资产调用 content-app 时，提示词必须合并最终 Plan 清单的正式名称、`description` 和 `three_view_prompt/image_prompt`，不能只取生图字段而丢失文字说明里的外观、材质或颜色约束。
- 全局固定资产：`characters`、`scenes`、`props`、`visual_style`。
- `characters` 只能是人物角色，每个角色必须生成同一人物的正面、侧面、背面三视图。
- 产品、商品、包装、工具、卖点物件必须进入 `props`，不能放进 `characters`。
- 逐片段变化字段：`storyline`、`shot_description`、`narration`。
- `shot_description.text` 是一整段文本，不能拆成多个表单字段；文本里可以使用 `@asset_id` 关联角色、场景、道具。
- `shot_description.text` 里的时间范围必须展示为秒级，例如 `0-10秒`、`10-15秒`；不要出现 `ms`、`毫秒` 或 `00:00.000` 这类毫秒时间码。
- `shot_description.mentions` 保存 @ 选择对应的图片 URL，生成视频时这些 URL 会作为参考图集合。
- 镜头描述由 `generate/seedance_prompt.py` 应用 vendored `skills/seedance-prompt/SKILL.md` 规则生成。该 Skill 对所有启用的 Seedance 系列模型通用，场景包 Prompt 必须显式携带用户确认的 `video_model`，并经过秒级时间码、`@asset_id` 和最多 9 张引用校验。
- `skills/seedance-prompt/THIRD_PARTY_NOTICE.md` 保留两个输入来源、哈希和授权边界，具有来源审计价值，不能当作无用文件删除。
- 每个视频场景片段最多 9 张参考图，前端和后端都要限制。
- M11.2 仅接受每个清单资产恰好一个 HTTPS 图片 URL 并按 `asset_id` 回填 mentions；真实供应商 Operation、部分失败、额度暂停、重试和单镜生成属于 M11.3，不得提前塞入本 Service。
- 前端 `SceneMentionEditor` 是 `contentEditable`，用户输入 `@` 后弹出素材下拉，素材 chip 可预览。
- 全局素材图片可在 `StoryboardPanel` 点击预览并“引用素材”到左侧输入框；用户发送编辑指令后，`WorkspacePage` 识别 `materials.source="scene_global_asset"`，调用 `/agent/flows/image/edit-asset/start` 走可恢复图片编辑 job。编辑成功后先展示候选图，用户确认后才替换 `global_assets` 中原图：角色替换 `three_view_images[0]`，场景/道具替换 `images[0]`，并同步同 `asset_id` 的 `shot_description.mentions[].image_url`。全局素材编辑或融合失败卡片必须保存 `imageEditRequest`、素材引用、修改意见、模型参数、场景包来源和上传参考图；点击“重新生成图片”时必须先于普通图片的 `imagePrepare` 判断恢复 `scene_global_asset` 上下文并重新打开参数确认卡，确认后调用对应 `/edit-asset/start` 或 `/fuse-asset/start`，不能静默退出或掉回普通采集 Agent。历史失败卡片缺少完整请求时，至少从 `materials.source="scene_global_asset"` 恢复引用、修改意见和编辑链路。
- 全局素材预览里的“删除素材”只预填左侧固定删除文案并带上素材 chip；用户发送后，`WorkspacePage` 根据 `scene_global_asset_action="delete"` 在当前场景包内原地清理该素材的 `reference_asset_ids`、`shot_description.mentions`、精确 `@素材名/@asset_id` 文本和相关 `image_urls`，同时保留 `global_assets` 素材记录但清空图片 URL 作为占位符，不推送新的场景包确认卡片。
- `StoryboardPanel` 的角色、场景、道具三行末尾必须固定显示“添加素材”，并以 `operation="add"` 复用替换素材弹层。添加结果只追加到当前场景包 `global_assets`：ID 使用 `character-manual-*`、`scene-manual-*`、`prop-manual-*` 并保持全局唯一，三类来源名称重名时自动追加序号，角色写 `three_view_images`，场景/道具写 `images`，数字人保留 `generation_reference_url=asset://thirdAssetId`。不得修改当前 Plan `asset_manifest` 或自动写入镜头；只有用户之后在镜头描述中手动 `@` 选择时，才更新 mentions/reference IDs、标记 dirty scene，并在已有视频上只重生成受影响镜头。新增资产必须原地持久化到当前消息和 conversation context，保留已有分镜视频、合并视频及 dirty scene 状态。
- 全局素材替换弹窗保留两条本地图片入口：原“本地上传”只调用 `/api/upload` 并在二次确认后临时替换，不创建资产记录；图片素材列表第一张“上传到资产库”依次调用 `/api/projects`、`/api/upload`、`/api/asset/create`，再回查 `/api/asset/assets` 第一页，创建响应的 `data.id` 仅用于本次弹窗标记“刚刚上传”和同步重试。资产上传只校验 JPG/JPEG/PNG/WEBP 与 20MB，不校验宽高；取消替换时已创建资产继续保留。需要真实上传进度时复用 `uploadAttachment(file, { onProgress })`，其内部用 XHR 监听上传进度，默认无回调调用仍走 fetch。
- 对话中只有最后一个 `video_scene_packages` 卡片能展示“查看分镜 / 确认并生成视频 / 重新生成参考图”等操作；旧场景包卡片只能作为历史预览，防止用户基于过期素材继续生成。
- 场景视频和合并视频生成完成后，未结束的 `video_result` 卡片固定展示“无意见，结束 / 生成剪映草稿 / 提出修改意见”三个按钮；草稿运行中锁定三个按钮。最终视频卡片不再展示“查看分镜”。
- 场景视频和合并视频生成完成后，前端会把 `generatedSceneVideos` 和 `mergedVideo` 回填到原 `video_scene_packages` 卡片。用户继续点击原来的“查看分镜”时仍打开 `StoryboardPanel`，但镜头预览优先展示每个分镜已生成的视频，缺视频时才回退到参考图。
- 场景视频 job 内部仍可并发调度多个分镜，但 `run_generation.py` 对所有会创建 content-app 计费生成任务的 POST 使用进程内串行闸门：前一个创建接口返回 taskId 并完成 content-app 扣费确认后，才提交下一个图片或视频创建任务；`/api/task/{taskId}/status` 轮询不加锁，可以并行等待结果。必须等本批所有分镜都成功、失败或额度暂停后才汇总返回。全部成功后仍按 `scene_index` 调用 `/agent/flows/video/merge/start` 启动可恢复视频合并 job，再轮询 `/agent/flows/video/merge/jobs/{job_id}`，不能按完成先后顺序合并；如果只有 1 个分镜，merge job 直接把该分镜视频作为最终合成视频返回，不再调用 content-app `/api/video/merge`。
- 多个分镜的视频合并由 Python merge job 调用 content-app `/api/video/merge`。content-app 该接口本身是同步等待下载、ffmpeg 合并和上传完成，不走 `/api/task/{taskId}/status` 轮询；PixelFlow 前端不能直接长连接等待，只能保存 `pendingVideoJob.kind="video_merge"` 并轮询 Python job。后端必须使用 `BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT` 控制 content-app 读等待，默认 1 小时，不能复用普通 HTTP 30 秒超时。若 content-app 返回业务失败或网络异常，Python job 必须标记 `status=failed`，并在 `result.error/message/raw.details` 中保留 content-app 原始错误，前端不能把失败合并展示成“合并完成”。
- 场景视频生成时，每个分镜的可恢复异常最多尝试 3 次；HTTP 4xx 参数校验、模型价格配置缺失和能力不匹配属于不可重试业务失败，只调用一次并保留 content-app 原始字段错误。普通异常重试耗尽后写入 `failed_scenes`，字段至少包含 `scene_id`、`scene_index`、`error`、`attempts`。额度不足不对每个分镜重复刷屏，整批只提示一次额度不足，同时保留具体额度暂停分镜到 `failed_scenes`。
- 场景视频失败或额度暂停后，前端再次点击同一场景包的“确认并生成视频”时，只把 `generatedSceneVideos.failed_scenes` 中的分镜提交到 `/agent/flows/video/generate-scenes/start`，已成功的分镜视频从 `generatedSceneVideos.scene_videos` 复用；补齐后再按 `scene_index` 合并完整视频。
- 用户在原场景包的 `StoryboardPanel` 里修改单个分镜故事线、镜头描述、旁白或 @参考图时，前端必须记录 `videoScenePackageEditedSceneIds`。再次点击“确认并生成视频”时只把这些已修改分镜提交到 `/agent/flows/video/generate-scenes/start`；未修改分镜复用旧 `generatedSceneVideos.scene_videos`，随后按 `scene_index` 重新调用 `/agent/flows/video/merge/start` 合并新版最终视频，并再次回填原场景包卡片。
- 视频 plan.md 同意后，前端必须调用 `/agent/flows/video/prepare-scene-packages/start`，后端 job 内部顺序执行“生成可编辑场景包 -> 生成角色三视图、场景图、道具图”。前端拿到 `job_id` 后必须立即写入 conversation context 的 `pendingScenePackageJob` / `pending_scene_package_job`，字段包含 `job_id`、`conversation_id`、`kind`、`started_at`、`request`、`artifact`、`source_message_id`。
- 场景资产图片必须使用当前 Plan 合同中的 `image_model/scene_image_ratio/scene_image_size`；分镜视频必须使用 `video_model/video_ratio/video_size/video_sound`。图片模型不能传给视频接口，视频模型也不能传给图片接口。
- `pendingScenePackageJob.kind` 固定为 `scene_package_generation` 或 `scene_asset_generation`。用户离开再返回同一对话时，只继续查询已有 `/prepare-scene-packages/jobs/{job_id}` 或 `/generate-scene-assets/jobs/{job_id}`，不能重新调用 `/start`。job 404 或过期时只提示用户从最新 plan 或场景包卡片手动重试，避免重复计费。
- 场景包主链路 job 的 `stage` 包含 `prepare_scene_packages`、`generate_scene_assets`、`completed`。参考图额度不足时 job 状态为 `quota_paused`，result 必须保留已生成的 `videoScenePackages` 和 `sceneAssetFailures`，前端展示可继续的 `video_scene_packages` 卡片。
- 每个 `sceneAssetFailures` 条目必须保留 `asset_id/asset_name/asset_type`、所属 `scene_id/scene_index`、`endpoint/model/ratio/size`、最终 `error`、`attempts` 和供应商 `raw`。场景包卡片除失败数量外必须提供“查看失败原因”，逐张展示失败素材和真实原因；参考生成回退文生图时要同时展示两次尝试。素材缺少生图提示词、调用失败、成功响应没有 URL 都必须生成明确失败条目，不能静默留下“待生成”占位，也不能折叠成泛化的“图片生成失败”。

场景视频接口选择：

- 如果片段显式给了 `generation_mode`，以后端传入为准。
- 否则 `pixelflow_video.py` 根据图片、视频、音频素材和提示词选择 `text_to_video`、`image_to_video`、`two_image_to_video`、`reference_mode_video`、`edit_video` 或 `extend_video`。
- 场景视频生成使用异步 job，前端轮询 job 状态，避免网关长时间阻塞；job 内部可以并发调度多个分镜视频，但 content-app 创建任务必须经 `run_generation.py` 串行提交，对前端仍表现为一个可恢复 job。
- 场景视频生成和视频修改重生成启动后，前端必须把 `job_id`、原始请求、来源 artifact 和 `conversation_id` 写入 conversation context 的 `pendingVideoJob` / `pending_video_job`。用户离开再返回同一对话时只继续查询 `/agent/flows/video/generate-scenes/jobs/{job_id}`，不能重新调用 `/start`，避免重复生成和重复计费。
- 视频 QAAgent QC 必须走 `/agent/flows/video/quality-review/start` 和 `/agent/flows/video/quality-review/jobs/{job_id}`；如果 content-app 返回业务失败或模型网关错误，Python job 状态必须是 `failed`，并保留 `result.error/message/raw.details`，前端展示“视频质检失败”而不是“质检完成”。content-app 侧会对长视频生成低码率质检预览再送入模型，避免 300 秒级成片直接 base64 后超过模型请求体限制。

## PPT流程要点

PPT 主流程是：PPT需求识别 -> PPT表单 -> 垂类画像 -> SmartPPT大纲 -> 用户确认/修改大纲 -> 页面JSON -> 页面图片 -> PPT文件。

- PPT 表单字段在 `intake/forms.py`，包含 `ppt_topic`、`ppt_style`、`attachments`；`ppt_style` 预设含“自定义”，用户选中后前端展示文本框，并把输入内容作为 `ppt_style` 传给 SmartPPT。
- PPT 附件只允许 Word、Excel、PDF，前端上传仍走 content-app `/api/upload`。
- PPT 行业补充复用 `resolve_industry_profile()`：先查项目内垂类模板，未命中就调用当前项目 LLM `deepseek-v4-pro` 生成同结构行业画像，LLM 失败时才使用通用电商兜底。
- SmartPPT 的大纲、更新大纲、转 JSON、生成页面图、生成文件都经 `/agent/flows/ppt/*/start` 启动异步 job，再由前端轮询 `/agent/flows/ppt/jobs/{job_id}`；多页图片生成可以并发调度页面任务，但 content-app 创建任务同样由 `run_generation.py` 串行提交，轮询结果可并行等待。
- PPT 页面图片阶段会先展示所有页面格子为“图片生成中...”，省略号动态循环；后端每生成一页更新一次 job result，前端把 partial status 原地写回同一张卡片。
- PPT 单页图片 `running` 时不展示重新生成按钮；已生成或失败后才允许重试。重试单页时必须把原小格子切回生成中并原位更新，不能追加新的整组 PPT 图片卡片；所有页面都完成且无失败时才展示“开始生成PPT附件”。
- 任意 SmartPPT 阶段如果遇到额度不足，需要进入可恢复暂停；用户充值后回到同一对话可以继续点击上一阶段按钮重新执行。
- PPT 文件生成完成后前端展示下载入口；用户选择重新生成附件时只重复文件生成阶段，不重新跑大纲和页面图。

## 对话隔离与恢复

对话是用户可见工作台的主上下文：

- 新建对话必须新建 `conversation_id`，不能复用旧对话。
- 用户消息、Agent 消息、artifact、当前上下文都要保存到 `pixelflow_conversations` / `pixelflow_conversation_messages`。
- 新需求入口的用户消息保存必须走 `/agent/conversations/{conversation_id}/messages/start` + `/messages/jobs/{job_id}`，并把 `pendingMessageJob` / `pending_message_job` 写入 conversation context；消息保存 job 完成后再启动采集 job。切换页面、离开 iframe 或刷新恢复时只查询已有 job，不重新追加同一条用户消息。
- 新需求入口的采集意图识别必须走 `/agent/flows/intake/analyze/start` + `/analyze/jobs/{job_id}`，并把 `pendingIntakeJob` / `pending_intake_job` 写入 conversation context；恢复时只轮询已有 job，不重新调用 `/start`，避免重复推进流程。`/agent/flows/intake/analyze` 和 `/agent/conversations/{conversation_id}/messages` 只保留兼容旧调用。
- 最近对话默认 5 条，继续下拉按 cursor 分页；SQL store 按 `created_at desc, conversation_id desc` 排序。
- 前端切换对话后，异步回调必须写回原来的 `conversation_id`，不能写到当前可见对话。
- 进入历史对话时应恢复 `context`，允许从原先的表单、plan、场景包、额度不足暂停点继续。
- 进入历史对话时如果发现 `pendingMessageJob` / `pending_message_job`，应先恢复并轮询已有消息保存 job；完成后按 job 中的 continuation 启动或恢复采集 job。
- 进入历史对话时如果发现 `pendingIntakeJob` / `pending_intake_job`，应恢复并轮询已有采集意图识别 job；job 404 或过期只提示用户重新发送需求，不自动重启任务。
- 进入历史对话时如果发现 `pendingScenePackageJob` / `pending_scene_package_job`，应静默轮询已有场景包/参考图 job，不重复追加“已恢复上次场景包生成任务”这类进度消息；如果用户再次切走该对话，前端停止轮询但保留 pending job，等用户回来再查询已有 job。完成后补齐场景包结果卡，404 或过期只提示手动重试，不自动重启任务。
- 进入历史对话时如果发现 `pendingVideoJob` / `pending_video_job`，应恢复并轮询已有视频 job；如果 job 404 或过期，只提示用户手动重新生成，不自动重启任务。
- 进入历史对话时如果发现 `pendingImageJob` / `pending_image_job`，应静默轮询已有图片生成或全局素材编辑 job；如果 job 404 或过期，只提示用户从最新图片卡片手动重试，不自动重新启动，避免重复计费。
- 当前对话中任意阶段正在处理时，所有历史消息里的操作按钮都必须禁用；处理完成后只允许最新可操作 artifact 的按钮继续，旧 artifact 只能作为历史预览。失败或额度暂停时，最新可恢复 artifact 的重试按钮保留可用。

## 鉴权与安全

- PixelFlow 不维护自己的登录、注册、cookie session。
- 所有非公开接口使用 content-app `Authorization: Bearer <token>`。
- `AuthMiddleware` / `content_app_auth.py` 只读取 JWT payload 的 `sub` 作为用户名，再调用 content-app `/api/auth/verify` 实时校验。
- 禁用用户必须立即无法访问任务列表、对话、SSE 和生成接口。
- 需要用户身份时只使用 content-app username。
- 前端本地调试可用 `/auth-token` 写入 `localStorage.Authorization`；正式集成优先由 content-app 宿主注入 `window.__CONTENT_APP_AUTHORIZATION__`。
- 前端展示“Agent 正在做什么”时，只能展示业务摘要，不要展示原始 prompt、思维链、token、供应商密钥、完整异常堆栈或本地部署目录。

## 分层边界

| 要做的事 | 应放位置 |
| --- | --- |
| HTTP 入参、出参、状态码 | `backend/app/gateway/routers/` |
| 鉴权、配置、运行时初始化 | `backend/app/gateway/` |
| 采集表单、意图、行业画像 | `backend/pixelflow/intake/` |
| plan.md 和 Brief 纯逻辑 | `backend/pixelflow/creative/` |
| 图片/视频生成准备逻辑 | `backend/pixelflow/generate/` |
| PowerMem 语义记忆 Client 和现有记忆上下文整理 | `backend/pixelflow/memory/` |
| 新 Agent Runtime 的模型预算、结构化摘要与全局上下文压缩 | `backend/pixelflow/agent_runtime/context/` |
| 第三方 API、上传、轮询、错误归一 | `backend/pixelflow/skills/` |
| 任务、会话、资产持久化 | `backend/pixelflow/tasks/` |
| 用户偏好 | `backend/pixelflow/preferences/` |
| PowerMem 运行时 helper | `backend/app/gateway/pixelflow_memory.py` |
| 前端 API 类型和请求 | `web/src/lib/api.ts`、`web/src/lib/types.ts` |
| 前端主流程编排 | `web/src/pages/WorkspacePage.tsx` |
| 前端分镜和 @ 素材编辑 | `web/src/components/canvas/StoryboardPanel.tsx`、`SceneMentionEditor.tsx` |

Harness 边界：

- `app.*` 可以 import `deerflow.*`。
- `deerflow.*` 不允许 import `app.*`。
- 由 `backend/tests/test_harness_boundary.py` 保护。

## 常用命令

后端：

```bash
cd backend
uv sync
make dev
PIXELFLOW_CONFIG_ENV=prod make gateway
make test
make lint
```

后端针对性验证：

```bash
cd backend
uv run pytest tests/test_intake_llm.py tests/test_intake_forms.py tests/test_industry_profile.py -q
uv run pytest tests/test_creative_plan_markdown.py tests/test_image_prepare.py -q
uv run pytest tests/test_video_scene_packages.py tests/test_pixelflow_video_router.py -q
uv run pytest tests/test_borgrise_poll.py tests/test_borgrise_authorization_passthrough.py -q
uv run ruff check .
```

前端：

```bash
cd web
corepack enable
corepack pnpm install
corepack pnpm dev
corepack pnpm dev:test -- --host 0.0.0.0 --port 5273
corepack pnpm lint
corepack pnpm build
```

`corepack pnpm dev` 读取 `web/.env.development`，`/agent` 代理测试环境；`corepack pnpm dev:test` 读取 `web/.env.test`，`/agent` 代理本地 `http://127.0.0.1:8001`。本地完整联调推荐流程是先启动 `backend` 的 `make dev`，再启动 PixelFlow `dev:test`，最后在同级 `content_frontend` 里启动 `yarn test -- --host 0.0.0.0 --port 5174`，由 content_frontend test 环境嵌入 `http://localhost:5273/agentfrontend/`。

如果本机没有 corepack/pnpm，可以临时用 `npm install && npm run build`。不要直接运行裸 `tsc`，本地没有全局 `tsc` 时会报 `command not found`；应使用 `corepack pnpm build`、`pnpm build` 或 `npm run build` 这类包管理器脚本。

前端针对性验证：

```bash
cd web
corepack pnpm test:scene-packages
corepack pnpm test:scene-mentions
corepack pnpm test:conversation-routing
corepack pnpm build
```

## 修改场景指南

### 修改采集/创意方向

读：

- `backend/app/gateway/routers/pixelflow_intake.py`
- `backend/pixelflow/intake/llm.py`
- `backend/pixelflow/intake/forms.py`
- `backend/pixelflow/intake/context.py`
- `backend/pixelflow/intake/industry_profile.py`

重点检查：

- 自然语言“分析这个视频/拆解视频/视频拆解”等 fallback 是否覆盖。
- 产品主体是否丢失，例如“书包宣传图”不能变成“宣传”。
- 图片数量是否进入 `requested_output_count`。
- 未命中垂类模板时是否走 LLM 通用行业画像。

### 修改 plan.md

读：

- `backend/app/gateway/routers/pixelflow_planning.py`
- `backend/pixelflow/creative/plan_markdown.py`
- `backend/pixelflow/creative/plan_llm.py`
- `backend/pixelflow/creative/contract.py`
- `backend/pixelflow/creative/duration.py`
- `backend/skills/public/borgrise-creative-assistant-v2/templates/plan_video.md`
- `backend/skills/public/borgrise-creative-assistant-v2/templates/plan_image.md`

改模板时要同步检查图片、视频生成准备逻辑是否仍能解析关键信息，并保证 Plan 版本历史和 `creation_contract` 不丢失。视频 Plan 修改还要检查场景资产图片模型能力、每镜 4-15 秒和精确总时长。

### 修改图片生成

读：

- `backend/app/gateway/routers/pixelflow_image.py`
- `backend/pixelflow/generate/image_prepare.py`
- `backend/pixelflow/skills/base.py`
- `backend/pixelflow/skills/borgrise/skill.py`
- `backend/pixelflow/skills/borgrise/run_generation.py`
- `web/src/pages/WorkspacePage.tsx`

重点检查：

- 四个图片接口的参数是否与 content-app 当前 Controller 一致。
- 图片编辑必须带原图 URL。
- 参考图生成、多图融合必须从 `materials` 或结果 artifact 中拿到图片 URL。
- 多张生成时前端展示数量和后端循环数量一致。
- 图片生成和全局素材图片编辑必须经 `/start` + `/jobs/{job_id}` 的可恢复 job 流程，前端写入 `pendingImageJob` / `pending_image_job` 后再轮询，不能重新回到同步等待 `/generate` 或 `/edit-asset`。

### 修改视频场景包/生成

读：

- `backend/app/gateway/routers/pixelflow_video.py`
- `backend/pixelflow/generate/scene_packages.py`
- `web/src/lib/scenePackages.ts`
- `web/src/lib/sceneMentions.ts`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/src/components/canvas/SceneMentionEditor.tsx`

重点检查：

- `characters` 只能人物三视图。
- 产品和道具只能进入 `props`。
- `shot_description.text` 保持一段文本。
- @ 选择的 mentions 要包含 `image_url`，后端生成场景视频时用作参考图。
- 每段最多 9 张参考图。
- 场景视频失败时 artifact 要保留 `failed_scenes`，方便用户查看失败原因和重试。
- 全局素材图片编辑后要同步替换 `global_assets` 和 mentions，避免后续场景视频仍引用旧图。

### 修改 PPT 流程

读：

- `backend/app/gateway/routers/pixelflow_ppt.py`
- `backend/pixelflow/intake/forms.py`
- `backend/pixelflow/intake/industry_profile.py`
- `backend/pixelflow/skills/base.py`
- `backend/pixelflow/skills/borgrise/run_generation.py`
- `backend/pixelflow/skills/borgrise/skill.py`
- `web/src/pages/WorkspacePage.tsx`
- `web/src/components/composer/GenParamsDialog.tsx`
- `web/src/components/chat/MessageBubble.tsx`

重点检查：

- PPT intent 是否由 LLM 和 fallback 都能识别。
- PPT 主题不能丢失，未知行业必须先调用 LLM 生成行业画像。
- 附件必须只允许 `.doc`、`.docx`、`.xls`、`.xlsx`、`.pdf`。
- 每个 SmartPPT 接口都是异步任务，前端必须轮询 Python job，不能让浏览器请求卡 2 小时。
- 页面图片生成要逐页更新卡片，失败页要展示错误并允许单页重新生成。
- 新增或调整 SmartPPT content-app 接口调用时同步修改 `CONTENT_APP_API_CALLS.md`。

### 修改对话历史/串会话问题

读：

- `backend/app/gateway/routers/pixelflow_conversations.py`
- `backend/pixelflow/tasks/store.py`
- `web/src/pages/WorkspacePage.tsx`
- `web/src/lib/conversationRouting.ts`

重点检查：

- 异步回调必须带原 `conversation_id`。
- 图片生成 pending job 必须保存在 conversation context，恢复历史对话后只能轮询已有 `job_id`，不能重复启动 `/image/generate/start` 或 `/image/edit-asset/start`。
- 视频场景包/参考图 pending job 必须保存在 conversation context，恢复历史对话后只能轮询已有 `job_id`，不能重复启动 `/prepare-scene-packages/start` 或 `/generate-scene-assets/start`。
- 视频场景生成 pending job 必须保存在 conversation context，恢复历史对话后只能轮询已有 `job_id`，不能重复启动 `/generate-scenes/start`。
- 新建对话不能继承旧对话消息或 context。
- 历史消息顺序按 `created_at asc, message_id asc` 展示。
- 最近对话列表按创建时间倒序分页。

## 文档同步规则

只要改了 Agent 流程、Skill 边界、前端确认/重试逻辑、content-app 接口合同、核心配置或对话恢复逻辑，就必须同步更新：

1. `docs/pixelflow-agent-skill-flow-latest-design.md`
2. `README.md`
3. 本 `AGENTS.md` 中受影响的入口、命令或约束

凡是项目里新增、删除或调整任何博观/content-app 接口调用，包括接口路径、请求参数、响应字段、轮询方式、鉴权方式、额度不足处理或错误处理，都必须同步更新 `/Users/wu-bob/Documents/study/IDEA/MySpaces/cmyqCode/pixelflow/CONTENT_APP_API_CALLS.md`，把调用方、接口路径、用途、关键参数和注意事项记录清楚。

## 已知注意点

- `AGENTS.md` 目前是本地协作手册，`.gitignore` 默认忽略；如果需要提交给远端，先和用户确认是否解除忽略。
- `docs/` 目录默认只跟踪 `pixelflow-agent-skill-flow-latest-design.md`，避免把临时设计草稿全提交。
- 旧 LangGraph `backend/pixelflow/graph.py` 注释仍有历史痕迹，当前前端主流程以 v2 分段 API 为准。
- `run_generation.py` 是历史 CLI + 当前 Borgrise Client 的混合文件，改动前要跑 ruff 和相关 Borgrise 单测。
- `web/` 没有全局 `pnpm` 时使用 `npm install` / `npm run build`。
