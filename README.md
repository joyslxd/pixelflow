# PixelFlow

> 项目状态：开发中（Work in Progress）。
>
> PixelFlow 当前已经具备「后端任务 API + LangGraph 阶段化流水线 + Borgrise 视频生成/参考拆解 + 剪映/FFmpeg 剪辑边界 + React 对话/Canvas 工作台」主链路，但仍不是生产可交付系统。前端部分能力仍是占位或半接通，接口和数据结构可能继续调整。

PixelFlow 是一个电商带货短视频生成 AI Agent 平台：输入商品信息、商品图、创意诉求和可选参考视频，系统按「采集 -> 策划 -> Brief 人工确认 -> 生成 -> 剪辑 -> 质检」的阶段化流水线推进，最终产出生成片段、剪映草稿或 FFmpeg 成片。

后端基于从 [DeerFlow](https://github.com/bytedance/deer-flow) 提取的 harness：FastAPI 网关、LangGraph runtime/checkpointer、模型工厂、工具、skills、memory、sandbox、MCP、run/thread 管理等基础设施都在 harness 中；PixelFlow 自己的业务逻辑集中在 `backend/pixelflow/`。版权说明见 [NOTICE](NOTICE)。

## 适合谁读

如果你是 Java 后端开发，可以这样类比：

| Java / Spring 概念 | PixelFlow 对应概念 | 主要位置 |
| --- | --- | --- |
| Controller | FastAPI Router | `backend/app/gateway/routers/*.py` |
| Service / 流程节点 | LangGraph node | `backend/pixelflow/nodes.py` |
| 工作流引擎 / 状态机 | LangGraph `StateGraph` | `backend/pixelflow/graph.py` |
| DTO / VO | Pydantic `BaseModel`、`TypedDict`、dataclass | `backend/pixelflow/**/models.py` |
| Repository / DAO | Store Protocol + Memory/SQL/MySQL 实现 | `backend/pixelflow/tasks/`、`backend/pixelflow/preferences/` |
| 第三方 Client | Skill Protocol + 实现 | `backend/pixelflow/skills/` |
| Filter / Interceptor | FastAPI Middleware / LangGraph Middleware | `backend/app/gateway/*middleware.py`、`backend/packages/harness/deerflow/agents/middlewares/` |
| 前端 API client | TypeScript fetch 封装 | `web/src/lib/api.ts` |

更详细的代码导读见 `docs/pixelflow-project-overview.md`（该目录在当前仓库中作为内部文档被 `.gitignore` 忽略）。后续 agent 工作约定见 `AGENTS.md`。

## 架构概览

```text
pixelflow/
├── backend/
│   ├── app/gateway/                 # FastAPI 网关：认证、CSRF、API、runtime 初始化
│   ├── pixelflow/                   # PixelFlow 业务核心
│   ├── packages/harness/deerflow/    # DeerFlow harness：Agent 基础设施
│   ├── skills/public/               # 已提交的 agent skills，含 Borgrise skill v2
│   ├── tests/                       # 后端测试
│   ├── langgraph.json               # LangGraph 图注册
│   ├── pyproject.toml               # Python 依赖与 uv workspace
│   ├── Makefile                     # 后端命令
│   └── .env.example                 # 业务环境变量示例
├── web/
│   ├── src/                         # React + Vite + TypeScript 前端
│   ├── package.json
│   └── vite.config.ts
├── AGENTS.md                        # 后续 agent 工作手册
├── LICENSE
├── NOTICE
└── README.md
```

运行时主链路：

```mermaid
flowchart LR
  User["浏览器用户"] --> Web["React 工作台 web/"]
  Web -->|POST /api/tasks| Gateway["FastAPI Gateway"]
  Web -->|GET /api/tasks/{id}/events SSE| Gateway
  Gateway --> Runtime["DeerFlow Runtime: RunManager / StreamBridge / Checkpointer"]
  Runtime --> Graph["PixelFlow LangGraph"]
  Graph --> Intake["intake 采集"]
  Graph --> Creative["creative 策划"]
  Graph --> Review["brief_review 人工确认"]
  Graph --> Generate["generate 生成"]
  Graph --> Edit["edit 剪辑"]
  Graph --> QC["qc 质检"]
  Generate --> Borgrise["Borgrise 视频生成"]
  Intake --> Decompose["Borgrise 参考视频拆解"]
  Edit --> Renderer["JianYing / FFmpeg"]
  Gateway --> Store["TaskStore / PreferenceStore"]
  Store --> DB["Memory / SQL / MySQL"]
```

## 核心设计原则

- 创意交给 LLM，机械逻辑用纯函数：完整性检查、Brief 校验、分段规划、Timeline、DraftPlan、QC 等都可以离线测试。
- 业务编排在 LangGraph node 中，第三方 API、下载、FFmpeg、剪映草稿等 I/O 统一收敛到 `skills/` 边界。
- PixelFlow 业务 API 用 `/api/tasks` 包装底层 LangGraph run/thread，让前端按“任务、Brief、资产、进度事件”的业务概念工作。
- 外部能力失败时尽量归一化成 `ok=false` 和错误信息，避免单个 vendor 异常直接打断整条流程。
- 支持用户隔离：认证用户会写入 request state 和 run context，任务、偏好、thread/run 数据按用户过滤。

## PixelFlow 业务包

`backend/pixelflow/` 是最重要的目录：

```text
backend/pixelflow/
├── state.py              # TaskState：贯穿全图的流程上下文
├── graph.py              # LangGraph StateGraph 组装与条件流转
├── nodes.py              # intake / creative / review / generate / edit / qc 阶段处理器
├── intake/               # 商品信息抽取、视频参数归一、需求完整性检查、参考视频摘要
├── creative/             # Brief 结构、LLM 生成、硬约束校验与修复
├── generate/             # Seedance prompt 与 segment 分段规划
├── edit/                 # Timeline IR、DraftPlan、FFmpeg 参数规划
├── qc/                   # 片段完整性与时长质检
├── skills/               # Borgrise / JianYing / FFmpeg 能力边界
├── tasks/                # PixelFlow 任务、事件、资产持久化
└── preferences/          # P0 结构化用户偏好
```

### 流水线

```text
intake -> creative -> brief_review -> generate -> edit -> qc -> done
            ^              |                                |
            |              | approved=false                 |
            +--------------+                                |
                                                            |
                 qc failed and attempts < MAX_QC_ATTEMPTS --+
```

关键常量：

| 常量 | 当前值 | 含义 |
| --- | --- | --- |
| `MAX_INTAKE_ROUNDS` | `3` | 需求不完整时最多补充/追问 3 轮 |
| `MAX_QC_ATTEMPTS` | `2` | QC 不通过时最多回到生成阶段 2 次 |
| `SEEDANCE_MIN_DURATION` | `4` | 单次生成最小时长 |
| `SEEDANCE_MAX_DURATION` | `10` | 当前 seedance-2.0 单次生成上限，超过后按 segment 并行生成 |

阶段说明：

| 阶段 | 主要文件 | 作用 |
| --- | --- | --- |
| `intake` | `pixelflow/nodes.py`、`pixelflow/intake/*` | 抽取商品信息、拆解参考视频、归一化参数、检查需求完整性 |
| `creative` | `pixelflow/creative/*` | 调 LLM 生成结构化 Brief，并用纯逻辑修复硬约束 |
| `brief_review` | `pixelflow/nodes.py` | 使用 LangGraph `interrupt()` 暂停，等待用户确认或拒绝 |
| `generate` | `pixelflow/generate/*`、`pixelflow/skills/borgrise/*` | 按 segment 生成视频片段，多个 segment 并行 |
| `edit` | `pixelflow/edit/*`、`pixelflow/skills/jianying/*`、`pixelflow/skills/ffmpeg/*` | 组装 Timeline，并输出剪映草稿或 mp4 |
| `qc` | `pixelflow/qc/*` | 检查片段完整性和成片时长，必要时回到生成 |

## 后端网关

`backend/app/gateway/` 是 HTTP 和运行时入口。

主要职责：

- 启动 FastAPI app。
- 初始化 DeerFlow runtime：StreamBridge、RunManager、checkpointer、store、run event store。
- 初始化 PixelFlow task/preference store。
- 挂载通用 DeerFlow API 与 PixelFlow 业务 API。
- 处理 JWT cookie 鉴权、CSRF、CORS。

关键文件：

| 文件 | 作用 |
| --- | --- |
| `app.py` | FastAPI app 创建、lifespan、router 挂载、PixelFlow store 初始化 |
| `deps.py` | 从 `app.state` 获取 runtime 依赖，构造 `RunContext` |
| `services.py` | run 创建、输入规范化、SSE 格式化、run config 构造 |
| `auth_middleware.py` | 非公开路径鉴权 |
| `csrf_middleware.py` | Double Submit Cookie CSRF 防护 |
| `routers/pixelflow_tasks.py` | PixelFlow 任务 API |
| `routers/pixelflow_preferences.py` | PixelFlow 用户偏好 API |

## 前端工作台

`web/` 是 React + Vite + TypeScript 工作台，当前已接入真实 `/api/tasks` 主链路。

```text
web/src/
├── main.tsx                    # Router + QueryClient + AuthGate
├── pages/WorkspacePage.tsx      # 主工作台：聊天、任务创建、SSE、Brief、结果
├── lib/api.ts                   # /api/tasks 与认证接口封装
├── lib/types.ts                 # 后端对齐类型
├── lib/chat.ts                  # 前端消息与 Canvas 状态类型
├── components/auth/             # 登录门禁
├── components/layout/           # 侧边栏和页面布局
├── components/chat/             # 聊天面板
├── components/composer/         # 输入框和生成参数弹窗
└── components/canvas/           # Brief 卡片和视频结果展示
```

前端主流程：

1. 用户在聊天框输入视频诉求。
2. `WorkspacePage` 用关键词判断是否是视频生成意图。
3. 打开 `GenParamsDialog`，收集商品名、商品图 URL、核心诉求、平台、比例、时长等。
4. 调 `api.createTask()` -> `POST /api/tasks`。
5. 使用 `EventSource` 订阅 `/api/tasks/{task_id}/events`。
6. 收到 `brief_ready` 后在右侧 Canvas 展示 Brief。
7. 用户确认后调 `POST /api/tasks/{task_id}/brief/confirm`。
8. 收到完成事件后调 `/api/tasks/{task_id}/assets` 展示结果。

当前前端仍有未完成项：

- `Sidebar` 的最近对话仍是硬编码假数据。
- `/c/:taskId` 路由尚未按 URL taskId 恢复历史任务。
- 参数弹窗里的 `count`、`sound` 暂未进入后端业务链路。
- 后端支持 `reference_videos`，但前端参数弹窗尚未提供参考视频输入。
- FFmpeg 产出的 `final_video_url` 可能是本地路径，浏览器直接播放需要额外 artifact/静态服务或上传 URL。

## 业务 API

### 任务 API

前端主链路优先使用这一组接口。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/tasks` | 创建 PixelFlow 任务，可 `auto_start` 自动启动 |
| `GET` | `/api/tasks` | 查询当前用户任务列表 |
| `GET` | `/api/tasks/{task_id}` | 查询任务详情，并从 checkpoint 同步最新状态 |
| `GET` | `/api/tasks/{task_id}/result` | 查询任务结果 |
| `GET` | `/api/tasks/{task_id}/assets` | 查询生成资产 |
| `POST` | `/api/tasks/{task_id}/brief/confirm` | 确认或拒绝 Brief，并恢复 LangGraph |
| `POST` | `/api/tasks/{task_id}/brief/revise` | 修改 Brief，写入反馈和偏好 |
| `GET` | `/api/tasks/{task_id}/events` | SSE 订阅任务事件 |
| `GET` | `/api/tasks/{task_id}/events/history` | 查询历史事件 |

创建任务示例：

```json
{
  "task_type": "ecom_video",
  "product_url": "https://example.com/item/1",
  "product_info": {
    "product_name": "极简不锈钢保温杯 500ml",
    "main_image_url": "https://example.com/product.jpg"
  },
  "video_params": {
    "platform": "douyin",
    "duration_sec": 15,
    "ratio": "9:16",
    "size": "1080x1920",
    "business_goal": "冬季通勤种草"
  },
  "reference_videos": [
    "https://example.com/ref.mp4"
  ],
  "creative_direction": {
    "core_message": "12小时保温，冬天通勤随时喝热水",
    "creative_style": "情绪种草"
  },
  "user_message": "帮保温杯做一条冬季通勤的种草短视频",
  "auto_start": true
}
```

常见 SSE 事件：

| 事件 | 说明 |
| --- | --- |
| `task_created` | 任务已创建 |
| `run_started` | LangGraph run 已启动 |
| `phase_change` | 阶段变化 |
| `brief_ready` | Brief 已生成，等待用户确认 |
| `brief_confirmed` | Brief 已确认 |
| `brief_rejected` | Brief 被拒绝 |
| `brief_revised` | Brief 已修订 |
| `preferences_updated` | 偏好已更新 |
| `task_done` | 任务完成 |
| `run_finished` | run 结束，但任务可能还不是 done |
| `task_failed` | 任务失败 |

### 用户偏好 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/users/{user_id}/preferences` | 查询结构化偏好 |
| `PUT` | `/api/users/{user_id}/preferences` | 更新结构化偏好 |
| `POST` | `/api/users/{user_id}/preferences/feedback` | 追加反馈 |

P0 偏好是结构化字段，不是向量语义记忆：

- `style_preferences`
- `negative_rules`
- `defaults`
- `recent_feedback`
- `semantic_memory` 当前返回 `reserved_for_p1`

### 认证与通用平台 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/auth/login/local` | 本地登录 |
| `POST` | `/api/v1/auth/register` | 注册普通用户 |
| `POST` | `/api/v1/auth/logout` | 登出 |
| `GET` | `/api/v1/auth/me` | 当前用户 |
| `GET` | `/api/v1/auth/setup-status` | 是否需要初始化管理员 |
| `POST` | `/api/v1/auth/initialize` | 首次创建管理员 |
| `GET` | `/api/models` | 模型列表 |
| `GET/PUT` | `/api/mcp/config` | MCP 配置 |
| `GET/PUT` | `/api/skills` | skills 管理 |
| `GET` | `/api/memory` | memory 数据 |
| `POST/GET/PATCH/DELETE` | `/api/threads...` | LangGraph thread/run 兼容接口 |
| `POST` | `/api/threads/{thread_id}/uploads` | 上传文件 |
| `GET` | `/api/threads/{thread_id}/artifacts/{path}` | 下载 artifact |

## 数据存储

PixelFlow 业务数据有三种后端：

| 条件 | 任务存储 | 偏好存储 |
| --- | --- | --- |
| 设置 `PIXELFLOW_MYSQL_URL` | 独立 MySQL | 独立 MySQL |
| 未设置，但 DeerFlow runtime 有 SQL session factory | 复用 DeerFlow SQL | 复用 DeerFlow SQL |
| 都不可用 | 内存 | 内存 |

业务表：

| 表 | 作用 |
| --- | --- |
| `pixelflow_tasks` | 任务主表 |
| `pixelflow_task_events` | 任务事件表，供 SSE/history 使用 |
| `pixelflow_assets` | 生成资产表 |
| `pixelflow_user_preferences` | 用户结构化偏好 |

注意：`/api/tasks/{task_id}` 会先从 LangGraph checkpoint 同步一次最新 state，再返回业务任务记录。

## 环境变量

`backend/.env.example` 只列出业务关键项。Borgrise 和 LLM secret 不会填入默认值。

### PixelFlow

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PIXELFLOW_MYSQL_URL` | 空 | PixelFlow 业务数据 MySQL 连接串 |
| `PIXELFLOW_MEM0_ENABLED` | `false` 示例 | P1 语义记忆预留，P0 不使用 |
| `PIXELFLOW_VIDEO_SKILL` | `borgrise` | 视频生成实现 |
| `PIXELFLOW_EDIT_SKILL` | `jianying` | 剪辑实现，支持 `jianying` / `ffmpeg` |
| `PIXELFLOW_DECOMPOSE_SKILL` | `borgrise` | 参考视频拆解实现 |
| `PIXELFLOW_DRAFT_ROOT` | 系统临时目录 | 剪映草稿输出根目录 |
| `PIXELFLOW_RENDER_ROOT` | 系统临时目录 | FFmpeg 成片输出根目录 |
| `PIXELFLOW_CAPTION_FONT` | 空 | FFmpeg 花字 drawtext 字体路径 |

### Borgrise

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BORGRISE_API_TOKEN` | 空 | Borgrise API token |
| `BORGRISE_USERNAME` | 空 | token 过期自动登录用户名 |
| `BORGRISE_PASSWORD` | 空 | token 过期自动登录密码 |
| `BORGRISE_BASE_URL` | `https://test-video.borgrise.com/api` | API base URL |
| `BORGRISE_PROJECT_ID` | `1` | Borgrise projectId |
| `BORGRISE_SKIP_SSL_VERIFY` | false | 是否跳过 SSL 校验 |
| `BORGRISE_POLL_TIMEOUT` | `600` | 任务轮询超时秒数 |
| `BORGRISE_MAX_RETRIES` | `3` | 请求最大重试次数 |

### Gateway / Auth

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GATEWAY_HOST` | `0.0.0.0` | 后端监听 host |
| `GATEWAY_PORT` | `8001` | 后端监听端口 |
| `GATEWAY_ENABLE_DOCS` | `true` | 是否开启 `/docs`、`/redoc`、`/openapi.json` |
| `GATEWAY_CORS_ORIGINS` | 空 | 分离部署时允许的浏览器 origin |
| `AUTH_JWT_SECRET` | 自动生成并持久化 | JWT secret，生产建议显式设置 |

### DeerFlow / 模型配置

DeerFlow harness 还会读取：

| 变量 | 说明 |
| --- | --- |
| `DEER_FLOW_CONFIG_PATH` | 指定 `config.yaml` 路径 |
| `DEER_FLOW_EXTENSIONS_CONFIG_PATH` | 指定扩展配置路径 |
| `DEER_FLOW_HOME` | 指定 DeerFlow home/base |
| `DEER_FLOW_PROJECT_ROOT` | 指定项目根 |
| `DEER_FLOW_SKILLS_PATH` | 指定 skills 目录 |
| `ANTHROPIC_API_KEY` | Claude/Anthropic provider key |
| `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_AUTH_TOKEN` | Claude OAuth/token 相关 |

具体模型、工具、sandbox、memory、run_events、checkpointer 等由 DeerFlow `config.yaml` 控制。

## 本地开发

### 前置依赖

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ 或 22+
- pnpm
- 可选：FFmpeg（使用 `PIXELFLOW_EDIT_SKILL=ffmpeg` 时需要）
- 可选：pyJianYingDraft + MediaInfo（使用默认 `jianying` 草稿输出时需要）

### 启动后端

```bash
cd backend
uv sync
make dev
```

默认地址：

- Gateway: `http://localhost:8001`
- OpenAPI: `http://localhost:8001/docs`
- Health: `http://localhost:8001/health`

等价命令：

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload
```

首次无管理员时，后端不会自动创建账号。可通过认证初始化接口创建首个管理员：

```bash
curl -X POST http://localhost:8001/api/v1/auth/initialize \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"ChangeMe123!"}'
```

### 启动前端

```bash
cd web
pnpm install
pnpm dev
```

默认地址：

- Web: `http://localhost:5273`
- `/api` 代理：默认到 `http://localhost:8001`

如需覆盖后端地址：

```bash
VITE_API_TARGET=http://localhost:8123 pnpm dev
```

### 常用测试与检查

后端全量测试：

```bash
cd backend
make test
```

PixelFlow 业务相关测试：

```bash
cd backend
uv run pytest \
  tests/test_intake_integrity.py \
  tests/test_intake_reference_summary.py \
  tests/test_creative_validator.py \
  tests/test_generate_segment_plan.py \
  tests/test_generate_node.py \
  tests/test_edit_timeline.py \
  tests/test_edit_draft_plan.py \
  tests/test_edit_ffmpeg_plan.py \
  tests/test_edit_node.py \
  tests/test_qc_check.py \
  tests/test_pixelflow_task_store.py \
  tests/test_pixelflow_preferences.py \
  tests/test_borgrise_decompose.py \
  tests/test_borgrise_poll.py \
  -q
```

后端 lint：

```bash
cd backend
make lint
```

前端类型检查和构建：

```bash
cd web
pnpm lint
pnpm build
```

## Docker

`backend/Dockerfile` 是多阶段构建：

- `builder`：Python 3.12 slim + build-essential + Node.js + uv，安装依赖。
- `dev`：保留工具链和 Docker CLI，适合开发容器。
- `runtime`：精简运行镜像，暴露 `8001`。

默认 runtime 命令：

```bash
cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001
```

## 当前能力状态

| 模块 | 当前状态 | 说明 |
| --- | --- | --- |
| 采集 `intake` | 可用 | 商品 URL 抽取、参数归一、需求完整性检查、参考视频拆解 |
| 策划 `creative` | 可用 | LLM 生成 Brief，纯逻辑校验修复 |
| Brief 人工确认 | 可用 | LangGraph `interrupt()` + `/brief/confirm` 恢复 |
| 视频生成 `generate` | 可用 | 按 `SEEDANCE_MAX_DURATION=10` 分 segment，并行调用 Borgrise |
| 剪辑 `edit` | v1 可用 | 默认剪映草稿；`PIXELFLOW_EDIT_SKILL=ffmpeg` 输出 mp4 |
| 质检 `qc` | 可用 | 片段完整性 fail，时长不达标 warn |
| 任务 API | 可用 | 创建、查询、资产、Brief 确认/修改、SSE/history |
| 用户偏好 P0 | 可用 | 结构化偏好、负向规则、默认参数、最近反馈 |
| 前端工作台 | 部分可用 | 登录、建任务、SSE、Brief 确认、结果展示已接；历史/参考视频/部分参数尚未完成 |
| P1 语义记忆 | 预留 | `semantic_memory` 返回 reserved_for_p1 |

## 已知限制和注意点

- 项目仍处于 WIP，不建议直接用于生产。
- 前端 `Sidebar` 最近对话是占位数据。
- 前端 `/c/:taskId` 尚未恢复历史任务。
- 前端参数面板中的 `count`、`sound` 未贯穿到后端。
- 后端支持 `reference_videos`，前端尚无输入入口。
- FFmpeg 成片路径当前可能是本地路径，浏览器播放需要可访问 URL 或 artifact 服务承接。
- `_sync_task_from_checkpoint()` 当前读取 generated asset 的 `shot_index`，而生成节点写的是 `segment_index`；多段资产同步时需要留意资产 ID 覆盖风险。
- `web/README.md` 仍有旧描述，根 README 以当前源码为准。

## 许可证

见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
