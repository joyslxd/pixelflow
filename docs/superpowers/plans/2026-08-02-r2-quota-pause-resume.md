# R2 视频 status 402 持久化暂停与恢复实施计划

> **For agentic workers（面向 Agent 执行者）：** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实施本计划。所有步骤使用 checkbox（`- [ ]`）跟踪。

**Goal（目标）：** 让已绑定 Provider job 的视频 Operation 在 status 402 后，通过持久化 quota Outbox 在原 Workflow/Turn 打开授权中断，并由用户携新 Authorization 恢复同一 job、同一 attempt，且不再次调用 Provider start。

**Architecture（架构）：** M06 Repository 在同一 Memory 临界区或 SQL 事务中提交 Operation 的 `quota_pause_revision` 与 `external_job.quota_state_changed` 非终态事件；Recovery Runtime 先投递 quota 事件，再投递 completion，最后轮询 Provider。视频层用共享的 `VideoOperationQuotaProjectionService` 为后台 Outbox 与当前 interrupt 响应构造完全相同的 Graph/Workflow/Turn 投影，使任一崩溃窗口都按稳定事件 ID 幂等重放。

**Tech Stack（技术栈）：** Python 3.12、Pydantic v2、FastAPI、SQLAlchemy async、Alembic、SQLite/MySQL、LangGraph Checkpointer、pytest、Ruff、PowerShell 中文工程门禁。

## Global Constraints（全局约束）

- 测试、开发和生产使用同一套 Python 实现；测试只注入 Fake Provider、可控 Clock 和临时数据库。
- 不修改 `backend/config.prod.yml`；生产继续保持 `assist / enabled_intents=[] / 100% / context_compaction=true`。
- 不在生产执行本计划新增的数据库迁移，不部署、不调用真实付费 Provider、不切换 `primary(video)`。
- 不新增 AgentAction、前端动作或 HTTP API；授权恢复复用 `retry_failed`，安全 patch 只含 `job_id` 与 `quota_pause_revision`。
- Operation 身份继续固定为 `workflow_id + stage + stage_version + attempt`；402 不改变 `provider_job_id`、attempt 或 start 次数。
- `quota_pause_revision` 从 0 单调递增；同一 revision 的 pause/resume 事件分别幂等，旧 revision 响应失败关闭。
- Authorization 只存在于 Router → Service → Executor → Vault → Handler → Bridge 当前调用栈，禁止进入 Operation、Outbox、Turn、checkpoint、Snapshot/SSE、投影或日志。
- 402 不生成 completion；timeout、failed、404/expired 继续使用现有终态事件与新 attempt 语义。
- 上下文预算保持 896K/32K/32K，`require_verified_model_profile=true`，压缩失败退避 30 秒；不增加节点级窗口常量。
- 新增或修改的注释、docstring、提交说明和测试结论使用中文；机器指令保留最小英文例外。

---

## 文件结构与职责

| 文件 | 操作 | 单一职责 |
| --- | --- | --- |
| `backend/packages/harness/deerflow/persistence/migrations/versions/20260802_07_operation_quota_revision.py` | 新建 | 增加并安全回退 `quota_pause_revision` 数据库列与非负约束 |
| `backend/pixelflow/agent_runtime/persistence/models.py` | 修改 | 为 SQLAlchemy Operation 行声明 revision 列和约束 |
| `backend/pixelflow/agent_runtime/persistence/repositories.py` | 修改 | Memory/SQL 原子 pause/resume + quota Outbox、事件租约、扫描和 due 隔离 |
| `backend/pixelflow/agent_runtime/contracts/enums.py` | 修改 | 增加非终态 `external_job.quota_state_changed` 事件枚举 |
| `backend/pixelflow/agent_runtime/jobs/quota.py` | 新建 | 稳定事件身份、pause/resume Coordinator、Dispatcher 与 Graph Port |
| `backend/pixelflow/agent_runtime/jobs/recovery.py` | 修改 | 先投递 quota，再投递 completion，最后领取轮询；移除无授权恢复旁路 |
| `backend/pixelflow/agent_runtime/jobs/__init__.py` | 修改 | 导出 quota 公共合同 |
| `backend/pixelflow/agent_runtime/persistence/__init__.py` | 修改 | 导出 quota Repository DTO |
| `backend/pixelflow/agent_runtime/persistence/video_runtime.py` | 修改 | 原子投影 quota Workflow、原 Turn、interrupt 与 Outbox 确认 |
| `backend/pixelflow/agent_runtime/executor.py` | 修改 | 将当前响应持有的安全 resume Event claim 传入原子 Turn 提交 |
| `backend/pixelflow/agent_workflows/video/live_quota.py` | 新建 | 从 quota 事件构造视频领域状态、Workflow、授权中断和稳定 checkpoint |
| `backend/pixelflow/agent_workflows/video/live_operations.py` | 修改 | 截获 quota claim；消费瞬时凭据后恢复原 Operation |
| `backend/pixelflow/agent_workflows/video/live_capabilities.py` | 修改 | 增加只供 quota 恢复边界使用的一次性 Authorization 消费函数 |
| `backend/pixelflow/agent_workflows/video/live_handler.py` | 修改 | 在阶段动作前识别 quota `retry_failed`，校验权威 Operation 与 revision |
| `backend/pixelflow/agent_workflows/video/__init__.py` | 修改 | 导出视频 quota 投影 Service/Handler |
| `backend/app/gateway/pixelflow_agent_runtime.py` | 修改 | 将 quota Handler 同现有 Graph/Bridge/Recovery Runtime 全有或全无装配 |
| `backend/tests/test_agent_runtime_migration.py` | 修改 | 校验迁移升级、重跑、非零 revision 降级保护 |
| `backend/tests/test_agent_runtime_contracts.py` | 修改 | 冻结 Python quota Event 枚举值 |
| `backend/tests/test_agent_runtime_repositories.py` | 修改 | 校验 Memory/SQL 同构、CAS、Outbox 租约和 due 隔离 |
| `backend/tests/test_agent_runtime_operation_recovery.py` | 修改 | 校验 402 原子暂停、事件顺序、重复 402 和重启恢复 |
| `backend/tests/test_agent_video_live_operations.py` | 修改 | 校验 Graph checkpoint、原 Turn/interrupt、凭据消费和崩溃窗口 |
| `backend/tests/test_agent_video_live_handler.py` | 修改 | 校验 Handler 的 quota 动作路由、缺凭据重开中断和旧 revision 拒绝 |
| `backend/tests/test_agent_runtime_gateway_readiness.py` | 修改 | 校验 Gateway 缺任一 quota 组件时保持 v2、完整装配时才 ready |
| `backend/tests/test_agent_runtime_r2_integration.py` | 修改 | 让 fault matrix 真实经过生产 Completion/Quota Handler 并扫描日志 |
| `backend/tests/test_agent_runtime_video_live_e2e.py` | 修改 | 从 FastAPI 公共入口跑通 status 402 → 新 Authorization → 同 job 成功 |
| `web/src/lib/supervisor/contracts.ts` | 修改 | 接受 SSE/Snapshot 的 quota 状态事件，不增加 UI 动作 |
| `web/tests/agentRuntimeContracts.test.mjs` | 修改 | 冻结 Web quota Event 枚举值与解析合同 |
| `docs/agentization/test-reports/R2-video-live-handler.md` | 修改 | 纠正旧 402 自证描述并登记复审与最终命令证据 |
| `docs/pixelflow-agent-skill-flow-latest-design.md` | 修改 | 固化 status 402 的持久化 pause/resume 合同 |
| `docs/agentization/status/BOARD.md` | 修改 | 只登记 Task 14 breaker 修复候选状态，不宣称发布或集成 |
| `docs/agentization/status/M13-status.md` | 修改 | 同步 Task 14 最终门禁、已知基线失败和生产边界 |

---

### Task 1：冻结 Operation revision 模型与安全迁移

**Files:**
- Create: `backend/packages/harness/deerflow/persistence/migrations/versions/20260802_07_operation_quota_revision.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/models.py:249-282`
- Modify: `backend/pixelflow/agent_runtime/persistence/repositories.py:45-63,502-528,1609-1628,2232-2255`
- Test: `backend/tests/test_agent_runtime_migration.py`
- Test: `backend/tests/test_agent_runtime_repositories.py`

**Interfaces:**
- Consumes: Alembic head `20260801_06`；现有 `PixelFlowAgentOperationRow` 与 `OperationRecord`。
- Produces: `OperationRecord.quota_pause_revision: int = Field(default=0, ge=0)`；SQL 列 `quota_pause_revision INTEGER NOT NULL DEFAULT 0`；revision `20260802_07`。

- [ ] **Step 1: 写入迁移与模型 RED 测试**

在 `test_agent_runtime_migration.py` 增加以下断言，并在现有 Operation 列集合中加入新列：

```python
def test_operation_quota_revision_migration_is_additive_and_data_safe(tmp_path: Path) -> None:
    database_path = tmp_path / "operation-quota-revision.db"
    config = _migration_config(database_path)
    command.upgrade(config, "20260801_06")
    _insert_polling_operation(database_path, job_id="job-quota-migration")

    command.upgrade(config, "head")
    row = _fetch_operation(database_path, "job-quota-migration")
    assert row["quota_pause_revision"] == 0

    _execute_sql(
        database_path,
        "UPDATE pixelflow_agent_operations SET quota_pause_revision = 1 WHERE job_id = :job_id",
        {"job_id": "job-quota-migration"},
    )
    with pytest.raises(RuntimeError, match="存在 quota pause revision"):
        command.downgrade(config, "20260801_06")
```

在 `test_agent_runtime_repositories.py` 增加默认值与负数拒绝：

```python
def test_operation_record_defaults_quota_pause_revision_to_zero() -> None:
    record = _operation("job-quota-default", "idem-quota-default")
    assert record.quota_pause_revision == 0
    with pytest.raises(ValidationError):
        OperationRecord.model_validate(
            record.model_dump(mode="python") | {"quota_pause_revision": -1}
        )
```

- [ ] **Step 2: 运行 RED 并确认失败原因**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_migration.py::test_operation_quota_revision_migration_is_additive_and_data_safe tests/test_agent_runtime_repositories.py::test_operation_record_defaults_quota_pause_revision_to_zero -q
```

Expected: FAIL，分别指出 Alembic head 缺少 `quota_pause_revision`、`OperationRecord` 没有该字段。

- [ ] **Step 3: 实现最小模型与迁移**

`OperationRecord` 与 SQLAlchemy 行模型使用同一默认值：

```python
class OperationRecord(ContractModel):
    # 既有字段保持原顺序。
    quota_pause_revision: int = Field(default=0, ge=0)


class PixelFlowAgentOperationRow(Base):
    # 用途：记录同一 Provider job 已确认的 402 暂停代次；影响：恢复请求必须匹配最新代次，默认 0。
    quota_pause_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
```

迁移固定使用：

```python
revision: str = "20260802_07"
down_revision: str | None = "20260801_06"


def upgrade() -> None:
    """幂等增加非负 quota pause revision，并保留既有 Operation。"""
    # 先在线检查表和列；已有正确列时只校验并返回。
    with op.batch_alter_table("pixelflow_agent_operations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "quota_pause_revision",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_check_constraint(
            "ck_pf_agent_operations_quota_pause_revision",
            "quota_pause_revision >= 0",
        )


def downgrade() -> None:
    """只有全部 revision 仍为 0 时才移除列，拒绝丢弃生产审计数据。"""
    count = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM pixelflow_agent_operations "
            "WHERE quota_pause_revision <> 0"
        )
    ).scalar_one()
    if count:
        raise RuntimeError("存在 quota pause revision，拒绝降级并丢弃审计数据")
    with op.batch_alter_table("pixelflow_agent_operations") as batch_op:
        batch_op.drop_constraint(
            "ck_pf_agent_operations_quota_pause_revision",
            type_="check",
        )
        batch_op.drop_column("quota_pause_revision")
```

同步 `_operation_from_row()`、`create_operation()` 与归一化路径，所有返回值继续通过 `OperationRecord.model_validate()` 重建严格 DTO。

- [ ] **Step 4: 运行 GREEN 与迁移回归**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_migration.py tests/test_agent_runtime_repositories.py -q
.venv\Scripts\python.exe -m ruff check packages/harness/deerflow/persistence/migrations/versions/20260802_07_operation_quota_revision.py pixelflow/agent_runtime/persistence/models.py pixelflow/agent_runtime/persistence/repositories.py tests/test_agent_runtime_migration.py tests/test_agent_runtime_repositories.py
```

Expected: 两条命令均 exit 0；迁移从 `20260801_06` 升到 head 保留旧行，revision 非零时降级 fail-closed。

- [ ] **Step 5: 提交 Task 1**

```powershell
git add backend/packages/harness/deerflow/persistence/migrations/versions/20260802_07_operation_quota_revision.py backend/pixelflow/agent_runtime/persistence/models.py backend/pixelflow/agent_runtime/persistence/repositories.py backend/tests/test_agent_runtime_migration.py backend/tests/test_agent_runtime_repositories.py
git commit -m "实现：增加 Operation 配额暂停代次迁移" -m "为 Memory 与 SQL Operation 固化非负 quota_pause_revision，并在存在审计数据时拒绝破坏性降级。"
```

---

### Task 2：实现 Memory/SQL quota Outbox 原子事务

**Files:**
- Modify: `backend/pixelflow/agent_runtime/contracts/enums.py:80-95`
- Modify: `backend/pixelflow/agent_runtime/persistence/repositories.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/__init__.py`
- Test: `backend/tests/test_agent_runtime_repositories.py`
- Test: `backend/tests/test_agent_runtime_event_outbox.py`
- Test: `backend/tests/test_agent_runtime_contracts.py`
- Modify: `web/src/lib/supervisor/contracts.ts:170-195`
- Test: `web/tests/agentRuntimeContracts.test.mjs`

**Interfaces:**
- Consumes: Task 1 的 `OperationRecord.quota_pause_revision`。
- Produces: `AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED`、`OperationQuotaEventRecord`、`OwnedOperationQuotaEvent`、`pause_operation_for_quota()`、`resume_operation_from_quota()`、`list_pending_operation_quota_events()`、`claim_operation_quota_event()`。

- [ ] **Step 1: 写入 Repository 合同 RED 测试**

Memory 与 SQL 参数化测试必须使用同一断言：

```python
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_pause_and_resume_are_atomic_idempotent_and_block_polling(kind: str) -> None:
    repository = await _repository(kind)
    started = await _create_claimed_polling_operation(repository, job_id="job-quota-atomic")
    pause_event = _quota_event_record(started, revision=1, quota_state="paused")

    paused, stored_pause = await repository.pause_operation_for_quota(
        USER_ID,
        CONVERSATION_ID,
        started.job_id,
        provider_job_id=started.provider_job_id,
        lease_owner="worker-a",
        expected_revision=0,
        now=NOW,
        event=pause_event,
    )
    replayed_pause = await repository.pause_operation_for_quota(
        USER_ID,
        CONVERSATION_ID,
        started.job_id,
        provider_job_id=started.provider_job_id,
        lease_owner="worker-a",
        expected_revision=0,
        now=NOW,
        event=pause_event,
    )
    assert paused.quota_pause_revision == 1
    assert paused.next_poll_at is None
    assert replayed_pause == (paused, stored_pause)
    assert await repository.list_due_operations(now=NOW, limit=100) == []

    resume_event = _quota_event_record(paused, revision=1, quota_state="resumed")
    resumed, resume_claim = await repository.resume_operation_from_quota(
        USER_ID,
        CONVERSATION_ID,
        paused.job_id,
        workflow_id=paused.workflow_id,
        expected_revision=1,
        now=NOW,
        delivery_lease_owner="request-resume-a",
        delivery_lease_expires_at=NOW + timedelta(seconds=30),
        event=resume_event,
    )
    assert resumed.job_id == paused.job_id
    assert resumed.provider_job_id == paused.provider_job_id
    assert resumed.attempt == paused.attempt
    assert resumed.quota_pause_revision == 1
    assert resume_claim.event.payload["quota_state"] == "resumed"
    assert resume_claim.lease_owner == "request-resume-a"
    assert await repository.list_due_operations(now=NOW, limit=100) == []
```

另加并发 CAS、旧 revision、事件租约过期接管和通用 Outbox 队首阻塞测试。

Python 与 Web 合同测试同时加入精确字符串：

```python
assert AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED.value == (
    "external_job.quota_state_changed"
)
```

```javascript
assert.equal(
  parseAgentEvent(quotaStateEvent).type,
  "external_job.quota_state_changed",
);
```

- [ ] **Step 2: 运行 RED 并确认协议缺失**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_repositories.py -k "quota_pause or quota_event" -q
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_event_outbox.py -k "operation_internal" -q
```

Expected: FAIL，指出 Python/Web 事件枚举缺失、Repository Protocol 与双实现缺少 quota 方法，或通用 Outbox 越过 quota 队首。

- [ ] **Step 3: 增加严格 DTO 与 Repository Protocol**

在 `repositories.py` 定义：

```python
class OperationQuotaEventRecord(ContractModel):
    """原子暂停或恢复 Operation 时写入的安全非终态事件。"""

    event_id: str = Field(min_length=1)
    cursor: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    occurred_at: datetime
    quota_pause_revision: int = Field(ge=1)
    quota_state: Literal["paused", "resumed"]
    payload: dict[str, JsonValue]


class OwnedOperationQuotaEvent(ContractModel):
    """恢复扫描使用的 Operation、quota event 与所有者快照。"""

    user_id: str = Field(min_length=1)
    operation: OperationRecord
    event: AgentEvent
```

`AgentEventType` 与 Web `EVENT_TYPE_VALUES` 都增加且只增加：

```python
EXTERNAL_JOB_QUOTA_STATE_CHANGED = "external_job.quota_state_changed"
```

Repository 构造 quota Event 时必须使用该枚举；completion 继续使用原 `EXTERNAL_JOB_STATE_CHANGED`，两条通道不得按 payload 猜测彼此类型。

Protocol 签名固定为：

```python
async def pause_operation_for_quota(
    self,
    user_id: str,
    conversation_id: str,
    job_id: str,
    *,
    provider_job_id: str,
    lease_owner: str,
    expected_revision: int,
    now: datetime,
    event: OperationQuotaEventRecord,
) -> tuple[OperationRecord, AgentEvent]: ...

async def resume_operation_from_quota(
    self,
    user_id: str,
    conversation_id: str,
    job_id: str,
    *,
    workflow_id: str,
    expected_revision: int,
    now: datetime,
    delivery_lease_owner: str,
    delivery_lease_expires_at: datetime,
    event: OperationQuotaEventRecord,
) -> tuple[OperationRecord, EventDeliveryClaim]: ...

async def list_pending_operation_quota_events(
    self, *, now: datetime, limit: int = 100
) -> list[OwnedOperationQuotaEvent]: ...

async def claim_operation_quota_event(
    self,
    user_id: str,
    conversation_id: str,
    event_id: str,
    job_id: str,
    *,
    quota_pause_revision: int,
    quota_state: Literal["paused", "resumed"],
    lease_owner: str,
    now: datetime,
    lease_expires_at: datetime,
) -> EventDeliveryClaim | None: ...
```

- [ ] **Step 4: 实现 Memory 与 SQL 同构事务**

pause 事务必须在同一锁/事务中执行以下 CAS：

```python
if current.quota_pause_revision == expected_revision + 1:
    return _require_same_existing_quota_event(current, event)
if (
    current.quota_pause_revision != expected_revision
    or current.status is not ExternalJobStatus.POLLING
    or current.provider_job_id != provider_job_id
    or current.lease_owner != lease_owner
    or current.lease_expires_at is None
    or current.lease_expires_at <= now
):
    raise AgentRuntimeRecordConflictError("Operation quota pause CAS 冲突")
```

成功后只写入：revision `+1`、`next_poll_at=None`、清租约、`updated_at=now` 与唯一 pause Event。

resume 事务要求 Operation 仍为 `polling`、Provider job 已绑定、`next_poll_at=None`、无租约、workflow 与 expected revision 精确匹配；成功后 `next_poll_at=now`，revision 不变，并写入同 revision 的 resume Event。该 Event 在同一事务中直接进入 `delivering`，租约 owner/expiry 使用当前授权请求提供的安全 worker 身份与 30 秒窗口，`delivery_attempts=1`；因此后台扫描没有机会在当前 Graph 提交前抢占。相同 resume Event 与同一有效租约重放回读原 claim，不创建第二行；进程退出后只能在租约过期时由后台接管。

`list_due_operations()` 使用以下规则排除未确认 quota Event：

```python
def _has_pending_quota_event(owner: str, job_id: str) -> bool:
    return any(
        event_owner == owner
        and event.payload.get("job_id") == job_id
        and _is_operation_quota_event(event)
        and delivery.status != "published"
        for (event_owner, event_id), event in self._events.items()
        if (delivery := self._event_delivery[(event_owner, event_id)])
    )
```

SQL 使用相关 `NOT EXISTS` 子查询实现相同条件，不能在 Python 中无界物化 Event。

将 `_is_operation_completion_event()` 收窄重命名为 `_is_operation_internal_event()`，仅匹配 `evt_job_done_` 与 `evt_job_quota_` 前缀；`claim_next_event()` 在队首遇到任一内部事件时返回 `None`，不能越过 sequence。

- [ ] **Step 5: 运行 GREEN、并发与 Outbox 回归**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_contracts.py tests/test_agent_runtime_repositories.py tests/test_agent_runtime_event_outbox.py -q
.venv\Scripts\python.exe -m ruff check pixelflow/agent_runtime/persistence tests/test_agent_runtime_repositories.py tests/test_agent_runtime_event_outbox.py
cd ..\web
node --test tests/agentRuntimeContracts.test.mjs
```

Expected: exit 0；Memory/SQL 返回相同事件身份与顺序，resume Event 发布前 due-operation 为 0。

- [ ] **Step 6: 提交 Task 2**

```powershell
git add backend/pixelflow/agent_runtime/contracts/enums.py backend/pixelflow/agent_runtime/persistence backend/tests/test_agent_runtime_contracts.py backend/tests/test_agent_runtime_repositories.py backend/tests/test_agent_runtime_event_outbox.py web/src/lib/supervisor/contracts.ts web/tests/agentRuntimeContracts.test.mjs
git commit -m "实现：原子保存配额暂停恢复事件" -m "为 Memory 与 SQL Repository 增加 revision CAS、quota Outbox 租约及轮询隔离，通用发布器不越过内部事件。"
```

---

### Task 3：增加 quota Coordinator、Dispatcher 与 Recovery 顺序

**Files:**
- Create: `backend/pixelflow/agent_runtime/jobs/quota.py`
- Modify: `backend/pixelflow/agent_runtime/jobs/recovery.py:15-31,233-457,493-501`
- Modify: `backend/pixelflow/agent_runtime/jobs/__init__.py`
- Test: `backend/tests/test_agent_runtime_operation_recovery.py`
- Test: `backend/tests/test_agent_runtime_operation_completion.py`

**Interfaces:**
- Consumes: Task 2 的 quota Repository 方法。
- Produces: `OperationQuotaEventPayload`、`OperationQuotaTransitionRecord`、`OperationQuotaAuthorizedResume`、`OperationQuotaCoordinator.record_pause()`、`OperationQuotaCoordinator.authorize_resume()`、`OperationQuotaDispatcher.dispatch()`、`WorkflowGraphQuotaStatePort.resume_external_job_quota()`。

- [ ] **Step 1: 写稳定身份、Dispatcher 和恢复顺序 RED 测试**

```python
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_status_402_persists_pause_event_before_graph_and_replays_same_identity(kind: str) -> None:
    repository = await _repository(kind)
    graph = _RecordingQuotaGraph()
    runtime, adapter, operation = await _polling_runtime(
        repository,
        quota_resumer=graph,
        outcomes=[ProviderJobOutcome.PAUSED_QUOTA],
    )

    await runtime.run_once()
    paused = await repository.get_operation(USER_ID, operation.job_id)
    assert paused is not None
    assert paused.quota_pause_revision == 1
    assert paused.next_poll_at is None
    assert len(graph.calls) == 1
    assert graph.calls[0].quota_event.payload["quota_state"] == "paused"

    await runtime.run_once()
    assert len(graph.calls) == 1
    assert adapter.start_calls == 0
```

增加一个 `_OrderedRepository`，记录调用顺序并断言：

```python
assert repository.calls == [
    "list_pending_operation_quota_events",
    "list_pending_operation_completions",
    "list_due_operations",
]
```

另加第二次 402 产生 revision 2、旧 revision resume 失败、Graph 抛错后事件保留和租约过期接管测试。

- [ ] **Step 2: 运行 RED**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_operation_recovery.py -k "quota or scan_order" -q
```

Expected: FAIL，指出 quota Coordinator/Dispatcher/Port 不存在，或 Runtime 仍直接调用 `pause_operation_poll()`。

- [ ] **Step 3: 实现 quota 事件合同与 Coordinator**

`quota.py` 固定定义：

```python
class OperationQuotaState(StrEnum):
    PAUSED = "paused"
    RESUMED = "resumed"


class OperationQuotaTransitionRecord(ContractModel):
    """返回深度只读且可稳定 JSON 序列化的 Operation 与 quota Event。"""

    model_config = ConfigDict(frozen=True)
    operation: OperationRecord
    event: AgentEvent


class OperationQuotaAuthorizedResume(ContractModel):
    """原子恢复事务返回的 Operation 与当前请求 Event claim。"""

    model_config = ConfigDict(frozen=True)
    operation: OperationRecord
    claim: EventDeliveryClaim


class OperationQuotaEventPayload(ContractModel):
    """严格限制 quota Outbox 的公开安全字段。"""

    job_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    stage_version: int = Field(ge=1)
    attempt: int = Field(ge=1)
    quota_pause_revision: int = Field(ge=1)
    quota_state: OperationQuotaState
    reason_code: Literal[
        "provider_quota_insufficient",
        "provider_quota_resume_authorized",
    ]


@runtime_checkable
class WorkflowGraphQuotaStatePort(Protocol):
    async def resume_external_job_quota(
        self,
        namespace: GraphExecutionNamespace,
        *,
        quota_event: AgentEvent,
        idempotency_key: str,
    ) -> None: ...
```

稳定身份只使用内部字段：

```python
def build_operation_quota_event_id(
    job_id: str,
    revision: int,
    quota_state: OperationQuotaState,
) -> str:
    digest = hashlib.sha256(
        f"pixelflow:external-job-quota:v1:{job_id}:{revision}:{quota_state.value}".encode()
    ).hexdigest()
    return f"evt_job_quota_{digest[:32]}"
```

`OperationQuotaTransitionRecord.model_post_init()` 与 `OperationQuotaAuthorizedResume.model_post_init()` 必须像现有 completion 记录一样重建并递归冻结 Operation/Event/claim 及嵌套 payload，同时保留普通 JSON 序列化。`record_pause()` 先严格回读 Operation，构造 revision `current + 1` 的 `OperationQuotaEventPayload`，再调用 Task 2 原子方法。`authorize_resume()` 要求 expected revision 与当前值相等且 Operation 确为暂停轮询，构造同 revision 的 resume Event，并把 `delivery_lease_owner`、`now`、`delivery_lease_expires_at` 传给 Task 2 的原子恢复方法。两个方法的 payload 只允许：

```python
{
    "job_id": operation.job_id,
    "workflow_id": operation.workflow_id,
    "stage": operation.stage,
    "stage_version": operation.stage_version,
    "attempt": operation.attempt,
    "quota_pause_revision": revision,
    "quota_state": quota_state.value,
    "reason_code": (
        "provider_quota_insufficient"
        if quota_state is OperationQuotaState.PAUSED
        else "provider_quota_resume_authorized"
    ),
}
```

`authorize_resume()` 的公开签名固定为：

```python
async def authorize_resume(
    self,
    job_id: str,
    *,
    workflow_id: str,
    expected_revision: int,
    delivery_lease_owner: str,
    now: datetime,
    delivery_lease_expires_at: datetime,
) -> OperationQuotaAuthorizedResume: ...
```

- [ ] **Step 4: 实现 Dispatcher 与 Runtime 调度顺序**

`OperationQuotaDispatcher.dispatch()` 使用 candidate 中的 event ID 精确领取，不按普通 Outbox 过滤：

```python
claim = await repository.claim_operation_quota_event(
    user_id,
    operation.conversation_id,
    event.event_id,
    operation.job_id,
    quota_pause_revision=event.payload["quota_pause_revision"],
    quota_state=event.payload["quota_state"],
    lease_owner=lease_owner,
    now=now,
    lease_expires_at=lease_expires_at,
)
if claim is None:
    return None
await quota_resumer.resume_external_job_quota(
    workflow_namespace(operation.conversation_id, operation.workflow_id),
    quota_event=_freeze_event(claim.event),
    idempotency_key=claim.event.event_id,
)
return await repository.complete_event_delivery(
    user_id,
    claim.event.event_id,
    lease_owner=lease_owner,
    published_at=clock(),
)
```

`OperationRecoveryRuntime.run_once()` 固定为：

```python
pending_quota = await repository.list_pending_operation_quota_events(...)
for candidate in pending_quota:
    await self._dispatch_quota(candidate, now=self._clock())

pending_completion = await repository.list_pending_operation_completions(...)
for candidate in pending_completion:
    await self._dispatch_completion(candidate, now=self._clock())

due = await repository.list_due_operations(...)
```

`_poll_claimed()` 遇到 `PAUSED_QUOTA` 改为 `OperationQuotaCoordinator.record_pause()`；不再调用旧 `pause_operation_poll()`。`recover_manually()` 对已暂停 Operation 固定抛出 `OperationConflictError("quota_resume_requires_authorized_handler")`，只保留终态 `NEW_ATTEMPT_REQUIRED` 查询能力，防止无 Authorization 的生产旁路。

为兼容通用 M06 单测，构造器允许 `quota_resumer: WorkflowGraphQuotaStatePort | None = None`；没有 quota resumer 时事件保持 pending 并记录固定 `phase=quota_dispatch`/异常类型，不丢事件、不轮询。Gateway Task 6 必须始终注入真实 quota resumer，缺失时不进入 ready。

- [ ] **Step 5: 运行 GREEN 与 M06 回归**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_operation_completion.py tests/test_agent_runtime_event_outbox.py -q
.venv\Scripts\python.exe -m ruff check pixelflow/agent_runtime/jobs tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_operation_completion.py
```

Expected: exit 0；402 后先有 durable pause Event，Graph/进程失败不触发 Provider start，旧人工旁路被拒绝。

- [ ] **Step 6: 提交 Task 3**

```powershell
git add backend/pixelflow/agent_runtime/jobs backend/tests/test_agent_runtime_operation_recovery.py backend/tests/test_agent_runtime_operation_completion.py
git commit -m "实现：接入配额事件协调与恢复调度" -m "status 402 改为持久化 pause Outbox，恢复 Runtime 先投递 quota 与 completion 再轮询，并关闭无授权人工恢复旁路。"
```

---

### Task 4：原子投影 quota Workflow、原 Turn 与 Graph checkpoint

**Files:**
- Create: `backend/pixelflow/agent_workflows/video/live_quota.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/video_runtime.py:676-791,2217-2348,4118-4291`
- Modify: `backend/pixelflow/agent_runtime/executor.py:1007-1034`
- Modify: `backend/pixelflow/agent_workflows/video/live_handler.py:94-109`
- Modify: `backend/pixelflow/agent_workflows/video/live_operations.py:135-320,627-651`
- Modify: `backend/pixelflow/agent_workflows/video/__init__.py`
- Test: `backend/tests/test_agent_video_live_operations.py`

**Interfaces:**
- Consumes: Task 3 的 `WorkflowGraphQuotaStatePort`、`OperationQuotaTransitionRecord` 与事件 ID。
- Produces: `VideoOperationQuotaProjectionService.build()`、`VideoOperationQuotaStateHandler.resume_external_job_quota()`、`VideoRuntimeRepository.commit_operation_quota_state()`，以及 `WorkflowDispatchResult.operation_event_claim` → `VideoTurnCommit.operation_event_claim` 的安全传递。

- [ ] **Step 1: 写 Graph、原 Turn 与 crash window RED 测试**

Memory/SQL 各覆盖 pause 与 resume：

```python
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_pause_event_opens_one_graph_interrupt_on_original_turn(kind: str) -> None:
    harness = await _quota_live_harness(kind)
    pause = await harness.pause_status_402()
    await harness.recovery.run_once()

    snapshot = await harness.repository.export_safe_snapshot(USER_ID, CONVERSATION_ID)
    assert snapshot.workflows[0].status is WorkflowStatus.PAUSED_QUOTA
    assert snapshot.turns[0].turn.turn_id == harness.original_turn_id
    assert snapshot.turns[0].turn.status is TurnStatus.WAITING_USER
    assert len(snapshot.interrupts) == 1
    assert snapshot.interrupts[0].payload["authorization_action"]["patch"] == {
        "job_id": pause.operation.job_id,
        "quota_pause_revision": 1,
    }
    assert harness.checkpoint_interrupt_count(pause.event.event_id) == 1
```

为 pause/resume 分别注入四个 crash hook：事件领取后、Graph checkpoint 前、业务 Repository 提交后、Outbox 确认前。每次重启后断言同一 event、同一 interrupt、同一 Turn，消息和状态不重复。

另外用独立用例冻结“当前授权响应优先持有 resume Event claim”的竞态边界：

```python
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_authorized_resume_claim_blocks_background_until_turn_commit(
    kind: str,
) -> None:
    harness = await _paused_quota_live_harness(kind)
    authorized = await harness.authorize_resume(
        expected_revision=1,
        authorization="Bearer quota-resume-race-marker",
    )

    assert authorized.claim.event.payload["quota_state"] == "resumed"
    assert await harness.repository.list_pending_operation_quota_events(
        now=harness.now,
        limit=100,
    ) == []
    assert await harness.repository.list_due_operations(
        now=harness.now,
        limit=100,
    ) == []

    await harness.commit_authorized_turn(authorized.claim)
    due = await harness.repository.list_due_operations(
        now=harness.now,
        limit=100,
    )
    assert [candidate.operation.job_id for candidate in due] == [harness.job_id]
```

同一用例再增加 crash 分支：授权事务返回 claim 后不提交 Turn，将时钟推进到 claim expiry；此时后台只能领取同一 event ID，并通过 `VideoOperationQuotaStateHandler` 完成同一投影。断言 resume Event 仍只有一行、Provider start 仍为 1，且恢复前后 `workflow_state.last_action_key` 都等于该 event ID。

- [ ] **Step 2: 运行 RED**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_video_live_operations.py -k "quota_pause_event or quota_resume_event or quota_crash" -q
```

Expected: FAIL，指出 quota Graph Port 或 `commit_operation_quota_state()` 缺失。

- [ ] **Step 3: 实现共享投影 Service**

`live_quota.py` 定义一个供“后台 Outbox”和“当前 interrupt 响应”共同调用的纯构造器：

```python
class VideoOperationQuotaProjection(ContractModel):
    """同一 quota Event 对应的完整视频投影目标。"""

    workflow_state: VideoWorkflowStateEnvelope
    workflow: WorkflowRecord
    open_interrupt: StoredAgentInterrupt | None = None
    close_interrupt_revision: int | None = Field(default=None, ge=1)


class VideoOperationQuotaProjectionService:
    def build(
        self,
        *,
        user_id: str,
        envelope: VideoWorkflowStateEnvelope,
        operation: OperationRecord,
        quota_event: AgentEvent,
    ) -> VideoOperationQuotaProjection:
        payload = OperationQuotaEventPayload.model_validate(quota_event.payload)
        state = decode_video_workflow_state(envelope)
        _validate_pending_operation_identity(state, operation, payload)
        next_envelope = encode_video_workflow_state(
            user_id=user_id,
            state=state,
            workflow_version=envelope.workflow_version + 1,
            last_turn_id=envelope.last_turn_id,
            last_action_key=quota_event.event_id,
        )
        workflow = project_video_workflow_state(state)
        if payload.quota_state is OperationQuotaState.PAUSED:
            workflow = workflow.model_copy(
                update={
                    "status": WorkflowStatus.PAUSED_QUOTA,
                    "updated_at": quota_event.occurred_at,
                }
            )
            interrupt = _quota_authorization_interrupt(
                envelope=next_envelope,
                operation=operation,
                event=quota_event,
            )
            return VideoOperationQuotaProjection(
                workflow_state=next_envelope,
                workflow=workflow,
                open_interrupt=interrupt,
            )
        return VideoOperationQuotaProjection(
            workflow_state=next_envelope,
            workflow=workflow,
            close_interrupt_revision=payload.quota_pause_revision,
        )
```

为避免当前 interrupt 响应与后台 worker 竞争同一个 resume Event，`WorkflowDispatchResult` 和 `VideoTurnCommit` 各增加：

```python
operation_event_claim: EventDeliveryClaim | None = None
```

该字段只允许携带 `external_job.quota_state_changed + quota_state=resumed` 的 claim；事件 payload 已由 Task 3 严格白名单化，不含 Authorization。Graph checkpoint 可以持久化安全 claim，但凭据仍只存在 Vault。`SupervisorTurnExecutor._commit_from_graph()` 必须严格重建 claim 后传入 `VideoTurnCommit`，其他动作携带 claim 一律 fail-closed。

授权 interrupt 固定：`kind=authorization_required`、`reason_code=authorization_required`、原 `turn_id=envelope.last_turn_id`、`checkpoint_ns="root"`；payload 只包含 workflow/stage 与：

```python
"authorization_action": {
    "action": "retry_failed",
    "intent": "video",
    "workflow_id": operation.workflow_id,
    "stage": operation.stage,
    "artifact_ref": None,
    "patch": {
        "job_id": operation.job_id,
        "quota_pause_revision": payload.quota_pause_revision,
    },
}
```

- [ ] **Step 4: 实现 quota claim 桥与原子 VideoRuntime 提交**

将 `_CompletionClaimRegistry` 泛化为 `_OperationEventClaimRegistry`，按 event ID 保存 completion/quota 的原 user、conversation、job 与不可变 `EventDeliveryClaim`。`_CompletionClaimRepositoryProxy` 重命名 `_OperationEventClaimRepositoryProxy`，同时截获：

```python
async def claim_operation_completion_event(...): ...
async def claim_operation_quota_event(...): ...
```

`VideoRuntimeRepository` 增加：

```python
async def commit_operation_quota_state(
    self,
    claim: EventDeliveryClaim,
    *,
    user_id: str,
    workflow_state: VideoWorkflowStateEnvelope,
    workflow: WorkflowRecord,
    expected_workflow_version: int,
    open_interrupt: StoredAgentInterrupt | None,
    close_interrupt_revision: int | None,
    occurred_at: datetime,
) -> WorkflowRecord: ...
```

Memory 临界区同时持有 compaction、operation、event 写锁；SQL 使用一个 `_repository_write_transaction()`。提交前验证：

- claim 租约 owner/attempt/expiry 完整且实际完成时间仍早于 expiry；
- Event 是 `evt_job_quota_`，payload 的 job/workflow/revision/state 与权威 Operation 一致；
- pause 要求 Operation `next_poll_at=None`，resume 要求 `next_poll_at` 非空；
- pause 的原 Turn 只允许 `completed/waiting_user`，resume 允许当前响应正在处理或已完成；
- 同 event ID + 同 envelope payload hash 回读成功，不同内容 fail-closed。

pause 原子写 Workflow `paused_quota`、原 Turn `waiting_user`、唯一 interrupt，并把 quota Event 标记 published。resume 原子写 Workflow 的领域原状态，并在同一事务中按 `conversation_id + workflow_id + authorization_required + authorization_action.patch.job_id + quota_pause_revision` 查找唯一未关闭 interrupt：找到一个则关闭，找不到但同 revision 已有 closed interrupt 则幂等继续，出现两个未关闭匹配项则 fail-closed。interrupt 已被当前响应关闭时保留既有 `closed_at`，不能因后台重放制造不同快照；最后把 quota Event 标记 published。

正常用户响应不等待后台扫描：Bridge 在创建 resume Event 后先领取该 Event 的 30 秒投递租约，Handler 把 claim 放入 `WorkflowDispatchResult.operation_event_claim`。Memory/SQL `commit_turn()` 在原有 Workflow/Turn/interrupt 事务内验证 claim owner、attempt、expiry、job/workflow/revision、`quota_state=resumed` 以及 `workflow_state.last_action_key == event_id`，然后把该 Event 标记 published。后台 `list_pending_operation_quota_events()` 在租约有效期内看不到该 Event；若进程在 claim 或 Graph checkpoint 后退出，租约过期后 `VideoOperationQuotaStateHandler` 使用相同 Event 与投影接管。

- [ ] **Step 5: 实现稳定 Graph checkpoint Handler**

`VideoOperationQuotaStateHandler.resume_external_job_quota()`：

1. 从 Bridge 的 claim registry 取得原 claim；
2. 严格重建 `AgentEvent` 与 quota payload，拒绝 `serialize_as_any=True` 子类额外字段；
3. 用 `VideoOperationQuotaProjectionService.build()` 构造唯一目标；
4. pause 使用 thread ID `quota-paused:<event_id>`，resume 使用 `quota-resumed:<event_id>`；
5. checkpoint 已存在时精确比较 event ID、workflow version、interrupt ID 与投影摘要；
6. 调用 `commit_operation_quota_state()`；
7. Graph/Repository 后退出时由同 event ID 重放，绝不调用 Provider。

- [ ] **Step 6: 运行 GREEN 与 completion 回归**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_video_live_operations.py -k "quota or completion or checkpoint" -q
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_graph_interrupts.py tests/test_agent_runtime_turn_executor.py -q
.venv\Scripts\python.exe -m ruff check pixelflow/agent_runtime/persistence/video_runtime.py pixelflow/agent_runtime/executor.py pixelflow/agent_workflows/video/live_quota.py pixelflow/agent_workflows/video/live_operations.py pixelflow/agent_workflows/video/live_handler.py tests/test_agent_video_live_operations.py
```

Expected: exit 0；pause/resume crash window 均只产生一个 checkpoint/interrupt；既有 completion 行为不变。

- [ ] **Step 7: 提交 Task 4**

```powershell
git add backend/pixelflow/agent_runtime/persistence/video_runtime.py backend/pixelflow/agent_runtime/executor.py backend/pixelflow/agent_workflows/video/live_quota.py backend/pixelflow/agent_workflows/video/live_operations.py backend/pixelflow/agent_workflows/video/live_handler.py backend/pixelflow/agent_workflows/video/__init__.py backend/tests/test_agent_video_live_operations.py backend/tests/test_agent_runtime_graph_interrupts.py backend/tests/test_agent_runtime_turn_executor.py
git commit -m "实现：持久投影视频配额中断与恢复" -m "以稳定 quota checkpoint 原子更新原 Workflow、Turn 和 interrupt，并让当前响应与后台重放共享同一投影目标。"
```

---

### Task 5：接入授权 Handler、一次性凭据与 revision 校验

**Files:**
- Modify: `backend/pixelflow/agent_workflows/video/live_capabilities.py:602-638`
- Modify: `backend/pixelflow/agent_workflows/video/live_operations.py:297-400,587-651`
- Modify: `backend/pixelflow/agent_workflows/video/live_handler.py:149-214,698-714,1534-1562,1585-1640`
- Test: `backend/tests/test_agent_video_live_handler.py`
- Test: `backend/tests/test_agent_video_live_operations.py`

**Interfaces:**
- Consumes: Task 3 的 `OperationQuotaCoordinator.authorize_resume()` 与 Task 4 的共享投影 Service。
- Produces: `VideoLiveOperationBridge.resume_paused_operation()`；Handler 的 quota `retry_failed` 公开动作路径。

- [ ] **Step 1: 写缺凭据、有效凭据、旧 revision 与并发 RED 测试**

```python
async def test_quota_retry_consumes_new_credential_and_resumes_same_operation() -> None:
    harness = await _paused_video_handler_harness(revision=1)
    credential = TransientTurnCredential(authorization="Bearer quota-resume-marker")
    harness.vault.put(harness.turn_id, credential)

    result = await harness.handler.dispatch(
        harness.command(
            action=AgentAction.RETRY_FAILED,
            patch={"job_id": harness.job_id, "quota_pause_revision": 1},
        )
    )

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.job_id,
    )
    assert operation is not None
    assert operation.job_id == harness.job_id
    assert operation.provider_job_id == harness.provider_job_id
    assert operation.attempt == 1
    assert harness.provider.start_calls == 0
    assert result.workflow.status is WorkflowStatus.RUNNING
    with pytest.raises(RuntimeError, match="不可用"):
        _consume_authorization_for_quota_resume_boundary(credential)
```

缺凭据测试断言 Operation 仍暂停、没有 resume Event，并打开同 revision 的新授权 interrupt。旧 revision、跨用户、跨会话、错误 workflow/job/attempt 固定抛出 `VideoLiveStateConflictError("video_quota_resume_stale")` 或相应安全 reason，不回显输入值。

- [ ] **Step 2: 运行 RED**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_video_live_handler.py -k "quota_retry or quota_revision" -q
.venv\Scripts\python.exe -m pytest tests/test_agent_video_live_operations.py -k "quota_credential" -q
```

Expected: FAIL，指出 Bridge/Handler 缺少 quota 恢复入口，或凭据未被一次性消费。

- [ ] **Step 3: 增加一次性凭据消费边界**

`live_capabilities.py` 增加：

```python
def _consume_authorization_for_quota_resume_boundary(
    credential: TransientTurnCredential,
) -> str:
    """仅为原 Provider job 的 quota resume 原子消费一次 Authorization。"""

    if not isinstance(credential, TransientTurnCredential):
        raise TypeError("credential 必须是 TransientTurnCredential")
    with _TRANSIENT_CREDENTIAL_LOCK:
        authorization = _TRANSIENT_CREDENTIAL_SECRETS.pop(credential, None)
    if authorization is None:
        raise RuntimeError("当前 Turn 临时凭据不可用")
    return authorization
```

Bridge 只用该字符串证明当前认证请求确实持有凭据，绝不传给不接受 Authorization 的 Provider status：

```python
async def resume_paused_operation(
    self,
    *,
    user_id: str,
    conversation_id: str,
    workflow_id: str,
    job_id: str,
    expected_revision: int,
    resume_request_key: str,
    credential: TransientTurnCredential,
) -> OperationQuotaAuthorizedResume:
    authorization = _consume_authorization_for_quota_resume_boundary(credential)
    try:
        if not authorization.strip():
            raise OperationConflictError("quota_resume_authorization_required")
        if not isinstance(resume_request_key, str) or not resume_request_key.strip():
            raise OperationConflictError("quota_resume_request_key_required")
        claim_time = self._now()
        request_digest = hashlib.sha256(
            resume_request_key.strip().encode()
        ).hexdigest()[:16]
        return await OperationQuotaCoordinator(
            self._repository,
            user_id=user_id,
            conversation_id=conversation_id,
        ).authorize_resume(
            job_id,
            workflow_id=workflow_id,
            expected_revision=expected_revision,
            delivery_lease_owner=(
                f"{self._lease_owner}:quota-resume:{request_digest}"
            ),
            now=claim_time,
            delivery_lease_expires_at=(
                claim_time + self._quota_lease_duration
            ),
        )
    finally:
        authorization = ""
```

Executor/Handler 现有 `finally` 继续 `credential.discard()`，重复清理保持幂等。

`resume_request_key` 只能传 `command.decision.idempotency_key`（即当前
`client_response_id` 派生的安全键），不得传 Authorization。不同并发响应因此使用不同 claim
owner：只有赢得 resume CAS 的响应可提交当前 Turn；另一响应看到有效异主租约或已关闭 interrupt
时固定失败关闭。同一 `client_response_id` 的重放仍由现有 Turn/interrupt 幂等层回读，不创建第二个
claim。

- [ ] **Step 4: 在所有阶段分发前接入 quota 动作**

在 `VideoLiveWorkflowHandler.dispatch()` 解码权威 state 后、进入具体 stage handler 前增加窄路由：

```python
if (
    command.decision.action is AgentAction.RETRY_FAILED
    and set(command.decision.patch) == {"job_id", "quota_pause_revision"}
):
    return await self._resume_quota_operation(
        command,
        state,
        existing_envelope=existing_envelope,
    )
```

`_resume_quota_operation()` 固定执行：

1. 只接受非空 `job_id` 与非 bool、`>=1` 的整数 revision；
2. 通过 Bridge 按 user/conversation/workflow/job 回读权威 Operation；
3. 校验 `status=polling`、Provider job 已绑定、`next_poll_at=None`、attempt/stage 与 M11 pending Operation 一致；
4. 没有 Vault 凭据时复用 `_wait_for_authorization()`，但 payload 固定保留 `{job_id, quota_pause_revision}`；
5. 有凭据时调用 `resume_paused_operation()`，传入 `resume_request_key=command.decision.idempotency_key`，并在恢复事务内同时取得 resume Event 租约；
6. 用 Task 4 的 `VideoOperationQuotaProjectionService.build()` 和 `authorized.claim.event` 构造返回值，`workflow_state.last_action_key=resume_event.event_id`，并设置 `operation_event_claim=authorized.claim`；
7. `finally` 销毁凭据。

这样当前 interrupt 响应的正常 Turn 提交与后台 resume Outbox 会写入相同 event ID、相同 envelope version/hash。任一方先提交，另一方只做幂等回读；resume Event 未 published 前 Task 2 的 due 查询仍返回 0。

并发测试必须用两个不同 `client_response_id` 同时响应同一 revision：断言只有一个响应获得 claim 并
返回 200，另一响应固定 409；Repository 中只有一个 resume Event，Provider start 仍为 1，且失败
响应的 Authorization marker 不进入日志或任一快照。

- [ ] **Step 5: 运行 GREEN 与九动作回归**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_video_live_handler.py tests/test_agent_video_live_operations.py -q
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_supervisor_routing.py tests/test_agent_runtime_graph_dispatcher.py -q
.venv\Scripts\python.exe -m ruff check pixelflow/agent_workflows/video/live_capabilities.py pixelflow/agent_workflows/video/live_operations.py pixelflow/agent_workflows/video/live_handler.py tests/test_agent_video_live_handler.py tests/test_agent_video_live_operations.py
```

Expected: exit 0；合法 quota patch 只恢复原 job，现有分镜/合并/质检/剪映的 `retry_failed` patch 仍走原阶段逻辑。

- [ ] **Step 6: 提交 Task 5**

```powershell
git add backend/pixelflow/agent_workflows/video/live_capabilities.py backend/pixelflow/agent_workflows/video/live_operations.py backend/pixelflow/agent_workflows/video/live_handler.py backend/tests/test_agent_video_live_handler.py backend/tests/test_agent_video_live_operations.py
git commit -m "实现：接入视频配额授权恢复动作" -m "复用 retry_failed 携带 job 与 revision，通过瞬时凭据恢复原 Operation，并拒绝旧代次和跨作用域引用。"
```

---

### Task 6：完成 Gateway 全有或全无装配

**Files:**
- Modify: `backend/app/gateway/pixelflow_agent_runtime.py:84-136,219-346`
- Modify: `backend/pixelflow/agent_workflows/video/live_operations.py:627-651`
- Test: `backend/tests/test_agent_runtime_gateway_readiness.py`
- Test: `backend/tests/test_gateway_runtime_cleanup.py`

**Interfaces:**
- Consumes: Task 4 的 `VideoOperationQuotaStateHandler` 与 Task 3 的 `quota_resumer` 参数。
- Produces: ready Gateway 中唯一共享 Graph/Repository/Bridge 的 completion + quota recovery；缺任一组件时 `primary_execution_intents=frozenset()`。

- [ ] **Step 1: 写 Gateway readiness RED 测试**

```python
@pytest.mark.parametrize("missing", ["quota_handler", "graph", "providers", "repository"])
async def test_gateway_keeps_video_on_v2_when_quota_recovery_is_incomplete(missing: str) -> None:
    runtime = await _assemble_runtime_with_missing_component(missing)
    assert runtime.ready is False
    assert runtime.primary_execution_intents == frozenset()
    assert runtime.operation_recovery is None


async def test_gateway_wires_quota_and_completion_to_same_live_graph() -> None:
    runtime = await _ready_runtime()
    assert runtime.ready is True
    assert runtime.operation_recovery is not None
    assert runtime.quota_handler is not None
    assert runtime.operation_recovery.quota_resumer is runtime.quota_handler
    assert runtime.primary_execution_intents == frozenset({"video"})
```

- [ ] **Step 2: 运行 RED**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_gateway_readiness.py -k "quota or same_live_graph" -q
```

Expected: FAIL，当前 Gateway 只装配 completion handler，没有 quota Graph resumer。

- [ ] **Step 3: 接入生产装配与关闭顺序**

在 `_assemble_ready_runtime()` 中，completion 与 quota handler 复用同一对象边界：

```python
quota_handler = VideoOperationQuotaStateHandler(
    repository=runtime.repository,
    operations=operation_bridge,
    clock=clock,
    graph=graph_runtime.graph,
    external_job_observer=executor,
)
recovery = operation_bridge.build_recovery_runtime(
    resumer=completion_handler,
    quota_resumer=quota_handler,
    worker_id=f"gateway-video-recovery:{worker_suffix}",
)
```

`PixelFlowAgentLiveRuntime` 增加 `quota_handler: VideoOperationQuotaStateHandler | None`；`OperationRecoveryRuntime` 增加只读 `quota_resumer` 属性供 readiness 验证，不暴露凭据或业务数据。只有 repository、capabilities、providers、Graph、Executor、completion handler、quota handler 与 Recovery Runtime 全部构造并启动后，才写 `registered_intents={video}` 与 `primary_execution_intents`。任何异常按既有逆序关闭 Recovery → Executor → Graph，并保留固定 `VIDEO_LIVE_HANDLER_NOT_READY` 降级状态；日志只含组件名和异常类型。

- [ ] **Step 4: 运行 GREEN 与生命周期回归**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_gateway_readiness.py tests/test_gateway_runtime_cleanup.py tests/test_gateway_run_recovery.py -q
.venv\Scripts\python.exe -m ruff check app/gateway/pixelflow_agent_runtime.py tests/test_agent_runtime_gateway_readiness.py tests/test_gateway_runtime_cleanup.py
```

Expected: exit 0；完整依赖时 ready，缺任一 quota 依赖时不注册 live video，关闭后无残留后台任务。

- [ ] **Step 5: 提交 Task 6**

```powershell
git add backend/app/gateway/pixelflow_agent_runtime.py backend/pixelflow/agent_workflows/video/live_operations.py backend/tests/test_agent_runtime_gateway_readiness.py backend/tests/test_gateway_runtime_cleanup.py backend/tests/test_gateway_run_recovery.py
git commit -m "实现：装配 Gateway 配额恢复处理器" -m "将 quota 与 completion 共用生产 Graph 和 Operation Bridge，依赖不完整时保持视频 v2 接力。"
```

---

### Task 7：重做 Task 14 公共 402 全流程与故障矩阵

**Files:**
- Modify: `backend/tests/test_agent_runtime_video_live_e2e.py`
- Modify: `backend/tests/test_agent_runtime_r2_integration.py`
- Modify: `backend/tests/test_agent_runtime_turn_executor.py`
- Modify: `backend/tests/test_agent_runtime_context_assembler.py`

**Interfaces:**
- Consumes: Tasks 1–6 的完整生产调用链。
- Produces: 真实 FastAPI status 402 恢复证据；11 项故障矩阵不再依赖预置 Turn 或手工恢复。

- [ ] **Step 1: 先把旧 402 自证改成真实 RED**

删除测试中的以下旁路：

```python
await runtime.recover_manually(...)
await operations.start(existing_request, credential=new_credential)
```

公共 E2E 改为：

```python
await public_worker.run_until_status(
    job_id=scene_job_id,
    outcome=ProviderJobOutcome.PAUSED_QUOTA,
)
paused_snapshot = await client.get(
    f"/agent/conversations/{conversation_id}",
    headers=OWNER_HEADERS,
)
interrupt = _single_open_interrupt(paused_snapshot.json())
assert interrupt["payload"]["authorization_action"]["patch"] == {
    "job_id": scene_job_id,
    "quota_pause_revision": 1,
}

resume = await client.post(
    f"/agent/conversations/{conversation_id}/interrupts/{interrupt['interrupt_id']}/responses",
    headers={**OWNER_HEADERS, "Authorization": "Bearer quota-resume-e2e-marker"},
    json={
        "client_response_id": str(RESUME_RESPONSE_ID),
        "value": {
            "content": "额度已恢复，继续原任务",
            "materials": [],
            "artifact_refs": [],
            "explicit_action": interrupt["payload"]["authorization_action"],
        },
    },
)
assert resume.status_code == 200
await public_worker.run_until_status(
    job_id=scene_job_id,
    outcome=ProviderJobOutcome.SUCCEEDED,
)
assert fake_provider.start_count(scene_job_id) == 1
assert fake_provider.provider_job_ids(scene_job_id) == {original_provider_job_id}
```

先运行该用例，Expected: 在 Tasks 1–6 尚未完整连接时固定 FAIL 于没有 pause interrupt 或响应未恢复原 job。

- [ ] **Step 2: 补齐重复 402、旧 revision 与增量 SSE**

同一 job 成功前再返回一次 402：

```python
assert second_interrupt["payload"]["authorization_action"]["patch"]["quota_pause_revision"] == 2
stale = await _respond_interrupt(
    client,
    second_interrupt,
    patch={"job_id": scene_job_id, "quota_pause_revision": 1},
    authorization="Bearer stale-revision-marker",
)
assert stale.status_code == 409
assert stale.json()["reason_code"] == "video_quota_resume_stale"
```

从 pause 前 cursor、pause cursor、resume cursor 和 terminal cursor 分段消费 SSE；每段都送入独立 reducer，并逐段比较 Snapshot 的 workflow、Turn、interrupt、context version、cursor、sequence 与附件。禁止只比较最终轮。

- [ ] **Step 3: 让所有 fault 真实穿过生产链路**

`test_agent_runtime_r2_integration.py` 的参数表保留 11 项，但每项都从真实 Operation/Graph/Turn 开始。timeout、failed、404/expired 与 partial failure 必须调用生产 `OperationCompletionCoordinator`、`OperationCompletionDispatcher`、`VideoOperationCompletionHandler`；402 必须调用生产 `OperationQuotaCoordinator`、`OperationQuotaDispatcher`、`VideoOperationQuotaStateHandler`。不得预置 `WAITING_USER` 后比较不变。

每个 fault 固定断言精确 reason：

```python
EXPECTED_REASON = {
    "status_402": "provider_quota_insufficient",
    "timeout": "provider_timeout",
    "failed": "provider_business_failed",
    "expired_404": "provider_job_expired",
    "cross_tenant": "tenant_scope_not_found",
    "model_profile_expired": "model_profile_unverified",
    "handler_missing_after_restart": "agent_runtime_unavailable",
}
```

人工裁定（2026-08-03）：沿用 M06 Provider Adapter 和公共 API 已冻结的
`provider_timeout`、`provider_business_failed`、`tenant_scope_not_found` 原因码，
只校正本计划、测试和交付报告的预期，不修改生产原因合同，也不引入兼容迁移。
402 场景继续区分暂停故障原因 `provider_quota_insufficient` 与最终完成原因
`provider_succeeded`。

对 checkpoint 前后退出使用生产 Supervisor Graph 与 SQLite Checkpointer；进程重建后校验原 Turn/interrupt、event ID 和 Provider start 计数。

- [ ] **Step 4: 扩展安全泄漏与对抗子类扫描**

为 402、timeout、failed、404 分别捕获 `caplog.records`，并扫描：

```python
serialized_boundaries = [
    operation_snapshot,
    quota_events,
    completion_events,
    turn_records,
    checkpoint_values,
    snapshot_payload,
    sse_segments,
    projection_messages,
    [record.getMessage() for record in caplog.records],
]
dumped = json.dumps(serialized_boundaries, ensure_ascii=False, default=str)
assert "quota-resume-e2e-marker" not in dumped
assert "provider-raw-error-marker" not in dumped
```

带 `secret_only` 字段的 Turn、Context、completion 和 quota Event 对抗子类必须实际经过 Executor、CompletionHandler 或 QuotaStateHandler；消费边界立即 `model_validate(model_dump(...))`，额外字段要么被严格 DTO 丢弃，要么固定 fail-closed。

- [ ] **Step 5: 运行 Task 14 聚焦 GREEN**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_r2_integration.py tests/test_agent_runtime_video_live_e2e.py -q
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_turn_executor.py tests/test_agent_runtime_context_assembler.py tests/test_agent_video_live_handler.py tests/test_agent_video_live_operations.py tests/test_agent_runtime_operation_recovery.py -q
.venv\Scripts\python.exe -m ruff check pixelflow/agent_runtime pixelflow/agent_workflows/video app/gateway/pixelflow_agent_runtime.py tests/test_agent_runtime_r2_integration.py tests/test_agent_runtime_video_live_e2e.py tests/test_agent_runtime_turn_executor.py tests/test_agent_runtime_context_assembler.py tests/test_agent_video_live_handler.py tests/test_agent_video_live_operations.py tests/test_agent_runtime_operation_recovery.py
```

Expected: 三条命令均 exit 0；status 402 从公共入口恢复同一 job/attempt，start 新增 0，第二次 402 使用 revision 2，所有边界无 marker。

- [ ] **Step 6: 提交 Task 7**

```powershell
git add backend/tests/test_agent_runtime_r2_integration.py backend/tests/test_agent_runtime_video_live_e2e.py backend/tests/test_agent_runtime_turn_executor.py backend/tests/test_agent_runtime_context_assembler.py
git commit -m "验证：重做 R2 配额恢复真实全流程" -m "移除手工恢复和预置 Turn 自证，让 402、终态、崩溃窗口、SSE 与泄漏矩阵真实经过生产链路。"
```

---

### Task 8：更新文档、运行最终门禁并请求独立复审

**Files:**
- Modify: `docs/agentization/test-reports/R2-video-live-handler.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Modify: `docs/agentization/status/BOARD.md`
- Modify: `docs/agentization/status/M13-status.md`
- Modify: `.superpowers/sdd/task-14-report.md`（本地交接证据，若仍被忽略则不强制跟踪）
- Modify: `.superpowers/sdd/task-14-review.md`（由独立 reviewer 写入）

**Interfaces:**
- Consumes: Tasks 1–7 的提交、测试输出和独立 reviewer 结论。
- Produces: 可复现的 Task 14 最终门禁记录；状态仍为开发候选，生产仍为 R1。

- [ ] **Step 1: 更新设计、报告与状态措辞**

必须删除或纠正以下旧结论：

- “先 `recover_manually()`，再 `VideoLiveOperationBridge.start()` 能证明公开 402 恢复”；
- “402 已有生产调用者”；
- “预置 WAITING_USER Turn 可以证明 Completion/Quota Handler”；
- “Task 14 已可执行 R2 生产发布”。

报告写入：实现提交 SHA、Memory/SQL 结果、pause/resume event ID/revision、Graph crash window、公共 FastAPI 请求、Provider start 计数、逐段 SSE、泄漏扫描、完整命令与通过数。BOARD/M13 只允许记录：

```text
review_fix_local_verified:Task14 / awaiting_independent_slot_integration
生产继续保持 R1 assist / [] / 100 / true
未执行生产迁移、真实付费 Provider、R2 发布、M13.3 或 Agent→dev 合并
```

- [ ] **Step 2: 运行后端聚焦与全量门禁**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_r2_integration.py tests/test_agent_runtime_video_live_e2e.py tests/test_agent_video_live_handler.py tests/test_agent_video_live_operations.py tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_operation_completion.py tests/test_agent_runtime_repositories.py tests/test_agent_runtime_migration.py tests/test_agent_runtime_event_outbox.py tests/test_agent_runtime_gateway_readiness.py tests/test_agent_runtime_turn_executor.py tests/test_agent_runtime_context_assembler.py -q
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
```

Expected: 聚焦与 Ruff exit 0；全量除已登记且可从基线复现的 `tests/test_agent_video_live_capabilities.py::test_agent_runtime_package_keeps_public_export_identity_and_errors` 外不得新增失败。若该基线在当前 HEAD 已修复，则全量必须零失败。

- [ ] **Step 3: 运行 Web、中文与配置隔离门禁**

Run:

```powershell
cd web
corepack pnpm test:agent-runtime-contracts
corepack pnpm test
corepack pnpm lint
corepack pnpm build-prod
cd ..
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 -RepositoryPath (Get-Location).Path -BaseRef b1d2a64 -HeadRef HEAD
git diff --check b1d2a64..HEAD
git diff --exit-code b1d2a64..HEAD -- backend/config.prod.yml
$redFlags = @('TO' + 'DO', 'TB' + 'D', 'FIX' + 'ME', '待' + '定', '稍后' + '补', '后续' + '补')
rg -n ($redFlags -join '|') backend docs/agentization docs/pixelflow-agent-skill-flow-latest-design.md
```

Expected: Web 四条、中文门禁、diff 与生产配置隔离均 exit 0；占位符扫描只允许仓库既有且与本切片无关的命中，新改文件必须零命中。

- [ ] **Step 4: 核查安全边界和测试进程残留**

Run:

```powershell
git status --short
git diff --name-only b1d2a64..HEAD
git diff --exit-code b1d2a64..HEAD -- backend/config.prod.yml backend/config.dev.yml
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'pytest|operation-recovery|pixelflow' -and
    $_.CommandLine -match 'r2-live-video-handler'
} | Select-Object ProcessId, Name, CommandLine
```

Expected: 工作区只含计划内文件；prod/dev 配置零差异；没有本 worktree 残留 pytest、Recovery Runtime 或测试服务进程。发现门禁失败、敏感值、配置漂移或残留时立即停止，不提交完成状态。

- [ ] **Step 5: 提交文档证据**

```powershell
git add docs/agentization/test-reports/R2-video-live-handler.md docs/pixelflow-agent-skill-flow-latest-design.md docs/agentization/status/BOARD.md docs/agentization/status/M13-status.md
git commit -m "文档：登记 R2 配额恢复门禁证据" -m "记录真实 402 pause/resume Outbox、崩溃恢复、公共 E2E 和安全扫描结果，并明确生产仍保持 R1。"
```

- [ ] **Step 6: 请求独立双阶段复审**

按 `superpowers:requesting-code-review` 提交完整 diff，要求 reviewer 分两阶段输出：

1. spec compliance：逐条核对已批准设计第 1–14 节；
2. code quality：Repository 原子性、Graph/Turn 幂等、凭据生命周期、测试是否自证、注释中文规范。

Critical 或 Important 结论未清零时不得写完成状态。允许的修复必须先增加复现 RED，再最小实现 GREEN，并重新执行本 Task 的全部门禁。

- [ ] **Step 7: 最终候选提交与停止点**

复审修复完成后，确认：

```powershell
git status --short
git log --oneline --decorate -12
git diff --exit-code b1d2a64..HEAD -- backend/config.prod.yml
```

Expected: 工作区干净、中文提交链完整、生产配置零差异。随后立即停止并报告证据；不得 push、不得执行数据库生产迁移、独立单槽集成、真实付费测试、R2 发布、M13.3 或 Agent→dev 合并，除非用户分别明确授权。

---

## 计划完成判定

本计划只有在以下证据同时成立时才算完成：

1. 402 pause 与用户 resume 都产生持久化、可租约重放的 `external_job.quota_state_changed` 事件；
2. Memory/SQL 的 revision、CAS、Outbox、due-operation 与崩溃恢复语义一致；
3. 原 provider job、attempt 与 start 次数不变；恢复请求不会调用 Provider start；
4. 每次 402 有独立 revision、事件、checkpoint 与 interrupt，旧 revision 固定失败关闭；
5. 新 Authorization 只消费一次并销毁，所有持久化、投影、SSE、checkpoint 和日志无 marker；
6. timeout/failed/404/partial failure 真正经过生产 CompletionHandler，402 真正经过生产 QuotaStateHandler；
7. 聚焦、全量、Web、Ruff、中文、diff、生产配置隔离和进程残留门禁均有可复现结果；
8. 独立复审没有未处理的 Critical/Important；
9. 状态只登记开发候选完成，生产保持 R1，未越权执行 R2 发布或生产迁移。
