# AGENTS.md

本文件是后续 agent 进入 PixelFlow 仓库时必须先读的工作说明。用户主要是 Java 后端开发，对 Python、React 和 Agent 编排不熟；解释实现时请尽量用 Controller / Service / Repository / Client / DTO / Filter / 工作流编排类比说明。

## 项目定位

PixelFlow 是面向电商内容创作的图片、视频、视频分析 Agent 工作台。当前源码里同时存在两条能力线：

| 能力线 | 主要入口 | Java 类比 | 当前用途 |
| --- | --- | --- | --- |
| v2 分段工作流 | `backend/app/gateway/routers/pixelflow_intake.py`、`pixelflow_planning.py`、`pixelflow_image.py`、`pixelflow_video.py` | 一组面向前端步骤的 Controller + Service | 当前前端工作台主流程 |
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
8. `backend/app/gateway/routers/pixelflow_conversations.py`
9. `backend/pixelflow/intake/llm.py`
10. `backend/pixelflow/intake/forms.py`
11. `backend/pixelflow/intake/industry_profile.py`
12. `backend/pixelflow/creative/plan_markdown.py`
13. `backend/pixelflow/generate/image_prepare.py`
14. `backend/pixelflow/generate/scene_packages.py`
15. `backend/pixelflow/skills/base.py`
16. `backend/pixelflow/skills/borgrise/skill.py`
17. `backend/pixelflow/skills/borgrise/run_generation.py`
18. `web/src/pages/WorkspacePage.tsx`
19. `web/src/lib/api.ts`
20. `web/src/components/canvas/StoryboardPanel.tsx`
21. `web/src/components/canvas/SceneMentionEditor.tsx`

模板和垂类资料在：

| 文件 | 用途 |
| --- | --- |
| `CONTENT_APP_API_CALLS.md` | PixelFlow 调用 content-app/Borgrise 接口的清单和合同记录 |
| `backend/skills/public/borgrise-creative-assistant-v2/templates/plan.md` | 策划 Agent 填充 plan.md 的模板 |
| `backend/skills/public/borgrise-creative-assistant-v2/templates/industry_profile.md` | 垂类 Skill 的预制行业画像 |
| `backend/skills/public/borgrise-creative-assistant-v2/references/` | Borgrise/content-app 能力调用说明 |

## 当前主流程

前端工作台主流程不是一次性自由聊天，而是阶段化编排：

```text
用户输入 + 附件
  -> 采集 Agent 识别 intent
  -> 表单补全与垂类画像
  -> 生成 3 个创意方向
  -> 策划 Agent 填充 plan.md
  -> 人工审核 plan.md
  -> 图片生成 / 视频生成 / 视频分析
  -> 用户确认、修改、重试或结束
```

三类 intent：

| intent | 判定入口 | 后续流程 |
| --- | --- | --- |
| `image` | 图片生成、图片编辑、参考图生成、融合等需求 | 表单 -> 创意方向 -> plan.md -> 图片参数准备 -> Borgrise 图片接口 |
| `video` | 文生视频、图生视频、参考生成视频、编辑视频、延伸视频等需求 | 表单 -> 创意方向 -> plan.md -> 视频场景包 -> 场景资产图 -> 场景视频 -> 合并 -> 修改循环 |
| `video_analysis` | 分析视频、拆解视频、视频拆解等需求 | 媒体链接识别 -> 单视频/多视频 storyboard 拆解 |

默认人工确认规则：

- 创意方向选择：前端 30 秒未选时默认采用推荐方向。
- plan.md 审核：后端返回 `review_timeout_sec=30`，前端 30 秒未操作默认同意。
- 图片生成结果、视频生成结果：30 秒未反馈默认满意或无意见。
- 视频场景包确认：当前代码返回 `review_timeout_sec=None`，不做倒计时自动确认。

## 核心 API

所有 Python 网关对前端或第三方暴露的新接口必须以 `/agent` 开头。当前前端 API client 里 `AGENT_API_PREFIX = "/agent"`，所以 `api.ts` 里的 `FLOW_BASE="/flows"` 最终会拼成 `/agent/flows/...`。

| 模块 | 路径 | 说明 |
| --- | --- | --- |
| 采集 | `POST /agent/flows/intake/analyze` | LLM 识别 intent、主体、行业、目标、数量 |
| 采集 | `GET /agent/flows/intake/forms/{intent}` | 获取图片或视频表单 schema |
| 采集 | `POST /agent/flows/intake/validate` | 表单完整性校验，最多 3 轮 |
| 采集 | `POST /agent/flows/intake/directions` | 生成 3 个创意方向 |
| 策划 | `POST /agent/flows/planning/plan` | 根据模板填充 plan.md |
| 图片 | `POST /agent/flows/image/prepare` | 判断图片接口并生成参数 |
| 图片 | `POST /agent/flows/image/generate` | 调用图片 skill 生成，支持多张循环生成 |
| 图片 | `POST /agent/flows/image/edit-asset` | 编辑视频场景包全局素材图片，复用图片编辑 skill |
| 视频 | `POST /agent/flows/video/analyze-storyboards` | 视频分析，自动单个/批量拆解 |
| 视频 | `POST /agent/flows/video/prepare-scene-packages` | 生成可编辑视频场景包 |
| 视频 | `POST /agent/flows/video/generate-scene-assets` | 生成角色三视图、场景图、道具图 |
| 视频 | `POST /agent/flows/video/generate-scenes/start` | 启动场景视频生成异步任务 |
| 视频 | `GET /agent/flows/video/generate-scenes/jobs/{job_id}` | 轮询场景视频结果 |
| 视频 | `POST /agent/flows/video/generate-direct/start` | 启动直接视频生成异步任务 |
| 视频 | `GET /agent/flows/video/generate-direct/jobs/{job_id}` | 轮询直接视频生成结果 |
| 视频 | `POST /agent/flows/video/merge` | 按场景顺序合并视频 |
| 视频 | `POST /agent/flows/video/analyze-flaws` | 视频穿帮分析 |
| 对话 | `POST /agent/conversations` | 新建独立对话 |
| 对话 | `GET /agent/conversations?page_size=5` | 最近对话列表，按创建时间倒序分页 |
| 对话 | `GET /agent/conversations/{conversation_id}` | 进入历史对话并恢复消息 |
| 对话 | `POST /agent/conversations/{conversation_id}/messages` | 保存用户/助手消息 |
| 偏好 | `GET/PUT /agent/users/{user_id}/preferences` | 用户偏好 |
| 旧任务流 | `/agent/flows`、`/agent/flows/{task_id}/events` 等 | LangGraph 任务、SSE、资产查询 |

附件上传是例外：前端文件上传直接调用 content-app 的 `/api/upload`，不是 Python `/agent` 接口。上传结果作为 `materials` 随用户输入传给 Agent。

## Agent 与 Skill

这里的 Skill 可以理解成 Java 里的第三方 Client / 策略 Service / 纯逻辑能力接口。主流程应只依赖稳定 DTO，不直接把供应商细节写进前端或 Controller。

| Agent | 主要文件 | 调用的 Skill / Service | 职责 |
| --- | --- | --- | --- |
| 采集 Agent | `pixelflow_intake.py`、`intake/llm.py`、`intake/forms.py`、`intake/industry_profile.py` | IntentRecognitionSkill、FormValidationSkill、IndustryProfileSkill、CreativeDirectionSkill | 识别图片/视频/视频分析，补全表单，生成创意方向 |
| 策划 Agent | `pixelflow_planning.py`、`creative/plan_markdown.py` | PlanTemplateFillSkill、PlanConsistencyCheckSkill | 使用项目内模板生成 plan.md |
| 人工审核 Agent | `WorkspacePage.tsx` | 前端状态与对话存储 | plan.md、图片结果、视频结果的确认/修改循环 |
| 图片生成 Agent | `pixelflow_image.py`、`generate/image_prepare.py` | ImageEndpointDecisionSkill、ImagePromptBuildSkill、ImageGenerationSkill | 选择文生图/图片编辑/参考图/多图融合，支持多图生成 |
| 视频生成 Agent | `pixelflow_video.py`、`generate/scene_packages.py` | ScenePackageSkill、SceneAssetImageSkill、SceneVideoGenerationSkill、VideoMergeSkill、VideoFlawAnalysisSkill | 生成场景包、资产图、场景视频、合并、穿帮分析和修改循环 |
| 视频分析 Agent | `pixelflow_video.py` | MediaLinkExtractionSkill、VideoDecomposeSkill | 抽取媒体链接，按单个或多个视频调用 storyboard 拆解 |
| 对话持久化 | `pixelflow_conversations.py`、`tasks/store.py` | PixelFlowTaskStore | 保存对话、消息、上下文，避免切换对话串流程 |

## Borgrise/content-app 能力

`backend/pixelflow/skills/base.py` 定义 Protocol，`backend/pixelflow/skills/borgrise/skill.py` 是实现层，阻塞 HTTP 和轮询集中在 `run_generation.py`。

图片接口：

| Skill 方法 | content-app/Borgrise 接口 |
| --- | --- |
| `text_to_image` | `/api/picture/text_to_image` |
| `reference_image` | `/api/picture/multi_reference_image_generation` |
| `image_edit` | `/api/picture/image_edit` |
| `multi_image_fusion` | `/api/picture/multi_image_fusion` |

视频接口：

| Skill 方法 | content-app/Borgrise 接口 |
| --- | --- |
| `text_to_video` | `/api/video/text-to-video` |
| `image_to_video` | `/api/video/image-to-video` |
| `two_image_to_video` | `/api/video/two-image-to-video` |
| `reference_mode_video` | `/api/video/reference-mode-video` |
| `edit_video` | `/api/video/edit-video` |
| `extend_video` | `/api/video/extend-video` |
| `merge_videos` | `/api/video/merge` |

视频理解接口：

| Skill 方法 | content-app/Borgrise 接口 |
| --- | --- |
| `extract_media_links` | `/api/creative/extractMediaLinks` |
| `decompose_video_to_storyboard` | `/api/creative/decompose_video_to_storyboard` |
| `batch_decompose_video_to_storyboard` | `/api/creative/batch_decompose_video_to_storyboard` |
| `analyze_video_flaws` | `/api/creative/analyze_video_flaws` |

调用约束：

- 必须透传入口请求的 content-app `Authorization`。
- 不要把用户 token、账号、密码写入配置或代码。
- content-app 返回 HTTP 402 或“额度不足/余额不足/没有有效额度/充值”等文案时，必须暂停当前流程并返回可恢复提示。
- 图片轮询超时按配置默认 10 分钟，视频轮询默认 1 小时，视频分析默认 15 分钟。
- 第三方异常可按 `borgrise.max_retries` 重试；业务失败不要无意义重试。

## 图片流程要点

图片采集表单在 `intake/forms.py`：

- `image_goal` 必须保留真实目标，如“书包宣传图”，不能退化成“宣传”。
- `image_type`、`image_usage`、`image_style`、`image_size` 一起进入创意方向和 plan.md。
- 前端图片尺寸只展示 `1:1`、`16:9`、`9:16`、`自动适配`。
- `自动适配` 会由 `image_prepare.py` 根据用途和目标映射到供应商支持比例。
- 用户明确要求多张图片时，`requested_output_count` 会进入 `intake_context`，最终 `image/generate` 按数量循环调用，默认 1 张，最多 10 张。
- 有附件时，附件 URL 会进入 `materials`，图片编辑/参考图/多图融合会根据素材数量和用户语义选择接口。

## 视频流程要点

视频主流程仍是：plan.md -> 多个视频场景片段 -> 每段生成视频 -> 按顺序合并。

场景包结构：

- 每个片段最少 4 秒，最多 15 秒。
- 全局固定资产：`characters`、`scenes`、`props`、`visual_style`。
- `characters` 只能是人物角色，每个角色必须生成同一人物的正面、侧面、背面三视图。
- 产品、商品、包装、工具、卖点物件必须进入 `props`，不能放进 `characters`。
- 逐片段变化字段：`storyline`、`shot_description`、`narration`。
- `shot_description.text` 是一整段文本，不能拆成多个表单字段；文本里可以使用 `@asset_id` 关联角色、场景、道具。
- `shot_description.mentions` 保存 @ 选择对应的图片 URL，生成视频时这些 URL 会作为参考图集合。
- 每个视频场景片段最多 9 张参考图，前端和后端都要限制。
- 前端 `SceneMentionEditor` 是 `contentEditable`，用户输入 `@` 后弹出素材下拉，素材 chip 可预览。
- 全局素材图片可在 `StoryboardPanel` 点击预览并“引用素材”到左侧输入框；用户发送编辑指令后，`WorkspacePage` 识别 `materials.source="scene_global_asset"`，调用 `/agent/flows/image/edit-asset` 走原图片编辑 skill。编辑成功后直接替换 `global_assets` 中原图：角色替换 `three_view_images[0]`，场景/道具替换 `images[0]`，并同步同 `asset_id` 的 `shot_description.mentions[].image_url`。全局素材编辑结果卡片的“重新生成”仍由 `WorkspacePage` 保持 `scene_global_asset` 上下文，下一条输入继续调用 `edit-asset`，不能掉回普通采集 Agent。
- 对话中只有最后一个 `video_scene_packages` 卡片能展示“查看分镜 / 确认并生成视频 / 重新生成参考图”等操作；旧场景包卡片只能作为历史预览，防止用户基于过期素材继续生成。

场景视频接口选择：

- 如果片段显式给了 `generation_mode`，以后端传入为准。
- 否则 `pixelflow_video.py` 根据图片、视频、音频素材和提示词选择 `text_to_video`、`image_to_video`、`two_image_to_video`、`reference_mode_video`、`edit_video` 或 `extend_video`。
- 场景视频生成使用异步 job，前端轮询 job 状态，避免网关长时间阻塞。

## 对话隔离与恢复

对话是用户可见工作台的主上下文：

- 新建对话必须新建 `conversation_id`，不能复用旧对话。
- 用户消息、Agent 消息、artifact、当前上下文都要保存到 `pixelflow_conversations` / `pixelflow_conversation_messages`。
- 最近对话默认 5 条，继续下拉按 cursor 分页；SQL store 按 `created_at desc, conversation_id desc` 排序。
- 前端切换对话后，异步回调必须写回原来的 `conversation_id`，不能写到当前可见对话。
- 进入历史对话时应恢复 `context`，允许从原先的表单、plan、场景包、额度不足暂停点继续。

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
| 第三方 API、上传、轮询、错误归一 | `backend/pixelflow/skills/` |
| 任务、会话、资产持久化 | `backend/pixelflow/tasks/` |
| 用户偏好 | `backend/pixelflow/preferences/` |
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
corepack pnpm lint
corepack pnpm build
```

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
- `backend/skills/public/borgrise-creative-assistant-v2/templates/plan.md`

改模板时要同步检查图片、视频生成准备逻辑是否仍能解析关键信息。

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

### 修改对话历史/串会话问题

读：

- `backend/app/gateway/routers/pixelflow_conversations.py`
- `backend/pixelflow/tasks/store.py`
- `web/src/pages/WorkspacePage.tsx`
- `web/src/lib/conversationRouting.ts`

重点检查：

- 异步回调必须带原 `conversation_id`。
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
