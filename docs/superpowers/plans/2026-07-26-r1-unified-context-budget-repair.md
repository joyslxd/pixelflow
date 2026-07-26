# M13.1 / R1 统一上下文预算与压缩恢复修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复真实长视频上下文重复计入和失败重试风暴，把所有现有及未来 PixelFlow Agent 节点统一改为 dev/prod 配置驱动的 896K 有效窗口，并完成四流程自动化与 Chrome 视频验收。

**Architecture:** 模型物理能力继续由 `models[].context_profile` 描述，业务预算由 `pixelflow.agent_runtime.context_budget` 统一提供；Gateway 启动时把两者冻结并注入 Context Runtime，节点名只用于审计，不再选择不同窗口。压缩失败使用同一持久化租约保存 30 秒 `retry_not_before`，恢复入口只在到期后单飞接管；Conversation Store 保留完整恢复 DTO，模型投影排除重复恢复载荷。

**Tech Stack:** Python 3.12、Pydantic、FastAPI、SQLAlchemy、pytest/pytest-asyncio、SQLite、React/TypeScript 合同测试、PowerShell 中文工程门禁、Chrome 本地浏览器验收。

## Global Constraints

- DeepSeek V4 Pro 物理上下文硬上限按用户确认的 `1,000,000 tokens` 记录。
- 全局有效窗口配置为 `effective_context_k=896`，按 `1K=1024 tokens` 转换为 `917,504 tokens`。
- 输出预留配置为 `output_reserve_k=32`，安全预留配置为 `safety_reserve_k=32`，可用输入必须为 `832K=851,968 tokens`。
- 压缩阈值保持 `60% / 72% / 85% / 92%`，严格目标保持可用输入的 45%。
- dev/prod 使用相同预算和模型档案；dev 保持 `assist / [] / 100 / true`，prod 保持 `mode=off`。
- 严格模式下模型档案缺失、未验证、未来验证时间或过期时必须 fail-closed，不得实际使用 128K fallback。
- 每个新增或修改的配置叶子项必须有紧邻中文注释，说明用途、影响、单位、默认值、范围、重启要求、生效对象和回滚方式。
- 所有新增或修改的人工注释、docstring、测试结论、提交标题和正文使用中文主体语义。
- 不调用付费图片、视频、PPT 或剪映生成接口；Chrome 验收停在 plan.md 或场景包确认之前。
- 不修改生产 rollout、`enabled_intents` 或自动化状态，不把 `automation_local_ready` 提升为 `automation_active`。

---

### Task 1: 排除模型预算中的恢复专用 Plan 修订请求

**Files:**
- Modify: `backend/tests/test_agent_runtime_r1_integration.py`
- Modify: `backend/pixelflow/agent_runtime/runtime_compaction.py`

**Interfaces:**
- Consumes: `ContextBudgetGuard.build_request()`、`_business_context(context: dict) -> dict`
- Produces: Store 完整保留恢复 DTO、模型预算排除 camelCase/snake_case Plan 修订请求的共享投影

- [ ] **Step 1: 写入真实体量失败测试**

在 `test_agent_runtime_r1_integration.py` 的旧修订快照测试旁增加：

```python
@pytest.mark.asyncio
async def test_r1_budget_excludes_plan_revision_request_but_keeps_store() -> None:
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    revision_request = {
        "conversationId": "r1-plan-revision-request",
        "artifact": {
            "plan": "完整 Plan 修订恢复数据" * 12_000,
            "intent": "video",
        },
    }
    assignment = service.assignment_for_new_conversation(
        {
            "creation_contract": {"duration_seconds": 30, "ratio": "9:16"},
            "pendingPlanRevisionRequest": revision_request,
            "pending_plan_revision_request": revision_request,
        },
    )
    conversation = await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="r1-plan-revision-request",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    current = PixelFlowConversationMessageRecord(
        message_id="r1-plan-revision-current",
        conversation_id=conversation.conversation_id,
        user_id=str(USER_ID),
        role="user",
        content="只调整开头氛围，创作合同保持不变",
        payload={},
        created_at=NOW.isoformat(),
    )
    await task_store.append_conversation_message(current)
    guard = ContextBudgetGuard(
        task_store=task_store,
        repository=repository,
        model_name=R1_TEST_MODEL,
        model_profiles={R1_TEST_MODEL: _r1_test_profile()},
        clock=lambda: NOW,
    )

    request = await guard.build_request(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        current_message_id=current.message_id,
    )
    restored = await task_store.get_conversation(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )

    assert request.budget_report.estimated_input_tokens < 10_000
    assert restored is not None
    assert restored.context["pendingPlanRevisionRequest"] == revision_request
    assert restored.context["pending_plan_revision_request"] == revision_request
```

该测试捕获的生产破坏是：任一恢复请求字段重新进入 `_business_context()`，预算会显著超过字面上限。

- [ ] **Step 2: 运行 RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_runtime_r1_integration.py::test_r1_budget_excludes_plan_revision_request_but_keeps_store -q
```

Expected: FAIL，`estimated_input_tokens` 大于 `10_000`；Store 保留断言仍通过。

- [ ] **Step 3: 写最小实现**

把 `runtime_compaction.py` 的恢复专用集合改名为 `_RECOVERY_ONLY_CONTEXT_KEYS`，在既有八个旧修订键之外增加：

```python
"pendingPlanRevisionRequest",
"pending_plan_revision_request",
```

`_business_context()` 只过滤该集合，继续对其余字段 `deepcopy()`，不得修改 Conversation Store。

- [ ] **Step 4: 运行 GREEN 和相邻回归**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_r1_integration.py::test_r1_budget_excludes_plan_revision_request_but_keeps_store \
  tests/test_agent_runtime_r1_integration.py::test_r1_budget_excludes_legacy_revision_snapshot_but_keeps_store \
  -q
```

Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/pixelflow/agent_runtime/runtime_compaction.py backend/tests/test_agent_runtime_r1_integration.py
git commit -m "修复(R1)：排除恢复专用修订请求" -m "保留对话恢复数据，同时避免完整 Plan 修订请求重复进入模型上下文预算。"
```

### Task 2: 为失败压缩增加持久化 30 秒退避

**Files:**
- Modify: `backend/tests/test_agent_runtime_compaction_events.py`
- Modify: `backend/tests/test_agent_runtime_compaction_queue.py`
- Modify: `backend/pixelflow/agent_runtime/context/compaction.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/compaction_queue.py`

**Interfaces:**
- Consumes: `ConversationCompactionRuntime.compact()`、`CompactionQueueRepository.finish_compaction*()`
- Produces: `retry_not_before: datetime | None` 的 Memory/SQL 一致合同

- [ ] **Step 1: 把失败租约测试改为未来到期**

在 `test_agent_runtime_compaction_events.py` 中使用 `RETRY_BACKOFF = timedelta(seconds=30)` 装配 Runtime，并把现有：

```python
assert recovery.lease_expires_at <= NOW
```

改为：

```python
assert recovery.lease_expires_at == NOW + RETRY_BACKOFF
```

同时为暂停、执行异常、进度事件失败三个分支分别保留断言，确保任何失败都不能立即到期。

- [ ] **Step 2: 运行 RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_compaction_events.py::test_progress_event_failure_stops_compaction_and_preserves_recovery_marker \
  tests/test_agent_runtime_compaction_events.py::test_runtime_persists_safe_recoverable_failed_event \
  -q
```

Expected: FAIL，实际租约时间仍等于 `NOW`。

- [ ] **Step 3: 固定 Repository 参数合同**

在 `CompactionQueueRepository` Protocol、Memory 和 SQL 实现的两个收尾方法增加：

```python
retry_not_before: datetime | None = None
```

统一校验：

```python
if claim_next and retry_not_before is not None:
    raise ValueError("压缩成功领取队首时不得设置重试时间")
if not claim_next:
    normalized_retry = _normalize_datetime(
        "retry_not_before",
        retry_not_before,
    )
    if normalized_retry <= normalized_now:
        raise ValueError("retry_not_before 必须晚于压缩失败时间")
```

Memory 的失败租约写入 `normalized_retry`；SQL 的 `coordination.lease_expires_at` 同样写入 `normalized_retry`。事件 `occurred_at` 仍使用真实失败时间，不得写成未来时间。

- [ ] **Step 4: Runtime 传递退避时间**

`ConversationCompactionRuntime.__init__()` 增加：

```python
retry_backoff: timedelta = timedelta(seconds=30)
```

拒绝小于等于零的值。所有 `claim_next=False` 收尾使用同一次 `failed_at = self._clock()`，传递：

```python
now=failed_at,
retry_not_before=failed_at + self._retry_backoff,
```

成功收尾显式传 `retry_not_before=None`。

- [ ] **Step 5: 增加 Memory/SQL 参数化测试并运行 GREEN**

在 `test_agent_runtime_compaction_queue.py` 复用现有 repository fixture，验证：

```python
await repository.finish_compaction(
    OWNER,
    CONVERSATION,
    lease_owner=lease.lease_owner,
    lease_token=lease.lease_token,
    now=NOW,
    claim_next=False,
    retry_not_before=NOW + timedelta(seconds=30),
)
recovery = await repository.get_compaction_lease(OWNER, CONVERSATION)
assert recovery is not None
assert recovery.lease_expires_at == NOW + timedelta(seconds=30)
```

另加两个非法组合测试：失败缺少未来时间、成功携带未来时间。

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_compaction_events.py \
  tests/test_agent_runtime_compaction_queue.py \
  -q
```

Expected: 全部通过。

- [ ] **Step 6: 提交**

```bash
git add \
  backend/pixelflow/agent_runtime/context/compaction.py \
  backend/pixelflow/agent_runtime/persistence/compaction_queue.py \
  backend/tests/test_agent_runtime_compaction_events.py \
  backend/tests/test_agent_runtime_compaction_queue.py
git commit -m "修复(R1)：持久化压缩失败退避时间" -m "Memory 与 SQL 队列统一保存三十秒重试边界，避免失败租约立即重新到期。"
```

### Task 3: 三个恢复入口只在退避到期后单飞唤醒

**Files:**
- Modify: `backend/tests/test_agent_runtime_r1_integration.py`
- Modify: `backend/pixelflow/agent_runtime/service.py`

**Interfaces:**
- Consumes: 持久化 `ConversationCompactionLease.lease_expires_at`
- Produces: `_schedule_compaction_recovery_if_due(user_id, conversation_id, lease=None)` 共享唤醒门禁

- [ ] **Step 1: 写入轮询风暴失败测试**

把现有 `test_r1_failed_compaction_is_recovered_by_snapshot_or_event_reader` 改为可推进时钟：

```python
current_time = [NOW]
service = AgentRuntimeService(
    ...,
    clock=lambda: current_time[0],
)
```

首次失败后连续调用三种入口：

```python
for _ in range(5):
    await service.snapshot(user_id=str(USER_ID), conversation_id=conversation.conversation_id)
    await service.events_after(user_id=str(USER_ID), conversation_id=conversation.conversation_id, cursor=None)
    await service.get_run(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        run_id=started.run_id,
    )
    await asyncio.sleep(0)

events_during_backoff = await repository.list_events(str(USER_ID), conversation.conversation_id)
assert len(events_during_backoff) == 2
assert executor.calls == 1
```

推进到边界：

```python
current_time[0] = NOW + timedelta(seconds=30)
await service.snapshot(...)
for _ in range(20):
    await asyncio.sleep(0)
    if executor.calls == 2:
        break
assert executor.calls == 2
```

再连续读取，断言不会产生第三次接管。

- [ ] **Step 2: 运行 RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_runtime_r1_integration.py::test_r1_failed_compaction_is_recovered_by_snapshot_or_event_reader -q
```

Expected: FAIL，退避期读取触发第二次压缩或增加失败事件。

- [ ] **Step 3: 实现统一到期判断**

在 `AgentRuntimeService` 增加异步 helper：

```python
async def _schedule_compaction_recovery_if_due(
    self,
    user_id: str,
    conversation_id: str,
    *,
    lease: ConversationCompactionLease | None = None,
) -> None:
    current = lease or await self.repository.get_compaction_lease(
        user_id,
        conversation_id,
    )
    if current is None or current.lease_expires_at > self._clock():
        return
    self._schedule_compaction_recovery(user_id, conversation_id)
```

Snapshot 复用已经读取的 lease；`events_after()` 和 `get_run()` 调用该 helper。删除“看到任何 failed event 就直接 schedule”和“只要 queued 就直接 schedule”的旧分支。保留 `_schedule_compaction_recovery()` 的进程内 `(user_id, conversation_id)` 单飞字典。

- [ ] **Step 4: 运行 GREEN 与队列恢复回归**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_r1_integration.py::test_r1_failed_compaction_is_recovered_by_snapshot_or_event_reader \
  tests/test_agent_runtime_r1_integration.py::test_r1_compaction_queue_and_recovery_snapshot_share_one_repository \
  -q
```

Expected: 2 passed，退避到期后原 Turn 进入 processing，消息和 Turn 数量不增加。

- [ ] **Step 5: 提交**

```bash
git add backend/pixelflow/agent_runtime/service.py backend/tests/test_agent_runtime_r1_integration.py
git commit -m "修复(R1)：限制压缩恢复唤醒频率" -m "Snapshot、SSE 与 Turn 轮询共用持久化到期门禁，到期前不创建恢复任务。"
```

### Task 4: 增加配置驱动的统一 896K 预算合同

**Files:**
- Modify: `backend/tests/test_agent_runtime_config.py`
- Modify: `backend/tests/test_profile_config.py`
- Modify: `backend/pixelflow/agent_runtime/config.py`
- Modify: `backend/app/gateway/profile_config.py`

**Interfaces:**
- Consumes: dev/prod YAML 与环境变量映射
- Produces: `ContextBudgetConfig`、`AgentRuntimeConfig.context_budget`、`AgentRuntimeConfig.compaction_retry_backoff_seconds`

- [ ] **Step 1: 写配置模型失败测试**

在 `test_agent_runtime_config.py` 增加：

```python
def test_context_budget_config_converts_k_values_to_tokens() -> None:
    config = AgentRuntimeConfig(
        context_budget={
            "effective_context_k": 896,
            "output_reserve_k": 32,
            "safety_reserve_k": 32,
            "require_verified_model_profile": True,
        },
        compaction_retry_backoff_seconds=30,
    )

    assert config.context_budget.effective_context_tokens == 917_504
    assert config.context_budget.output_reserve_tokens == 32_768
    assert config.context_budget.safety_reserve_tokens == 32_768
    assert config.context_budget.usable_input_tokens == 851_968
```

增加参数化非法输入：布尔值、0、负数、预留和大于等于有效窗口、退避小于 1 秒。

- [ ] **Step 2: 写 Profile loader 失败测试**

在 `test_profile_config.py` 构造临时 YAML，断言五个叶子项映射到：

```text
PIXELFLOW_AGENT_RUNTIME_CONTEXT_EFFECTIVE_K
PIXELFLOW_AGENT_RUNTIME_CONTEXT_OUTPUT_RESERVE_K
PIXELFLOW_AGENT_RUNTIME_CONTEXT_SAFETY_RESERVE_K
PIXELFLOW_AGENT_RUNTIME_REQUIRE_VERIFIED_MODEL_PROFILE
PIXELFLOW_AGENT_RUNTIME_COMPACTION_RETRY_BACKOFF_SECONDS
```

同时断言嵌套未知键和空值 fail-closed。

- [ ] **Step 3: 运行 RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_runtime_config.py tests/test_profile_config.py -q
```

Expected: FAIL，当前配置模型拒绝 `context_budget`，Profile loader 报未知键。

- [ ] **Step 4: 实现不可变配置模型**

在 `config.py` 增加：

```python
class ContextBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effective_context_k: int = Field(ge=1)
    output_reserve_k: int = Field(ge=1)
    safety_reserve_k: int = Field(ge=1)
    require_verified_model_profile: bool = True

    @property
    def effective_context_tokens(self) -> int:
        return self.effective_context_k * 1024

    @property
    def output_reserve_tokens(self) -> int:
        return self.output_reserve_k * 1024

    @property
    def safety_reserve_tokens(self) -> int:
        return self.safety_reserve_k * 1024

    @property
    def usable_input_tokens(self) -> int:
        return (
            self.effective_context_tokens
            - self.output_reserve_tokens
            - self.safety_reserve_tokens
        )
```

用 `model_validator` 拒绝非正可用输入。`AgentRuntimeConfig` 增加该字段和 `compaction_retry_backoff_seconds: int = Field(default=30, ge=1)`。环境解析使用严格十进制整数解析器，不允许布尔字符串混入整数。

- [ ] **Step 5: 扩展 YAML 映射与嵌套校验**

`profile_config.py` 的允许字段把 `context_budget` 识别为对象；分别校验四个叶子项。五个环境变量映射必须逐项加入 `_ENV_KEY_MAP`，不得使用 `environment.variables` 绕过中文配置合同。

- [ ] **Step 6: 运行 GREEN**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_runtime_config.py tests/test_profile_config.py -q
```

Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
git add \
  backend/pixelflow/agent_runtime/config.py \
  backend/app/gateway/profile_config.py \
  backend/tests/test_agent_runtime_config.py \
  backend/tests/test_profile_config.py
git commit -m "功能(R1)：增加统一上下文预算配置" -m "以K为单位绑定896K窗口、32K输出、32K安全预留和三十秒退避。"
```

### Task 5: 所有现有与未来节点共用同一预算 Provider

**Files:**
- Modify: `backend/tests/test_agent_runtime_token_meter.py`
- Modify: `backend/tests/test_agent_runtime_context_profiles.py`
- Modify: `backend/tests/test_agent_runtime_context_assembler.py`
- Modify: `backend/tests/test_agent_runtime_compaction_coordinator.py`
- Modify: `backend/pixelflow/agent_runtime/context/token_meter.py`
- Modify: `backend/pixelflow/agent_runtime/context/assembler.py`
- Modify: `backend/pixelflow/agent_runtime/context/compaction.py`
- Modify: `backend/pixelflow/agent_runtime/runtime_compaction.py`

**Interfaces:**
- Consumes: `ContextBudgetConfig`、`ModelContextProfileResolution`
- Produces: `ContextBudgetPolicyProvider.policy_for(node: str)` 与严格生产装配

- [ ] **Step 1: 写未来节点继承失败测试**

在 `test_agent_runtime_token_meter.py` 增加：

```python
def test_configured_policy_provider_gives_every_node_same_budget() -> None:
    provider = ContextBudgetPolicyProvider(
        effective_context_tokens=917_504,
        output_reserve_tokens=32_768,
        safety_reserve_tokens=32_768,
    )

    policies = [
        provider.policy_for(node)
        for node in (
            "supervisor",
            "image",
            "image_edit",
            "video",
            "ppt",
            "video_analysis",
            "summary",
            "future_agent_node",
        )
    ]

    assert all(policy == policies[0] for policy in policies)
    assert policies[0].effective_context_cap_tokens == 917_504
    assert policies[0].output_reserve_tokens == 32_768
    assert policies[0].safety_reserve_tokens == 32_768
```

再构造第二个 Provider 使用 `640/16/48K`，断言所有节点同步变化，证明预算来自实例配置而非模块常量。

- [ ] **Step 2: 写严格档案失败测试**

在 `test_agent_runtime_context_profiles.py` 增加：

```python
@pytest.mark.parametrize(
    "status",
    ["fallback_missing", "fallback_unverified", "fallback_expired"],
)
def test_require_verified_profile_rejects_fallback_status(status: str) -> None:
    resolution = ModelContextProfileResolution(
        status=status,
        profile=_conservative_profile("target-model"),
    )
    with pytest.raises(ValueError, match="已验证模型上下文档案"):
        require_verified_model_context_profile(resolution)
```

验证 `verified` 返回原档案。

- [ ] **Step 3: 运行 RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_token_meter.py \
  tests/test_agent_runtime_context_profiles.py \
  -q
```

Expected: FAIL，新 Provider 和严格 helper 尚不存在。

- [ ] **Step 4: 实现 Provider 和严格 helper**

删除 `_CONTEXT_BUDGET_POLICIES` 分节点表，增加冻结 Provider：

```python
class ContextBudgetPolicyProvider:
    def __init__(
        self,
        *,
        effective_context_tokens: int,
        output_reserve_tokens: int,
        safety_reserve_tokens: int,
    ) -> None:
        self._policy = ContextBudgetPolicy(
            effective_context_cap_tokens=effective_context_tokens,
            output_reserve_tokens=output_reserve_tokens,
            safety_reserve_tokens=safety_reserve_tokens,
        )

    def policy_for(self, node: str) -> ContextBudgetPolicy:
        if not node.strip():
            raise ValueError("上下文预算节点名不能为空")
        return self._policy
```

保留一个仅供兼容测试使用的默认保守 Provider，但生产 Runtime 装配必须显式传入配置 Provider。严格 helper 只接受 `resolution.status == "verified"`。

- [ ] **Step 5: 注入三个消费者**

给以下构造函数增加 `budget_policy_provider`：

- `ContextAssembler`
- `ContextCompactionCoordinator`
- `ContextBudgetGuard`

把所有 `get_context_budget_policy("...")` 调用改为 `self._budget_policy_provider.policy_for("...")`。节点名保留用于审计，但预算相同。

`build_agent_context_compactor()`：

1. 从 `AgentRuntimeConfig.context_budget` 创建一次 Provider。
2. 解析实际摘要模型档案。
3. `require_verified_model_profile=true` 时调用严格 helper；失败即阻止压缩组件装配。
4. 把同一 Provider 注入 Guard 和 Coordinator。
5. 把 `compaction_retry_backoff_seconds` 转成 `timedelta` 注入 Runtime。

- [ ] **Step 6: 更新 assembler/coordinator 测试并运行 GREEN**

所有测试 helper 显式注入小窗口 Provider，避免依赖生产 896K。增加断言：

```python
assert envelope.budget_report.effective_context_tokens == 917_504
assert envelope.budget_report.usable_input_tokens == 851_968
```

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_token_meter.py \
  tests/test_agent_runtime_context_profiles.py \
  tests/test_agent_runtime_context_assembler.py \
  tests/test_agent_runtime_compaction_coordinator.py \
  tests/test_agent_runtime_r1_integration.py \
  -q
```

Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
git add \
  backend/pixelflow/agent_runtime/context/token_meter.py \
  backend/pixelflow/agent_runtime/context/profiles.py \
  backend/pixelflow/agent_runtime/context/assembler.py \
  backend/pixelflow/agent_runtime/context/compaction.py \
  backend/pixelflow/agent_runtime/runtime_compaction.py \
  backend/tests/test_agent_runtime_token_meter.py \
  backend/tests/test_agent_runtime_context_profiles.py \
  backend/tests/test_agent_runtime_context_assembler.py \
  backend/tests/test_agent_runtime_compaction_coordinator.py \
  backend/tests/test_agent_runtime_r1_integration.py
git commit -m "功能(R1)：统一所有 Agent 节点预算" -m "现有和未来节点共用配置 Provider，严格模式拒绝一百二十八K降级档案。"
```

### Task 6: 写入 dev/prod 配置并通过中文配置门禁

**Files:**
- Modify: `backend/config.dev.yml`
- Modify: `backend/config.prod.yml`
- Modify: `backend/tests/test_profile_config.py`
- Modify: `scripts/agentization/tests/ChineseEngineeringPolicy.Tests.ps1`（仅当既有门禁不能识别嵌套配置时）

**Interfaces:**
- Consumes: Task 4 配置字段、Task 5 严格模型档案
- Produces: dev/prod 同构启动配置

- [ ] **Step 1: 写 Profile 真实文件断言**

在 `test_profile_config.py` 增加读取两个真实 YAML 的参数化测试，断言：

```python
runtime["context_budget"] == {
    "effective_context_k": 896,
    "output_reserve_k": 32,
    "safety_reserve_k": 32,
    "require_verified_model_profile": True,
}
runtime["compaction_retry_backoff_seconds"] == 30
models[0]["context_profile"]["max_context_tokens"] == 1_000_000
models[0]["context_profile"]["max_output_tokens"] == 32_768
```

同时断言 prod `mode == "off"`，dev 仍为 R1 测试候选值。

- [ ] **Step 2: 运行 RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_profile_config.py -q
```

Expected: FAIL，真实 YAML 尚无新字段。

- [ ] **Step 3: 修改 dev/prod YAML**

在两个 profile 写入设计确认值。每个叶子项使用紧邻中文注释，至少包含：

- 类型和单位。
- 修改后需要重启。
- 重启后影响历史对话下一 Turn 和所有新对话。
- prod 只有未来启用 Runtime 后才生效。
- 回滚值和模型能力证据来源。

不得改动 `api_key`、Authorization、rollout 或生产 mode。

- [ ] **Step 4: 运行配置与中文门禁**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_profile_config.py tests/test_agent_runtime_config.py -q
cd ..
pwsh -NoProfile -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 -RepositoryPath .
```

如果本机无 `pwsh`，使用仓库现有 Windows PowerShell 5.1 门禁入口；不得跳过配置逐叶说明检查。

Expected: 配置测试通过；中文门禁无违规。

- [ ] **Step 5: 提交**

```bash
git add \
  backend/config.dev.yml \
  backend/config.prod.yml \
  backend/tests/test_profile_config.py \
  scripts/agentization/tests/ChineseEngineeringPolicy.Tests.ps1
git commit -m "配置(R1)：启用统一八百九十六K预算" -m "开发与生产配置同步模型档案和全局预算，生产 Runtime 继续保持关闭。"
```

### Task 7: 更新 agentization 长期权威设计

**Files:**
- Modify: `docs/agentization/contracts-v1.md`
- Modify: `docs/agentization/architecture-design.md`
- Modify: `docs/agentization/phased-rollout-plan.md`
- Modify: `docs/agentization/work-breakdown.md`
- Modify: `docs/agentization/branch-and-codex-runbook.md`
- Modify: `docs/agentization/test-matrix.md`
- Modify: `docs/agentization/integration/DECISIONS.md`
- Modify: `docs/agentization/status/M13-status.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`

**Interfaces:**
- Consumes: 已通过测试的最终代码与配置键
- Produces: R2/R3/R4 后续 Agent 任务读取的统一预算权威合同

- [ ] **Step 1: 更新 `contracts-v1.md`**

删除分节点 `256K/384K/512K` 表和“实际流程使用 128K fallback”的旧设计，写入：

```text
模型物理上限：models[].context_profile.max_context_tokens
统一业务窗口：pixelflow.agent_runtime.context_budget.effective_context_k
统一输出预留：output_reserve_k
统一安全预留：safety_reserve_k
严格档案开关：require_verified_model_profile
```

保留 60/72/85/92 与 45% 合同，明确所有未来节点必须从 Provider 获取预算，不得增加节点私有窗口。

- [ ] **Step 2: 更新架构、阶段计划和工作拆分**

`architecture-design.md` 说明 Config DTO → Policy Provider → Assembler/Guard/Coordinator 的依赖方向。

`phased-rollout-plan.md` 在 R2、R3、R4 每个阶段加入：

- 继承统一预算。
- 新增模型先提供验证档案。
- 新增恢复 DTO 必须分类为权威事实、恢复专用或可外置载荷。
- 不得重新引入 128K 实际 fallback。

`work-breakdown.md` 给未来节点切片增加配置继承和长上下文回归验收项。

- [ ] **Step 3: 更新运行手册和测试矩阵**

`branch-and-codex-runbook.md` 的后续任务提示明确要求先读取统一预算配置，不得复制旧分节点常量。

`test-matrix.md` 增加：

- 任意未来节点继承相同预算。
- 修改配置后全部节点同步变化。
- 四类长上下文压缩/排队/恢复。
- 严格档案失败关闭。
- 退避期事件稳定。

- [ ] **Step 4: 写决策、状态和最新设计**

`DECISIONS.md` 新增不可变决策号，记录用户确认的 `1,000,000 / 896K / 32K / 32K`。

`M13-status.md` 只记录本修复状态和验证结论，不误写为生产发布或新阶段集成。

`pixelflow-agent-skill-flow-latest-design.md` 同步当前代码事实和配置路径。

- [ ] **Step 5: 文档一致性检查**

Run:

```bash
rg -n "256K|384K|512K|128K 档案|128K 分块" \
  docs/agentization \
  docs/pixelflow-agent-skill-flow-latest-design.md
```

逐条判断历史记录与当前权威段落：历史状态证据可以保留，但当前合同、架构、计划、运行手册不得继续指示分节点窗口或实际 128K fallback。

Run:

```bash
git diff --check
pwsh -NoProfile -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 -RepositoryPath .
```

- [ ] **Step 6: 提交**

```bash
git add \
  docs/agentization/contracts-v1.md \
  docs/agentization/architecture-design.md \
  docs/agentization/phased-rollout-plan.md \
  docs/agentization/work-breakdown.md \
  docs/agentization/branch-and-codex-runbook.md \
  docs/agentization/test-matrix.md \
  docs/agentization/integration/DECISIONS.md \
  docs/agentization/status/M13-status.md \
  docs/pixelflow-agent-skill-flow-latest-design.md
git commit -m "文档(R1)：同步统一上下文预算设计" -m "更新R2至R4权威合同、实施提示、测试矩阵和M13状态，禁止未来节点复制分散窗口。"
```

### Task 8: 四流程代表性长上下文集成验证

**Files:**
- Modify: `backend/tests/test_agent_runtime_r1_integration.py`
- Create: `docs/agentization/test-reports/M13.1-R1-context-budget-repair.md`

**Interfaces:**
- Consumes: 统一 Provider、严格模型档案、压缩队列、退避恢复
- Produces: image/video/ppt/video_analysis 非付费回归证据

- [ ] **Step 1: 写四流程参数化测试**

增加参数：

```python
@pytest.mark.parametrize(
    ("intent", "business_context"),
    [
        ("image", {"pendingImageRevision": {"prompt": "图片恢复" * 40_000}}),
        ("video", {"pendingPlanRevisionRequest": {"plan": "视频计划" * 40_000}}),
        ("ppt", {"pendingPptOutlineRevision": {"outline": "大纲恢复" * 40_000}}),
        ("video_analysis", {"analysis_result": {"summary": "拆解结果" * 40_000}}),
    ],
)
```

每类流程断言：

- 当前输入完整持久化一次。
- 恢复专用载荷不进入模型投影；权威业务事实继续保留。
- 大型可外置载荷写入稳定 `context-payload:` 引用。
- 压缩期间第二个 Turn 为 queued。
- 压缩完成只领取队首。
- Snapshot 恢复不新增消息或 Turn。

对于 `video_analysis` 的权威结果，如果当前设计要求保留而不是过滤，fixture 必须通过 externalizer 路径证明低于 832K，不得为通过测试错误删除业务事实。

- [ ] **Step 2: 运行 RED 或确认既有实现差距**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_runtime_r1_integration.py -k "all_intents or context_budget" -q
```

Expected: 新增测试至少因缺失参数化行为或未来节点预算合同失败一次；记录每个正确失败原因。

- [ ] **Step 3: 仅修复测试揭示的共享 Runtime 差距**

只允许修改共享 Runtime 投影、externalizer 或测试 fixture。不得在图片、视频、PPT、视频分析 Controller 中复制压缩逻辑。

- [ ] **Step 4: 运行 R1 全集和前端合同**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_runtime_r1_integration.py -q
cd ../web
corepack pnpm test:agent-runtime-contracts
```

Expected: 全部通过。

- [ ] **Step 5: 写中文测试报告并提交**

报告必须记录 RED/GREEN、各 intent 覆盖、未调用付费接口、配置公式、剩余边界和 Chrome 待验项。

```bash
git add \
  backend/tests/test_agent_runtime_r1_integration.py \
  docs/agentization/test-reports/M13.1-R1-context-budget-repair.md
git commit -m "测试(R1)：覆盖四流程统一预算与恢复" -m "验证长上下文压缩、排队、去重和受控重试均走共享Runtime。"
```

### Task 9: 全量门禁与 Chrome 视频真实验收

**Files:**
- Modify: `docs/agentization/test-reports/M13.1-R1-context-budget-repair.md`
- Modify: `docs/agentization/status/M13-status.md`

**Interfaces:**
- Consumes: 前八个任务的代码、配置、文档和自动化
- Produces: 最终非付费真实浏览器证据与可交接状态

- [ ] **Step 1: 运行后端定向和全量门禁**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_config.py \
  tests/test_profile_config.py \
  tests/test_agent_runtime_context_profiles.py \
  tests/test_agent_runtime_token_meter.py \
  tests/test_agent_runtime_context_assembler.py \
  tests/test_agent_runtime_compaction_coordinator.py \
  tests/test_agent_runtime_compaction_events.py \
  tests/test_agent_runtime_compaction_queue.py \
  tests/test_agent_runtime_r1_integration.py \
  -q
.venv/bin/python -m ruff check pixelflow/agent_runtime app/gateway/profile_config.py tests/test_agent_runtime_*.py tests/test_profile_config.py
.venv/bin/python -m pytest -q
```

Expected: 所有命令退出码 0；已知第三方 warning 单独记录，不得把 warning 写成失败或忽略新增 warning。

- [ ] **Step 2: 运行前端、构建和中文门禁**

Run:

```bash
cd web
corepack pnpm test:agent-runtime-contracts
corepack pnpm test
corepack pnpm build
cd ..
pwsh -NoProfile -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 -RepositoryPath .
git diff --check
```

Expected: 全部退出码 0。

- [ ] **Step 3: 启动本地 dev profile**

后端：

```bash
cd backend
PIXELFLOW_CONFIG_ENV=dev PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  .venv/bin/python -m app.gateway.run --reload
```

前端：

```bash
cd web
corepack pnpm dev:test -- --host 0.0.0.0 --port 5273
```

确认端口 `8001`、`5273` 正常，启动日志显示 `assist / 100%`，并通过安全诊断接口或只读日志确认：

```text
effective_context_tokens=917504
output_reserve_tokens=32768
safety_reserve_tokens=32768
usable_input_tokens=851968
profile_status=verified
```

- [ ] **Step 4: 在本机 Chrome 执行视频流程**

使用 Chrome 调试 token 页面保存用户提供的 Authorization，不读取 localStorage/cookie，不在输出中回显 token。

按设计文档执行 30 秒、9:16、Seedance 2.0、gpt-image-2 视频流程，停在 plan.md 审核。通过非付费方式构造超过 60% 的上下文，压缩运行期间提交第二条输入。

可见验收：

- 压缩提示出现。
- 第二条输入显示稳定队列位置。
- 压缩完成提示出现。
- 第一条 Turn 进入 processing，第二条保持队首或按接力顺序执行。
- 刷新和切换对话不重复消息。

- [ ] **Step 5: 验证受控失败重试**

使用测试专用可恢复失败注入，不修改生产配置、不制造超大真实 LLM 请求。记录失败事件数，退避 30 秒内持续读取 Snapshot/SSE/Turn，事件数保持不变；边界到期后只增加一次恢复序列。

- [ ] **Step 6: 收尾**

离开会触发轮询的工作台页面，关闭本地前后端并确认端口释放。Chrome 标签页交还用户。报告记录对话 ID、Turn/消息去重计数、压缩事件序列和是否继续旧 v2。

- [ ] **Step 7: 最终提交**

```bash
git add \
  docs/agentization/test-reports/M13.1-R1-context-budget-repair.md \
  docs/agentization/status/M13-status.md
git commit -m "验证(R1)：完成统一预算真实视频验收" -m "记录自动压缩成功、排队接力、刷新恢复和三十秒受控重试证据。"
```

