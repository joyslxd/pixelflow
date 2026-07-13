# PixelFlow 后端贡献说明

这份文档给后续维护 PixelFlow 后端、前端联调和 content-app 联动的人看。用户主要是 Java 后端开发，所以这里会尽量用 Java/Spring 的思路解释 Python、FastAPI、React 和 Agent 工作流。

## 目录

- [项目定位](#项目定位)
- [本地启动](#本地启动)
- [content-app 鉴权联动](#content-app-鉴权联动)
- [前端本地测试 Authorization](#前端本地测试-authorization)
- [目录和分层](#目录和分层)
- [新增接口规则](#新增接口规则)
- [新增 content-app 调用规则](#新增-content-app-调用规则)
- [配置文件规则](#配置文件规则)
- [代码注释规则](#代码注释规则)
- [测试和验证](#测试和验证)
- [提交前检查清单](#提交前检查清单)

## 项目定位

PixelFlow 是电商带货短视频生成 AI Agent 平台。它不是单纯聊天机器人，而是一个阶段化视频生成流水线。

核心链路：

```text
intake 采集需求
  -> creative 生成 Brief
  -> brief_review 人工确认
  -> generate 生成视频片段
  -> edit 剪辑和渲染
  -> qc 质检
  -> done 产物完成
```

Java 类比：

| PixelFlow 概念 | Java/Spring 类比 | 说明 |
| --- | --- | --- |
| FastAPI Router | Controller | 接收 HTTP 请求、做参数校验、返回响应 |
| Pydantic Model | DTO/VO | 定义请求和响应字段，并做基础校验 |
| LangGraph Node | Service 方法 | 每个节点处理一个业务阶段 |
| TaskState | 流程上下文 DTO | 在整条 Agent 流程里传递商品、Brief、视频资产等信息 |
| Store/Repository | Repository/DAO | 保存任务、事件、资产、偏好 |
| Skill | 第三方 Client | 适配 Borgrise/content-app、剪映、FFmpeg 等外部能力 |
| Middleware | Filter/Interceptor | 全局鉴权、上下文注入、请求拦截 |
| React Component | 前端页面/组件 | 前端工作台、画布、时间线、参数弹窗 |

## 本地启动

后端默认使用 `config.dev.yml`。

```bash
cd backend
uv sync
make dev
```

生产配置启动：

```bash
cd backend
make prod
```

前端启动：

```bash
cd web
pnpm install
pnpm dev
```

常用访问地址：

| 地址 | 说明 |
| --- | --- |
| `http://localhost:5273/` | PixelFlow 前端工作台 |
| `http://localhost:5273/auth-token` | 本地调试 Authorization 设置页 |
| `http://localhost:8001/agent/docs` | FastAPI Swagger/OpenAPI 页面 |
| `http://localhost:8001/agent/openapi.json` | OpenAPI 3 JSON |

## content-app 鉴权联动

PixelFlow 自身登录体系已经废弃。登录统一发生在同级 `content-app` 项目，PixelFlow 只识别 content-app 传来的请求头：

```http
Authorization: Bearer <content-app-jwt>
```

后端处理顺序：

1. `AuthMiddleware` 读取 `Authorization`。
2. `content_app_auth.py` 只读取 JWT payload 里的 `sub` 字段，作为 PixelFlow 内部的 `user_id`。
3. 再调用 content-app `/api/auth/verify` 做远程实时校验，由 content-app 判断 token 真伪、过期状态和用户是否被禁用。
4. 校验通过后，把用户写入 `request.state.user` 和 `deerflow.runtime.user_context`。
5. 同时把原始 `Authorization` 写入 `content_app_auth_context.py` 的 `ContextVar`。
6. 后续 Skill 调 content-app/Borgrise 生成图片或视频时，从 `ContextVar` 里取同一个 `Authorization` 透传。

这样做的原因：

- content-app 才是真正的用户系统，PixelFlow 不再维护用户名、密码、cookie session。
- content-app 的生成视频、生成图片接口需要按登录用户扣费，必须透传真实用户 token。
- PixelFlow 本地只读取用户名，token 真伪、过期和“用户被禁用”都以远程 `/api/auth/verify` 为准。
- SSE 是长连接，中间件只能在建连时校验一次，所以 SSE 生成器里还会周期性调用 `/api/auth/verify`。

开发时必须遵守：

- 不要新增 PixelFlow 本地登录、注册、初始化管理员、改密码、cookie session 或 CSRF 登录态。
- 不要把用户 token、用户名、密码写进 `config.dev.yml`、`config.prod.yml`、`.env` 或代码。
- 需要当前用户时，走 `get_current_user_from_request()` 或 `get_current_user()`。
- 需要把 token 传给 content-app/Borgrise 时，走 `content_app_auth_context.require_current_authorization()`。
- 新增、删除、改名或改参数任何 content-app 接口调用时，都要同步更新根目录 `CONTENT_APP_API_CALLS.md` 和本小节。
- 新增任何调用 content-app 的 Client/Skill 时，都要补测试，至少覆盖“透传 Authorization”和“缺 token 失败”。

## 前端本地测试 Authorization

正式联动时，content-app 前端应该在进入 PixelFlow 时提供 Authorization。当前 PixelFlow 前端支持两种方式：

| 方式 | 适用场景 | 说明 |
| --- | --- | --- |
| `window.__CONTENT_APP_AUTHORIZATION__` | content-app 宿主页面集成 | 宿主页面直接注入完整 `Bearer xxx` |
| `localStorage.Authorization` | 本地独立调试 | 打开 `/auth-token` 页面保存一次，后续请求自动携带 |

本地调试步骤：

1. 先在 content-app 登录，拿到登录 token。
2. 启动 PixelFlow 后端和前端。
3. 打开 `http://localhost:5273/auth-token`。
4. 粘贴 token。可以粘贴完整 `Bearer xxx`，也可以只粘贴原始 JWT。
5. 点击“保存并验证”。
6. 页面会调用 `/agent/auth/me`，如果返回用户名，说明前端到 Python 后端的 Authorization 链路已打通。
7. 回到工作台后，创建任务、确认 Brief、订阅 SSE、拉取资产都会自动带同一个 `Authorization`。

前端读取优先级：

```text
window.__CONTENT_APP_AUTHORIZATION__
  -> localStorage 中的 Authorization / authorization / contentAppAuthorization / content_app_authorization / token / access_token
  -> sessionStorage 中的同名 key
```

前端统一封装位置：

| 文件 | 说明 |
| --- | --- |
| `web/src/lib/authStorage.ts` | 负责保存、清除、读取、归一化 Authorization |
| `web/src/lib/api.ts` | 统一 API Client，每个请求都会自动带 Authorization |
| `web/src/pages/AuthTokenPage.tsx` | 本地调试 Authorization 页面 |

## 目录和分层

主要目录：

```text
backend/
├── app/gateway/                 # FastAPI 网关层，类似 Spring Boot Controller + Filter + 启动配置
│   ├── app.py                    # FastAPI 应用创建、路由挂载、中间件挂载
│   ├── auth_middleware.py        # 全局 Authorization 鉴权中间件
│   ├── content_app_auth.py       # 读取 content-app JWT 用户名并远程 verify
│   ├── content_app_auth_context.py # 请求级 Authorization 上下文，供 Skill 透传 token
│   └── routers/                  # HTTP Controller
├── pixelflow/                    # PixelFlow 业务核心
│   ├── state.py                  # TaskState，全流程上下文 DTO
│   ├── graph.py                  # LangGraph 状态机
│   ├── nodes.py                  # 各阶段 Service 方法
│   ├── tasks/                    # 任务、事件、资产持久化
│   ├── preferences/              # 用户偏好
│   └── skills/                   # 第三方能力 Client/Adapter
├── packages/harness/deerflow/    # DeerFlow/LangGraph 基础设施
└── tests/                        # 后端测试
```

前端目录：

```text
web/src/
├── pages/                        # 页面入口，如 WorkspacePage、AuthTokenPage
├── components/                   # 页面组件，如侧边栏、画布、聊天面板
├── lib/api.ts                    # 后端 API Client
├── lib/authStorage.ts            # Authorization 本地调试存储工具
└── lib/types.ts                  # 前端类型定义
```

分层规则：

| 要做的事 | 应放位置 |
| --- | --- |
| HTTP 入参、出参、状态码 | `backend/app/gateway/routers/` |
| 全局鉴权、请求上下文 | `backend/app/gateway/auth_middleware.py`、`content_app_auth*.py` |
| PixelFlow 阶段编排 | `backend/pixelflow/nodes.py`、`graph.py` |
| 纯逻辑校验和转换 | `backend/pixelflow/intake/`、`creative/`、`generate/`、`edit/`、`qc/` |
| 第三方 API 调用 | `backend/pixelflow/skills/` |
| 任务和资产存储 | `backend/pixelflow/tasks/` |
| 前端 API 调用 | `web/src/lib/api.ts` |
| 前端页面 | `web/src/pages/` |

## 新增接口规则

PixelFlow 对前端或第三方暴露的新接口必须满足：

- 路径必须以 `/agent` 开头。
- PixelFlow 主业务流程统一放在 `/agent/flows` 下。
- 不要再新增 `/api/tasks`、`/api/users` 这类旧路径。
- 非公开接口默认需要 `Authorization`。
- 公开接口只能是健康检查、接口文档这类无用户数据的入口。
- Router 只做 HTTP 边界工作，不要把复杂业务逻辑直接写进 Router。

新增接口建议流程：

1. 在 `backend/app/gateway/routers/` 创建或修改 Router。
2. 用 Pydantic Model 定义请求和响应 DTO。
3. 调用 `backend/pixelflow/` 下的业务 Service/Store/Skill。
4. 在 `backend/app/gateway/app.py` 挂载 Router。
5. 写测试覆盖成功、参数错误、未认证、无权限等关键路径。
6. 如果接口给前端用，同步更新 `web/src/lib/api.ts` 和 TypeScript 类型。
7. 如果接口属于重要业务链路，同步更新 README、AGENTS 或项目说明文档。

## 新增 content-app 调用规则

content-app 调用一般发生在 Skill/Client 层，不要散落在 Router 或节点里。

必须遵守：

- 使用当前请求透传的 `Authorization`。
- 不要读取 `BORGRISE_API_TOKEN` 作为用户身份。
- 不要写死用户名、密码、token。
- 请求失败要在 Skill 边界转换成清晰错误，避免上层看到零散的第三方异常。
- 新增接口后同步更新 `CONTENT_APP_API_CALLS.md`。

示例：

```python
from app.gateway.content_app_auth_context import require_current_authorization


def build_headers() -> dict[str, str]:
    """构造调用 content-app 的请求头。

    这里必须使用入口请求透传来的 Authorization，不能写死测试 token。
    content-app 会根据这个 token 识别用户、扣费、写历史记录。
    """
    return {
        "Authorization": require_current_authorization(),
        "Content-Type": "application/json",
    }
```

## 配置文件规则

配置文件分两份：

| 文件 | 说明 |
| --- | --- |
| `backend/config.dev.yml` | 本地开发、测试环境配置 |
| `backend/config.prod.yml` | 生产环境配置 |

规则：

- 普通环境差异放进 YAML，例如端口、数据库地址、content-app 地址、超时、开关。
- 用户登录 token 不允许放进 YAML、`.env` 或代码。
- 不要在 PixelFlow 配置 content-app 的 token 签名密钥；token 真伪统一交给 content-app `/api/auth/verify` 判断。
- `borgrise.remote_verify_enabled` 默认开启，除非本地离线调试才临时关闭；它只控制 `/api/auth/verify` 登录态校验。
- `borgrise.verify_timeout_seconds` 只控制登录态实时校验，默认 10 秒；它不是生成任务轮询超时。
- Borgrise 异步任务轮询必须按业务类型选择配置：视频生成用 `borgrise.video_poll_timeout`，默认 1 小时；图片生成用 `borgrise.image_poll_timeout`，默认 10 分钟；视频分析/参考视频拆解用 `borgrise.video_analysis_poll_timeout`，默认 15 分钟；视频合并是 content-app 同步接口，用 `borgrise.video_merge_request_timeout` 控制读等待，默认 1 小时。
- `pixelflow.media_skill` 是图片生成、视频生成、参考视频拆解共用的媒体供应商开关；当前仅支持 `borgrise`，对应外部 Client 参数写在 `borgrise.*`。
- `pixelflow.edit_skill` 是剪辑/渲染开关，和媒体供应商不是同一类能力；当前支持 `jianying` 和 `ffmpeg`。
- 新增配置项必须写中文注释，说明用途、默认值和影响范围。

## 代码注释规则

本项目面向不熟悉 Python 和前端的 Java 开发维护，所以注释要更偏“解释业务意图”，不要只翻译语法。

后端注释要求：

- 公共函数、类、复杂私有函数都写中文 docstring。
- 关键变量说明它代表的业务含义，而不是只写类型。
- 调用 content-app、Borgrise、FFmpeg、剪映等外部系统时，说明为什么这样传参。
- 涉及异步、线程、`ContextVar`、SSE、LangGraph interrupt 的地方，要补充 Java 类比或流程说明。
- 不确定的逻辑不要瞎写注释；先读调用链或查资料。

前端注释要求：

- API Client、SSE、Blob 播放、本地 Authorization 存储等容易踩坑的地方要写中文注释。
- 页面组件内只在关键流程处写注释，不要给每个 JSX 标签写无意义注释。
- 用户可见文案要简洁，不要把大段技术说明塞进界面。

示例：

```python
async def stream_task_events(task_id: str, request: Request):
    """把任务事件表转换成 SSE 推给前端。

    Java 类比：这相当于一个持续写响应的 Controller。中间件只在建连时校验一次，
    所以这里每轮循环还要远程 verify content-app token，保证禁用用户立刻断开。
    """
```

## 测试和验证

后端常用验证：

```bash
cd backend
uv run pytest tests/test_content_app_auth.py tests/test_content_app_auth_middleware.py tests/test_borgrise_authorization_passthrough.py -q
uv run ruff check .
```

如果本机 `uv` 不在 PATH，可使用项目虚拟环境：

```bash
cd backend
./.venv/bin/python -m pytest tests/test_content_app_auth.py tests/test_content_app_auth_middleware.py tests/test_borgrise_authorization_passthrough.py -q
./.venv/bin/python -m ruff check .
```

前端常用验证：

```bash
cd web
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
```

Authorization 存储工具测试：

```bash
cd web
rm -rf /tmp/pixelflow-auth-storage-test
./node_modules/.bin/tsc src/lib/authStorage.ts --target ES2022 --module ES2022 --moduleResolution bundler --outDir /tmp/pixelflow-auth-storage-test --skipLibCheck --strict
AUTH_STORAGE_TEST_MODULE=file:///tmp/pixelflow-auth-storage-test/authStorage.js node --test tests/authStorage.test.mjs
```

## 提交前检查清单

提交前至少确认：

- 新增或修改的后端接口都以 `/agent` 开头。
- 需要登录的接口都依赖 content-app `Authorization`。
- Skill 调 content-app/Borgrise 时透传当前请求 token，没有写死 token。
- 新增 content-app 接口调用已更新 `CONTENT_APP_API_CALLS.md`。
- 新增配置项已写入 `config.dev.yml` 和 `config.prod.yml`，并有中文注释。
- 新增复杂逻辑有中文注释，注释解释业务原因和调用关系。
- 后端相关测试通过。
- 前端 TypeScript 编译和构建通过。
- 没有清理或回滚用户已有的无关改动。
