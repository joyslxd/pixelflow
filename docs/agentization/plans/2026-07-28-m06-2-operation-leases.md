# M06.2 Operation 数据库租约实施计划

> **执行要求：** 使用 `superpowers:test-driven-development` 严格执行红灯、绿灯、重构。本计划只覆盖 M06.2；完成独立只读审核、一个中文提交和 push 后必须停止。

**目标：** 为已进入 `polling` 的持久化 External Job 增加跨 worker 互斥的数据库租约、心跳续租、`next_poll_at` 调度和过期接管，保证同一时刻只有一个 worker 可以轮询供应商原任务。

**架构：** 新增 `OperationLeaseCoordinator` 作为类似 Java Service 的领域入口，按当前用户、对话和内部 `job_id` 调用 Repository 原子方法。Memory 实现用进程内锁模拟合同；SQL 实现使用行锁语义，并在 SQLite 下复用 `BEGIN IMMEDIATE` 覆盖不同 Engine 的竞争。租约领取只允许有供应商任务引用、已到轮询时间且状态为 `polling` 的 operation；旧 worker 的租约过期后不能心跳或排期，新 worker 可在边界时刻接管。

**技术栈：** Python 3.12、Pydantic v2、SQLAlchemy 2 async、SQLite/aiosqlite、pytest、ruff。

## 全局约束

- 不修改两个长期 feature 分支，不建立切片子分支或额外 worktree。
- 不新增表、字段、索引、migration、配置或 HTTP API；复用 M01 已落库的 `next_poll_at`、`lease_owner`、`lease_expires_at`。
- 不实现 M06.3 的 Provider Adapter 或供应商 start/status，不调用真实付费 API。
- 不实现 M06.4 的完成事件、Workflow resume、终态 claim 或 crash window。
- 不实现 M06.5 的 shutdown、restart、404、timeout 或人工恢复。
- owner 或 conversation 不匹配时返回不可领取，不泄露 operation 是否存在。
- 所有新增或修改的人工注释、计划、测试报告和 commit 使用中文主体语义。

---

### 任务一：冻结 lease、heartbeat 与轮询调度合同

**文件：**

- 新增：`backend/tests/test_agent_runtime_operation_leases.py`

- [x] **步骤 1：编写到期领取和 owner 隔离合同**

`polling + provider_job_id + next_poll_at <= now` 才可领取；`created`、终态、缺少 Provider 引用、尚未到期、其他 owner 或其他 conversation 均不可领取。

- [x] **步骤 2：编写双 worker 竞争合同**

Memory 使用两个 Coordinator 并发竞争；SQL 使用指向同一 SQLite 文件的两个独立 Engine/Repository 并发竞争。断言只有一个 worker 获得租约，数据库只保留胜出者。

- [x] **步骤 3：编写幂等重领与 heartbeat 合同**

同一 worker 在有效租约内重复领取只回读原租约，不偷偷延长；合法 heartbeat 只能延长有效租约，错误 worker、已过期租约或非延长时间均失败且不改库。

- [x] **步骤 4：编写 `next_poll_at` 与过期接管合同**

持租约 worker 完成一次轮询后原子写入未来 `next_poll_at` 并释放租约；到期前不能再次领取。租约在等于过期边界时允许新 worker 接管，旧 worker随后不能 heartbeat 或排期。

- [x] **步骤 5：运行新测试并确认 RED**

```powershell
& E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe `
  -m pytest tests/test_agent_runtime_operation_leases.py -q
```

### 任务二：实现 Repository 原子租约合同

**文件：**

- 修改：`backend/pixelflow/agent_runtime/persistence/repositories.py`

- [x] **步骤 1：扩展 Repository Port**

为 Memory/SQL 统一增加领取租约、心跳和安排下次轮询三个原子方法；所有方法同时约束 `user_id + conversation_id + job_id`。

- [x] **步骤 2：实现 Memory 合同**

使用 operation 专用锁串行修改；每次返回隔离副本，不允许失败调用留下半更新。

- [x] **步骤 3：实现 SQL 合同**

在写事务内锁定目标 operation 行，读取并判断状态、轮询时间和现有租约后再更新；SQLite 使用仓库既有数据库写锁覆盖跨 Engine/进程竞争。

- [x] **步骤 4：运行定向测试并确认 Repository GREEN**

### 任务三：实现领域 Coordinator 并重构

**文件：**

- 新增：`backend/pixelflow/agent_runtime/jobs/leases.py`
- 修改：`backend/pixelflow/agent_runtime/jobs/__init__.py`

- [x] **步骤 1：实现 `OperationLeaseCoordinator`**

固定用户与对话作用域，对外暴露 `claim()`、`heartbeat()` 和 `schedule_next_poll()`；Repository 返回 `None` 时保持 fail-closed，不猜测任务状态或自动重启。

- [x] **步骤 2：收紧租约参数**

租约截止时间必须晚于当前时刻；heartbeat 必须严格延长现有租约；下次轮询时间必须晚于释放时刻；所有时间统一转为 UTC。

- [x] **步骤 3：运行定向和 M06.1 回归**

验证新租约逻辑不改变 operation 身份、重复 start、状态机和敏感请求不落库合同。

### 任务四：独立审核、验证和交接

**文件：**

- 修改：`docs/agentization/status/M06-status.md`
- 新增：`docs/agentization/test-reports/M06.2.md`
- 修改：`docs/agentization/plans/2026-07-28-m06-2-operation-leases.md`
- 修改：`README.md`
- 修改：`AGENTS.md`
- 修改：`docs/pixelflow-agent-skill-flow-latest-design.md`

- [x] **步骤 1：运行 M06.2 范围回归与静态检查**

覆盖新 lease 合同、M06.1 operation 合同、M00 冻结合同、M01 Repository/migration、全部 Agent Runtime 扩展回归、Ruff、格式与 `git diff --check`。

- [x] **步骤 2：发起独立只读审核**

审核重点为双 worker 原子性、边界时刻、旧 worker 失效、owner/conversation 隔离、SQLite 跨 Engine 竞争、敏感字段和 M06.3+ 越界。

- [x] **步骤 3：处理 Critical/Important 并重新验证**

每个有效问题先补失败合同再做最小修复；最终记录 Critical、Important、Minor 状态。

- [x] **步骤 4：完成中文状态与测试记录**

勾选 M06.2，释放唯一写入权，下一切片设为 M06.3；phase 保持 `in_progress`，不得写任何 integration ready 状态。

- [x] **步骤 5：执行中文工程门禁并提交推送**

仅暂存本切片文件，使用一个中文 commit；push `codex/agent-0.8.4-m06-external-jobs` 后核对远端 SHA 并停止。
