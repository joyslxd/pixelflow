# PixelFlow 真实图片与视频全流程 Verification Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to drive this plan, superpowers:verification-before-completion before every success claim, superpowers:systematic-debugging for any failure, and agent-browser for the real browser flows. Read the complete agent-browser `SKILL.md` immediately before browser actions. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在完成 PowerMem、Plan 回退和 Seedance Skill 改造后，真实启动本地前后端，使用测试环境 content-app 与 PowerMem，分别跑通一条完整图片流程和一条完整视频流程，并验证所有本次修改点。

**Architecture:** 自动化测试先覆盖业务类、Controller 路由、前端静态合同和构建；随后本地 FastAPI Gateway 使用 `config.dev.yml`，React 使用 `.env.test` 代理本地 Gateway 和测试 content-app。浏览器测试使用用户临时授权，不把 token 写入仓库、报告或日志；后端日志、conversation trace 和最终卡片共同作为验收证据。

**Tech Stack:** uv、pytest、ruff、Node.js、pnpm、Vite、FastAPI、React、agent-browser、content-app 测试环境、PowerMem 测试环境。

## Global Constraints

- 测试必须调用真实 content-app 生成接口，最终得到至少 1 张图片和 1 个可播放视频。
- 图片和视频都从采集需求开始，经过表单、创意方向、Plan 审核、生成阶段，直到用户确认结束。
- 图片和视频都验证 `v1 -> 修改为 v2 -> 回退 v1（历史仍为 2 条）-> 再修改为 v3`。
- 视频优先选择实时配置中已启用的非 2.0 Seedance 型号；若测试环境只有 2.0，则使用实际可用型号，并以两个非同型号的自动化测试覆盖家族级适配。
- 视频使用实时配置允许的最短总时长，优先 4 秒单分镜，以减少成本但仍走场景包、参考图、场景视频和 merge job 全链路。
- 不在文件、Git diff、测试报告、截图、命令输出或日志中保存/展示 Authorization、JWT、PowerMem key 或供应商 key。
- 每次异步 job 启动后先确认 conversation context 已保存 job id，再轮询；刷新或重新进入对话不能重复调用 `/start`。
- content-app 402/额度不足、token 401/过期属于外部阻塞，不能伪造通过；保留可恢复上下文并向用户请求新的授权或额度后继续。
- 发现本次修改引入的问题时，先定位根因、写失败测试、最小修复、重跑目标测试，再从原对话可恢复点继续真实流程。
- 新增测试说明、报告、注释和提交信息使用中文；程序标识符保持英文。
- 除同一代码块内明确切换目录外，所有 `Run` 代码块都从仓库根目录开始执行；跨代码块不依赖 PowerShell 局部变量。

---

### Task 1: 准备可复现的本地测试环境

**Files:**
- Read: `backend/pyproject.toml`
- Read: `backend/config.dev.yml`
- Read: `web/package.json`
- Read: `web/.env.test`
- Read: `web/vite.config.ts`
- Do not modify: any config file containing credentials

**Interfaces:**
- Produces: 固定依赖、本地 8001 Gateway、本地 5273 前端的可启动环境。

- [ ] **Step 1: 确认仓库状态和当前提交**

Run:

```powershell
git status --short
git log -1 --oneline
```

Expected: 只有本实施计划允许的修改；记录当前提交号用于最终报告。

- [ ] **Step 2: 检查端口，不终止不明进程**

Run:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8001,5273 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

Expected: 端口空闲。若已占用，先用 `Get-CimInstance Win32_Process -Filter "ProcessId=<pid>"` 核实是否为本仓库本轮启动的服务；不擅自停止其他项目进程。

- [ ] **Step 3: 安装固定依赖**

Run:

```powershell
py -3.13 -m uv --version *> $null
if ($LASTEXITCODE -ne 0) {
  py -3.13 -m pip install --user uv
}
Set-Location backend
py -3.13 -m uv sync --group dev
Set-Location ..\web
corepack pnpm install --frozen-lockfile
```

Expected: 后端和前端依赖安装成功，`pnpm-lock.yaml` 不发生变化。

- [ ] **Step 4: 验证 dev 配置加载但不输出密钥**

Run:

```powershell
Set-Location backend
$env:PIXELFLOW_CONFIG_ENV = "dev"
@'
from app.gateway.profile_config import load_profile_config
from pixelflow.memory import load_power_mem_config_from_env

load_profile_config()
config = load_power_mem_config_from_env()
print({
    "enabled": config.available,
    "write_enabled": config.write_enabled,
    "timeout_seconds": config.timeout_seconds,
    "record_timeout_seconds": config.record_timeout_seconds,
    "base_url_configured": bool(config.base_url),
    "api_key_configured": bool(config.api_key),
})
'@ | py -3.13 -m uv run python -
```

Expected: 只输出布尔值和超时，不输出 URL 中的敏感查询参数或任何 key。

---

### Task 2: 运行本次修改点和全仓自动化验证

**Files:**
- Test: `backend/tests/test_powermem_service.py`
- Test: `backend/tests/test_creative_plan_markdown.py`
- Test: `backend/tests/test_pixelflow_planning_router.py`
- Test: `backend/tests/test_seedance_prompt_skill.py`
- Test: `backend/tests/test_video_scene_packages.py`
- Test: `web/tests/mainFlowContract.test.mjs`

**Interfaces:**
- Produces: 修改点的确定性测试证据和全仓回归基线。

- [ ] **Step 1: 运行四个改造点的后端目标测试**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest `
  tests/test_powermem_service.py `
  tests/test_creative_plan_markdown.py `
  tests/test_pixelflow_planning_router.py `
  tests/test_seedance_prompt_skill.py `
  tests/test_video_scene_packages.py `
  -q
py -3.13 -m uv run python skills/public/borgrise-creative-assistant-v2/tests/test_skill_structure.py
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行后端全量测试和静态检查**

Run:

```powershell
Set-Location backend
py -3.13 -m uv run pytest -q
py -3.13 -m uv run ruff check .
```

Expected: 全部 PASS。若仓库存在与本次无关且可稳定复现的既有失败，保存测试名称和原始基线证据，不借机修改无关业务；与本次路径有关的失败必须修复。

- [ ] **Step 3: 运行前端高风险合同测试**

Run:

```powershell
Set-Location web
node --test tests/mainFlowContract.test.mjs
node --test tests/videoResultGrid.test.mjs
node --test tests/videoSceneUiContract.test.mjs
```

Expected: 全部 PASS。

- [ ] **Step 4: 运行 TypeScript 和 test 配置构建**

Run:

```powershell
Set-Location web
corepack pnpm build-test
```

Expected: TypeScript 无错误，Vite test mode 构建成功。

- [ ] **Step 5: 检查自动化测试后工作树**

Run:

```powershell
git status --short
git diff --check
```

Expected: 依赖安装、测试和构建没有修改受控源码或 lockfile；只有预期实现/测试/文档差异。

---

### Task 3: 启动服务并对真实 PowerMem 做受控并发探针

**Files:**
- Runtime log only: `$env:TEMP/pixelflow-e2e-gateway.out.log`
- Runtime log only: `$env:TEMP/pixelflow-e2e-gateway.err.log`
- Runtime log only: `$env:TEMP/pixelflow-e2e-web.out.log`
- Runtime log only: `$env:TEMP/pixelflow-e2e-web.err.log`

**Interfaces:**
- Consumes: 测试环境 PowerMem `/system/health`、`/memories/search`、`/memories`。
- Produces: 不出现 `OB_SESSION_ENTRY_EXIST` 的真实并发证据和可供浏览器访问的前后端。

- [ ] **Step 1: 启动本地 Gateway，记录本轮唯一 PID**

Run:

```powershell
if (Get-NetTCPConnection -State Listen -LocalPort 8001,5273 -ErrorAction SilentlyContinue) {
  throw "8001 或 5273 已被占用，不能启动本轮 E2E 服务"
}
$env:PIXELFLOW_CONFIG_ENV = "dev"
$pythonLauncher = (Get-Command py.exe).Source
$backendOut = Join-Path $env:TEMP "pixelflow-e2e-gateway.out.log"
$backendErr = Join-Path $env:TEMP "pixelflow-e2e-gateway.err.log"
$backendProcess = Start-Process `
  -FilePath $pythonLauncher `
  -ArgumentList @("-3.13", "-m", "uv", "run", "python", "-m", "app.gateway.run") `
  -WorkingDirectory (Resolve-Path "backend") `
  -WindowStyle Hidden `
  -RedirectStandardOutput $backendOut `
  -RedirectStandardError $backendErr `
  -PassThru
@{ launcher_pid = $backendProcess.Id; stdout = $backendOut; stderr = $backendErr } | ConvertTo-Json -Compress
```

Expected: 输出 launcher PID 和固定 temp 日志路径；执行代理记录该输出，但不依赖跨 shell 环境变量。

- [ ] **Step 2: 等待 Gateway 健康，不做超过 60 秒的阻塞等待**

Run:

```powershell
$deadline = (Get-Date).AddSeconds(60)
do {
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8001/health" -TimeoutSec 3
    break
  } catch {
    if ((Get-Date) -ge $deadline) { throw }
    Start-Sleep -Seconds 2
  }
} while ($true)
$listener = Get-NetTCPConnection -State Listen -LocalPort 8001 -ErrorAction Stop | Select-Object -First 1
@{
  health = $health
  backend_listener_pid = $listener.OwningProcess
} | ConvertTo-Json -Compress -Depth 5
```

Expected: `/health` 成功。若失败，只读取本轮 temp 日志定位，不打印任何配置密钥。

- [ ] **Step 3: 运行真实 PowerMem record/search/health 并发探针**

该探针用唯一测试 user id 写入 1 条 `infer=False` 的 `experience` 探针记忆，并与检索、健康检查并发；不写真实用户偏好。完整输出留在 temp 文件，控制台只打印摘要：

```powershell
Set-Location backend
$probeOutput = @'
import asyncio
import uuid

from app.gateway.profile_config import load_profile_config
from pixelflow.memory import PowerMemService, load_power_mem_config_from_env


async def main() -> None:
    load_profile_config()
    service = PowerMemService(load_power_mem_config_from_env())
    try:
        probe_user_id = f"codex-powermem-probe-{uuid.uuid4().hex}"
        calls = [
            service.record(
                user_id=probe_user_id,
                content="PixelFlow PowerMem 单进程串行化真实回归探针",
                category="experience",
                source_agent="verification_agent",
                metadata={"source": "codex_e2e_probe"},
                memory_type="experience",
                infer=False,
            )
        ]
        for _index in range(3):
            calls.append(service.health())
            calls.append(
                service.search(
                    user_id=probe_user_id,
                    query="PixelFlow PowerMem 并发回归探针",
                    categories=["experience"],
                    source_agent="verification_agent",
                    limit=1,
                )
            )
        results = await asyncio.gather(*calls, return_exceptions=True)
        post_health = await service.health()
        print({
            "calls": len(results),
            "exceptions": sum(isinstance(item, Exception) for item in results),
            "record_ok": results[0] is True,
            "post_status": post_health.get("status", "ok"),
        })
    finally:
        await service.aclose()


asyncio.run(main())
'@ | py -3.13 -m uv run python - 2>&1
@{
  ob_session_entry_exist = @($probeOutput | Select-String -SimpleMatch "OB_SESSION_ENTRY_EXIST").Count
  probe_summary = @($probeOutput | Select-Object -Last 1)[0]
} | ConvertTo-Json -Compress
```

Expected: `ob_session_entry_exist=0`、`exceptions=0`、`record_ok=True`；队列中个别 fail-open timeout 可以发生，但最终 post health 必须可达。

- [ ] **Step 4: 启动 test mode 前端，记录本轮唯一 PID**

Run:

```powershell
$corepack = (Get-Command corepack.cmd).Source
$webOut = Join-Path $env:TEMP "pixelflow-e2e-web.out.log"
$webErr = Join-Path $env:TEMP "pixelflow-e2e-web.err.log"
$webProcess = Start-Process `
  -FilePath $corepack `
  -ArgumentList @("pnpm", "dev:test", "--", "--host", "127.0.0.1", "--port", "5273") `
  -WorkingDirectory (Resolve-Path "web") `
  -WindowStyle Hidden `
  -RedirectStandardOutput $webOut `
  -RedirectStandardError $webErr `
  -PassThru
@{ launcher_pid = $webProcess.Id; stdout = $webOut; stderr = $webErr } | ConvertTo-Json -Compress
```

- [ ] **Step 5: 确认前端页面和 API 代理可达**

Run:

```powershell
$deadline = (Get-Date).AddSeconds(60)
do {
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:5273/agentfrontend/" -TimeoutSec 3
    break
  } catch {
    if ((Get-Date) -ge $deadline) { throw }
    Start-Sleep -Seconds 2
  }
} while ($true)
$listener = Get-NetTCPConnection -State Listen -LocalPort 5273 -ErrorAction Stop | Select-Object -First 1
@{ status_code = $response.StatusCode; web_listener_pid = $listener.OwningProcess } | ConvertTo-Json -Compress
```

Expected: `200`。

---

### Task 4: 用真实浏览器跑完整图片流程和版本回退

**Files:**
- Read runtime UI only
- Optional screenshot evidence: outside Git worktree or `docs/superpowers/verification/assets/`

**Interfaces:**
- Consumes: 用户临时 content-app Authorization、图片生成 API、对话持久化 API。
- Produces: 最终图片、Plan 版本序列、刷新恢复和不重复 job 的证据。

- [ ] **Step 1: 读取并启用 agent-browser Skill**

执行代理必须完整读取：

```text
C:/Users/11843/.agents/skills/agent-browser/SKILL.md
```

在 commentary 中说明：使用该 Skill 驱动本地页面、检查浏览器控制台和保存截图；不得在工具输出中回显 token。

- [ ] **Step 2: 安全保存并验证临时 Authorization**

打开：

```text
http://127.0.0.1:5273/agentfrontend/#/auth-token
```

将用户提供的 Authorization 填入 textarea，点击“保存并验证”。

Expected: 显示当前用户验证通过。若 401/过期，立即停止真实生成步骤并向用户索取新 token；不把旧 token 写入任何报告或命令。

- [ ] **Step 3: 从采集开始创建图片流程**

回到工作台，新建独立对话并输入：

```text
为一款蓝色防泼水通勤背包生成1张9:16电商宣传图，真实摄影风，突出大容量和防泼水，面向25-35岁通勤人群，用于社媒发布。
```

在表单中确认：图片目标保留“蓝色防泼水通勤背包宣传图”、数量 1、用途社媒、风格真实摄影、比例 9:16。手动选择任一创意方向。

Expected: 生成 `plan.md v1`，卡片显示版本历史和“继续修改/同意方案”等操作。

- [ ] **Step 4: 验证 v1 -> v2 -> 回退 v1 -> v3**

依次操作：

1. 点击“继续修改”，提交：`把办公室场景改成雨天通勤场景，保持9:16和真实摄影风。`
2. 确认得到 v2，历史只有 v1、v2。
3. 从历史版本选择 v1 并回退。
4. 确认当前显示 v1，历史仍只有 v1、v2，没有新 v3。
5. 刷新浏览器或离开后重新进入同一 `#/c/<conversation_id>`。
6. 确认仍激活 v1、历史仍为两条。
7. 再次“继续修改”，提交：`加强防泼水实测细节和大容量收纳证据，不改变其他规格。`
8. 确认得到 v3，历史为 v1、v2、v3。

保存不包含 token 的版本卡片截图作为证据。

- [ ] **Step 5: 同意 v3 并等待真实图片完成**

点击“同意方案”，确认前端只调用一次 `/agent/flows/image/generate/start`。每 30–60 秒检查 job；持续等待期间向用户发送简短进度更新，不能做超过 60 秒的无反馈阻塞等待。

Expected:

- 对话 context 有 `pendingImageJob`/`pending_image_job`，刷新后只轮询原 `job_id`。
- 最终 `image_result` 卡片至少有 1 个可打开的真实图片 URL。
- 主动选择“满意，结束”，确认流程状态完成，不用 60 秒自动确认代替人工验收。
- 后端日志没有 `OB_SESSION_ENTRY_EXIST`。

- [ ] **Step 6: 用 conversation trace 核对真实供应商调用**

若当前用户有管理员权限，打开：

```text
http://127.0.0.1:5273/agentfrontend/#/trace/<conversation_id>
```

Expected: 能看到图片生成调用对应 `/api/picture/...` 端点和成功结果。只记录端点名、状态和 job id 是否复用，不复制原始 prompt、Authorization 或完整供应商响应到报告。

---

### Task 5: 用真实浏览器跑完整视频流程和 Seedance 模型适配

**Files:**
- Read runtime UI only
- Optional screenshot evidence: outside Git worktree or `docs/superpowers/verification/assets/`

**Interfaces:**
- Consumes: 视频模型实时配置、图片参考资产接口、场景视频接口、merge job。
- Produces: 最终可播放视频、真实 Seedance 型号透传、版本回退和完整生成链路证据。

- [ ] **Step 1: 新建视频对话并完成采集表单**

输入：

```text
为蓝色防泼水通勤背包生成一条最短可用时长的9:16电商短视频，真实UGC摄影风，突出防泼水和大容量，面向25-35岁通勤人群，用于社媒转化。
```

在视频表单中：

- 自定义选择实时允许的最短总时长，优先 4 秒。
- 画幅 9:16。
- 视频模型优先选择实时列表中已启用的非 2.0 Seedance；若不存在，选择实际可用的系统推荐 Seedance。
- 图片模型使用实时可用默认值。
- 保留真实 UGC 风格、社媒用途和转化目标。

记录模型名称，不记录任何供应商密钥。

- [ ] **Step 2: 再次验证 Plan 直接回退合同**

依次操作：

1. 选择创意方向并得到 v1。
2. 修改：`让开场直接展示雨水泼在背包表面的真实测试。`，得到 v2。
3. 回退 v1，确认历史仍为 v1、v2。
4. 刷新并重新进入对话，确认激活版本和合同中的 `video_model`、时长、9:16 未丢失。
5. 修改：`保留雨天通勤语境，并在结尾用拉链打开动作证明大容量。`，得到 v3。

Expected: v3 沿用用户确认的真实 `video_model`，历史没有重复副本。

- [ ] **Step 3: 同意 v3 并完成场景包与参考资产阶段**

点击“同意方案”，确认只启动一次 `/agent/flows/video/prepare-scene-packages/start` 并立即保存 `pendingScenePackageJob`。等待 job 完成。

Expected:

- 4 秒总时长得到 1 个 4 秒分镜；如果实时合同把时长修正为其他允许值，则各分镜 4–15 秒且总和精确等于合同。
- 角色、场景、商品分别位于 `characters`、`scenes`、`props`。
- 镜头描述使用秒级时间码和声明过的 `@asset_id`，图片引用不超过 9 张。
- 参考资产图片真实生成并可预览。

- [ ] **Step 4: 确认场景包并生成最终视频**

点击“确认并生成视频”，确认只启动一次 `/agent/flows/video/generate-scenes/start`。等待场景视频全部成功，再确认 `/agent/flows/video/merge/start`：单分镜允许 merge job 直接复用该视频，不要求 content-app 再做 ffmpeg 合并。

Expected:

- 场景 job 返回 1 个成功 `scene_video`，没有 `failed_scenes`。
- merge job 返回最终视频 URL。
- `video_result` 卡片中的视频可播放，点击“无意见，结束”后流程完成。
- 原场景包卡片回填已生成视频，查看分镜时优先预览视频。

- [ ] **Step 5: 核对实际模型与供应商调用**

通过管理员 trace 或本轮后端日志确认：

- 场景包 Prompt 中的当前模型是表单确认的真实 `video_model`，不是硬编码 `seedance-2.0`。
- 实际调用 `/api/video/text-to-video`、`/api/video/image-to-video`、`/api/video/two-image-to-video` 或 `/api/video/reference-mode-video` 中与本场景匹配的端点。
- 单场景 merge job 没有错误调用 content-app `/api/video/merge`；多场景时才调用该端点。
- 全流程没有 `OB_SESSION_ENTRY_EXIST`。

只在报告中记录模型名、端点名和成功状态，不保存完整请求体或响应体。

---

### Task 6: 故障修复循环、最终证据和安全清理

**Files:**
- Create after verification: `docs/superpowers/verification/2026-07-11-powermem-plan-seedance-e2e.md`
- Do not include: token、keys、完整 prompt、完整异常堆栈、临时日志路径中的用户目录信息

**Interfaces:**
- Produces: 可审计的 PASS/FAIL 报告、干净工作树和已停止的本轮服务。

- [ ] **Step 1: 任一流程失败时执行系统化诊断循环**

每次失败按顺序执行：

1. 保存失败步骤、浏览器截图、console error、HTTP 状态、Python job 状态。
2. 只检查本轮 Gateway/Web temp 日志和当前 conversation trace。
3. 判断是 PixelFlow 代码、content-app 业务失败、额度、网络、token 还是 PowerMem fail-open。
4. 若为代码问题，先增加可稳定复现的失败测试，再最小修改实现。
5. 重跑对应目标测试和 build；重启本轮服务。
6. 从已持久化 conversation/job 恢复，不重复启动已计费任务。
7. 继续直到图片和视频 Completion Gate 全部满足。

若是 401 或 402，保留 job/context 并向用户请求新 token 或补充额度；这不是可以用代码掩盖的成功。

- [ ] **Step 2: 汇总 PowerMem 错误计数而不打印原始日志**

Run:

```powershell
$logs = @(
  (Join-Path $env:TEMP "pixelflow-e2e-gateway.out.log"),
  (Join-Path $env:TEMP "pixelflow-e2e-gateway.err.log")
)
@{
  ob_session_entry_exist = @(Select-String -LiteralPath $logs -SimpleMatch "OB_SESSION_ENTRY_EXIST" -ErrorAction SilentlyContinue).Count
  unhandled_tracebacks = @(Select-String -LiteralPath $logs -SimpleMatch "Traceback (most recent call last)" -ErrorAction SilentlyContinue).Count
} | ConvertTo-Json -Compress
```

Expected: 两项均为 0。已被预期测试捕获的异常不得出现在本轮运行日志。

- [ ] **Step 3: 用 apply_patch 创建中文验收报告**

报告只记录：

```markdown
# 2026-07-11 PixelFlow 可靠性真实流程验收

- 验证提交：`<commit>`
- PowerMem 并发探针：PASS/FAIL；OB_SESSION_ENTRY_EXIST 次数
- 图片流程：采集、表单、方向、v1/v2/回退v1/v3、生成、最终确认
- 图片产物：数量、可访问/可预览状态，不复制带签名的完整 URL
- 视频流程：采集、表单、实际模型、方向、v1/v2/回退v1/v3、场景包、资产、场景视频、merge、最终确认
- 视频产物：数量、可播放状态，不复制带签名的完整 URL
- conversation/job 恢复：刷新后未重复启动
- 自动化测试与构建：命令、通过数、失败数
- 遗留问题：无，或明确的外部阻塞和恢复点
```

必须使用 `apply_patch` 创建报告，不用 shell 重定向写仓库文件。

- [ ] **Step 4: 清除浏览器 Authorization**

回到 `#/auth-token` 点击“清除”，或按 agent-browser Skill 的安全方式清除 `localStorage.Authorization`。确认页面不再显示 token；关闭本轮浏览器会话。

- [ ] **Step 5: 核验命令行后只停止本轮两个监听进程**

Run:

```powershell
$listeners = Get-NetTCPConnection -State Listen -LocalPort 8001,5273 -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
  $commandLine = [string]$process.CommandLine
  $isExpected = if ($listener.LocalPort -eq 8001) {
    $commandLine -match "app\.gateway\.run|uvicorn"
  } else {
    $commandLine -match "vite" -and $commandLine -match "5273"
  }
  if (-not $isExpected) {
    throw "端口 $($listener.LocalPort) 的监听进程不是本轮 PixelFlow 服务，拒绝停止"
  }
  Stop-Process -Id $listener.OwningProcess -ErrorAction Stop
}
Start-Sleep -Seconds 2
$remainingListeners = Get-NetTCPConnection -State Listen -LocalPort 8001,5273 -ErrorAction SilentlyContinue
if ($remainingListeners) {
  $remainingListeners | Select-Object LocalPort,OwningProcess
  throw "本轮服务端口仍被监听；不得停止未核验归属的进程"
}
```

Expected: 只停止命令行与端口均匹配的两个监听进程，且两个端口释放；不要按进程名批量停止 Python、Node、Java 或其他服务。

- [ ] **Step 6: 最终验证和敏感信息检查**

Run:

```powershell
git diff --check
git status --short
git diff --name-only
git log --oneline -10
```

对本次新增/修改文件执行敏感信息扫描，只报告命中数量，不打印匹配内容：

```powershell
$changedFiles = @(git diff --name-only HEAD~10..HEAD) + @(git diff --name-only)
$changedFiles = $changedFiles | Sort-Object -Unique | Where-Object { Test-Path -LiteralPath $_ }
$sensitiveCount = @(
  Select-String -LiteralPath $changedFiles -Pattern "Bearer\s+eyJ|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+" -ErrorAction SilentlyContinue
).Count
@{ sensitive_matches = $sensitiveCount } | ConvertTo-Json -Compress
```

Expected: `sensitive_matches=0`，工作树只包含预期报告或尚待提交的实现文件。

- [ ] **Step 7: 提交验收报告**

`docs/*` 被 ignore 时强制加入这一个明确文件：

```powershell
git add -f docs/superpowers/verification/2026-07-11-powermem-plan-seedance-e2e.md
git commit -m "test: 记录图片与视频真实全流程验收"
```

## Completion Gate

- [ ] PowerMem 目标测试、真实并发探针和两条业务流程日志中均没有 `OB_SESSION_ENTRY_EXIST`。
- [ ] 图片 Plan 回退不新增版本，刷新后保持激活 v1，再修改生成 v3。
- [ ] 视频 Plan 回退不新增版本，创作合同和实际 `video_model` 刷新后不丢失，再修改生成 v3。
- [ ] Seedance 场景包 Prompt 使用实际模型；非 2.0 型号可用时已真实验证，否则已有至少两个不同型号的自动化合同测试。
- [ ] 最终得到至少 1 张可预览图片和 1 个可播放视频，并明确结束两个流程。
- [ ] 异步 job 在刷新/切换对话后没有重复启动或重复计费。
- [ ] 后端目标测试、全量测试、ruff、前端合同测试和 `build-test` 均有新鲜通过证据。
- [ ] token 已从浏览器清除，Git diff 和报告没有敏感信息。
- [ ] 本轮启动的 Gateway 和 Web 进程已按 PID 停止。
