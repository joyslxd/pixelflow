# PixelFlow 启动参考

本文件只记录 PixelFlow 当前仓库的启动映射，不能复制其中的 Secret 值。迁移到其他仓库时，将这里的路径、端口和模块替换为目标项目的真实配置。

## 服务拓扑

```text
浏览器 → Vite 前端 :5273 → Gateway :8001 → Harness Sidecar :8090
                              └→ SQLite：../.pixelflow/dev-data
```

Gateway 是业务状态和 Tool Broker 的权威写入方；Sidecar 只负责 Harness 模型循环和通过 Broker 调用 Tool。启动检查不得调用生图、生视频、恢复或其他计费接口。

## 模式 A：源码本地联调

仓库提供了一个本地统一启动器 `scripts/start-local-pixelflow.sh`。它加载 Gateway 与 Sidecar 各自的本地 `.env`，自动生成本次进程的本地 JWT、计算当前 Manifest，并启动 Gateway 与真实 Sidecar；不会启动前端，也不会主动发起模型请求。

### 模式 A.0：首次配置文件

启动器要求两份实际本地配置文件均存在；它先加载同目录 `.env.example` 中的非敏感默认值，再加载 `.env` 的开发者私有覆盖。`.env` 是隐藏文件且受 Git 忽略，禁止提交任何 Secret。

| 服务 | 实际配置文件 | 模板 | 配置归属 |
| --- | --- | --- | --- |
| Gateway | `backend/.env` | `backend/.env.example` | Gateway、数据库、Provider 开关、Mem0 |
| Harness Sidecar | `services/pixelflow-agent-harness/.env` | `services/pixelflow-agent-harness/.env.example` | 模型 API、Skill 根、Tool Broker |

若实际 `.env` 尚不存在，先从对应模板复制后再填写；不要将模板改为保存真实 Key：

```bash
cp backend/.env.example backend/.env
cp services/pixelflow-agent-harness/.env.example services/pixelflow-agent-harness/.env
```

#### Gateway：可选 Mem0 长期记忆

Mem0 仅属于 Gateway，配置写入 `backend/.env`；Sidecar 不需要 Mem0 配置。需要启用时填写以下变量，其中 API Key 与匿名化盐必须由开发者自行注入，不能写进 Skill、仓库、命令行或日志：

```dotenv
# 用途：启用 Gateway 长期记忆；影响：配置不完整时 Gateway 以 fail-open 模式运行，不阻塞启动。
PIXELFLOW_LONG_TERM_MEMORY_ENABLED=true
# 用途：指定 Mem0 服务地址；影响：Gateway 通过该地址读写长期记忆。
PIXELFLOW_VOLCENGINE_MEM0_BASE_URL=<Mem0 服务地址>
# 用途：认证 Mem0 服务；影响：仅在 Gateway 进程环境内使用，禁止提交。
PIXELFLOW_VOLCENGINE_MEM0_API_KEY=<Mem0 API Key>
# 用途：对用户标识进行稳定匿名化；影响：同一开发环境必须保持固定，禁止提交。
PIXELFLOW_LONG_TERM_MEMORY_USER_SALT=<随机且长期固定的私有字符串>
```

若未配置 Mem0 API Key 或匿名化盐，启动器会提示非敏感告警，Gateway 仍可启动，但长期记忆会显示为 `enabled=False`。这不是 DeepSeek、图片或视频生成失败的原因。

### 模式 A.1：用户手工启动

需要启动真实 Sidecar 时，将下面命令原样交给用户，让用户在自己的本地终端输入 Secret 并启动。Agent 不读取、回显或代填 `DEEPSEEK_API_KEY`。终端中非空的 Key 优先于 Sidecar 配置模板中的空占位：

```bash
cd /Users/williaman/Documents/joyce文档/code/pixelflow

read -r -s DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
printf '\n'

bash scripts/start-local-pixelflow.sh
```

如果用户当前不在这个路径，应将第一行替换为目标项目根目录；不要把真实 Key 写进 Skill、Shell 命令参数、日志或提交。`DEEPSEEK_BASE_URL`、模型名称、Skill 根和 Tool Broker 地址写入 Sidecar `.env`；本地默认值见其 `.env.example`。该启动器会在本地生成临时 JWT 并启动 Gateway 与真实 Sidecar，不启动前端，也不会主动发起生图、生视频或其他计费请求。

建议优先使用该入口，不要把历史 JWT 值复制到多个终端。默认日志和 PID 文件位于系统临时目录下；若 8001 或 8090 已占用，启动器会停止并要求先确认目标进程，不会误杀其他工作区服务。

### 0. 目录和环境

```bash
export PROJECT_ROOT="$(pwd)"
export BACKEND_DIR="$PROJECT_ROOT/backend"
export SIDECAR_DIR="$PROJECT_ROOT/services/pixelflow-agent-harness"
export WEB_DIR="$PROJECT_ROOT/web"
```

在实际执行前确认当前目录是仓库根目录；不要把真实密钥写进这些命令或文件。Gateway 和 Sidecar 至少需要各自文档列出的服务 JWT、实例身份、Tool Broker 地址/签名材料、Harness 发布摘要以及模型密钥。缺失时只报告变量名。

### 1. 同步依赖

```bash
cd "$BACKEND_DIR"
uv sync --locked --all-groups

cd "$SIDECAR_DIR"
uv sync --locked

cd "$WEB_DIR"
pnpm install --frozen-lockfile
```

仓库根目录 README 也记录了以下 Gateway 同步方式；适用于从根目录明确切换到 `backend` 的场景：

```bash
cd backend
uv sync --locked --all-groups
```

`backend/README.md` 的简化开发方式是 `uv sync`；存在 `uv.lock` 时优先采用上面的锁定同步，避免本地和容器解析出不同依赖。前端 README 使用 `npm run dev` 也可以工作；当前仓库存在 `web/pnpm-lock.yaml` 时，优先使用 `pnpm install --frozen-lockfile` 和 `pnpm dev`。

若仓库没有 `pnpm-lock.yaml`，根据现有锁文件使用 `npm ci`；若 Python 依赖锁文件不存在，使用项目文档规定的非锁定同步后立即补齐锁文件。遇到 `greenlet` 等运行时模块缺失时，应补充直接依赖并重新锁定，不要只向本地 venv 临时安装。

### 2. 启动 Sidecar

在独立终端执行：

```bash
cd "$SIDECAR_DIR"
PYTHONPATH=src uv run --no-sync uvicorn pixelflow_harness_sidecar.app:create_app \
  --factory --host 127.0.0.1 --port 8090
```

如果使用项目自定义启动器，应优先使用启动器。Sidecar 的 `PIXELFLOW_AGENT_HOME`、Run Store、JWT 和模型相关配置从进程环境或受保护 Secret 注入；不要从命令行参数传 Secret。

Sidecar README 中的 venv 方式适用于 `uv` 不可用的机器，仅作为兼容启动方式：

```bash
cd "$SIDECAR_DIR"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest pytest-asyncio ruff
PYTHONPATH=src python -m uvicorn pixelflow_harness_sidecar.app:create_app \
  --factory --host 127.0.0.1 --port 8090
```

优先使用 `uv sync --locked`，因为该 venv 兼容方式需要按 README 额外安装运行依赖，且不能替代锁定环境。不要把这套环境复制到 Linux 生产镜像，也不要混用两个 Python 环境。

### 3. 启动 Gateway

在 Sidecar `/live` 和 `/ready` 通过后，在另一个终端执行：

```bash
cd "$BACKEND_DIR"
PIXELFLOW_CONFIG_ENV=dev \
PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
uv run python -m app.gateway.run
```

源码热重载：

```bash
cd "$BACKEND_DIR"
PIXELFLOW_CONFIG_ENV=dev \
PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
uv run python -m app.gateway.run --reload
```

也可以使用 `make gateway` 或 `make dev`，但要确认其当前工作目录是 `backend`，并确认环境变量已被注入。`backend/config.dev.yml` 中的 Sidecar 地址默认为空；本地联调必须通过环境覆盖为 `http://127.0.0.1:8090`，否则 Gateway 会拒绝新的 Harness Run。

`backend/Makefile` 还提供生产 profile 的源码入口：

```bash
cd "$BACKEND_DIR"
make gateway-prod
```

生产 profile 只适用于已准备好生产配置的环境，不要在本地开发测试中用它替代 `PIXELFLOW_CONFIG_ENV=dev`。

若只需启动 Gateway、且不需要从 YAML profile 自动读取 host/port，可使用仓库 README 的直接 Uvicorn 方式：

```bash
cd "$BACKEND_DIR"
PYTHONPATH=. uv run uvicorn app.gateway.app:app \
  --host 127.0.0.1 --port 8001
```

这条命令的 host/port 来自命令行；`python -m app.gateway.run` 会先加载 `config.dev.yml`/`config.prod.yml`，更适合完整联调。

### 4. 启动前端

在 Gateway `/live`、`/ready` 通过后执行：

```bash
cd "$WEB_DIR"
VITE_API_TARGET=http://127.0.0.1:8001 \
pnpm dev --host 127.0.0.1 --port 5273
```

项目默认 Vite 代理目标也是 `http://localhost:8001`。若使用 npm：

```bash
cd "$WEB_DIR"
VITE_API_TARGET=http://127.0.0.1:8001 \
npm run dev -- --host 127.0.0.1 --port 5273
```

若使用其他 Gateway 端口，必须显式修改 `VITE_API_TARGET`，并确认代理的 `/agent` 路径仍然指向当前 Gateway。

## 模式 B：本地已有进程的安全重启

先查看目标端口和完整命令：

```bash
lsof -nP -iTCP:8001 -sTCP:LISTEN
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:5273 -sTCP:LISTEN
ps -axo pid,ppid,etime,stat,command
```

只对已确认的 Gateway 和 Sidecar PID 发送 `TERM`，等待退出后再按同一 PID 精确处理；不要按关键字杀进程，也不要停止另一个工作区的 Vite。前端若已经指向当前仓库且用户没有要求重启，可保持不动。停止前保存非敏感的启动命令、工作目录和 PID；不要保存完整环境值。

重启后重新执行三个服务的健康检查，并核对当前 commit、前端工作目录和 Gateway/Sidecar 的发布摘要。若接口仍出现已删除的 `operation-batch-*` 等旧链路标识，而源码已切换到 `generation-job-*`，先排查旧进程、旧容器、旧前端构建或历史会话回放。

### 模式 B.1：PixelFlow 当前分支一键重启命令

适用于已经通过 `scripts/start-local-pixelflow.sh` 启动、且 PID 文件仍位于默认临时目录的本地源码联调。执行前必须用 `lsof` 和 `ps` 确认 PID 确实属于当前仓库的 Gateway/Sidecar；不要把下面的 PID 替换成其他工作区进程。

```bash
cd <project-root>
LOCAL_LOG_DIR="${PIXELFLOW_LOCAL_LOG_DIR:-${TMPDIR:-/tmp}/pixelflow-local}"

lsof -nP -iTCP:8001 -sTCP:LISTEN
lsof -nP -iTCP:8090 -sTCP:LISTEN
ps -axo pid,ppid,etime,stat,command

for service in gateway sidecar; do
  pid_file="$LOCAL_LOG_DIR/$service.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(sed -n '1p' "$pid_file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid"
    fi
  fi
done

for port in 8001 8090; do
  for attempt in $(seq 1 20); do
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 1
  done
done

# 用途：将真实模型密钥仅注入本次 Sidecar 进程；影响：不会写入 Skill、命令历史或日志。
read -r -s -p "请输入 DeepSeek API Key（不会回显，也不会写入文件）：" DEEPSEEK_API_KEY
printf '\n'
export DEEPSEEK_API_KEY

bash scripts/start-local-pixelflow.sh
```

若当前终端已经由受保护的 Secret Manager 注入 `DEEPSEEK_API_KEY`，可跳过 `read`，但仍需确认变量非空。`DEEPSEEK_BASE_URL` 应在 Sidecar `.env` 中维护；`PIXELFLOW_GATEWAY_JWT_SIGNING_KEY` 和 `PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY` 未配置时，启动器会为本次本地进程生成临时值；不需要从历史消息复制旧 JWT。该命令只重启 Gateway 与 Sidecar，不重启前端，也不会发起生图、生视频或其他计费请求。

## 模式 C：Linux Docker Compose/服务器部署

PixelFlow 的 Compose 文件位于 `services/pixelflow-agent-harness/deploy/docker-compose.linux.yml`，只适用于 Linux 部署。部署目录必须有受保护的 `.env.gateway`、`.env.sidecar` 和非敏感的 `.env.harness-release`；发布身份、模型摘要和 Tool Manifest 必须由同一发布流程计算，不能手填漂移值。

在服务器部署目录执行仓库已有脚本：

```bash
./build-and-start-linux.sh
```

网络慢时，按脚本说明设置非 Secret 的镜像源/下载超时变量。脚本只重建 Gateway 和 Harness Sidecar，并检查两者 `/live`、`/ready`；不要替换为停止整个 Compose 项目。部署后确认：

- Gateway 仅绑定既定内部端口，Sidecar 不暴露公网；
- Gateway/Sidecar 使用同一发布摘要和兼容的服务 JWT；
- 数据卷仍指向原有 Gateway 数据、Run/Event Store 和只读 Skill 根；
- Nginx、数据库及其他不在本次范围的服务未被停止；
- 没有为了验证部署而发起真实 Provider 请求。

如果需要从当前分支向服务器传递源码，历史部署流程使用 Git bundle：

```bash
git bundle create /private/tmp/pixelflow-<commit>.bundle <branch>
scp /private/tmp/pixelflow-<commit>.bundle <server>:/tmp/
```

服务器端再进入已确认的部署目录，执行仓库已有的 `build-and-start-linux.sh`。这属于远程变更，必须在用户明确要求、目标主机和受保护环境均已确认后执行；不要把连接凭据、Token 或服务器 Secret 写进命令、日志或 Skill。

## 健康检查清单

```bash
curl -fsS http://127.0.0.1:8090/live
curl -fsS http://127.0.0.1:8090/ready
curl -fsS http://127.0.0.1:8001/live
curl -fsS http://127.0.0.1:8001/ready
curl -fsSI http://127.0.0.1:5273/agentfrontend/
```

如果 `/ready` 需要内部 JWT，不要把 Token 放进命令行、终端输出或 Skill；改用受保护的进程环境和项目已有检查器。只报告 HTTP 状态码、非敏感错误分类和服务是否就绪。

## 常见阻塞

- `greenlet` 或其他模块不存在：检查直接运行时依赖是否声明在 `pyproject.toml`，重新 `uv lock` 和 `uv sync --locked`。
- Gateway 启动后退出：优先看任务专用日志的最后一段，通常是配置或依赖缺失；不要把完整环境输出出来。
- Sidecar `/ready` 失败：核对 `PIXELFLOW_AGENT_HOME`、Run Store、JWT、模型发布摘要和 Tool Broker 地址是否存在且来自同一部署配置。
- Gateway `/ready` 失败：核对 Sidecar 地址、Gateway 服务身份、数据库目录和 Manifest 摘要。
- 前端能打开但请求失败：核对 `VITE_API_TARGET`、Vite 启动目录、`/agent` 代理和 Gateway CORS。
- 看到旧批次/旧 Operation：核对监听 PID、容器镜像、前端构建时间和会话是否为历史回放；不要伪造数据库状态。

## 交接格式

```text
模式：源码本地联调 / Docker / Linux 部署 / 只验证
代码标识：<当前 commit 或镜像标签>
服务：<名称> <PID/容器> <端口> <live/ready 状态>
前端代理：<VITE_API_TARGET 的非敏感 URL>
日志：<任务专用日志目录>
真实外部请求：未发起 / 已由用户明确授权发起（只报告类型和结果）
阻塞：<无，或非敏感错误分类>
```
