# M06.5 Operation 重启恢复与人工恢复实施计划

> **执行要求：** 使用 `superpowers:test-driven-development` 严格执行红灯、绿灯、重构。本计划只覆盖 M06.5；完成最终模块门禁、独立只读审核、一个中文提交和 push 后必须停止。

**目标：** 把 M06.1–M06.4 的幂等 claim、数据库租约、Provider Adapter 和完成事件串成可关闭、可重启的持久化 Operation Runtime；进程重启后只查询原 provider job，供应商 404 安全落为 `expired` 且不得自动重启，额度暂停只能由用户动作恢复原 job，并补齐 M06 最终权威门禁。

**架构：** `OperationStartCoordinator` 类似 Java 的幂等 Application Service：先创建或回读 Operation，再通过数据库 start lease 保证同一时刻只有一个请求调用 Provider `start`，最后只持久化 provider job ID、请求摘要和轮询计划，不保存 Authorization 或请求正文。`OperationRecoveryRuntime` 类似带数据库任务表的调度 Service：启动后扫描到期轮询和未投递完成事件，按 owner/conversation 领取租约、查询原 job、持久化终态并恢复 Workflow；关闭时取消本进程后台循环，未完成租约由新进程在过期后接管。`OperationManualRecoveryService` 只允许额度暂停恢复同一 provider job；`expired` 明确返回“新 attempt 必需”，绝不重开终态。

**技术栈：** Python 3.12、Pydantic v2、SQLAlchemy async、SQLite、asyncio、pytest、Windows PowerShell 5.1/Pester 3.4、ruff。

## 全局约束

- 不修改两个长期 feature 分支，不建立切片子分支或额外 worktree。
- 不新增数据库表、字段、索引、migration、配置、HTTP API、现有 v2 Router 或 content-app 合同。
- 不调用真实图片、视频、PPT、视频分析、剪映、LLM 或其他付费 API；测试只使用 Memory/SQLite Repository、确定性 fake Provider 和 fake Graph resumer。
- Provider 原请求、Authorization、token、API key、secret、凭据、原始异常和完整 traceback 不得进入 Operation、Event、状态、日志或测试快照。
- 重启恢复只允许查询已持久化的 `provider_job_id`；没有 provider job ID 的 `created` Operation 只能由显式业务请求携带当前 Authorization 继续。
- 供应商 404 映射为 `expired` 终态并写唯一完成事件；人工动作只能要求创建 `attempt + 1` 的新 Operation，不能把原终态改回 `created/polling`。
- 额度暂停保持原 Operation 和 provider job ID，清除自动轮询计划；充值后的显式人工动作只重新安排原 job 查询，不再次调用 Provider `start`。
- M06.5 是模块最后切片，不是四阶段计划中的中间检查点；最终门禁绿色后写 `ready_for_integration`，不更新 `status/BOARD.md`，并提示开发者手动使用 9.10A 单槽集成话术。

---

### 任务一：冻结启动竞争、重启、404 和人工恢复合同

**文件：**

- 新增：`backend/tests/test_agent_runtime_operation_recovery.py`
- 修改：`scripts/agentization/tests/BranchAutomation.Tests.ps1`

- [x] **步骤 1：编写双实现 start lease 与 Authorization 不落库合同**

  对同一 Operation 并发发起两次 start，Memory/SQLite 都只能调用一次 fake Provider；两次请求回读同一内部 job。断言 Repository、事件和 SQLite 文件不包含 Authorization、原请求或测试凭据。

- [x] **步骤 2：编写轮询、额度暂停和人工恢复合同**

  到期 Operation 只能由一个 worker 查询原 provider job；`polling` 安排下一次查询，402/额度状态清除自动轮询计划。显式人工恢复重新安排同一 job，且 Provider `start` 调用数不增加。

- [x] **步骤 3：编写 404/expired 合同**

  Provider status 404 映射为安全 `expired` 快照和唯一完成事件，Graph 收到同一稳定事件 ID；后续后台扫描或人工动作不能重新调用 start、不能重开终态，只返回 `new_attempt_required`。

- [x] **步骤 4：编写 SQLite 进程重启与 shutdown 合同**

  第一 Runtime 在已领取轮询租约时关闭，第二 Runtime 复用同一 SQLite 文件，在租约过期后查询原 provider job、保存完成事件并恢复 Graph。关闭后不再扫描，重启前后 `provider_job_id` 不变。

- [x] **步骤 5：编写最终门禁计划合同**

  把 Pester 中原“M06 必须 fail-closed”改为精确断言 M06 权威 pytest、Ruff、分支自动化和 flag-off/旧流程回归清单；禁止回退到后端全量。

- [x] **步骤 6：运行新测试并确认 RED**

  预期 Python 测试因 M06.5 runtime、start/manual recovery 类型和 Repository 方法尚不存在而收集失败；Pester 因 M06 门禁仍主动拒绝而失败。

### 任务二：实现最小持久化启动与恢复 Runtime

**文件：**

- 新增：`backend/pixelflow/agent_runtime/jobs/recovery.py`
- 修改：`backend/pixelflow/agent_runtime/jobs/providers.py`
- 修改：`backend/pixelflow/agent_runtime/jobs/completion.py`
- 修改：`backend/pixelflow/agent_runtime/jobs/__init__.py`
- 修改：`backend/pixelflow/agent_runtime/persistence/repositories.py`

- [x] **步骤 1：扩展安全 404/expired 映射**

  只依据异常或 response 的 HTTP 404 映射固定 `provider_job_expired` reason/message；不读取或回显响应体、URL和异常文本。把 `expired` 纳入完成原子事务和定向完成事件投递。

- [x] **步骤 2：实现 start lease 与 provider job 绑定**

  Memory/SQL Repository 原子领取 `created` Operation、校验有效租约后写入 provider job ID 并迁移到 `polling`。`OperationStartCoordinator` 校验请求摘要，只有租约胜者调用 Provider start；竞争失败者只回读 Operation。

- [x] **步骤 3：实现全局恢复候选扫描**

  Repository 返回带内部 owner 的到期轮询候选和未投递完成候选；SQL 查询按时间/ID稳定排序并限制批量，Memory 与 SQL 语义一致。候选只在 Runtime 内部使用，业务查询仍必须传 user/conversation。

- [x] **步骤 4：实现轮询、暂停与人工恢复**

  Runtime 领取到期租约后只调用 Adapter `status(provider_job_id)`；`polling` 排下一次轮询，`paused_quota` 清除轮询计划，终态原子写事件并恢复 Graph。人工恢复对额度暂停重新排期，对 expired 返回 `new_attempt_required`。

- [x] **步骤 5：实现可关闭后台生命周期**

  `start()` 幂等创建一个后台循环；`aclose()` 停止新扫描并取消/等待当前本进程任务。取消时不伪造终态、不清除他人可接管的数据库租约；新 Runtime 只能在租约到期后继续。

- [x] **步骤 6：运行定向 GREEN**

  只实现上述失败合同需要的最小行为，不装配未交付的 M08–M11 真实 Workflow/Provider，不新增配置或 Router。

### 任务三：建立 M06 最终门禁、审核与交接

**文件：**

- 修改：`scripts/agentization/Invoke-AgentModuleGate.ps1`
- 修改：`README.md`
- 修改：`AGENTS.md`
- 修改：`docs/pixelflow-agent-skill-flow-latest-design.md`
- 修改：`docs/agentization/status/M06-status.md`
- 新增：`docs/agentization/test-reports/M06.5.md`
- 修改：本计划

- [x] **步骤 1：建立并运行 M06 最终权威门禁**

  门禁固定覆盖 operation coordinator、lease、Provider Adapter、completion、recovery、Event Outbox、M00合同、M01 Repository/migration、M02 graph namespace/recovery、Gateway flag-off/旧流程边界、Pester 分支自动化、Ruff 和 `git diff --check`；不得用后端全量替代。

- [x] **步骤 2：运行扩展回归与静态检查**

  运行全部 `test_agent_runtime_*`、变更 Python 路径 `ruff check`、`ruff format --check` 和 `git diff --check`，确认唯一 warning 仍为既有 LangChain pending deprecation。

- [x] **步骤 3：发起独立只读审核**

  审核重点为 start 只执行一次、start crash/lease 接管、重启只查询原 job、shutdown 取消语义、404/expired 不自动重启、额度人工恢复、终态/Event 原子性、owner 隔离、凭据不落库、扫描饥饿和 M08–M11 越界。

- [x] **步骤 4：处理 Critical/Important 并重新验证**

  每个有效问题先补失败合同再做最小修复；最终记录 Critical、Important、Minor 状态和独立复跑结果。

- [x] **步骤 5：完成中文状态与测试记录**

  勾选 M06.5，释放唯一写入权，写 `phase: ready_for_integration`；记录最终门禁、feature-flag-off、审核和未调用付费 API 证据。不更新 `status/BOARD.md`。

- [x] **步骤 6：执行中文工程门禁并提交推送**

  只暂存本切片文件，使用一个中文 commit；确认远端仍以 M06.4 为父后 push `codex/agent-0.8.4-m06-external-jobs`，核对远端 SHA、提示开发者复制 9.10A 话术并停止。
