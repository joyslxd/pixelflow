# R2 视频 live Handler 与真实 Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让新建的 `supervisor_v1` 视频对话由后端真实消费 Turn，经过九动作决策、LangGraph、M11 视频领域服务与 M06 外部任务恢复，最终通过 Snapshot/SSE 和结构化前端动作完成从需求收集到视频交付的可恢复闭环。

**Architecture:** 在现有 `CompactionQueueRepository` 上增加视频 live Runtime 双实现，使用独立支持表保存完整视频状态、Turn 执行租约、助手消息投影和 interrupt，并在同一临界区/SQL 事务提交 Workflow、事件和 Turn 终态。`SupervisorTurnExecutor` 负责上下文、决策、Graph 和恢复；Graph 继续处理 `answer_only/clarify`，`VideoLiveWorkflowHandler` 处理其余 7 个有状态动作。Gateway 只有在完整装配成功时才把 `video` 加入实际 `primary_execution_intents`，前端按钮只提交结构化 Turn/interrupt response。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、LangGraph、pytest、React 19、TypeScript 5、Node test runner、PowerShell 本地门禁。

## Global Constraints

- 生产配置继续保持 R1：`agent_runtime.mode=assist`、`enabled_intents=[]`、`new_conversation_rollout_percent=100`、`context_compaction_enabled=true`；不得修改 `backend/config.prod.yml`。
- 上下文预算固定为 896K/32K/32K，`require_verified_model_profile=true`，压缩失败退避 30 秒；所有模型调用通过共享 `ContextBudgetPolicyProvider`，不得增加节点级窗口常量。
- `deepseek-v4-pro.max_context_tokens=1000000` 继续由已验证档案提供；缺失、未验证或过期时 fail-closed。
- Supervisor 总体覆盖 9 个 `AgentAction`；现有 Graph 节点处理 `answer_only/clarify`，视频 Handler 只接收并实现另外 7 个 Workflow 动作，不改变 `WorkflowCommandDispatcher` 的分层边界。
- Authorization 只能从当前 HTTP 请求短暂传到一次供应商 start 边界；不得进入数据库、Graph checkpoint、日志、事件、状态信封或测试快照。重启后缺少凭据时打开 `authorization_required` interrupt，不猜测重放 start。
- Operation 身份继续使用 `workflow_id + stage + stage_version + attempt`，同一身份不同请求摘要 fail-closed；402 暂停原 job，404/expired 只能由上层新 attempt，供应商 start 不得重复。
- 历史对话和运行中任务不迁移；`frontend_v2` 对话不得被执行器领取；`image/ppt/video_analysis` 不进入 primary。
- 不调用真实付费供应商；所有测试使用 fake Model、fake Provider、fake Clock 和本地 Memory/SQLite。
- 新增或修改的人工注释、docstring、测试说明、提交标题和正文必须使用中文主体语义；新增配置叶子必须带紧邻的中文“用途/影响”说明。
- 每个任务严格执行 RED → 最小实现 → GREEN → 中文提交；不得跨任务攒一个大提交。
- 执行环境从仓库根目录进入 `backend` 或 `web`；Windows 可复用主工作区已安装的 `backend/.venv` 与 `web/node_modules`，但不得把依赖目录提交到 Git。
- 人工确认必须保持 M12 已冻结合同：客户端只提交 `{client_response_id, value}`，服务端通过 `Command(resume={interrupt_id: response})` 恢复原 `waiting_user` Turn 和原 Graph；不得创建 follow-up Turn。`explicit_action` 只能放在既有 `value` 内。
- 本轮只执行 Task 1-13。Task 14 的 R2 真实全流程门禁属于用户定义的业务第二步，必须等待后续单独授权；Task 13 必须先同步 README、AGENTS、最新设计、M13 状态和开发验证记录，并明确“Handler 已接入，R2 真实全流程门禁待执行”。

---

## 文件结构锁定

| 文件 | 单一职责 |
| --- | --- |
| `backend/pixelflow/agent_runtime/contracts/live.py` | 结构化动作、interrupt response 和公开 interrupt 投影合同 |
| `backend/pixelflow/agent_runtime/identity.py` | Turn、消息、Workflow、事件和 interrupt 的跨进程稳定 ID |
| `backend/pixelflow/agent_runtime/persistence/video_runtime.py` | 视频 live Runtime Repository 协议及 Memory/SQL 双实现，含 active Workflow 投影 |
| `backend/pixelflow/agent_workflows/video/state_codec.py` | 五类 M11 视频状态与 `VideoWorkflowStateEnvelope` 的规范编解码 |
| `backend/pixelflow/agent_runtime/supervisor/decision_service.py` | 从 Turn 权威证据生成并校验 `ActionDecision` |
| `backend/pixelflow/agent_workflows/video/live_capabilities.py` | 把现有 LLM/Skill/Application Service 暴露成可注入能力端口 |
| `backend/pixelflow/agent_workflows/video/live_handler.py` | 7 个有状态动作到 M11/M06 状态转换的唯一映射 |
| `backend/pixelflow/agent_runtime/executor.py` | Turn 领取、Graph 调用、租约续期、恢复和生命周期 |
| `web/src/lib/supervisor/actions.ts` | 前端卡片动作到结构化 Turn/interrupt 的纯函数映射 |

现有 `repositories.py`、`compaction_queue.py`、M11 领域服务和大型 `WorkspacePage.tsx` 只做必要接线，不把新职责继续塞入这些大文件。

---

### Task 1: 冻结 live Turn、结构化动作和 interrupt 合同

**Files:**
- Create: `backend/pixelflow/agent_runtime/contracts/live.py`
- Create: `backend/pixelflow/agent_runtime/identity.py`
- Modify: `backend/pixelflow/agent_runtime/contracts/api.py`
- Modify: `backend/pixelflow/agent_runtime/contracts/__init__.py`
- Modify: `backend/pixelflow/agent_runtime/supervisor/resolver.py`
- Modify: `backend/pixelflow/agent_runtime/service.py:185-210`
- Modify: `backend/tests/fixtures/agent_runtime/contracts-v1.json`
- Modify: `backend/tests/test_agent_runtime_contracts.py`
- Modify: `web/src/lib/supervisor/contracts.ts`
- Modify: `web/scripts/run-tests.mjs`
- Modify: `web/tests/agentRuntimeContracts.type-test.ts`
- Modify: `web/tests/agentRuntimeContracts.test.mjs`
- Modify: `web/tests/supervisorTurnSubmission.test.mjs`

**Interfaces:**
- Produces: `ExplicitActionSignal`、`InterruptResponseRequest`、`AgentInterruptProjection`。
- Produces: `conversation_message_id(conversation_id: str, client_input_id: UUID) -> str`、`turn_id(conversation_id: str, client_input_id: UUID) -> str`、`workflow_id(conversation_id: str, client_input_id: UUID) -> str`、`interrupt_id(turn_id: str, reason_code: str) -> str`、`projection_message_id(workflow_id: str, stage: str, stage_version: int, action_key: str) -> str`。
- Changes: `TurnStartRequest.explicit_action: ExplicitActionSignal | None = None`。
- Consumes later: Task 5 决策服务、Task 9 执行器、Task 10 interrupt 路由和 Task 12 前端动作。

- [ ] **Step 1: 写入 Python 与 TypeScript 失败合同测试**

在 Python 测试加入严格字段和稳定 ID 断言：

```python
def test_turn_start_accepts_strict_explicit_action() -> None:
    request = TurnStartRequest.model_validate(
        {
            "client_input_id": "11111111-1111-4111-8111-111111111111",
            "content": "确认这个方案",
            "materials": [],
            "reply_to_message_id": "message-plan-v1",
            "artifact_refs": ["artifact:video-plan:wf-1:v1"],
            "expected_context_version": 3,
            "explicit_action": {
                "action": "continue_workflow",
                "intent": "video",
                "workflow_id": "wf-1",
                "stage": "plan_review",
                "artifact_ref": "artifact:video-plan:wf-1:v1",
                "patch": {"approved": True},
            },
        }
    )
    assert request.explicit_action.action is AgentAction.CONTINUE_WORKFLOW
    assert request.explicit_action.patch == {"approved": True}


def test_live_ids_are_stable_and_scope_sensitive() -> None:
    client_id = UUID("11111111-1111-4111-8111-111111111111")
    assert turn_id("conversation-1", client_id) == turn_id("conversation-1", client_id)
    assert workflow_id("conversation-1", client_id) != workflow_id("conversation-2", client_id)
    assert interrupt_id("turn-1", "plan_review_required") == interrupt_id(
        "turn-1", "plan_review_required"
    )
```

TypeScript 测试构造同样的 `explicit_action`，并断言未知键、空 `workflow_id`、非 JSON patch 和非 UUID `client_response_id` 被解析器拒绝。

- [ ] **Step 2: 运行合同测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_contracts.py -q
Set-Location ../web
corepack pnpm test:agent-runtime-contracts
```

Expected: FAIL，因为 `contracts/live.py`、稳定 ID 函数和 `explicit_action` 字段尚不存在。

- [ ] **Step 3: 实现严格合同和稳定身份**

`contracts/live.py` 的公开模型固定为：

```python
class ExplicitActionSignal(ContractModel):
    action: AgentAction
    intent: AgentIntent | None = None
    workflow_id: str | None = Field(default=None, min_length=1)
    stage: str | None = Field(default=None, min_length=1)
    artifact_ref: str | None = Field(default=None, min_length=1)
    patch: dict[str, JsonValue] = Field(default_factory=dict)


class InterruptResponseValue(ContractModel):
    content: str = Field(min_length=1)
    materials: list[dict[str, JsonValue]] = Field(default_factory=list)
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    explicit_action: ExplicitActionSignal | None = None


class InterruptResponseRequest(ContractModel):
    client_response_id: UUID
    value: InterruptResponseValue


class AgentInterruptProjection(ContractModel):
    interrupt_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    workflow_id: str | None = Field(default=None, min_length=1)
    turn_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    opened_at: datetime
```

把 resolver 自有的 `ExplicitActionSignal` 改为从合同层导入；`identity.py` 使用 `uuid5(NAMESPACE_URL, canonical_key)`，并让 `service.py` 复用，不保留两套实现。

`AgentRuntimeService.start_turn()` 保存可见消息时，还必须把 `explicit_action` 的 JSON 副本写进消息 payload；未提供时写 `None`。Executor 后续只从这份已原子登记的权威消息读取动作，不使用 HTTP 请求对象或前端内存状态。

- [ ] **Step 4: 同步唯一 fixture 和 TypeScript 类型**

在 `contracts-v1.json` 为 `turn_start_request.explicit_action` 增加完整样例，并新增 `interrupt_response_request` 与 `interrupt_projection` 顶级节点。TypeScript 使用与 Python 相同的 snake_case 字段，运行时 parser 必须拒绝额外键。

```typescript
export type ExplicitActionSignal = Readonly<{
  action: AgentAction;
  intent: "video" | null;
  workflow_id: string | null;
  stage: string | null;
  artifact_ref: string | null;
  patch: Readonly<Record<string, JsonValue>>;
}>;

export type InterruptResponseRequest = Readonly<{
  client_response_id: string;
  value: Readonly<{
    content: string;
    materials: readonly JsonObject[];
    reply_to_message_id: string | null;
    artifact_refs: readonly string[];
    explicit_action: ExplicitActionSignal | null;
  }>;
}>;
```

fixture 中 `turn_start_request.explicit_action` 与 `interrupt_response_request.value.explicit_action` 和上述类型逐字段相同；`interrupt_projection` 固定包含 `interrupt_id/workflow_id/turn_id/kind/reason_code/payload/opened_at`，不得放入内部 `user_id/thread_id/checkpoint_ns`。interrupt response 不增加 `expected_context_version`，开放 interrupt 本身就是原 Turn 的恢复边界。

同步 `web/scripts/run-tests.mjs` 中硬编码的 `CanonicalFixture` 顶级字段，使新增 fixture 节点接受同一 TypeScript 检查；`web/tests/supervisorTurnSubmission.test.mjs` 的 M12 builder 期望明确排除 Task 12 才接线的 `explicit_action`，继续验证 reply/Artifact 字段，不得提前修改生产 `turnSubmission.ts`。

- [ ] **Step 5: 运行合同测试并确认 GREEN**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_contracts.py tests/test_agent_runtime_supervisor_resolver.py -q
Set-Location ../web
corepack pnpm test:agent-runtime-contracts
```

Expected: PASS，Python/TypeScript 对同一 fixture 的字段、枚举和可空性一致。

- [ ] **Step 6: 提交合同切片**

```powershell
git add backend/pixelflow/agent_runtime/contracts backend/pixelflow/agent_runtime/identity.py backend/pixelflow/agent_runtime/service.py backend/pixelflow/agent_runtime/supervisor/resolver.py backend/tests/fixtures/agent_runtime/contracts-v1.json backend/tests/test_agent_runtime_contracts.py web/scripts/run-tests.mjs web/src/lib/supervisor/contracts.ts web/tests/agentRuntimeContracts.type-test.ts web/tests/agentRuntimeContracts.test.mjs web/tests/supervisorTurnSubmission.test.mjs
git commit -m "实现：冻结视频 live Turn 与人工确认合同" -m "统一结构化动作、interrupt response、公开投影和跨进程稳定身份，保持 Python 与 TypeScript 唯一 fixture 一致。"
```

### Task 2: 增加视频状态、Turn 租约、消息和 interrupt 支持表

**Files:**
- Modify: `backend/pixelflow/agent_runtime/persistence/models.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/__init__.py`
- Create: `backend/packages/harness/deerflow/persistence/migrations/versions/20260731_05_video_live_runtime.py`
- Modify: `backend/pixelflow/tasks/mysql.py`
- Modify: `backend/tests/test_agent_runtime_migration.py`

**Interfaces:**
- Produces rows: `PixelFlowAgentVideoStateRow`、`PixelFlowAgentTurnExecutionRow`、`PixelFlowAgentProjectionMessageRow`、`PixelFlowAgentInterruptRow`、`PixelFlowAgentConversationStateRow`。
- Preserves: M01 冻结的 `AGENT_RUNTIME_TABLES`；五张新表加入 `AGENT_RUNTIME_SUPPORT_TABLES`。
- Consumes later: Task 4 的 SQL Repository。

- [ ] **Step 1: 扩展迁移结构失败测试**

在 `EXPECTED_TABLE_COLUMNS` 增加五张支持表并断言：

```python
EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS = {
    "pixelflow_agent_video_states": {
        "workflow_id", "conversation_id", "user_id", "schema_version",
        "state_kind", "workflow_version", "context_version", "payload_json",
        "payload_sha256", "last_turn_id", "last_action_key", "created_at", "updated_at",
    },
    "pixelflow_agent_turn_executions": {
        "turn_id", "conversation_id", "user_id", "attempt", "lease_owner",
        "lease_token", "lease_expires_at", "next_attempt_at", "last_reason_code",
        "created_at", "updated_at",
    },
    "pixelflow_agent_projection_messages": {
        "message_id", "conversation_id", "user_id", "run_id", "role",
        "content", "payload_json", "created_at", "updated_at",
    },
    "pixelflow_agent_interrupts": {
        "interrupt_id", "conversation_id", "user_id", "workflow_id", "turn_id",
        "thread_id", "checkpoint_ns", "kind", "reason_code", "status",
        "payload_json", "response_id", "response_json", "opened_at", "closed_at",
    },
    "pixelflow_agent_conversation_states": {
        "conversation_id", "user_id", "active_workflow_id", "created_at", "updated_at",
    },
}
```

再断言 upgrade 只新增表，downgrade 只删除本迁移拥有的五表，`legacy_sentinel` 和既有 Agent Runtime 数据保持不变。

- [ ] **Step 2: 运行迁移测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_migration.py -q
```

Expected: FAIL，因为 ORM metadata 和 revision `20260731_05` 尚未定义。

- [ ] **Step 3: 定义五张支持表**

约束固定如下：

- `video_states.workflow_id` 为主键；`payload_sha256` 格式为 `sha256:` 加 64 位小写十六进制；`workflow_version >= 1`。
- `turn_executions.turn_id` 为主键；租约三个字段必须同时为空或同时非空；`attempt >= 0`；按 `next_attempt_at/lease_expires_at` 建恢复索引。
- `projection_messages.message_id` 为主键；只允许 `role IN ('assistant','system')`；按 owner/conversation/time 建索引。
- `interrupts.interrupt_id` 为主键；`status IN ('open','responded','closed')`；response 两字段必须同时为空或同时非空；按 owner/conversation/status 建索引。
- `conversation_states.conversation_id` 为主键；`active_workflow_id` 可空，但非空时必须由 Repository 验证属于同用户同对话；该行同时作为一个对话只允许一个 open interrupt 的锁对象。

五表加入 `AGENT_RUNTIME_SUPPORT_TABLES`，不要改变 `AGENT_RUNTIME_TABLES` 的既有集合。

```python
AGENT_RUNTIME_SUPPORT_TABLES = (
    "pixelflow_agent_video_states",
    "pixelflow_agent_turn_executions",
    "pixelflow_agent_projection_messages",
    "pixelflow_agent_interrupts",
    "pixelflow_agent_conversation_states",
)

class PixelFlowAgentConversationStateRow(Base):
    """保存会话当前活动 Workflow 的权威投影。"""

    __tablename__ = "pixelflow_agent_conversation_states"
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    active_workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        _timestamp_type(), nullable=False, default=_now, onupdate=_now
    )
```

其余四个 ORM 类逐项采用 Step 1 的列集合和本 Step 的约束，JSON 字段使用 SQLAlchemy `JSON`，时间字段统一 `_timestamp_type()`，并把所有中文类 docstring 纳入中文门禁。

- [ ] **Step 4: 编写 additive Alembic migration**

revision 使用：

```python
revision: str = "20260731_05"
down_revision: str | None = "20260725_04"
```

`upgrade()` 逐表在线检查：表不存在才创建；每张新表额外创建一个名称含 `20260731_05` 的 revision 私有、非唯一 marker 索引，该 marker 与 ORM/业务查询索引分离且不进入 `AGENT_RUNTIME_SUPPORT_TABLES`。发现同名表但缺私有 marker，或列类型/长度/nullability、主键、CHECK 规范化表达式、业务索引/marker 索引不完整时抛 `RuntimeError`，不能补 marker 或静默接管。CHECK 归一化只能去方言引号、字符集 introducer、无语义空白和可证明完整包住表达式的冗余最外层括号，必须保留内部布尔分组。

`downgrade()` 先预检全部带私有 marker 的候选表；只有 marker 与完整 schema 指纹都匹配时才统一删除，任一表异常时不得先删除其他表。无私有 marker 的同名 legacy 表必须保留；MySQL 非事务型 DDL 部分升级重试只能复用已经带 marker 且 schema 完整的前序表。由 ORM `create_all` 生成、没有私有 marker 的同名表后续运行本 revision 时按设计 fail-closed，必须人工核验，不能自动接管。

- [ ] **Step 5: 运行迁移与 MySQL bootstrap 测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_migration.py tests/test_pixelflow_task_store.py -q
```

Expected: PASS，Alembic、ORM metadata 和 `PIXELFLOW_TASK_TABLES` 使用同一组支持表。

- [ ] **Step 6: 提交持久化结构切片**

```powershell
git add backend/pixelflow/agent_runtime/persistence backend/pixelflow/tasks/mysql.py backend/packages/harness/deerflow/persistence/migrations/versions/20260731_05_video_live_runtime.py backend/tests/test_agent_runtime_migration.py
git commit -m "实现：增加视频 live Runtime 支持表" -m "新增完整视频状态、Turn 执行租约、助手消息投影和人工确认表，并提供只回滚本版本结构的 additive migration。"
```

### Task 3: 为五类 M11 视频状态实现规范编解码

**Files:**
- Create: `backend/pixelflow/agent_workflows/video/state_codec.py`
- Modify: `backend/pixelflow/agent_workflows/video/__init__.py`
- Modify: `backend/pixelflow/agent_workflows/video/planning.py`
- Create: `backend/tests/test_agent_video_workflow_state_codec.py`
- Modify: `backend/tests/test_agent_video_workflow_planning.py`

**Interfaces:**
- Produces: `VideoWorkflowStateKind`、`VideoWorkflowStateEnvelope`、`VideoWorkflowState` union。
- Produces: `encode_video_workflow_state(*, user_id: str, state: VideoWorkflowState, workflow_version: int, last_turn_id: str, last_action_key: str) -> VideoWorkflowStateEnvelope`、`decode_video_workflow_state(envelope: VideoWorkflowStateEnvelope) -> VideoWorkflowState`、`project_video_workflow_state(state: VideoWorkflowState) -> WorkflowRecord`。
- Consumes: M11 的 `VideoPlanningWorkflowState`、`VideoScenePackageWorkflowState`、`VideoSceneGenerationWorkflowState`、`VideoPostProductionWorkflowState`、`VideoDeliveryWorkflowState`。
- Consumed by: Task 4 Repository、Task 7 Handler 和 Task 8 完成事件桥接。

- [ ] **Step 1: 写入五类 round-trip 与篡改失败测试**

测试必须使用现有 M11 fixture 逐段构造五类状态，再验证：

```python
@pytest.mark.parametrize("state_factory", STATE_FACTORIES)
def test_video_state_codec_round_trips_without_mutable_alias(state_factory) -> None:
    state = state_factory()
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=state,
        workflow_version=4,
        last_turn_id="turn-4",
        last_action_key="decision:turn-4",
    )
    restored = decode_video_workflow_state(envelope)
    assert project_video_workflow_state(restored).model_dump(mode="json") == (
        project_video_workflow_state(state).model_dump(mode="json")
    )
    payload = envelope.model_dump(mode="python")["payload"]
    payload["workflow_id"] = "attacker"
    assert project_video_workflow_state(restored).workflow_id == state.workflow_id
    with pytest.raises(TypeError):
        envelope.payload["workflow_id"] = "tampered"
    json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)


def test_video_state_codec_rejects_checksum_or_unknown_schema() -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=STATE_FACTORIES[0](),
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )
    with pytest.raises(ValueError, match="摘要"):
        decode_video_workflow_state(
            envelope.model_copy(update={"payload_sha256": "sha256:" + "0" * 64})
        )
    with pytest.raises(ValueError, match="schema_version"):
        decode_video_workflow_state(envelope.model_copy(update={"schema_version": 2}))
```

- [ ] **Step 2: 运行 codec 测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_workflow_state_codec.py -q
```

Expected: FAIL，因为 codec 和状态信封尚不存在。

- [ ] **Step 3: 定义状态信封和 kind**

```python
class VideoWorkflowStateKind(StrEnum):
    PLANNING = "planning"
    SCENE_PACKAGE = "scene_package"
    SCENE_GENERATION = "scene_generation"
    POSTPRODUCTION = "postproduction"
    DELIVERY = "delivery"


class VideoWorkflowStateEnvelope(ContractModel):
    schema_version: Literal[1] = 1
    workflow_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    state_kind: VideoWorkflowStateKind
    workflow_version: int = Field(ge=1)
    context_version: int = Field(ge=1)
    payload: Mapping[str, JsonValue]
    payload_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    last_turn_id: str = Field(min_length=1)
    last_action_key: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: 显式实现五类 payload 映射**

不得使用 `pickle`、`repr` 或未校验的 `dataclasses.asdict()`。每类 payload 键固定为：

| kind | 必需 payload |
| --- | --- |
| `planning` | workflow/conversation/stage/status/version/time、`intake_context`、`form_values`、`creative_directions`、`selected_direction`、可空 `active_plan` |
| `scene_package` | 基础身份/version/time、`source_plan`、`source_plan_artifact_ref`、`scene_package` |
| `scene_generation` | 基础身份/version/time、`source_scene_package`、scene packages/videos/failures/requests、pending operations、attempts、terminal claims、edited/dirty IDs |
| `postproduction` | 基础身份/version/time、完整 `generation_state`、merge request/result/error、quality result/feedback、pending operation、attempts、terminal claims、`finalized_by_user` |
| `delivery` | 基础身份/version/time、完整 `postproduction_state`、剪映历史/attempt/pending request、pending operation、最终下载证据 |

decoder 必须重新调用各权威快照的构造器，并通过对应 `to_workflow_record()` 触发 M11 校验；workflow/conversation/context_version 与信封不一致时拒绝。

`VideoPlanningWorkflowService` 增加公开、无副作用的 `validate_state()`，由正常构造、`to_workflow_record()` 和 codec 共同校验阶段/状态组合、必需/禁止字段、Plan 权威、版本与时间，codec 不复制 planning 状态机。对 scene package → generation → postproduction → delivery 的每个父子层级，共享校验 workflow/conversation/created_at 相同、child stage/context version 不大于 parent、child updated_at 不晚于 parent。

信封构造时复用 M06 的递归 freeze/thaw 模式：运行时 `payload` 的嵌套 mapping/list 均只读，`model_dump(mode="json")` 和 `model_dump_json()` 通过 field serializer 恢复普通 JSON 容器；调用方传入的原始对象后续修改不得影响信封。

`STATE_FACTORIES` 在测试文件中显式复用现有 M11 测试 builder，依次产生 planning、scene package、scene generation、postproduction 和 delivery 五个有效状态；不得用 Mock 绕过状态构造器。

```python
_ENCODERS: dict[VideoWorkflowStateKind, Callable[[VideoWorkflowState], dict[str, JsonValue]]] = {
    VideoWorkflowStateKind.PLANNING: _encode_planning,
    VideoWorkflowStateKind.SCENE_PACKAGE: _encode_scene_package,
    VideoWorkflowStateKind.SCENE_GENERATION: _encode_scene_generation,
    VideoWorkflowStateKind.POSTPRODUCTION: _encode_postproduction,
    VideoWorkflowStateKind.DELIVERY: _encode_delivery,
}

_DECODERS: dict[VideoWorkflowStateKind, Callable[[Mapping[str, JsonValue]], VideoWorkflowState]] = {
    VideoWorkflowStateKind.PLANNING: _decode_planning,
    VideoWorkflowStateKind.SCENE_PACKAGE: _decode_scene_package,
    VideoWorkflowStateKind.SCENE_GENERATION: _decode_scene_generation,
    VideoWorkflowStateKind.POSTPRODUCTION: _decode_postproduction,
    VideoWorkflowStateKind.DELIVERY: _decode_delivery,
}

def canonical_payload_sha256(payload: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
```

`canonical_payload_sha256()` 继续作为纯 payload 规范工具；持久化字段 `payload_sha256` 在 schema v1 中实际保存完整规范信封摘要，编码时覆盖除摘要自身之外的 schema、全部身份、kind、Repository/context version、payload、Turn/动作游标和创建/更新时间，解码时必须在任何领域构造器前重算校验。该兼容语义避免回开 Task 2 数据库结构，并能拒绝格式合法但跨列漂移的元数据。

正式 round-trip 除 Workflow 投影外还必须断言完整 dataclass equality 与规范 payload equality；使用真实 M11 Service/fake Port 覆盖 generation pending/failed/edited-dirty、postproduction merge error/quality feedback/finalized，以及 delivery pending/成功历史/草稿下载/最终下载等非空互斥分支。信封启用实例强制重验证，`model_copy()` 注入可变 payload 后重新验证必须生成新的深度只读、无别名实例。

每个 `_decode_*` 必须逐字段调用对应 M11 构造器；上面注册表只做 kind 分派，不能跳过领域校验。

- [ ] **Step 5: 运行 codec 与原 M11 测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_workflow_state_codec.py tests/test_agent_video_workflow_planning.py tests/test_agent_video_workflow_scene_packages.py tests/test_agent_video_workflow_generation.py tests/test_agent_video_workflow_postproduction.py tests/test_agent_video_workflow_delivery.py -q
```

Expected: PASS，codec 不改变任何 M11 状态机语义。

- [ ] **Step 6: 提交状态 codec 切片**

```powershell
git add backend/pixelflow/agent_workflows/video/state_codec.py backend/pixelflow/agent_workflows/video/__init__.py backend/tests/test_agent_video_workflow_state_codec.py
git commit -m "实现：持久化恢复五类视频 Workflow 状态" -m "使用规范 JSON、SHA-256 和严格 schema 完成 M11 规划、场景包、生成、后期与交付状态的无损编解码。"
```

### Task 4: 实现 Turn 执行租约和原子业务提交 Repository

**Files:**
- Create: `backend/pixelflow/agent_runtime/persistence/video_runtime.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/__init__.py`
- Create: `backend/tests/test_agent_runtime_video_repository.py`

**Interfaces:**
- Produces: `TurnExecutionClaim`、`SupervisorProjectionMessage`、`StoredAgentInterrupt`、`VideoTurnCommit`、冻结的 `VideoRuntimeSafeSnapshot`。
- Produces Protocol: `VideoRuntimeRepository`。
- Produces implementations: `MemoryVideoRuntimeRepository(MemoryCompactionQueueRepository)`、`SQLVideoRuntimeRepository(SQLCompactionQueueRepository)`。
- Consumes: Task 2 rows、Task 3 `VideoWorkflowStateEnvelope`、现有 `TurnRecord/WorkflowRecord/AgentEvent`。
- Consumed by: Task 9 Executor、Task 10 Snapshot/interrupt 和 Task 11 Gateway。

- [ ] **Step 1: 写入 Memory/SQL 参数化失败测试**

两种实现运行同一合同测试，至少包含：

```python
@pytest.mark.parametrize("repository_factory", REPOSITORIES)
async def test_only_one_worker_claims_oldest_turn(repository_factory) -> None:
    repository = await repository_factory()
    await seed_two_turns(repository)
    first, second = await asyncio.gather(
        repository.claim_turn(
            "user-1", "conversation-1", "turn-1",
            lease_owner="worker-a", now=NOW, lease_expires_at=NOW + timedelta(seconds=30),
        ),
        repository.claim_turn(
            "user-1", "conversation-1", "turn-1",
            lease_owner="worker-b", now=NOW, lease_expires_at=NOW + timedelta(seconds=30),
        ),
    )
    assert sum(item is not None for item in (first, second)) == 1


@pytest.mark.parametrize("repository_factory", REPOSITORIES)
async def test_expired_worker_cannot_commit_after_takeover(repository_factory) -> None:
    repository = await repository_factory()
    old = await claim(repository, owner="old", expires=NOW + timedelta(seconds=5))
    new = await repository.claim_turn(
        "user-1", "conversation-1", "turn-1",
        lease_owner="new", now=NOW + timedelta(seconds=6),
        lease_expires_at=NOW + timedelta(seconds=36),
    )
    assert new is not None
    with pytest.raises(TurnExecutionLeaseConflictError):
        await repository.commit_turn(old, completed_commit())


@pytest.mark.parametrize("repository_factory", REPOSITORIES)
async def test_responded_interrupt_reclaims_original_waiting_turn(repository_factory) -> None:
    repository = await repository_factory()
    original_turn, interrupt = await seed_waiting_turn_with_responded_interrupt(repository)
    claim = await repository.claim_interrupt_resume(
        "user-1", "conversation-1", interrupt.interrupt_id,
        lease_owner="worker-a", now=NOW, lease_expires_at=NOW + timedelta(seconds=30),
    )
    assert claim is not None
    assert claim.turn.turn_id == original_turn.turn_id
    assert len(await repository.list_turns("user-1", "conversation-1")) == 1
```

原子性测试给 `VideoTurnCommit` 同时传入状态信封、Workflow 投影、助手消息、关闭的旧 interrupt、新打开的 interrupt/事件和同一 Turn 终态，故意制造重复 event ID 或 CAS 冲突，断言所有对象保持旧值。interrupt response 只能重新领取原 `waiting_user` Turn，不得插入新 Turn。

另用一个已领取的 `external_job.state_changed` 完成事件测试 `commit_operation_completion()`：有效 delivery lease 同事务推进状态并确认事件；在 Handler 返回期间过期的 lease 必须保持状态和事件均未确认。

同一参数化测试还调用 `export_safe_snapshot(user_id, conversation_id)` 两次，断言 `model_dump(mode="json")` 完全一致、可被 `json.dumps()` 序列化，且修改任意嵌套 mapping/list 都抛错或只改变调用方副本，不能反向修改 Repository。

- [ ] **Step 2: 运行 Repository 测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_video_repository.py -q
```

Expected: FAIL，因为 Repository 协议和双实现尚不存在。

- [ ] **Step 3: 定义不可变提交合同**

```python
class TurnExecutionClaim(ContractModel):
    user_id: str
    turn: TurnRecord
    lease_owner: str
    lease_token: UUID
    lease_expires_at: datetime
    attempt: int = Field(ge=1)


class SupervisorProjectionMessage(ContractModel):
    message_id: str
    conversation_id: str
    run_id: str
    role: Literal["assistant", "system"]
    content: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime


class StoredAgentInterrupt(AgentInterruptProjection):
    user_id: str
    thread_id: str
    checkpoint_ns: str
    status: Literal["open", "responded", "closed"] = "open"
    response_id: UUID | None = None
    response: dict[str, JsonValue] | None = None
    closed_at: datetime | None = None


class OwnedTurnRecord(ContractModel):
    user_id: str
    turn: TurnRecord
    next_attempt_at: datetime | None = None


class VideoTurnCommit(ContractModel):
    decision: ActionDecision
    turn_status: Literal[TurnStatus.WAITING_USER, TurnStatus.COMPLETED, TurnStatus.FAILED]
    workflow_state: VideoWorkflowStateEnvelope | None = None
    workflow: WorkflowRecord | None = None
    expected_workflow_version: int = Field(ge=0)
    messages: tuple[SupervisorProjectionMessage, ...] = ()
    open_interrupt: StoredAgentInterrupt | None = None
    close_interrupt_id: str | None = None
    update_active_workflow: bool = False
    active_workflow_id: str | None = None
    error_reason_code: str | None = None
    occurred_at: datetime


class VideoRuntimeSafeSnapshot(ContractModel):
    conversation_id: str
    active_workflow_id: str | None = None
    workflow_states: tuple[VideoWorkflowStateEnvelope, ...] = ()
    workflows: tuple[WorkflowRecord, ...] = ()
    turns: tuple[OwnedTurnRecord, ...] = ()
    messages: tuple[SupervisorProjectionMessage, ...] = ()
    interrupts: tuple[StoredAgentInterrupt, ...] = ()
```

model validator 固定：状态信封和投影必须同时存在或同时为空；两者身份/version/context 一致；`waiting_user` 必须打开 interrupt；`completed` 不得留下 open interrupt；`failed` 只能携带固定 reason code；`active_workflow_id` 非空时必须等于本提交 Workflow 或 Repository 中同对话既有 Workflow。

`VideoRuntimeSafeSnapshot` 构造时对包含的合同模型重新验证并递归冻结所有嵌套 JSON；serializer 只在输出边界 thaw 为普通容器，从而同时满足直接访问只读和稳定 JSON 序列化。`responded` interrupt 保存完整公开 `value` 和 `client_response_id`，由恢复执行器消费；只有原 Turn 的 Graph 结果通过 fencing 原子提交时，Repository 才把它变为 `closed`。

测试文件内定义 `seed_two_turns()`、`claim()` 和 `completed_commit()`，分别只负责建立两个有序 Turn、领取指定 owner 的 lease 和构造固定完成提交；不得在 helper 中吞掉 Repository 异常。

- [ ] **Step 4: 实现 Memory 租约与提交临界区**

Memory 实现复用继承类的 `_compaction_write_lock`，并在一个临界区内：

1. 校验最早未终态 Turn 和压缩 lease；
2. 创建/接管 execution lease 并递增 attempt；
3. 用 lease token 校验 commit；
4. CAS 写状态与 `_workflows`；
5. 按 `update_active_workflow` 更新 conversation state，`switch_workflow` 的 active 选择可跨重启恢复；
6. upsert 消息/interrupt，按连续 sequence 追加事件；
7. 更新 Turn 的 decision/status 并清空租约；
8. 若失败，恢复进入临界区前的深拷贝快照。

```python
async def commit_turn(
    self, claim: TurnExecutionClaim, commit: VideoTurnCommit
) -> TurnRecord:
    async with self._compaction_write_lock:
        before = self._snapshot_live_runtime_state()
        try:
            execution = self._require_current_turn_claim(claim, now=commit.occurred_at)
            self._compare_and_set_video_state(commit)
            self._upsert_workflow_and_active_projection(commit)
            self._upsert_projection_messages(commit.messages)
            self._apply_interrupt_transition(commit)
            self._append_turn_events(claim, commit)
            return self._finish_turn(execution, commit)
        except Exception:
            self._restore_live_runtime_state(before)
            raise
```

上述七个私有 helper 均在 `video_runtime.py` 定义为同步的锁内操作；任何 helper 不得再次获取锁或执行网络 I/O。

- [ ] **Step 5: 实现 SQL 条件领取和单事务 commit**

SQL 实现使用 `_repository_write_transaction(session, self._sqlite_write_lock)`，锁定 Turn、execution、state、workflow 和当前 event max sequence。更新语句必须同时匹配 `lease_owner + lease_token + lease_expires_at > occurred_at`。CAS 使用 `workflow_version == expected_workflow_version`；新建要求 expected=0。

同一 `last_action_key` 且相同 `payload_sha256` 重放返回既有快照；相同 action key 但不同摘要抛 `VideoWorkflowStateConflictError`。

```python
async with _repository_write_transaction(session, self._sqlite_write_lock):
    result = await session.execute(
        update(PixelFlowAgentTurnExecutionRow)
        .where(
            PixelFlowAgentTurnExecutionRow.turn_id == claim.turn.turn_id,
            PixelFlowAgentTurnExecutionRow.lease_owner == claim.lease_owner,
            PixelFlowAgentTurnExecutionRow.lease_token == str(claim.lease_token),
            PixelFlowAgentTurnExecutionRow.lease_expires_at > commit.occurred_at,
        )
        .values(lease_owner=None, lease_token=None, lease_expires_at=None)
    )
    if result.rowcount != 1:
        raise TurnExecutionLeaseConflictError(claim.turn.turn_id)
    await self._sql_compare_and_set_state(session, claim, commit)
    await self._sql_write_projection_and_events(session, claim, commit)
```

`_sql_compare_and_set_state()` 必须在同一 transaction 内锁定现有 state/workflow，并执行 `workflow_version == expected_workflow_version`；`_sql_write_projection_and_events()` 在同一 transaction 内锁定 conversation state 和 event 最大 sequence，不能自行 commit。

- [ ] **Step 6: 实现查询与恢复候选**

协议还需提供：

```python
async def list_due_turns(*, now: datetime, limit: int = 100) -> list[OwnedTurnRecord]: ...
async def list_due_interrupt_responses(*, now: datetime, limit: int = 100) -> list[StoredAgentInterrupt]: ...
async def claim_interrupt_resume(
    user_id: str, conversation_id: str, interrupt_id: str, *,
    lease_owner: str, now: datetime, lease_expires_at: datetime,
) -> TurnExecutionClaim | None: ...
async def heartbeat_turn(claim: TurnExecutionClaim, *, now: datetime, lease_expires_at: datetime) -> TurnExecutionClaim: ...
async def reschedule_turn(claim: TurnExecutionClaim, *, now: datetime, next_attempt_at: datetime, reason_code: str) -> TurnRecord: ...
async def get_video_state(user_id: str, workflow_id: str) -> VideoWorkflowStateEnvelope | None: ...
async def list_projection_messages(user_id: str, conversation_id: str) -> list[SupervisorProjectionMessage]: ...
async def get_open_interrupt(user_id: str, conversation_id: str) -> StoredAgentInterrupt | None: ...
async def get_active_workflow_id(user_id: str, conversation_id: str) -> str | None: ...
async def export_safe_snapshot(user_id: str, conversation_id: str) -> VideoRuntimeSafeSnapshot: ...
async def commit_operation_completion(
    claim: EventDeliveryClaim,
    *,
    user_id: str,
    workflow_state: VideoWorkflowStateEnvelope,
    workflow: WorkflowRecord,
    expected_workflow_version: int,
    messages: tuple[SupervisorProjectionMessage, ...],
    occurred_at: datetime,
) -> WorkflowRecord: ...
```

候选查询必须稳定排序、数据库先过滤再 `limit`，不能无界物化。普通 Turn 恢复候选只包括 `accepted`、退避时间已到的 `queued`，以及 execution lease 已过期的 `processing`；没有响应的 `waiting_user` 仅由 Snapshot 恢复开放 interrupt，不重新执行。`status=responded` 的 interrupt 是唯一例外：`list_due_interrupt_responses()` 返回它，`claim_interrupt_resume()` 只重新领取该 interrupt 绑定的原 `waiting_user` Turn，并用同一 execution lease/fencing 机制转回 `processing`。领取前必须再次确认对话冻结为 `supervisor_v1`、intent 为已注册 `video`、不存在更早未终态 Turn，且 context compaction 的 `retry_not_before` 已到。

`commit_operation_completion()` 使用 M06 完成事件的 delivery lease，而不是伪造 Turn claim；它在同一临界区/事务中 CAS 写状态、Workflow、消息和 `workflow.progressed`，再按实际完成时间确认原 event lease。租约已过期时不写状态也不确认事件，留给同 event ID 接管。

- [ ] **Step 7: 运行 Repository、Outbox 和 compaction 回归测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_video_repository.py tests/test_agent_runtime_compaction_queue.py tests/test_agent_runtime_event_outbox.py -q
```

Expected: PASS，视频提交没有破坏现有压缩队列和 Event Outbox 顺序。

- [ ] **Step 8: 提交 Repository 切片**

```powershell
git add backend/pixelflow/agent_runtime/persistence/video_runtime.py backend/pixelflow/agent_runtime/persistence/__init__.py backend/tests/test_agent_runtime_video_repository.py
git commit -m "实现：原子领取并提交视频 Supervisor Turn" -m "为 Memory 与 SQL 增加执行租约、fencing、状态 CAS、消息/interrupt 投影及同事务事件提交。"
```

### Task 5: 组合确定性解析、LLM 分类和 Validator

**Files:**
- Create: `backend/pixelflow/agent_runtime/supervisor/decision_service.py`
- Modify: `backend/pixelflow/agent_runtime/supervisor/__init__.py`
- Create: `backend/tests/test_agent_runtime_supervisor_decision_service.py`
- Modify: `backend/pixelflow/agent_runtime/context/token_meter.py`
- Modify: `backend/pixelflow/agent_runtime/context/__init__.py`
- Modify: `backend/tests/test_agent_runtime_token_meter.py`

审查修复补充：`ContextBudgetPolicyProvider.resolve_model_profile()` 必须抛出并公开专用的已验证模型档案异常，决策服务只捕获该类型并转换为 `SupervisorDecisionUnavailableError("model_profile_invalid")`。不得使用错误字符串、traceback 或 frame locals 猜测异常来源；其余 `ValueError` 必须保持原异常向上。

**Interfaces:**
- Produces: `SupervisorTurnEvidence`、`SupervisorDecisionResult`、`SupervisorAnswerPort`、`SupervisorDecisionService.decide()`。
- Consumes: `DeterministicTargetResolver`、可选 `LLMActionClassifier`、`DecisionValidator`、`ContextAssembler`、Task 1 `ExplicitActionSignal`。
- Consumed by: Task 9 Executor。

- [ ] **Step 1: 写入决策链失败测试**

测试覆盖显式动作不调用模型、自由文本确定性命中、歧义调用模型、模型失败转安全 clarify、旧 context version 拒绝：

```python
async def test_explicit_action_bypasses_model_but_still_uses_validator() -> None:
    model = CountingDecisionModel()
    result = await decision_service(model).decide(
        evidence(
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id="wf-1",
                stage="plan_review",
                patch={"approved": True},
            )
        )
    )
    assert result.decision.action is AgentAction.CONTINUE_WORKFLOW
    assert result.decision.patch == {"approved": True}
    assert model.calls == 0


async def test_classifier_failure_returns_stable_clarification() -> None:
    result = await decision_service(FailingDecisionModel()).decide(
        evidence(content="把那个再处理一下", candidates=two_video_candidates())
    )
    assert result.decision.action is AgentAction.CLARIFY
    assert result.decision.reason_code == "classifier_unavailable_requires_clarification"
    assert "provider" not in result.decision.clarification_question.lower()


async def test_answer_only_builds_stable_tool_free_ai_message() -> None:
    result = await decision_service(
        FixedDecisionModel(action="answer_only"),
        answer_port=FixedAnswerPort("当前视频方案仍在等待你确认。"),
    ).decide(evidence(content="当前做到哪一步了？"))
    assert result.answer_message.content == "当前视频方案仍在等待你确认。"
    assert result.answer_message.id == f"assistant:{result.decision.idempotency_key}"
    assert result.answer_message.tool_calls == []


async def test_requires_confirmation_is_converted_to_clarify_before_dispatch() -> None:
    result = await decision_service(
        FixedDecisionModel(action="continue_workflow", requires_confirmation=True)
    ).decide(evidence(content="继续生成视频"))
    assert result.decision.action is AgentAction.CLARIFY
    assert result.decision.reason_code == "decision_requires_confirmation"
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_supervisor_decision_service.py -q
```

Expected: FAIL，因为 `SupervisorDecisionService` 尚不存在。

- [ ] **Step 3: 实现候选和 ContextEnvelope 构造**

`SupervisorTurnEvidence` 必须包含当前 Turn、可见消息、Workflow 投影、active workflow、artifact refs、explicit action 和权威 context version。候选 `allowed_actions` 由状态表计算，不信任前端。`SupervisorDecisionResult` 固定包含 `decision`、`validation_request`、可空 `answer_message` 和已组装 `context`。调用模型前使用：

```python
class SupervisorTurnEvidence(ContractModel):
    user_id: str
    conversation_id: str
    turn: TurnRecord
    content: str
    visible_messages: tuple[dict[str, JsonValue], ...]
    workflows: tuple[WorkflowRecord, ...]
    active_workflow_id: str | None = None
    materials: tuple[dict[str, JsonValue], ...] = ()
    reply_to_message_id: str | None = None
    artifact_refs: tuple[str, ...] = ()
    explicit_action: ExplicitActionSignal | None = None
    expected_context_version: int
    authoritative_context_version: int


class SupervisorAnswerPort(Protocol):
    async def answer(self, context: ContextEnvelope) -> str: ...


@dataclass(frozen=True, slots=True)
class SupervisorDecisionResult:
    decision: ActionDecision
    validation_request: DecisionValidationRequest
    context: ContextEnvelope
    answer_message: AIMessage | None = None
```

```python
context = await self._context_assembler.assemble(
    ContextRequest(
        conversation_id=evidence.conversation_id,
        user_id=evidence.user_id,
        current_input=evidence.content,
        target_workflow_id=resolution.target_workflow_id,
        artifact_refs=evidence.artifact_refs,
        expected_context_version=evidence.expected_context_version,
    )
)
```

模型 profile 验证失败向上抛固定 `SupervisorDecisionUnavailableError("model_profile_invalid")`，Executor 后续将其 fail-closed。

- [ ] **Step 4: 实现决策顺序和固定 fallback**

顺序固定为 explicit → deterministic → classifier → validator。显式或完整确定性结论直接构造 `confidence=1.0` 的 `ActionDecision`；partial/ambiguous/unresolved 才调用 classifier。classifier 不存在或失败时只允许生成 `clarify`，不得生成计费动作。

每个 decision 的 `idempotency_key` 必须等于 `decision:{turn_id}`；Validator 使用决策前重新读取的 current candidates 与 context version。

当最终动作是 `answer_only` 时，调用 `SupervisorAnswerPort.answer(context) -> str` 生成只读答复，并构造 `AIMessage(id=f"assistant:{decision.idempotency_key}")`；答复模型同样使用已经验证的 `ContextEnvelope`，禁止 tool call。回答失败时把动作收敛为 `clarify`，reason code 固定为 `answer_model_unavailable_requires_clarification`。

```python
resolution = self._resolver.resolve(evidence.to_resolver_input())
if evidence.explicit_action is not None:
    decision = self._decision_from_explicit(evidence, resolution)
elif resolution.status is DeterministicResolutionStatus.RESOLVED:
    decision = self._decision_from_resolution(evidence, resolution)
else:
    decision = await self._classify_or_clarify(evidence, context, resolution)

validation = self._validator.validate(
    DecisionValidationRequest(
        decision=decision,
        candidates=self._current_candidates(evidence),
        current_context_version=evidence.authoritative_context_version,
    )
)
if not validation.accepted:
    decision = self._clarify_decision(evidence, validation.reason_code)
```

所有 `_decision_*` helper 都强制写入 `idempotency_key=f"decision:{evidence.turn.turn_id}"`；`_classify_or_clarify()` 捕获分类器不可用时只返回 `clarify`。

- [ ] **Step 5: 运行决策服务与现有 Supervisor 测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_supervisor_decision_service.py tests/test_agent_runtime_supervisor_resolver.py tests/test_agent_runtime_supervisor_classifier.py tests/test_agent_runtime_supervisor_validator.py -q
```

Expected: PASS，原有黄金合同保持不变。

- [ ] **Step 6: 提交决策服务切片**

```powershell
git add backend/pixelflow/agent_runtime/supervisor/decision_service.py backend/pixelflow/agent_runtime/supervisor/__init__.py backend/tests/test_agent_runtime_supervisor_decision_service.py
git commit -m "实现：生成并复核 live Turn 的 Supervisor 决策" -m "按显式动作、确定性证据、结构化分类和权威 Validator 的固定顺序生成九动作决策，模型失败时安全追问。"
```

### Task 6: 实现视频规划与场景包 live 能力端口

**Files:**
- Create: `backend/pixelflow/agent_workflows/video/live_capabilities.py`
- Create: `backend/tests/test_agent_video_live_capabilities.py`
- Modify: `backend/pixelflow/agent_workflows/video/__init__.py`
- Modify: `backend/app/gateway/routers/pixelflow_intake.py`
- Modify: `backend/app/gateway/routers/pixelflow_planning.py`
- Modify: `backend/app/gateway/routers/pixelflow_video.py`

集成修复补充：`video` 包入口必须保留现有公开导出，同时允许干净 Python 进程直接导入 `video.live_capabilities`，不得因 eager export 形成 `delivery → agent_runtime → persistence.video_runtime → video` 循环。使用可测试的惰性导出或等价安全顺序，并补直接导入回归。

**Interfaces:**
- Produces Protocol: `VideoLiveCapabilityPort`。
- Produces implementation: `DefaultVideoLiveCapabilities`、不可序列化的 `TransientTurnCredential`，以及只按 Turn 读取临时凭据的 `TurnCredentialProvider` 协议。
- Methods: `validate_intake`、`generate_directions`、`generate_initial_plan`、`revise_plan`、`restore_plan`、`generate_scene_assets`。
- Preserves: v2 Router DTO 和 URL；Router 与 live Handler 共享 Application 层函数，不互相 import。
- Consumed by: Task 7 Handler。

- [ ] **Step 1: 写入共享能力失败测试**

使用 fake model/skill 验证：完整视频表单、三个方向、初始 Plan、修订/恢复、场景资产都返回现有 M11 接受的 DTO；缺字段和能力不匹配拒绝；输出不含 Authorization。

```python
async def test_default_capabilities_feed_m11_without_router_imports() -> None:
    capabilities = DefaultVideoLiveCapabilities(
        model_factory=fake_model_factory,
        scene_asset_skill=fake_scene_asset_skill,
    )
    validation = await capabilities.validate_intake(VIDEO_FORM, intake_rounds=0)
    directions = await capabilities.generate_directions(validation.values, {})
    plan = await capabilities.generate_initial_plan(
        form_values=validation.values,
        selected_direction=directions[0].to_dict(),
        intake_context={},
        materials=MATERIALS,
    )
    assert validation.is_complete
    assert len(directions) == 3
    assert plan.error is None
    assert sum(plan.scene_durations_sec) == validation.values["video_duration_sec"]
```

- [ ] **Step 2: 运行能力测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_live_capabilities.py -q
```

Expected: FAIL，因为能力端口和共享实现尚不存在。

- [ ] **Step 3: 提取 Router 内共享 Application 函数**

能力端口签名固定为：

```python
@dataclass(frozen=True, slots=True)
class TransientTurnCredential:
    authorization: str = field(repr=False)


class TurnCredentialProvider(Protocol):
    def get(self, turn_id: str) -> TransientTurnCredential | None: ...


class VideoLiveCapabilityPort(Protocol):
    async def validate_intake(
        self, form_values: Mapping[str, Any], *, intake_rounds: int
    ) -> FormValidationResult: ...

    async def generate_directions(
        self, form_values: Mapping[str, Any], intake_context: Mapping[str, Any]
    ) -> list[CreativeDirection]: ...

    async def generate_initial_plan(
        self, *, form_values: Mapping[str, Any], selected_direction: Mapping[str, Any],
        intake_context: Mapping[str, Any], materials: Sequence[Mapping[str, Any]],
    ) -> PlanMarkdownResult: ...

    async def revise_plan(
        self, state: VideoPlanningWorkflowState, *, revision_feedback: str
    ) -> PlanMarkdownResult: ...

    async def restore_plan(
        self, state: VideoPlanningWorkflowState, *, plan_version: int
    ) -> PlanMarkdownResult: ...

    async def generate_scene_assets(
        self, state: VideoScenePackageWorkflowState, *, credential: TransientTurnCredential
    ) -> Mapping[str, Any]: ...
```

`DefaultVideoLiveCapabilities` 直接复用：

- `validate_form("video", values, intake_rounds)`；
- `draft_creative_directions_with_llm("video", values, profile)`；
- `build_plan_markdown_with_llm()`、`revise_plan_markdown_with_llm()`、`restore_plan_markdown()`；
- `generate_scene_assets()`。

Router 原有 `_create_creative_directions_response`、Plan job 和 scene asset job 改为调用同一能力实现。不得从 `live_capabilities.py` import FastAPI `Request`、HTTP DTO 或 Router 私有 job 字典。

- [ ] **Step 4: 固定测试注入边界**

构造器只接收 `model_factory`、`scene_asset_skill`、PowerMem 查询/记录 port 和 clock。真实默认实现使用现有 `create_chat_model`/Skill 装配；测试必须显式注入 fake，禁止 monkeypatch 网络库全局。

```python
class DefaultVideoLiveCapabilities(VideoLiveCapabilityPort):
    def __init__(
        self,
        *,
        model_factory: ChatModelFactory,
        scene_asset_skill: SceneAssetImageSkill,
        memory_search: MemorySearchPort,
        memory_record: MemoryRecordPort,
        clock: Clock,
    ) -> None:
        self._model_factory = model_factory
        self._scene_asset_skill = scene_asset_skill
        self._memory_search = memory_search
        self._memory_record = memory_record
        self._clock = clock
```

`ChatModelFactory/MemorySearchPort/MemoryRecordPort/Clock` 在本文件定义为最小 Protocol；它们只暴露该能力类实际使用的方法，不接受 FastAPI `Request`。

- [ ] **Step 5: 运行能力与三个 Router 回归测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_live_capabilities.py tests/test_pixelflow_intake_router.py tests/test_pixelflow_planning_router.py tests/test_pixelflow_video_router.py -q
```

Expected: PASS，v2 API 行为未变化，live 能力可脱离 Controller 调用。

- [ ] **Step 6: 提交共享能力切片**

```powershell
git add backend/pixelflow/agent_workflows/video/live_capabilities.py backend/tests/test_agent_video_live_capabilities.py backend/app/gateway/routers/pixelflow_intake.py backend/app/gateway/routers/pixelflow_planning.py backend/app/gateway/routers/pixelflow_video.py
git commit -m "重构：共享视频规划与场景资产能力" -m "把表单、方向、Plan 和场景资产能力下沉为可注入 Application Port，供 v2 Controller 与 live Handler 复用。"
```

### Task 7: 实现视频 Handler 的 7 个有状态动作

**Files:**
- Create: `backend/pixelflow/agent_workflows/video/live_handler.py`
- Modify: `backend/pixelflow/agent_workflows/video/__init__.py`
- Modify: `backend/pixelflow/agent_runtime/graph/registry.py`
- Modify: `backend/pixelflow/agent_runtime/graph/dispatcher.py`
- Modify: `backend/pixelflow/agent_runtime/graph/composition.py`
- Modify: `backend/pixelflow/agent_runtime/graph/state.py`
- Create: `backend/tests/test_agent_video_live_handler.py`
- Modify: `backend/tests/test_agent_runtime_graph_dispatcher.py`
- Modify: `backend/tests/test_agent_runtime_graph_composition.py`

**Interfaces:**
- Produces: `WorkflowDispatchResult`、`VideoLiveWorkflowHandler.dispatch(command) -> WorkflowDispatchResult`。
- Changes: Dispatcher 同时兼容旧 Handler 返回 `WorkflowRecord` 与 live Handler 返回 `WorkflowDispatchResult`；Graph 把 live 结果写入 checkpoint 后进入真实 `workflow_interrupt` 节点。
- Changes: `WorkflowRegistry.resolve()` 返回 `WorkflowCommandHandler | LiveWorkflowCommandHandler`；既有 `WorkflowCommandHandler.dispatch() -> WorkflowRecord` 合同本身不变。
- Consumes: Task 3 codec、Task 6 capability port/`TurnCredentialProvider`、M11 五个 Workflow Service、M06 `OperationPort`；临时凭据不得加入 `WorkflowCommand`、`SupervisorState` 或 Graph checkpoint。
- Preserves: `WorkflowCommandHandler` 的公开返回类型仍为 `WorkflowRecord`。

- [ ] **Step 1: 写入 7 动作状态表失败测试**

每个动作至少一个成功和一个非法状态用例：

```python
@pytest.mark.parametrize(
    ("action", "seed_stage", "patch", "expected_stage"),
    [
        ("start_workflow", None, {}, "intake"),
        ("continue_workflow", "intake", {"form_values": VIDEO_FORM}, "direction_review"),
        ("modify_workflow", "plan_review", {"revision_feedback": "节奏更快"}, "plan_review"),
        ("regenerate_stage", "direction_review", {}, "direction_review"),
        ("retry_failed", "merge_video", {}, "merge_video"),
        ("switch_workflow", "plan_review", {}, "plan_review"),
        ("cancel_workflow", "plan_review", {}, "plan_review"),
    ],
)
async def test_video_handler_action_table(
    action,
    seed_stage,
    patch,
    expected_stage,
    video_handler,
    command_factory,
    seeded_state_repository,
):
    if seed_stage is not None:
        await seeded_state_repository.seed_stage(seed_stage)
    result = await video_handler.dispatch(
        command_factory(action=action, patch=patch)
    )
    assert result.workflow.current_stage == expected_stage
```

同一测试文件必须定义 `video_handler`、`command_factory` 和 `seeded_state_repository` fixture；fixture 只使用 Memory Repository、fake capabilities 和 fake clock。另测 cross-conversation、stale stage/artifact、非法 patch 键、重复同 action key 和同 identity 不同摘要。

- [ ] **Step 2: 运行 Handler 测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_live_handler.py -q
```

Expected: FAIL，因为真实 Handler 尚不存在。

- [ ] **Step 3: 定义 Handler 结果和状态入口**

```python
class WorkflowDispatchResult(ContractModel):
    state: VideoWorkflowStateEnvelope
    workflow: WorkflowRecord
    messages: tuple[SupervisorProjectionMessage, ...] = ()
    interrupt: StoredAgentInterrupt | None = None
    turn_status: Literal[TurnStatus.WAITING_USER, TurnStatus.COMPLETED]
    update_active_workflow: bool = False
    active_workflow_id: str | None = None


class LiveWorkflowCommandHandler(Protocol):
    async def dispatch(self, command: WorkflowCommand) -> WorkflowDispatchResult:
        ...
```

上面 Protocol 方法体中的 `...` 是 Python Protocol/抽象签名语法；实际 `VideoLiveWorkflowHandler` 必须实现完整方法。Handler 构造器注入 `TurnCredentialProvider`，仅在即将执行付费供应商 start 的调用边界按 `command.turn_id` 读取一次；缺失时输出 `authorization_required` interrupt。凭据不得进入 command、结果、消息、事件或 checkpoint。`dispatch` 从 Repository 读取/解码既有状态；`start_workflow` 预期无状态，其余动作预期已有且与 `command.workflow` 投影一致。

`WorkflowCommandDispatcher.dispatch_result()` 把旧 Handler 返回的 `WorkflowRecord` 包装为无 interrupt 的兼容结果；原 `dispatch()` 继续只返回 `.workflow`，既有 M02 测试无需改写 fake。`SupervisorState` 增加可序列化的 `workflow_dispatch_result`。

- [ ] **Step 4: 实现规划阶段动作映射**

映射固定为：

- start → `VideoPlanningWorkflowService.start()`，保存首轮文本/附件引用，打开 `video_intake_form` interrupt；
- intake continue → validate + confirm_intake + generate/publish directions，打开 `video_direction_review`；
- direction continue → select_direction + generate/publish initial Plan，打开 `video_plan_review`；
- direction regenerate → `regenerate_directions()` + 生成/发布新三方向；
- Plan modify → capability revise + `publish_revision()`，继续等待审核；
- Plan restore → capability restore + `restore_plan()`，继续等待审核；
- Plan continue → `approve_plan()` + `prepare_from_approved_plan()`，进入场景资产生成。

每个人工节点只打开一个稳定 interrupt；不得使用 60 秒默认选择或倒计时确认。

```python
async def _dispatch_planning(
    self, command: WorkflowCommand, state: VideoPlanningWorkflowState | None
) -> WorkflowDispatchResult:
    if command.action is AgentAction.START_WORKFLOW:
        started = self._planning.start(self._planning_start_request(command))
        return self._wait_for_intake(command, started)
    if state is None:
        raise VideoLiveStateConflictError("video_planning_state_required")
    if state.stage is VideoPlanningStage.INTAKE:
        return await self._continue_intake(command, state)
    if state.stage is VideoPlanningStage.DIRECTION_REVIEW:
        return await self._dispatch_direction_review(command, state)
    if state.stage is VideoPlanningStage.PLAN_REVIEW:
        return await self._dispatch_plan_review(command, state)
    raise VideoLiveStateConflictError("video_planning_action_not_allowed")
```

三个 `_continue/_dispatch_*` 方法只调用上面固定列出的 M11 service/capability 方法，并在每个返回路径构造唯一 `WorkflowDispatchResult`；不允许在分支中直接修改 dataclass 字段。

- [ ] **Step 5: 让领域人工节点进入真实 LangGraph interrupt**

`dispatch_workflow` 节点先把 `WorkflowDispatchResult` 和 Workflow 投影写入 Graph state；结果含 interrupt 时转到新 `WORKFLOW_INTERRUPT_NODE`。该节点调用：

```python
interrupt(
    {
        "type": result.interrupt.kind,
        "interrupt_id": result.interrupt.interrupt_id,
        "reason_code": result.interrupt.reason_code,
        "payload": result.interrupt.payload,
    }
)
```

因为前一节点 update 已 checkpoint，进程退出后完整状态信封和消息仍可恢复。`interrupt()` 恢复值是服务端根据已持久化公开 `value` 和权威证据构造的内部信封，包含 `client_response_id`、经 DecisionService 校验的 `decision` 与原公开 response。`WORKFLOW_INTERRUPT_NODE` 必须再次校验 response 对应当前 interrupt/workflow/stage，把 decision 与 command 更新进同一 Graph state，清空旧 `workflow_dispatch_result`，然后 `goto=WORKFLOW_COMMAND_NODE` 继续派发；不得结束旧 Turn后创建新 Turn。若本次动作再次需要人工确认，同一原 Turn 进入新的 `waiting_user` interrupt。Graph state 同时保存稳定 `last_interrupt_response_id`，使进程在 Graph checkpoint 后、Repository commit 前退出时可识别同一响应并只补提交。Graph 测试必须证明 Memory/SQLite checkpointer 重建后使用原 interrupt ID、原 Turn ID 且 Turn 总数不增加。

- [ ] **Step 6: 实现场景包、生成、后期和交付动作映射**

- 场景资产完成 → `publish_generated_asset_images()`，打开场景包审核；
- 场景包确认 → `start_from_reviewed_scene_package()`；
- 单镜修改 → `modify_scene()`；重生成 → `regenerate_modified_scenes()`；失败重试 → `retry_failed_scenes()`；
- 全部分镜成功确认 → `start_merge()`；合并失败重试 → `retry_merge()`；
- 合并结果修改意见 → `start_quality_review()`；QC 重试 → `retry_quality_review()`；
- QA 修改范围 → `apply_user_revision()` 后只重生成受影响镜头；
- 最终确认 → `finish()`，初始化/同步 `VideoDeliveryWorkflowState`；
- 最终视频下载动作 → `record_final_video_download()` 完成交付；剪映动作复用 `VideoDeliveryWorkflowService`。

`switch_workflow` 只回读目标状态并返回 `update_active_workflow=True` 与目标 ID；Task 4 原子更新 conversation state，刷新后仍保持选择。`cancel_workflow` 只把未终态状态投影为 cancelled、停止本进程恢复，不伪造供应商取消。

```python
_STAGE_HANDLERS: Mapping[VideoWorkflowStateKind, str] = {
    VideoWorkflowStateKind.SCENE_PACKAGE: "_dispatch_scene_package",
    VideoWorkflowStateKind.SCENE_GENERATION: "_dispatch_scene_generation",
    VideoWorkflowStateKind.POSTPRODUCTION: "_dispatch_postproduction",
    VideoWorkflowStateKind.DELIVERY: "_dispatch_delivery",
}

method_name = _STAGE_HANDLERS.get(envelope.state_kind)
if method_name is None:
    raise VideoLiveStateConflictError("video_state_kind_not_supported")
result = await getattr(self, method_name)(command, decode_video_workflow_state(envelope))
```

四个 stage 方法分别只接受上面动作清单；非法 action 统一抛固定 `video_action_not_allowed_for_stage`，由 Executor 转为安全失败或人工确认，不能自动推进到下一阶段。

- [ ] **Step 7: 生成前端兼容 artifact 消息**

消息 payload 的 `artifact` 必须复用现有 Web 类型：`directions`、`plan`、`video_scene_packages`、`video_quality_review`、`video_result`、`jianying_draft`。每个消息 ID 从 workflow/stage/version/action 派生，重放执行 upsert 而不是追加重复卡片。

阶段提交成功后调用现有 `record_power_mem_background()` 写安全 `experience` 摘要，并由 helper 继续沉淀 `skill`；用户明确表达长期偏好或负向要求时另写 `preference`（默认 `infer=True`）。摘要不得包含 Authorization、供应商 key、完整 prompt、异常堆栈或本地路径。PowerMem 不可用继续按现有配置 fail-open，不能回滚已经完成的 Workflow 事务。

```python
def _projection_message(
    *, workflow: WorkflowRecord, action_key: str, artifact: Mapping[str, JsonValue], now: datetime
) -> SupervisorProjectionMessage:
    return SupervisorProjectionMessage(
        message_id=projection_message_id(
            workflow.workflow_id, workflow.current_stage, workflow.stage_version, action_key
        ),
        conversation_id=workflow.conversation_id,
        run_id=workflow.workflow_id,
        role="assistant",
        content=artifact_summary(artifact),
        payload={"artifact": deepcopy_json(artifact)},
        created_at=now,
    )
```

`artifact_summary()` 只能返回本地化的固定阶段摘要；`deepcopy_json()` 必须校验为 JSON 值并去除可变别名。

- [ ] **Step 8: 运行 Handler、Graph 与全部 M11 回归测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_live_handler.py tests/test_agent_runtime_graph_dispatcher.py tests/test_agent_runtime_graph_composition.py tests/test_agent_video_workflow_planning.py tests/test_agent_video_workflow_scene_packages.py tests/test_agent_video_workflow_generation.py tests/test_agent_video_workflow_postproduction.py tests/test_agent_video_workflow_delivery.py -q
```

Expected: PASS，7 个业务动作和 M11 原有状态机全部绿色。

- [ ] **Step 9: 提交 Handler 切片**

```powershell
git add backend/pixelflow/agent_workflows/video/live_handler.py backend/pixelflow/agent_workflows/video/__init__.py backend/pixelflow/agent_runtime/graph/registry.py backend/pixelflow/agent_runtime/graph/dispatcher.py backend/pixelflow/agent_runtime/graph/composition.py backend/pixelflow/agent_runtime/graph/state.py backend/tests/test_agent_video_live_handler.py backend/tests/test_agent_runtime_graph_dispatcher.py backend/tests/test_agent_runtime_graph_composition.py
git commit -m "实现：接通视频 Workflow 七类业务动作" -m "将新建、继续、修改、重生成、失败重试、切换和取消映射到 M11 权威状态，并输出可恢复消息与人工确认。"
```

### Task 8: 接通 M06 Provider start、完成事件与临时 Authorization

**Files:**
- Modify: `backend/pixelflow/agent_workflows/video/live_capabilities.py`
- Modify: `backend/pixelflow/agent_workflows/video/live_handler.py`
- Create: `backend/pixelflow/agent_workflows/video/live_operations.py`
- Create: `backend/tests/test_agent_video_live_operations.py`

**Interfaces:**
- Produces: `VideoOperationStartRequest`、`VideoLiveOperationBridge.start(request, credential) -> OperationRecord`、`VideoOperationAdapterResolver`、`VideoOperationCompletionHandler`、进程内 `TransientCredentialVault`。
- Consumes: Task 6 `TransientTurnCredential`。
- Consumes: `OperationStartCoordinator`、`OperationRecoveryRuntime`、`ProviderJobAdapter`、M11 atomic Operation ports。
- Consumed by: Task 9 Executor 与 Task 11 Gateway。

- [ ] **Step 1: 写入零重复 start 与凭据不落库失败测试**

```python
async def test_concurrent_live_start_calls_provider_once_and_never_persists_auth() -> None:
    provider = CountingProvider()
    runtime = build_live_operations(provider)
    credential = TransientTurnCredential(authorization="Bearer transient-only")
    first, second = await asyncio.gather(
        runtime.start(request(), credential=credential),
        runtime.start(request(), credential=credential),
    )
    assert first.job_id == second.job_id
    assert provider.start_calls == 1
    snapshot = await runtime.safe_persistence_snapshot()
    assert "transient-only" not in json.dumps(snapshot, ensure_ascii=False)


async def test_recovery_without_credential_opens_authorization_interrupt_before_start() -> None:
    result = await handler_without_credential.dispatch(paid_stage_command())
    assert result.turn_status is TurnStatus.WAITING_USER
    assert result.interrupt.reason_code == "authorization_required"
    assert provider.start_calls == 0
```

还要覆盖 402 暂停/恢复原 provider job、timeout、404/expired 新 attempt、Provider 成功后进程退出、完成 Outbox 重放。

测试文件定义 `CountingProvider`、`build_live_operations()`、`request()`、`handler_without_credential` 和 `paid_stage_command()`；这些 helper 只返回固定 DTO 和计数器，禁止访问网络、环境变量或真实 Authorization。

- [ ] **Step 2: 运行 live Operation 测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_live_operations.py -q
```

Expected: FAIL，因为 live Operation bridge 和 transient credential 尚不存在。

- [ ] **Step 3: 约束临时凭据并实现 Resolver**

为 Task 6 的 `TransientTurnCredential` 增加 `__post_init__`：空 Authorization 直接拒绝。禁止为该类实现 `to_dict`、Pydantic serializer 或持久化接口。

`TransientCredentialVault` 只在当前进程的加锁内存映射中保存 `turn_id -> TransientTurnCredential`，提供 `put/get/pop/clear`。`repr`、metrics 和异常不得包含凭据值；Executor 在 Graph 调用前 `put`，并在 `finally` 中 `pop`，进程关闭时 `clear`。服务重启后 Vault 为空，付费 start 必须转为 `authorization_required` interrupt，不能从持久层恢复或猜测凭据。

`VideoOperationAdapterResolver.resolve(stage)` 只返回已注册 stage 的 `ProviderJobAdapter`，缺失时 fail-closed 并让 Gateway readiness 为 false。

```python
class TransientCredentialVault(TurnCredentialProvider):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._credentials: dict[str, TransientTurnCredential] = {}

    def put(self, turn_id: str, credential: TransientTurnCredential) -> None:
        with self._lock:
            self._credentials[turn_id] = credential

    def get(self, turn_id: str) -> TransientTurnCredential | None:
        with self._lock:
            return self._credentials.get(turn_id)

    def pop(self, turn_id: str) -> None:
        with self._lock:
            self._credentials.pop(turn_id, None)

    def clear(self) -> None:
        with self._lock:
            self._credentials.clear()
```

- [ ] **Step 4: 把 M11 OperationPort 绑定到 M06 Repository**

live bridge 为每个 user/conversation 创建 `OperationCoordinator`，并使用 M06 已有 completion/lease 方法实现 `claim/get/finalize_*`。外部 start 必须经过 `OperationStartCoordinator.start()`；status 只查询已持久化 provider job ID。

```python
async def start(
    self, request: VideoOperationStartRequest, *, credential: TransientTurnCredential
) -> OperationRecord:
    adapter = self._resolver.resolve(request.stage)
    coordinator = OperationStartCoordinator(
        self._repository,
        adapter=adapter,
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        clock=self._clock,
    )
    return await coordinator.start(
        request.operation_request,
        provider_request=request.provider_request,
        authorization=credential.authorization,
        lease_owner=self._lease_owner,
    )
```

`VideoOperationStartRequest` 只保存 operation identity、规范请求和摘要；`authorization` 只作为上面一次函数调用的命名参数传入，不加入 request DTO。

- [ ] **Step 5: 实现完成事件回灌**

`VideoOperationCompletionHandler` 以完成 event ID 作为 Graph checkpoint 幂等键，加载对应视频状态后调用 `record_scene_success/failure`、`record_merge_*`、`record_quality_*` 或 `record_jianying_result`，再通过 Task 4 Repository 原子提交新状态和 `workflow.progressed`。未知 stage、job ID 错配或状态漂移固定返回安全失败，不回显供应商响应。

```python
async def handle(self, claim: EventDeliveryClaim) -> WorkflowRecord:
    event = claim.event
    completion = self._parse_safe_completion(event)
    envelope = await self._repository.get_video_state(event.user_id, completion.workflow_id)
    if envelope is None:
        raise VideoOperationCompletionError("video_state_not_found")
    next_state = self._apply_completion(decode_video_workflow_state(envelope), completion)
    return await self._repository.commit_operation_completion(
        claim,
        user_id=event.user_id,
        workflow_state=encode_video_workflow_state_from_completion(envelope, next_state, event),
        workflow=project_video_workflow_state(next_state),
        expected_workflow_version=envelope.workflow_version,
        messages=self._completion_messages(next_state, event),
        occurred_at=event.occurred_at,
    )
```

- [ ] **Step 6: 运行 M06 与 live Operation 测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_video_live_operations.py tests/test_agent_runtime_operation_coordinator.py tests/test_agent_runtime_operation_leases.py tests/test_agent_runtime_operation_completion.py tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_provider_job_adapter.py -q
```

Expected: PASS，所有 M06 冻结语义保持绿色，fake Provider start 次数符合预期。

- [ ] **Step 7: 提交 Operation 接线切片**

```powershell
git add backend/pixelflow/agent_workflows/video/live_capabilities.py backend/pixelflow/agent_workflows/video/live_handler.py backend/pixelflow/agent_workflows/video/live_operations.py backend/tests/test_agent_video_live_operations.py
git commit -m "实现：接通视频 live Operation 与恢复事件" -m "复用 M06 单次 start、租约和完成 Outbox，并让 Authorization 只存在于当前调用边界。"
```

### Task 9: 实现 SupervisorTurnExecutor 与重启恢复

**Files:**
- Create: `backend/pixelflow/agent_runtime/executor.py`
- Modify: `backend/pixelflow/agent_runtime/__init__.py`
- Create: `backend/tests/test_agent_runtime_turn_executor.py`

**Interfaces:**
- Produces: `SupervisorTurnExecutor.start()`、`notify_turn()`、`notify_interrupt()`、`recover_due_turns()`、`recover_due_interrupts()`、`metrics_snapshot()`、`aclose()`。
- Consumes: Task 4 Repository、Task 5 DecisionService、编译 Graph、Task 7 Handler、Task 8 `TransientCredentialVault`。
- Consumed by: Task 10 Service/router 和 Task 11 Gateway。

- [ ] **Step 1: 写入执行与生命周期失败测试**

覆盖同对话顺序、跨对话并行、Graph checkpoint 恢复、旧 lease fencing、关闭不伪造终态：

```python
async def test_executor_processes_one_conversation_in_order() -> None:
    executor, repository = runtime_with_two_turns_same_conversation()
    await executor.recover_due_turns()
    await executor.wait_idle()
    assert [item.status for item in await repository.list_turns("user-1", "conversation-1")] == [
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
    ]
    assert handler.turn_ids == ["turn-1", "turn-2"]


async def test_executor_shutdown_leaves_claim_recoverable() -> None:
    executor, repository = runtime_with_blocked_handler()
    await executor.notify_turn(scope("turn-1"), credential=None)
    await executor.aclose()
    stored = await repository.get_turn("user-1", "turn-1")
    assert stored.status is TurnStatus.PROCESSING
    assert await repository.list_due_turns(now=LEASE_EXPIRY + EPSILON, limit=10)


async def test_executor_resumes_interrupt_on_original_turn_without_followup() -> None:
    executor, repository, opened = runtime_with_responded_interrupt()
    await executor.recover_due_interrupts()
    await executor.wait_idle()
    turns = await repository.list_turns("user-1", opened.conversation_id)
    assert [item.turn_id for item in turns] == [opened.turn_id]
    assert turns[0].status in {TurnStatus.WAITING_USER, TurnStatus.COMPLETED}
```

测试文件中的 `runtime_with_two_turns_same_conversation()` 和 `runtime_with_blocked_handler()` 必须装配真实 Memory Repository、编译 Graph、fake DecisionService/Handler 和可控 Clock；`scope()` 只构造 user/conversation/turn 三个稳定 ID。

- [ ] **Step 2: 运行 Executor 测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_turn_executor.py -q
```

Expected: FAIL，因为 Executor 尚不存在。

- [ ] **Step 3: 实现一次 Turn 执行流程**

`_execute_claim()` 固定顺序：

1. 再读 conversation，确认 user 与 `supervisor_v1`；
2. 从稳定 message ID 读取当前输入、materials/reply/artifact/explicit action；
3. 读取 Workflow、开放 interrupt 和 ContextEnvelope；
4. 调用 DecisionService 并把 decision 写入 claim 上下文；
5. 若 HTTP 入口传入临时凭据，先以当前 `turn_id` 放入 `TransientCredentialVault`，再使用 `supervisor_namespace(conversation_id)` 为当前 Turn 调用 `graph.ainvoke()`，并在 `finally` 中删除该凭据；
6. 对业务动作由 Graph Registry 派发 Handler；
7. 从 Graph state/checkpoint 读取 answer message、clarify interrupt 或 `WorkflowDispatchResult`，转换成 `VideoTurnCommit`；
8. 用 fencing token 原子提交；
9. 唤醒同对话下一 Turn。

Graph 输入必须包含现有 `SupervisorState` 全字段；`active_workflow_id` 从权威状态决定，不能从客户端读取。

```python
async def _execute_claim(
    self, claim: TurnExecutionClaim, credential: TransientTurnCredential | None
) -> None:
    if credential is not None:
        self._credential_vault.put(claim.turn.turn_id, credential)
    try:
        evidence = await self._load_authoritative_evidence(claim)
        decision = await self._decision_service.decide(evidence)
        graph_state = await self._graph.ainvoke(
            self._graph_input(evidence, decision),
            config={"configurable": {"thread_id": evidence.conversation_id,
                                     "checkpoint_ns": supervisor_namespace(evidence.conversation_id)}},
        )
        commit = self._commit_from_graph(claim, decision, graph_state)
        await self._repository.commit_turn(claim, commit)
        await self._notify_next_turn(evidence.user_id, evidence.conversation_id)
    finally:
        self._credential_vault.pop(claim.turn.turn_id)
```

`_load_authoritative_evidence()`、`_graph_input()` 和 `_commit_from_graph()` 分别对应上面 1-4、5-6 和 7-8 的固定职责；均在本文件实现，不得读取 FastAPI request 或前端 React 状态。普通 Turn 路径不得识别 `source_interrupt_id`，因为人工响应不会登记新 Turn。

`_resume_interrupt_claim()` 是独立路径：读取 `status=responded` 的 `StoredAgentInterrupt` 与原 `waiting_user` Turn，用同一 Turn ID 获取恢复 claim；DecisionService 只根据已持久化 `response.value.explicit_action` 和权威 Workflow/Artifact 证据生成 decision；随后调用 `resume_graph_from_interrupt(graph, namespace, interrupt_id=..., response=internal_resume_envelope)`。内部恢复信封包含 `client_response_id`、公开 `value` 和已校验 decision，但不包含 Authorization。Graph 已存在相同 `last_interrupt_response_id` 时不重复 `Command(resume)`，只从 checkpoint 构造并补交 `VideoTurnCommit`。commit 在一个 fencing 临界区内关闭旧 interrupt、按结果选择完成同一 Turn或为同一 Turn打开下一 interrupt；任何路径不得插入 follow-up Turn。

- [ ] **Step 4: 实现租约 heartbeat、退避和错误分类**

- heartbeat 只延长到严格更晚时间；失败或旧 token 立即停止提交；
- 合同/隔离/状态损坏 → `failed` + 固定 `error.raised`；
- 临时数据库/模型基础设施失败 → `reschedule_turn`，指数退避最大 30 秒；
- context compaction `retry_not_before` 未到 → 不领取；
- 无 credential 的计费 start → `authorization_required` interrupt，不归类为基础设施失败。

```python
async def _heartbeat(self, claim: TurnExecutionClaim) -> TurnExecutionClaim:
    now = self._clock()
    renewed_until = max(claim.lease_expires_at + self._heartbeat_step, now + self._lease_duration)
    return await self._repository.heartbeat_turn(
        claim, now=now, lease_expires_at=renewed_until
    )

async def _reschedule_transient_failure(
    self, claim: TurnExecutionClaim, *, reason_code: str
) -> None:
    delay = min(30, 2 ** min(claim.attempt, 5))
    await self._repository.reschedule_turn(
        claim,
        now=self._clock(),
        next_attempt_at=self._clock() + timedelta(seconds=delay),
        reason_code=reason_code,
    )
```

heartbeat 返回冲突时立即取消当前 Graph task；错误映射只接收预先枚举的 reason code，禁止把异常字符串传给 Repository。

- [ ] **Step 5: 实现恢复扫描与关闭**

`start()` 创建单个扫描任务；每轮先稳定扫描 `list_due_interrupt_responses(now, limit=100)`，再扫描 `list_due_turns(now, limit=100)`，每个候选独立 try/catch，同一原 Turn 只能被其中一个路径取得 lease。`notify_interrupt()` 只唤醒已持久化响应对应的原 Turn。`aclose()` 设置关闭标志、拒绝新 notify、取消并等待本进程任务，不改写 Turn、interrupt、lease 或 Operation 状态。

Executor 内置线程安全的 `SupervisorExecutionMetrics` 聚合器，只保存计数/耗时：Turn 等待、领取、完成、重试、租约冲突、九动作分布、interrupt 等待时间、阶段耗时、M06 六态和安全 reason code。`metrics_snapshot()` 返回深拷贝 JSON；键和值不得包含 prompt、Authorization、供应商错误或完整 URL。

```python
async def start(self) -> None:
    if self._scan_task is not None:
        return
    self._closing = False
    self._scan_task = asyncio.create_task(self._scan_loop(), name="supervisor-turn-scan")

async def aclose(self) -> None:
    self._closing = True
    if self._scan_task is not None:
        self._scan_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._scan_task
        self._scan_task = None
    await self._cancel_and_join_local_turn_tasks()
    self._credential_vault.clear()
```

`_cancel_and_join_local_turn_tasks()` 只取消本进程 task 并等待退出，不调用 Repository 释放或改写尚未完成的 lease。

- [ ] **Step 6: 运行 Executor、Graph 和 compaction 回归测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_turn_executor.py tests/test_agent_runtime_graph_composition.py tests/test_agent_runtime_graph_interrupts.py tests/test_agent_runtime_compaction_queue.py -q
```

Expected: PASS，重启和关闭边界不重复执行副作用。

- [ ] **Step 7: 提交 Executor 切片**

```powershell
git add backend/pixelflow/agent_runtime/executor.py backend/pixelflow/agent_runtime/__init__.py backend/tests/test_agent_runtime_turn_executor.py
git commit -m "实现：持续消费并恢复 Supervisor Turn" -m "增加按会话顺序执行、租约续期、Graph 调用、受控退避和进程重启恢复，旧 worker 不能越过 fencing 提交。"
```

### Task 10: 接通 Turn 入口、Snapshot 和真实 interrupt response

**Files:**
- Modify: `backend/pixelflow/agent_runtime/persistence/turn_registration.py`
- Modify: `backend/pixelflow/agent_runtime/service.py`
- Modify: `backend/app/gateway/routers/pixelflow_conversations.py`
- Modify: `backend/tests/test_agent_runtime_r1_integration.py`
- Create: `backend/tests/test_agent_runtime_live_api.py`

**Interfaces:**
- Changes: `AgentRuntimeService` 注入可选 `turn_executor` 和 `video_repository`。
- Produces: `AgentRuntimeService.respond_to_interrupt(*, user_id: str, conversation_id: str, interrupt_id: str, request: InterruptResponseRequest) -> AgentTurnJobResponse`，始终返回原 Turn/run。
- Produces: `AgentRuntimeService.notify_registered_turn(turn_id: str, credential: TransientTurnCredential | None) -> None`，仅非阻塞唤醒 Task 9 Executor。
- Produces: `AgentRuntimeService.notify_registered_interrupt(interrupt_id: str, credential: TransientTurnCredential | None) -> None`，仅非阻塞唤醒原 Turn 的恢复路径。
- Changes: `snapshot().interrupt` 返回 Task 1 `AgentInterruptProjection`，messages 合并 task store user messages与 live projection messages。
- Consumes: Task 1/4/9。

- [ ] **Step 1: 写入 API 失败测试**

```python
def test_supervisor_interrupt_response_resumes_original_turn_idempotently(client) -> None:
    opened = seed_open_interrupt(client)
    body = {
        "client_response_id": "22222222-2222-4222-8222-222222222222",
        "value": {
            "content": "同意方案",
            "materials": [],
            "reply_to_message_id": "message-plan-v1",
            "artifact_refs": ["artifact:video-plan:wf-1:v1"],
            "explicit_action": {
                "action": "continue_workflow", "intent": "video",
                "workflow_id": "wf-1", "stage": "plan_review",
                "artifact_ref": "artifact:video-plan:wf-1:v1", "patch": {"approved": True},
            },
        },
    }
    first = client.post(opened["response_url"], json=body)
    second = client.post(opened["response_url"], json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["turn_id"] == second.json()["turn_id"]
    assert first.json()["turn_id"] == opened["waiting_turn_id"]
    assert live_repository.turn_count(opened["conversation_id"]) == 1
```

再测错误 user/对话/interrupt、已关闭不同响应、刷新恢复、assistant message 与 event 投影一致。

`seed_open_interrupt(client)` 必须通过真实 `/turns/start` 和 fake Handler 推进到 waiting_user，不允许直接改 Repository 私有字段；返回 response URL、原 waiting Turn ID 和 Snapshot context version。

- [ ] **Step 2: 运行 API 测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_live_api.py -q
```

Expected: FAIL，当前 interrupt endpoint 固定返回 legacy 409，Snapshot 固定 `interrupt=None`。

- [ ] **Step 3: 扩展 TurnRegistrationStore 的 interrupt 原子登记**

Memory 复用 `_registration_lock(user_id, conversation_id)`；SQL 锁 conversation、open interrupt 和原 waiting Turn，在同一事务中：

- 校验 interrupt 为 open 且属于当前 user/conversation；
- 按 `client_response_id` 创建或复用可见 user message，消息 payload 保存 interrupt ID、公开 `value` 和 `explicit_action`；
- 在同一 interrupt 上保存 response ID/JSON，并把状态从 `open` 改为 `responded`；此时不能提前写 `closed`；
- 保持原 waiting Turn 的 turn_id/run_id，不创建任何 Turn 记录；
- 写 `interrupt.responded`、`message.upserted` 和同一 run 的 `input.state_changed/run.state_changed`；
- 递增权威 context version，但不改变会话编排归属。

同 response ID 同内容重复回读原 Turn；同 ID 不同内容返回 409；已 `closed` 后重复同一 ID/内容仍返回原 Turn，不再次恢复。若进程在 HTTP 成功后退出，Task 9 从 `responded` interrupt 恢复扫描并重新领取原 waiting Turn；Graph 与 Repository 原子提交完成后才写 `interrupt.closed`，并按结果让同一 Turn completed 或进入下一个 waiting_user。

```python
async def register_interrupt_response(
    self,
    *,
    user_id: str,
    conversation_id: str,
    interrupt_id: str,
    request: InterruptResponseRequest,
    occurred_at: datetime,
) -> AgentTurnJobResponse:
    async with self._registration_lock(user_id, conversation_id):
        interrupt = self._require_owned_interrupt(user_id, conversation_id, interrupt_id)
        existing = self._find_response_registration(interrupt, request.client_response_id)
        if existing is not None:
            return self._require_same_response(existing, request)
        self._require_interrupt_status(interrupt, "open")
        return self._atomically_record_response_on_original_turn(
            interrupt=interrupt, request=request, occurred_at=occurred_at
        )
```

SQL 实现使用同一公开签名，但在 `_repository_write_transaction` 内分别通过 SQLAlchemy `select(PixelFlowAgentConversationStateRow).with_for_update()` 等语句锁定 conversation state、interrupt、原 Turn 和 context；Memory 私有 helper 与 SQL 私有 helper 必须生成相同稳定 message/event ID，且两者都不得生成 Turn ID。

- [ ] **Step 4: 唤醒 Executor 并传递请求期凭据**

Router 读取经过现有 auth middleware 规范化的 Authorization，只构造 `TransientTurnCredential`；普通 `start_turn` 传给 `notify_turn()`，`respond_to_interrupt` 传给 `notify_interrupt()`。两个入口返回前登记都已持久化。notify 失败不得让 HTTP 5xx 诱导前端重建 Turn，Executor 扫描会恢复。

```python
registration = await runtime_service.respond_to_interrupt(
    user_id=current_user_id,
    conversation_id=conversation_id,
    interrupt_id=interrupt_id,
    request=request,
)
credential = (
    TransientTurnCredential(authorization=authorization)
    if authorization is not None
    else None
)
runtime_service.notify_registered_interrupt(interrupt_id, credential=credential)
return registration
```

两个 notify 方法都只进行非阻塞唤醒并捕获内部唤醒异常写固定 warning；持久化登记失败仍按现有 HTTP 错误返回。interrupt notify 必须把凭据绑定原 turn_id，不得以 interrupt ID 作为 Vault 持久身份。

- [ ] **Step 5: 实现 Snapshot 合并**

Snapshot 消息排序使用 `created_at + message_id`，同 ID live projection 覆盖旧投影；只返回当前唯一 open interrupt。发现同会话多个 open interrupt 时 fail-closed，返回固定 `agent_runtime_interrupt_state_invalid`，不能任选一个。

```python
live_messages = await self._video_repository.list_projection_messages(user_id, conversation_id)
messages_by_id = {str(message["message_id"]): deepcopy(message) for message in stored_snapshot.messages}
messages_by_id.update(
    {message.message_id: message.model_dump(mode="json") for message in live_messages}
)
messages = sorted(
    messages_by_id.values(), key=lambda item: (str(item["created_at"]), str(item["message_id"]))
)
interrupt = await self._video_repository.get_open_interrupt(user_id, conversation_id)
public_interrupt = None if interrupt is None else AgentInterruptProjection(
    interrupt_id=interrupt.interrupt_id,
    workflow_id=interrupt.workflow_id,
    turn_id=interrupt.turn_id,
    kind=interrupt.kind,
    reason_code=interrupt.reason_code,
    payload=interrupt.payload,
    opened_at=interrupt.opened_at,
)
return stored_snapshot.model_copy(
    update={
        "messages": messages,
        "interrupt": public_interrupt,
    }
)
```

- [ ] **Step 6: 运行 API、R1 和 OpenAPI 回归测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_live_api.py tests/test_agent_runtime_r1_integration.py tests/test_agent_runtime_conversation_cas.py -q
```

Expected: PASS；assist/frontend_v2 仍得到原 legacy 所有权语义，supervisor_v1 才使用新 response。

- [ ] **Step 7: 提交 API 接线切片**

```powershell
git add backend/pixelflow/agent_runtime/persistence/turn_registration.py backend/pixelflow/agent_runtime/service.py backend/app/gateway/routers/pixelflow_conversations.py backend/tests/test_agent_runtime_r1_integration.py backend/tests/test_agent_runtime_live_api.py
git commit -m "实现：恢复 Supervisor 消息与人工确认" -m "让 live Turn 入口唤醒执行器，Snapshot 返回权威消息和 interrupt，并在原 Turn 上幂等恢复同一 Graph。"
```

### Task 11: 在 Gateway 受控装配真实 Registry 与 readiness

**Files:**
- Modify: `backend/app/gateway/pixelflow_agent_runtime.py`
- Modify: `backend/app/gateway/deps.py`
- Modify: `backend/app/gateway/app.py`
- Modify: `backend/tests/test_agent_runtime_graph_composition.py`
- Modify: `backend/tests/test_gateway_run_recovery.py`
- Create: `backend/tests/test_agent_runtime_gateway_readiness.py`

**Interfaces:**
- Produces: `PixelFlowAgentLiveRuntime` 聚合对象，持有 graph composition、Executor、OperationRecoveryRuntime 和注册 intent。
- Changes: `make_pixelflow_agent_graph_runtime` 新增必传 `registry: WorkflowRegistry` 参数，不再让默认空 registry 参与 primary readiness。
- Changes: `AgentRuntimeService.primary_execution_intents = configured_enabled ∩ successfully_registered`。

- [ ] **Step 1: 写入 Gateway readiness 失败测试**

```python
async def test_gateway_registers_video_only_after_all_dependencies_start(app_factory) -> None:
    app = app_factory(video_dependencies="ready")
    async with app.router.lifespan_context(app):
        runtime = app.state.pixelflow_agent_live_runtime
        assert runtime.registered_intents == frozenset({"video"})
        assert app.state.pixelflow_agent_runtime_service.primary_execution_intents == frozenset({"video"})
        assert runtime.graph.registry.resolve(WorkflowKind.VIDEO) is runtime.video_handler


async def test_missing_provider_adapter_keeps_video_on_frontend_v2(app_factory) -> None:
    app = app_factory(video_dependencies="missing_provider")
    async with app.router.lifespan_context(app):
        assert app.state.pixelflow_agent_runtime_service.primary_execution_intents == frozenset()
        assignment = app.state.pixelflow_agent_runtime_service.assignment_for_new_conversation(
            {}, initial_intent="video"
        )
        assert assignment.orchestration_mode is OrchestrationMode.FRONTEND_V2
```

`app_factory` 使用 Test App 和依赖注入构造 Memory/SQLite 两种装配；`ready` 注入全部 fake adapter，`missing_provider` 只移除一个付费 stage adapter，其余依赖保持相同，确保测试真正验证能力交集而非配置差异。

- [ ] **Step 2: 运行 Gateway 测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_gateway_readiness.py -q
```

Expected: FAIL，当前 Gateway 在 repository 创建前装配空 `FakeWorkflowRegistry`，且没有 Executor。

- [ ] **Step 3: 调整生命周期装配顺序**

从 `deps.langgraph_runtime()` 移除 Agent Graph 的提前创建，只保留 checkpointer。`app.lifespan` 在 task store、video repository、ContextAssembler、capabilities、Provider resolver、Handler 和 Operation runtime 全部创建后，进入 `make_pixelflow_agent_graph_runtime()`。

顺序固定为 repository → capability/adapter → handler/registry → graph → decision service → executor/recovery → `AgentRuntimeService` → yield。关闭顺序反向执行。

```python
video_repository = make_video_runtime_repository(task_store)
capabilities = make_video_live_capabilities(settings)
adapter_resolver = make_video_operation_adapter_resolver(settings)
video_handler = make_video_live_handler(video_repository, capabilities, adapter_resolver)
registry = WorkflowHandlerRegistry({WorkflowKind.VIDEO: video_handler})
graph = make_pixelflow_agent_graph_runtime(checkpointer=checkpointer, registry=registry)
decision_service = make_supervisor_decision_service(settings, context_assembler)
executor = SupervisorTurnExecutor(video_repository, decision_service, graph)
operation_recovery = make_video_operation_recovery(video_repository, adapter_resolver)
live_runtime = PixelFlowAgentLiveRuntime(
    graph=graph,
    executor=executor,
    operation_recovery=operation_recovery,
    registered_intents=frozenset({"video"}),
)
await operation_recovery.start()
await executor.start()
```

把这一装配放在现有 app lifespan 的 `AsyncExitStack` 管理范围；依赖构造任一步失败时不加入 `registered_intents`，并关闭已经成功创建的前序资源。

- [ ] **Step 4: 计算实际 readiness**

```python
configured = frozenset(agent_runtime_config.enabled_intents)
registered = live_runtime.registered_intents
primary_execution_intents = configured.intersection(registered)
```

任一视频依赖缺失时不创建半可用 Handler，记录固定 reason code `video_live_handler_not_ready`，不包含异常字符串或配置值。健康快照只公开 `registered_intents` 和 reason code。

`PixelFlowAgentLiveRuntime.status_snapshot()` 同时返回 Executor 的安全聚合指标和 Operation recovery 运行状态；只挂到 `app.state` 和现有运维日志，不新增包含用户维度的公开指标标签。

- [ ] **Step 5: 运行 Gateway、Graph 和配置测试**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_gateway_readiness.py tests/test_agent_runtime_graph_composition.py tests/test_gateway_run_recovery.py tests/test_agent_runtime_config.py -q
```

Expected: PASS；关闭后 graph/executor/recovery 均无活动任务，非视频 intent 不注册。

- [ ] **Step 6: 提交 Gateway 切片**

```powershell
git add backend/app/gateway/pixelflow_agent_runtime.py backend/app/gateway/deps.py backend/app/gateway/app.py backend/tests/test_agent_runtime_graph_composition.py backend/tests/test_gateway_run_recovery.py backend/tests/test_agent_runtime_gateway_readiness.py
git commit -m "实现：受控注册视频 live Handler" -m "按依赖就绪交集计算 primary 执行能力，并把真实 Registry、Executor 和 Operation 恢复绑定到 Gateway 生命周期。"
```

### Task 12: 增加前端结构化 Supervisor 动作 Client

**Files:**
- Create: `web/src/lib/supervisor/actions.ts`
- Modify: `web/src/lib/supervisor/turnSubmission.ts`
- Modify: `web/src/lib/supervisor/contracts.ts`
- Modify: `web/scripts/run-tests.mjs`
- Create: `web/tests/supervisorActions.test.mjs`
- Modify: `web/tests/supervisorTurnSubmission.test.mjs`

**Interfaces:**
- Produces: `SupervisorWorkflowActionInput`、`buildSupervisorWorkflowAction()`。
- Changes: `buildSupervisorSubmission()` 可携带 `explicitAction`，并在有 open interrupt 时构造 Task 1 `InterruptResponseRequest`。
- Consumed by: Task 13 Workspace handlers。

- [ ] **Step 1: 写入九动作映射失败测试**

```javascript
test("视频卡片动作生成结构化 continue，而不是自然语言模拟", () => {
  const action = buildSupervisorWorkflowAction({
    action: "continue_workflow",
    intent: "video",
    workflowId: "wf-1",
    stage: "plan_review",
    artifactRef: "artifact:video-plan:wf-1:v1",
    patch: { approved: true },
  });
  assert.deepEqual(action, {
    action: "continue_workflow",
    intent: "video",
    workflow_id: "wf-1",
    stage: "plan_review",
    artifact_ref: "artifact:video-plan:wf-1:v1",
    patch: { approved: true },
  });
});
```

参数化覆盖 9 个 action；`answer_only/clarify` 只允许空 patch；跨会话材料、非法 artifact、空目标和循环 JSON 必须拒绝。

- [ ] **Step 2: 运行前端动作测试并确认 RED**

Run:

```powershell
Set-Location web
corepack pnpm test
```

Expected: FAIL，因为 `actions.ts` 和 `explicitAction` 尚不存在。

- [ ] **Step 3: 实现纯函数动作 builder**

`buildSupervisorWorkflowAction()` 只做字段规范化/深拷贝，不读取 React state、不发送请求。已有 Workflow 的 6 个目标动作必须提供 workflow ID；start 必须无 workflow ID 且 intent=video；answer/clarify 的 patch 必须为空。

```typescript
export function buildSupervisorWorkflowAction(
  input: SupervisorWorkflowActionInput,
): ExplicitActionSignal {
  const patch = cloneJsonObject(input.patch ?? {});
  if (input.action === "start_workflow") {
    assert(input.intent === "video" && input.workflowId == null, "start_workflow_target_invalid");
  } else if (input.action === "answer_only" || input.action === "clarify") {
    assert(Object.keys(patch).length === 0, "read_only_action_patch_forbidden");
  } else {
    assert(Boolean(input.workflowId), "workflow_id_required");
  }
  return {
    action: input.action,
    intent: input.intent ?? null,
    workflow_id: input.workflowId ?? null,
    stage: input.stage ?? null,
    artifact_ref: input.artifactRef ?? null,
    patch,
  };
}
```

`assert()` 和 `cloneJsonObject()` 在 `actions.ts` 内实现：前者抛只含固定 reason code 的 `SupervisorActionValidationError`，后者拒绝循环引用、非有限数字、函数和原型对象。

- [ ] **Step 4: 扩展 Turn/interrupt submission**

普通 Turn body 增加 `explicit_action`。interrupt body 改为 Task 1 冻结字段：

```typescript
{
  client_response_id: clientInputId,
  value: {
    content: input.content,
    materials,
    reply_to_message_id: replyToMessageId,
    artifact_refs: artifactRefs,
    explicit_action: input.explicitAction ?? null,
  },
}
```

客户端网络重试复用同一个 `clientInputId`，不得为第二次请求重新生成 UUID。interrupt response 不携带 `expected_context_version`，也不经过普通 Turn builder；它只恢复 Snapshot 暴露的原 interrupt 和原 waiting Turn。

- [ ] **Step 5: 将新模块加入测试编译器并运行 GREEN**

Run:

```powershell
Set-Location web
corepack pnpm test
corepack pnpm lint
```

Expected: PASS，Node tests 和 TypeScript 严格检查均绿色。

- [ ] **Step 6: 提交前端动作合同切片**

```powershell
git add web/src/lib/supervisor/actions.ts web/src/lib/supervisor/turnSubmission.ts web/src/lib/supervisor/contracts.ts web/scripts/run-tests.mjs web/tests/supervisorActions.test.mjs web/tests/supervisorTurnSubmission.test.mjs
git commit -m "实现：提交结构化 Supervisor 视频动作" -m "为九动作、表单 patch 和 interrupt response 增加唯一前端 builder，网络重试复用原客户端输入 ID。"
```

### Task 13: 将视频表单、卡片和画布操作接到 Supervisor

**Files:**
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/src/components/chat/ChatPanel.tsx`
- Modify: `web/src/components/chat/MessageBubble.tsx`
- Modify: `web/src/components/composer/GenParamsDialog.tsx`
- Modify: `web/src/components/canvas/StoryboardPanel.tsx`
- Modify: `web/src/components/canvas/VideoResultCard.tsx`
- Modify: `web/tests/workspaceOrchestrationMode.test.mjs`
- Modify: `web/tests/mainFlowContract.test.mjs`
- Modify: `web/tests/videoSceneUiContract.test.mjs`
- Modify: `web/tests/jianyingDraftUiContract.test.mjs`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Modify: `docs/agentization/status/M13-status.md`
- Create: `docs/agentization/test-reports/R2-video-live-handler-development.md`

**Interfaces:**
- Consumes: Task 12 action builder、现有 `useSupervisorConversation`、Snapshot interrupt/workflow projection。
- Produces: supervisor_v1 视频控件统一 `submitSupervisorAction()` 路径。
- Preserves: frontend_v2 原 handler 和所有非视频行为。

- [ ] **Step 1: 写入源码合同失败测试**

断言 Supervisor 分支为视频控件提供 handler，且 handler 调用 `submitSupervisorAction`，不调用以下 legacy 推进函数：`handleSelectDirection`、`handleApprovePlan`、`handleGenerateVideoFromScenePackages`、`handleRetryVideoMerge`、`handleAcceptVideoResult`、`handleGenerateJianyingDraft`。

还要断言刷新后从 `supervisorRuntime.state.interrupt` 恢复表单/审核弹窗，不根据本地旧 state 自动执行动作。

```javascript
test("supervisor 视频控件只走结构化提交入口", () => {
  const source = readWorkspaceSource();
  const supervisorBranch = extractFunctionBody(source, "renderSupervisorVideoArtifact");
  assert.match(supervisorBranch, /submitSupervisorAction/);
  for (const legacyName of [
    "handleSelectDirection",
    "handleApprovePlan",
    "handleGenerateVideoFromScenePackages",
    "handleRetryVideoMerge",
    "handleAcceptVideoResult",
    "handleGenerateJianyingDraft",
  ]) {
    assert.doesNotMatch(supervisorBranch, new RegExp(`\\b${legacyName}\\b`));
  }
  assert.match(source, /supervisorRuntime\.state\.interrupt/);
});
```

`readWorkspaceSource()` 和 `extractFunctionBody()` 沿用当前源码合同测试 helper；若现有 helper 名不同，在同一测试文件定义等价的 UTF-8 读取和花括号配对实现，不能只做全文件字符串计数。

- [ ] **Step 2: 运行 UI 合同测试并确认 RED**

Run:

```powershell
Set-Location web
corepack pnpm test
```

Expected: FAIL，当前 `legacyArtifactActionsEnabled=false` 时大多数按钮没有 handler。

- [ ] **Step 3: 增加统一提交函数**

在 `WorkspacePage.tsx` 增加：

```typescript
const submitSupervisorAction = async (
  content: string,
  explicitAction: ExplicitActionSignal,
  options: { materials?: JsonObject[]; replyToMessageId?: string | null; artifactRefs?: string[] } = {},
) => {
  const clientInputId = crypto.randomUUID();
  // 复用现有 pendingSupervisorTurns 持久化与 handleSupervisorTurn；
  // explicitAction 必须随 pending 记录保存，刷新后仍复用同一 ID。
};
```

扩展 `PendingSupervisorTurn` 保存 `explicitAction`；已 registered 的 pending 只轮询原 run，不重发动作。

- [ ] **Step 4: 映射视频 UI 动作**

- 表单确认/关闭 → continue/cancel，patch 为 `form_values` 或 `form_cancelled`；
- 方向选择/重生成 → continue/regenerate；
- Plan 同意/修订/历史恢复/新创意 → continue/modify/regenerate；
- 场景包保存、资产替换/删除/新增、开始生成 → modify/continue；
- 分镜修改、重生成、失败重试、合并重试 → modify/regenerate/retry；
- 视频修改意见/QC/最终同意 → modify/continue；
- 剪映生成/重试/下载和最终视频下载 → continue/retry；最终视频下载使用 `continue_workflow` 并在 patch 中携带当前成品 `delivery_download_url`，不能用只读 `answer_only` 修改交付状态。

每个动作携带当前 workflow/stage/artifact，不能从卡片标题文本反推目标。

```typescript
const submitVideoContinue = (stage: string, artifactRef: string, patch: JsonObject) =>
  submitSupervisorAction("继续视频流程", buildSupervisorWorkflowAction({
    action: "continue_workflow",
    intent: "video",
    workflowId: activeVideoWorkflow.workflow_id,
    stage,
    artifactRef,
    patch,
  }), { artifactRefs: [artifactRef] });

const submitVideoRetry = (stage: string, artifactRef: string, patch: JsonObject) =>
  submitSupervisorAction("重试失败步骤", buildSupervisorWorkflowAction({
    action: "retry_failed",
    intent: "video",
    workflowId: activeVideoWorkflow.workflow_id,
    stage,
    artifactRef,
    patch,
  }), { artifactRefs: [artifactRef] });
```

方向、Plan、场景包、分镜、合并、QC、剪映和下载 handler 只组合这类函数的 action/stage/patch；Plan 修订使用 `modify_workflow`，方向重生成使用 `regenerate_stage`，关闭表单使用 `cancel_workflow`。

- [ ] **Step 5: 恢复 Supervisor 表单和画布**

interrupt payload 必须包含 `ui_kind` 和已验证字段。`video_intake_form` 打开 `GenParamsDialog`；direction/plan/scene package/video review 使用现有卡片；刷新只根据 Snapshot 重建，不触发 `onConfirm`。

```typescript
const restoredSupervisorUi = useMemo(() => {
  const interrupt = supervisorRuntime.state.interrupt;
  if (!interrupt) return null;
  switch (interrupt.payload.ui_kind) {
    case "video_intake_form": return restoreVideoForm(interrupt.payload);
    case "video_direction_review": return restoreDirectionReview(interrupt.payload);
    case "video_plan_review": return restorePlanReview(interrupt.payload);
    case "video_scene_package_review": return restoreScenePackageReview(interrupt.payload);
    case "video_result_review": return restoreVideoResultReview(interrupt.payload);
    default: return null;
  }
}, [supervisorRuntime.state.interrupt]);
```

所有 `restore*` 为纯函数，只解析后端已经验证的 JSON 并返回渲染 props；不得调用提交函数、旧 job start 或定时器。

- [ ] **Step 6: 运行 Web 全量和生产构建**

Run:

```powershell
Set-Location web
corepack pnpm test
corepack pnpm lint
corepack pnpm build-prod
```

Expected: PASS；supervisor_v1 视频按钮可用，frontend_v2 和非视频 handler 保持原样。

- [ ] **Step 7: 同步业务第一步文档与开发证据**

按 AGENTS.md 的流程变更同步要求，更新 README、仓库 AGENTS、最新设计和 M13 状态：说明 Gateway 已安装视频 live Graph Handler，`supervisor_v1` 视频对话由原 Turn 通过结构化 interrupt response 恢复；`frontend_v2`、历史对话、运行中任务和非视频 intent 不迁移。所有文档必须明确生产仍为 R1，R2 真实全流程门禁（Task 14）尚未执行，不能写“R2 已完成”“可生产发布”或 `ready_for_integration`。

开发报告只记录 Task 1-13 已实际取得的证据：分支/提交、定向后端测试、Web 测试/类型检查/构建、fake Provider、无真实付费网络、`backend/config.prod.yml` 无差异、同一原 Turn 恢复断言和已知限制。没有执行的 Task 14 故障矩阵、从 0 全链路和全量门禁必须明确标为“待业务第二步执行”，不得用定向测试冒充。

- [ ] **Step 8: 运行中文工程规范与第一步差异检查**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\agentization\Test-ChineseEngineeringPolicy.ps1 -RepositoryPath (Get-Location).Path -BaseRef origin/feature/agent_0.8.4_boguan -HeadRef HEAD
git diff --check origin/feature/agent_0.8.4_boguan...HEAD
git diff origin/feature/agent_0.8.4_boguan...HEAD -- backend/config.prod.yml
rg -n "T[O]DO|T[B]D|implement[ ]later|fill[ ]in[ ]details" backend/pixelflow/agent_runtime backend/pixelflow/agent_workflows/video web/src/lib/supervisor docs/agentization/test-reports/R2-video-live-handler-development.md
```

Expected: 中文门禁 `Passed=True`，diff check 和生产配置 diff 无输出，占位符扫描无输出。失败时只修复业务第一步范围，不能提前运行 Task 14。

- [ ] **Step 9: 提交 UI、文档与第一步验证切片**

```powershell
git add web/src/pages/WorkspacePage.tsx web/src/components/chat/ChatPanel.tsx web/src/components/chat/MessageBubble.tsx web/src/components/composer/GenParamsDialog.tsx web/src/components/canvas/StoryboardPanel.tsx web/src/components/canvas/VideoResultCard.tsx web/tests/workspaceOrchestrationMode.test.mjs web/tests/mainFlowContract.test.mjs web/tests/videoSceneUiContract.test.mjs web/tests/jianyingDraftUiContract.test.mjs README.md AGENTS.md docs/pixelflow-agent-skill-flow-latest-design.md docs/agentization/status/M13-status.md docs/agentization/test-reports/R2-video-live-handler-development.md
git commit -m "实现：接通 Supervisor 视频人工节点" -m "把表单、方向、Plan、场景包、视频审核和剪映操作映射到原 Turn 的结构化恢复路径，同步第一步开发证据；R2 真实全流程门禁仍待单独执行。"
```

### Task 14: 完成从 0 视频链路、重启恢复和隔离验收

> **本轮排除：** 本任务属于用户定义的业务第二步。完成 Task 1-13、独立代码复核和第一步报告后必须停止，等待用户再次授权，不能自动继续。

**Files:**
- Create: `backend/tests/test_agent_runtime_video_live_e2e.py`
- Modify: `backend/tests/test_agent_runtime_r2_integration.py`
- Modify: `backend/tests/fixtures/supervisor_golden_cases.json`
- Modify: `docs/pixelflow-agent-skill-flow-latest-design.md`
- Modify: `docs/agentization/status/BOARD.md`
- Modify: `docs/agentization/status/M13-status.md`
- Create: `docs/agentization/test-reports/R2-video-live-handler.md`

**Interfaces:**
- Verifies: Tasks 1-13 的跨模块合同。
- Produces: 本切片中文测试证据；不改变生产发布状态。

- [ ] **Step 1: 写入从 0 的 fake 全链路测试**

测试必须通过真实 FastAPI endpoint 驱动：创建 `initial_intent=video` 新对话 → 首轮带图片附件 Turn → 表单 → 三方向 → Plan → 场景包/素材 → 分镜视频 → 合并 → QA 修改循环 → 最终确认/下载。断言：

- 新对话为 `supervisor_v1`；
- 每轮 Snapshot 与 SSE 结果相同；
- 附件 URL/名称/引用完整；
- fake Provider 的每个 operation start 恰好一次；
- 刷新和相同 ID 重放的新增 start 为 0；
- 最终 Workflow completed，下载证据只属于当前合并视频。

```python
async def test_video_live_flow_from_zero_to_delivery(live_video_scenario) -> None:
    conversation = await live_video_scenario.create_video_conversation()
    assert conversation["orchestration_mode"] == "supervisor_v1"
    await live_video_scenario.submit_initial_turn_with_image()
    await live_video_scenario.confirm_intake_form()
    await live_video_scenario.select_direction()
    await live_video_scenario.approve_plan()
    await live_video_scenario.approve_scene_packages()
    await live_video_scenario.complete_fake_scene_operations()
    await live_video_scenario.complete_fake_merge()
    await live_video_scenario.submit_quality_revision_and_complete_affected_scenes()
    await live_video_scenario.finish_and_record_final_download()
    await live_video_scenario.assert_snapshot_matches_sse()
    assert live_video_scenario.provider_start_counts == live_video_scenario.expected_start_counts
    assert live_video_scenario.final_workflow.status is WorkflowStatus.COMPLETED
```

`live_video_scenario` fixture 只能调用 TestClient/AsyncClient 的公开 conversation、turn、snapshot、SSE 和 interrupt response endpoint；fake 完成事件通过已公开的 M06 worker port 投递，不能直接赋值 Repository 私有字段。

- [ ] **Step 2: 写入故障与隔离测试**

参数化覆盖：

- Graph checkpoint 前后进程退出；
- Provider start 后、完成 event 前退出；
- 402 暂停后用新请求凭据恢复原 provider job；
- timeout/failed 重试；
- 404/expired 创建新 attempt；
- 多分镜部分失败和只重试失败镜头；
- 用户 A 不能引用用户 B 的 conversation/workflow/artifact/interrupt；
- `frontend_v2` 历史对话不被执行器领取；
- image/ppt/video_analysis 仍为 v2；
- 模型档案失效时不执行 Graph/Provider。
- Handler 在重启后不可用时，后续新视频对话安全保持 `frontend_v2`；已经冻结为 `supervisor_v1` 的对话返回固定不可用状态，不暗中改回 v2、不迁移运行中 Turn。

```python
@pytest.mark.parametrize(
    "fault",
    [
        "checkpoint_before_commit",
        "checkpoint_after_commit",
        "provider_started_before_event",
        "quota_402",
        "provider_timeout",
        "provider_failed",
        "provider_expired_404",
        "partial_scene_failure",
        "cross_tenant_reference",
        "invalid_model_profile",
        "handler_missing_after_restart",
    ],
)
async def test_video_live_fault_matrix_is_recoverable_and_isolated(
    fault, live_video_fault_scenario
) -> None:
    result = await live_video_fault_scenario.run(fault)
    assert result.safe_reason_code in live_video_fault_scenario.allowed_reason_codes(fault)
    assert result.leaked_sensitive_values == ()
    assert result.duplicate_provider_starts == 0
    assert result.cross_tenant_objects == ()
```

fixture 对每个 fault 明确断言 expected attempt/provider job ID/Turn status；`invalid_model_profile` 的 Graph 和 Provider 调用计数必须均为 0，`handler_missing_after_restart` 还要分别断言新对话 `frontend_v2` 与旧 `supervisor_v1` 固定不可用响应。

- [ ] **Step 3: 运行 E2E 并确认先 RED 后 GREEN**

首次在实现完成前运行新 E2E，记录具体失败点；补齐的修复只能修改对应 Task 的实现文件和测试，不能降低断言。

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_video_live_e2e.py tests/test_agent_runtime_r2_integration.py -q
```

Expected final: PASS，所有外部调用均来自 fake。

- [ ] **Step 4: 运行后端定向与全量门禁**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_runtime_*.py tests/test_agent_video_*.py -q
uv run pytest -q
```

Expected: PASS；若仓库全量存在与本切片无关的既有失败，必须记录原始命令、失败测试和基线对比，不能把定向绿色冒充全量绿色。

- [ ] **Step 5: 运行前端和配置不变检查**

Run:

```powershell
Set-Location web
corepack pnpm test
corepack pnpm lint
corepack pnpm build-prod
Set-Location ..
git diff origin/feature/agent_0.8.4_boguan...HEAD -- backend/config.prod.yml
```

Expected: Web 全绿，最后一条无输出。

- [ ] **Step 6: 更新中文设计、看板和测试报告**

只写“视频 live Handler 开发切片已完成/待独立集成”，不得写 R2 已生产发布。报告必须列出：分支、提交、测试命令/结果、fake Provider start 计数、无真实网络证据、prod 配置 diff、已知限制和回滚 commit。

先采集不可伪造的本地证据，再用 `apply_patch` 把命令原文、退出码和测试摘要逐项写入报告：

```powershell
$evidenceSha = git rev-parse HEAD
$evidenceBranch = git branch --show-current
$productionDiff = git diff origin/feature/agent_0.8.4_boguan...HEAD -- backend/config.prod.yml
git log -1 --format="%H%n%s%n%b"
git status --short
```

报告状态固定写“开发切片已完成，待独立单槽集成”，网络证据固定写“未调用真实付费供应商”；提交、测试数、fake start 计数、生产配置 diff 和回滚 commit 必须来自本 Step 与前面门禁的真实输出，未获得的证据不得写入报告。

- [ ] **Step 7: 运行中文工程规范与差异检查**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\agentization\Test-ChineseEngineeringPolicy.ps1 -RepositoryPath (Get-Location).Path -BaseRef origin/feature/agent_0.8.4_boguan -HeadRef HEAD
git diff --check origin/feature/agent_0.8.4_boguan...HEAD
rg -n "T[O]DO|T[B]D|implement[ ]later|fill[ ]in[ ]details" backend/pixelflow/agent_runtime backend/pixelflow/agent_workflows/video web/src/lib/supervisor docs/agentization/test-reports/R2-video-live-handler.md
```

Expected: 中文门禁 `Passed=True`，diff check 无输出，占位符扫描无输出。

- [ ] **Step 8: 提交最终验收记录**

```powershell
git add backend/tests/test_agent_runtime_video_live_e2e.py backend/tests/test_agent_runtime_r2_integration.py backend/tests/fixtures/supervisor_golden_cases.json docs/pixelflow-agent-skill-flow-latest-design.md docs/agentization/status/BOARD.md docs/agentization/status/M13-status.md docs/agentization/test-reports/R2-video-live-handler.md
git commit -m "验证：完成 R2 视频 live Handler 本地门禁" -m "记录从零视频链路、重启恢复、配额与失败语义、租户隔离、前后端全量及生产配置未变化证据；本提交不代表生产发布。"
git push origin codex/r2-live-video-handler
```

---

## 执行完成后的停止条件

本轮完成 Task 1-13、独立代码复核、第一步开发验证与文档提交后立即停止，只报告分支、提交、已运行门禁、Task 14 未执行、未发布事实和下一步需要用户单独发起的“R2 真实全流程门禁”指令。不得自动执行 Task 14、R2 生产发布、M13.3、真实付费测试或 Agent→dev 合并。后续若用户单独授权业务第二步，则完成 Task 14 并推送后再次立即停止，等待独立发布/集成授权。
