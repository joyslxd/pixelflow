# M06.1 Operation 幂等与状态机实施计划

> **执行要求：** 使用 `superpowers:test-driven-development` 严格执行红灯、绿灯、重构。本计划只覆盖 M06.1；完成独立只读审核、一个中文提交和 push 后必须停止。

**目标：** 为持久化 External Job 建立可复用的 operation 幂等身份、规范请求哈希、显式状态迁移表和并发安全的首次 claim，使相同 start 重试只得到一个内部 job。

**架构：** 新增 `agent_runtime.jobs` 领域层，职责类似 Java 中位于 Controller 与 Repository 之间的幂等 Service。领域层只持久化请求哈希，不保存供应商请求体或 Authorization；底层 Repository 增加按 owner 查询幂等键的只读能力。Memory 与 SQL 保持相同合同，SQL 唯一约束负责解决并发插入竞争，失败方回读胜出的 operation。

**技术栈：** Python 3.12、Pydantic v2、SQLAlchemy 2 async、SQLite/aiosqlite、pytest、ruff。

## 全局约束

- 不修改两个长期 feature 分支，不建立切片子分支或额外 worktree。
- 不新增表或 migration；复用 M01 已落库的 `request_hash`、`idempotency_key` 与唯一约束。
- 不实现 M06.2 的 lease、heartbeat、`next_poll_at` 或过期接管。
- 不实现 M06.3 的 Provider Adapter、M06.4 的完成事件/工作流恢复、M06.5 的 shutdown/restart 恢复。
- operation 幂等身份固定为 `workflow_id + stage + stage_version + attempt`；请求体变化只能改变 `request_hash`，不得绕过同一 attempt 的冲突检测。
- 只持久化规范 SHA-256，不持久化供应商原始请求、Authorization、token 或密钥。
- 所有新增或修改的人工注释、计划、测试报告和 commit 使用中文主体语义。

---

### 任务一：冻结身份、哈希和状态迁移合同

**文件：**

- 新增：`backend/tests/test_agent_runtime_operation_coordinator.py`

- [x] **步骤 1：编写规范请求哈希合同**

相同 JSON 语义即使对象键顺序不同也必须得到相同 `sha256:<hex>`；值变化必须改变哈希；非 JSON 值、NaN 和 Infinity 必须 fail-closed。

- [x] **步骤 2：编写 operation 幂等键合同**

同一 `workflow_id/stage/stage_version/attempt` 产生稳定键；任一身份字段变化都产生不同键；空标识和非法正整数必须拒绝。

- [x] **步骤 3：编写显式状态迁移表合同**

`created` 可进入 `polling` 或任一终态，`polling` 可进入任一终态；同状态重放为幂等；终态不得重开或互相切换。

- [x] **步骤 4：编写 Memory/SQL 重复 claim 合同**

顺序和并发重复 start 均返回同一 `job_id`，Repository 中只存在一条记录；相同幂等键但请求哈希、conversation 或身份不同必须抛 `OperationConflictError`。

- [x] **步骤 5：编写敏感请求不落库合同**

用包含 Authorization 的供应商请求计算哈希后 claim，断言 operation 记录和持久化模型中只存在哈希，不包含原始凭据。

- [x] **步骤 6：运行新测试并确认 RED**

```powershell
& E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe `
  -m pytest tests/test_agent_runtime_operation_coordinator.py -q
```

### 任务二：实现 operation 身份与状态机

**文件：**

- 新增：`backend/pixelflow/agent_runtime/jobs/__init__.py`
- 新增：`backend/pixelflow/agent_runtime/jobs/identity.py`
- 新增：`backend/pixelflow/agent_runtime/jobs/state_machine.py`

- [x] **步骤 1：实现 JSON 规范化 SHA-256**

使用稳定键排序、紧凑分隔符和 UTF-8 编码；拒绝 JSON 合同外对象和非有限浮点数。

- [x] **步骤 2：实现稳定幂等键**

校验非空 workflow/stage 与正整数版本/attempt，再构造包含四个身份字段的可读稳定键。

- [x] **步骤 3：实现显式迁移表**

统一暴露 `ensure_operation_transition()`；非法迁移抛领域冲突异常，供后续 M06 切片复用。

### 任务三：实现持久化首次 claim

**文件：**

- 修改：`backend/pixelflow/agent_runtime/persistence/repositories.py`
- 新增：`backend/pixelflow/agent_runtime/jobs/coordinator.py`

- [x] **步骤 1：扩展 Repository 查询合同**

Memory/SQL 新增按 `user_id + idempotency_key` 查询 operation；owner 不匹配返回 `None`，不得泄露其他用户记录。

- [x] **步骤 2：实现 `OperationCoordinator.claim()`**

先校验请求中的幂等键与规范格式，再回读重复记录；首次请求创建 `created` operation。命中同键时逐项核对 workflow、conversation、stage、stage version、attempt 和 request hash，完全一致才返回既有 job。

- [x] **步骤 3：处理并发唯一约束竞争**

插入冲突后只按当前 owner 与幂等键回读；一致则返回胜出记录，否则统一抛 `OperationConflictError`。

- [x] **步骤 4：运行定向测试并确认 GREEN**

```powershell
& E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe `
  -m pytest tests/test_agent_runtime_operation_coordinator.py -q
```

### 任务四：独立审核、验证和交接

**文件：**

- 修改：`docs/agentization/status/M06-status.md`
- 新增：`docs/agentization/test-reports/M06.1.md`
- 修改：`docs/agentization/plans/2026-07-28-m06-1-operation-idempotency-state-machine.md`
- 修改：`README.md`
- 修改：`AGENTS.md`
- 修改：`docs/pixelflow-agent-skill-flow-latest-design.md`

- [x] **步骤 1：运行 M06.1 范围回归与静态检查**

覆盖新 operation 合同、M00 冻结合同、M01 Repository/migration、格式与 `git diff --check`。

- [x] **步骤 2：发起独立只读审核**

审核重点为幂等身份边界、并发重复 start、owner 隔离、敏感请求不落库、状态机是否允许终态重开，以及是否越界实现 M06.2+。

- [x] **步骤 3：处理 Critical/Important 并重新验证**

每个有效问题先补失败合同再做最小修复；最终记录 Critical、Important、Minor 状态。

- [x] **步骤 4：完成中文状态与测试记录**

勾选 M06.1，释放唯一写入权，下一切片设为 M06.2；phase 保持 `in_progress`，不得写任何 integration ready 状态。

- [x] **步骤 5：执行中文工程门禁并提交推送**

仅暂存本切片文件，使用一个中文 commit；push `codex/agent-0.8.4-m06-external-jobs` 后核对远端 SHA 并停止。
