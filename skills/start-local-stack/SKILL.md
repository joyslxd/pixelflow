---
name: start-local-stack
description: "Use when a repository has multiple local services such as a frontend, API gateway, worker, model sidecar, or Docker Compose stack and the user asks to start, restart, rebuild, or verify the development environment."
---

# Start Local Stack

## 目标

把一个项目的启动方式整理成可执行、可迁移的本地运行流程，并安全地启动或重启前端、API、Sidecar/Worker 和容器编排服务。流程会先从仓库文档与配置推导真实命令，再核对端口、进程、健康状态和代码版本；不会打印 Secret、用户正文或供应商原始异常，也不会在健康检查阶段触发计费模型请求。

## 启动模式决策

按用户请求和仓库证据选择一种模式；不要把不同模式混在同一个进程里。

- 本地源码联调：依次启动依赖、Sidecar/Worker、Gateway/API、前端，适合需要实时改代码的测试。
- 本地 Docker：使用仓库已有的 Compose/启动脚本，适合复现容器环境；先阅读该脚本说明，不在 macOS 上盲跑 Linux 专用脚本。
- 远程或 Linux 部署：只有用户明确要求时执行；先确认目标主机、镜像/分支、受保护环境文件和回滚方式。
- 只验证不启动：只检查现有监听进程和 `/live`、`/ready`、前端页面，不重启、不触发业务动作。

## 标准流程

### 1. 识别项目与边界

在项目根目录读取 `AGENTS.md`、`README.md`、各服务 README、`Makefile`、`package.json`、`pyproject.toml`、锁文件、Compose 文件和 `.env.example`。记录以下信息：

- 服务角色、启动入口、工作目录、端口和健康检查地址；
- 包管理器与锁文件（例如 `uv`、`pnpm`、`npm`）；
- 必填的非 Secret 配置、必须由外部环境注入的 Secret 名称；
- 前端 API 代理目标、服务间 URL、数据库/Run Store 的持久化目录；
- 当前分支、工作区是否有未提交改动，以及正在运行的相关进程。

优先采用仓库已记录的命令。没有证据时使用项目实际入口探测，不凭经验改端口或添加新启动器。

### 2. 安全预检

先确认依赖目录、环境变量和端口，再启动服务。

- 只读取 Secret 的变量名，不读取或回显值；缺少 Secret 时说明变量名和注入位置，不能自行猜值。
- 不把 Secret、Authorization、用户正文、模型响应或供应商原始异常写进日志、测试夹具、Skill 或提交。
- 检查工作区状态，保留与本任务无关的未提交改动；不要用会覆盖工作区的 Git 命令。
- 识别准确的服务 PID 和监听端口后再停止进程；不要使用宽泛的 `pkill -f`、按端口误杀或停止其他工作区的前端。
- 数据库、SQLite、Run Store 和上传目录默认保留；不要为“重启干净”删除数据。若必须清理，先得到用户明确授权并限定到已确认的临时目录。

### 3. 同步依赖

按锁文件选择命令，并在对应工作目录执行：

```bash
# Python：锁文件存在时优先使用锁定同步
uv sync --locked --all-groups

# Node：按仓库锁文件选择一种
pnpm install --frozen-lockfile
npm ci
```

如果启动时报“运行时缺少模块”，先确认该模块是否已声明为直接运行时依赖；修复 `pyproject.toml`/`package.json` 后重新生成锁文件，再同步。不要仅在当前虚拟环境里临时安装来掩盖依赖声明问题。依赖同步后应运行项目规定的最小 lint/contract 测试。

### 4. 启动顺序

默认顺序如下；如果仓库文档明确要求不同顺序，以仓库为准：

```text
持久化/基础依赖 → Sidecar/Worker → Gateway/API → Frontend
```

对于每个服务使用独立终端或受控后台进程，记录工作目录、命令、PID、端口和日志路径。不要把 Secret 拼进命令行或日志。常见源码启动模板如下，必须替换为仓库真实模块和目录：

```bash
# Gateway/API（示例）
PYTHONPATH=<backend-dir> uv run uvicorn <module>:app --host 127.0.0.1 --port <api-port>

# HTTP/SSE Sidecar（示例）
PYTHONPATH=<sidecar-src-dir> uv run uvicorn <module>:create_app --factory --host 127.0.0.1 --port <sidecar-port>

# 前端（示例）
VITE_API_TARGET=http://127.0.0.1:<api-port> pnpm dev --host 127.0.0.1 --port <frontend-port>
```

如果使用 `Makefile`、`npm run` 或仓库脚本，优先调用它们，以免漏掉配置加载、编码、迁移或 reload 选项。开发热重载只用于本地源码联调；需要验证稳定进程时使用无 reload 启动。

### Secret 手工注入交接

如果启动真实模型 Sidecar 需要 Secret，默认把 Secret 注入和启动动作交给用户在本地终端执行。不要让 Agent 读取、回显、写入命令行参数、写入 Skill 或保存 Secret 值；Agent 只报告需要的变量名，并提供不含 Secret 值的命令模板。PixelFlow 的可直接复制命令见 [PixelFlow 启动参考](references/pixelflow.md) 的“模式 A.1：用户手工启动”。

### 5. 健康检查与版本检查

启动后按顺序检查：

1. 进程仍存活，监听地址和端口正确；
2. 每个后端的 `/live`；
3. 每个后端的 `/ready`，确认必需配置、依赖和服务间连接已就绪；
4. 前端首页可访问，代理目标指向当前 Gateway；
5. 通过无副作用接口或公开版本信息确认响应来自当前分支/镜像，而不是旧进程、旧容器或旧前端构建。

健康检查只允许 GET/HEAD 或仓库明确标注的无副作用探针。不要用生成、提交、恢复、扣费或真实模型请求来证明服务“已启动”。

### 6. 排障顺序

按“配置 → 依赖 → 端口/PID → 服务日志 → 服务间连接 → 版本漂移”的顺序缩小范围：

- 端口占用：列出监听 PID 和完整命令，只停止已确认属于目标服务的 PID；
- 模块缺失：检查直接依赖和锁文件，重新锁定并同步；
- `/ready` 失败：查看缺失配置的变量名和非敏感错误摘要，不输出值；
- Gateway 无法访问 Sidecar：核对 loopback/容器网络地址、服务 JWT 的存在性和两侧发布摘要是否一致；
- 前端显示旧行为：核对 Vite 代理、浏览器缓存、前端进程工作目录、当前 Git commit 和容器镜像版本；
- 业务结果仍出现旧实体或旧链路名称：优先怀疑旧进程、旧镜像、旧前端或历史会话回放，不要先修改数据库伪造结果。

日志只保留必要的时间、服务、状态码、追踪 ID 和脱敏错误分类。把详细日志写入任务专用临时目录，交接时只报告路径和摘要。

## PixelFlow 参考

当前仓库的端口、命令、配置和 Linux Compose 方式见 [PixelFlow 启动参考](references/pixelflow.md)。迁移到其他项目时保留本文件，删除或替换该参考文件即可。

## 完成标准

向用户交接时给出：启动模式、实际使用的命令、服务 PID/端口、各健康检查结果、当前代码/镜像标识、日志位置、未解决阻塞和是否执行了任何真实外部请求。明确说明“未发起真实模型/计费请求”，不能用模糊的“服务正常”代替证据。

PixelFlow 的当前分支源码重启命令见 [PixelFlow 启动参考](references/pixelflow.md) 的“模式 B.1：PixelFlow 当前分支一键重启命令”。执行重启前必须精确核对 PID，重新注入必需的模型 Secret，并在重启后完成 `/live`、`/ready` 和代码版本检查。
