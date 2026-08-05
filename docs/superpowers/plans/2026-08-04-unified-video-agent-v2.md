# 统一视频智能体V2落地实施方案
> **面向自动化开发人员：强制配套子能力**
必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 工具，按任务逐条落地本方案；文档内任务采用复选框 `[ ]` 标记进度。

## 整体目标
5天交付P0内部可用一体化视频智能体，支持**脚本、创意文案、参考视频**三种创作启动方式，同时提供镜头定向局部修复能力；P1阶段完成线上稳定性加固，并彻底删除V1全量代码。

## 整体架构
轻量化跨业务路由分发视频对话至`VideoAgent`。`VideoAgent`读取持久化项目工作区`VideoWorkspace`，加载已注册创作能力说明与受控工具，持久存储`AgentPlan`执行方案、`AgentPlanStep`单步任务，并将异步任务交由现有智能体运行调度器处理。
P0阶段V2适配层可复用底层V1能力服务，但不会向用户开放V1流程引擎与前端页面；P1阶段彻底清除所有V1代码依赖。

## 技术栈
Python 3.12、FastAPI、Pydantic、SQLAlchemy/Alembic、LangGraph/LangChain、基于ChatOpenAI封装DeepSeek；React 19、TypeScript、Vite、Node测试运行器

## 全局约束
1. V2默认模型为`deepseek-v4-pro`，本迭代不接入Kimi K3；
2. 完整保留现有机制：用户数据隔离、事件队列有序投递、接口幂等、任务租约故障恢复、额度权限校验；
3. 大模型仅能调用服务端注册工具，禁止直接调用厂商接口、数据库、FFmpeg、Shell脚本；
4. `Plan.md`仅作为可选脚本附件，不再是视频生成强制前置文件；
5. V2全新业务代码禁止写入 `backend/pixelflow/agent_workflows/video/`、`web/src/pages/WorkspacePage.tsx`；
6. P0替换用户可见的V1入口与旧工作区页面；P0验收通过后，P1删除V1视频工作流、调度器视频处理分支及所有残留导入代码；P0收尾时`WorkspacePage.tsx`需精简至100-200行，仅保留V2路由布局外壳；
7. 页面展示步骤耗时**仅以持久化存储的时间戳为准**，禁止对外暴露模型内部推理过程；
8. 保留原有**视频镜头面板**作为工作区核心视图，承载镜头画面、素材、多版本、选中操作；V2可重构该面板实现逻辑，但不得删减、弱化原有查看交互；
9 用户可在镜头面板直接编辑单个镜头，生成的执行方案仅限定作用于该镜头；镜头重绘完成后，对应镜头卡片必须展示可见的`重新生成完成`标识，附带新版本号与完成时间，同时保留历史版本用于对比、选用；
10. P0仅开放唯一视频运行模式`VIDEO_AGENT`，存量V1对话对常规入口隐藏；P1将存量V1对话转为只读历史记录，返回标识`video_workflow_retired`；
11. 数据库迁移脚本`20260804_08_video_agent_runtime.py`仅做新增操作：创建V2数据表、索引；绝不删除/重命名/回填/修改V1数据表数据；执行DDL语句前，生成生产环境SQLite快照并暂停数据库写入。

---

## 目录文件结构
```text
backend/pixelflow/video_agent/
  contracts/{plan.py,workspace.py,tools.py,__init__.py} 方案/工作区/工具数据契约
  workspace/{repository.py,evidence.py,__init__.py}     项目持久仓储、素材摘要
  skills/{catalog.py,__init__.py}                       创作能力清单
  tools/{registry.py,inspect_workspace.py,script.py,reference.py,scene.py,delivery.py,__init__.py} 工具注册与实现
  adapters/{video_domain.py,__init__.py}                 底层视频能力适配层
  planner/{model.py,loop.py,__init__.py}                大模型规划器、工具循环调度
  executor/{service.py,__init__.py}                     方案执行、确认逻辑

backend/pixelflow/agent_runtime/
  contracts/{enums.py,events.py,api.py,__init__.py}     运行时枚举、事件、接口定义
  persistence/{models.py,repositories.py}              数据库模型、持久化仓储
  service.py
  executor.py
  config.py

backend/packages/harness/deerflow/persistence/migrations/versions/
  20260804_08_video_agent_runtime.py  V2数据库迁移脚本

web/src/
  features/video-agent/{VideoAgentWorkspace.tsx,AgentPlanTimeline.tsx,AgentConfirmationCard.tsx,SceneEvidencePanel.tsx} V2核心页面组件
  features/video-agent/hooks/useVideoAgent.ts           智能体通用逻辑钩子
  features/video-agent/state/{contracts.ts,reducer.ts}  前端状态、事件规约
  pages/WorkspacePage.tsx                              顶层路由外壳
```

## P0阶段进度跟踪
### 已完成项
- [x] 统一视频智能体入口：`POST /turns/start` 接口将视频对话定向分发至确定性`VideoAgentEntrypoint`，持久化并恢复项目工作区、初始化执行方案，推送`agent.plan.created`事件；该链路不再唤醒旧版执行器（上线日期：2026-08-05）。
- [x] 事务级事件落库：新增`start_step_with_event`、`complete_step_with_event`方法，在同一SQL事务内完成V2步骤状态持久化与运行时事件写入；内存用例覆盖事务回滚、幂等重放场景（上线日期：2026-08-05）。
- [x] 工作区页面结构拆分：原10802行旧页面完整迁移至`features/legacy-workspace/LegacyWorkspace.tsx`；`WorkspacePage.tsx`简化为轻量化路由外壳，`VideoAgentWorkspace`作为新旧代码隔离边界，同时完整保留原有镜头面板交互（上线日期：2026-08-05）。
- [x] V2前后端通信契约与标准事件：新增`VideoWorkspace`、`AgentPlan`、`AgentPlanStep`、工具契约、持久耗时计算逻辑与6类`agent.*`标准事件；由`tests/test_agent_runtime_contracts.py`、`tests/test_video_agent_contracts.py`完成校验，代码提交哈希：`c9e18f`。
- [x] 项目、方案、步骤持久化：新增内存/SQLite双端仓储、用户数据隔离、步骤完成幂等写入、SQLite时区修复、增量迁移脚本`20260804_08`；配套用例`tests/test_video_agent_repository.py`、`tests/test_video_agent_migration.py`验证，提交哈希：`c9e18f`。
- [x] 事件载荷构造器：安全生成方案创建、步骤完成事件，载荷不携带工具参数、模型推理内容；配套用例`tests/test_video_agent_plan_events.py`校验，提交哈希：`0f9ee32`。
- [x] 前端时间线状态解析器：独立V2状态契约与状态处理器，解析步骤状态、结果摘要、素材ID、持久耗时；前端用例`web/tests/videoAgentTimelineReducer.test.mjs`验证，提交哈希：`b6310c3`。

### 进行中
- [ ] V2工作台页面开发：将时间线状态处理器对接`VideoAgentWorkspace`，P0第五天替换旧页面外壳。
- [ ] 镜头面板迁移+单镜头局部重绘能力：完整迁移原有镜头面板至V2工作台，不丢失镜头、多版本查看逻辑；新增单镜头编辑入口，触发仅作用于该镜头的执行方案，生成完成后镜头卡片展示「重新生成完成」标识。

# 任务1：用V2页面外壳替换旧工作区页面
## 涉及文件
- 新建：`web/src/features/video-agent/VideoAgentWorkspace.tsx`
- 修改：`web/src/pages/WorkspacePage.tsx`
- 新建：`web/src/features/legacy-workspace/LegacyWorkspace.tsx`，完整迁移旧页面代码，**不改动原有交互逻辑**
- 测试文件：`web/tests/videoAgentWorkspaceShell.test.mjs`

## 接口规范
入参：现有工作区路由参数、V2工作区快照接口；
输出：导出`VideoAgentWorkspace()`组件，顶层页面仅渲染V2功能模块。

- [x] 步骤1：编写页面行数、导出校验失败用例
```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../src/pages/WorkspacePage.tsx", import.meta.url), "utf8");
assert.match(page, /VideoAgentWorkspace/);
assert.ok(page.split("\n").length <= 200);
```
- [x] 步骤2：执行测试验证失败
执行命令：`cd web && node --test tests/videoAgentWorkspaceShell.test.mjs`
预期结果：测试失败，原因是V2页面不存在、原页面代码行数超过200行。
- [x] 步骤3：替换旧页面主体逻辑
将旧页面本地状态、副作用、事件处理、旧流程UI完整迁移至`LegacyWorkspace.tsx`，交互逻辑完全不变；`WorkspacePage.tsx`仅保留路由布局代码，新旧逻辑隔离边界写在`VideoAgentWorkspace.tsx`。
V2镜头面板功能未开发完成前，`VideoAgentWorkspace`临时渲染`LegacyWorkspace`，保证原有镜头面板可正常使用。
```tsx
// web/src/pages/WorkspacePage.tsx
import { VideoAgentWorkspace } from "@/features/video-agent/VideoAgentWorkspace";

export default function WorkspacePage() {
  return <VideoAgentWorkspace />;
}
```
- [ ] 步骤4：执行页面测试与TS类型校验
执行：`cd web && node --test tests/videoAgentWorkspaceShell.test.mjs && npm run lint`
预期：测试编译通过；V2页面临时兼容旧组件，待镜头面板功能完整后移除兼容逻辑。
- [ ] 步骤5：提交代码
```bash
git add -A web/src/pages/WorkspacePage.tsx web/src/features/video-agent web/tests/videoAgentWorkspaceShell.test.mjs
git commit -m "refactor: 重构，替换旧工作区页面外壳"
```

# 任务2：新增V2前后端通信契约与事件定义
## 涉及文件
- 新建：`backend/pixelflow/video_agent/contracts/{plan.py,workspace.py,tools.py,__init__.py}`
- 修改：`backend/pixelflow/agent_runtime/contracts/{enums.py,events.py,api.py,__init__.py}`
- 后端测试：`backend/tests/test_video_agent_contracts.py`
- 前端状态：`web/src/features/video-agent/state/{contracts.ts,reducer.ts,workspaceProjection.ts}`
- 前端测试：`web/tests/videoAgentContracts.test.mjs`

## 接口产出
数据结构：`VideoWorkspace`、`AgentPlan`、`AgentPlanStep`、`VideoToolCall`、`VideoToolResult`；
枚举：`AgentPlanStatus`方案状态、`PlanStepStatus`步骤状态、唯一流程模式`OrchestrationMode.VIDEO_AGENT`；
事件：`agent.plan.created`、`agent.step.started`、`agent.step.progressed`、`agent.step.completed`、`agent.step.failed`、`agent.confirmation.requested`。

- [x] 步骤1：编写Python契约校验失败用例
```python
def test_completed_step_requires_timestamps_and_duration_source():
    step = AgentPlanStep(
        step_id="step-1", plan_id="plan-1", sequence=1,
        tool_name="inspect_video_workspace", title="读取项目",
        status=PlanStepStatus.COMPLETED,
        started_at=datetime(2026, 8, 4, tzinfo=UTC),
        completed_at=datetime(2026, 8, 4, 0, 0, 3, tzinfo=UTC),
    )
    assert step.duration_ms == 3000
```
- [ ] 步骤2：编写TS事件解析失败用例
```js
assert.equal(parseAgentEvent({ type: "agent.step.completed", payload: stepPayload }).type,
  "agent.step.completed");
assert.equal(projectVideoAgentDuration(stepPayload, new Date("2026-08-04T00:00:03Z")), 3000);
```
- [ ] 步骤3：实现强约束数据契约
采用Pydantic冻结模型，开启`extra="forbid"`禁止多余字段；
方案状态：planning规划中、running执行中、awaiting_confirmation待确认、completed完成、failed失败、cancelled取消；
步骤状态：pending待执行、running运行中、awaiting_confirmation待确认、completed完成、failed失败、skipped跳过；
终态步骤必填`completed_at`，非待执行步骤必填`started_at`；耗时仅通过时间戳计算；前后端枚举字面量完全对齐。
```python
class VideoToolCall(ContractModel):
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    requires_confirmation: bool = False
```
- [ ] 步骤4：执行契约全量测试
后端：`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_contracts.py -v`
前端：`cd web && node --test tests/videoAgentContracts.test.mjs && npm run test:agent-runtime-contracts`
预期：前后端枚举、事件名称完全匹配，全部用例通过。
- [ ] 步骤5：提交契约代码
```bash
git add backend/pixelflow/video_agent backend/pixelflow/agent_runtime/contracts backend/tests/test_video_agent_contracts.py web/src/lib/supervisor web/tests/videoAgentContracts.test.mjs
git commit -m "feat: 新增视频智能体数据契约"
```

# 任务3：持久化存储项目、执行方案、步骤记录
## 涉及文件
- 新建迁移脚本：`backend/packages/harness/deerflow/persistence/migrations/versions/20260804_08_video_agent_runtime.py`
- 修改：`backend/pixelflow/agent_runtime/persistence/{models.py,repositories.py}`
- 新建仓储：`backend/pixelflow/video_agent/workspace/repository.py`
- 后端测试：`backend/tests/test_video_agent_repository.py`

## 仓储接口
提供方法：`create_workspace`创建项目、`get_workspace`查询、`save_plan`保存方案、`start_step`启动步骤、`complete_step`完成、`fail_step`失败、`list_plan_steps`查询步骤；
所有接口强制传入`user_id`，禁止跨用户读取数据。

- [x] 步骤1：权限、幂等、耗时校验用例
```python
async def test_complete_step_persists_timestamps_and_rejects_other_user(repository):
    await repository.create_workspace("u1", workspace)
    await repository.save_plan("u1", plan)
    started = await repository.start_step("u1", "plan-1", "step-1", now=t0)
    completed = await repository.complete_step("u1", "plan-1", "step-1", result, now=t3)
    assert completed.duration_ms == 3000
    assert await repository.get_workspace("u2", workspace.workspace_id) is None
```
- [x] 步骤2：执行仓储测试
执行：`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_repository.py -v`
预期失败：V2数据表、仓储方法未创建。
- [x] 步骤3：新建数据表与迁移逻辑
三张数据表：`pixelflow_video_agent_workspaces`、`pixelflow_video_agent_plans`、`pixelflow_video_agent_plan_steps`；
业务载荷以JSON快照存储；联合索引`(user_id, workspace_id)`、`(plan_id, sequence)`，唯一主键`(plan_id, step_id)`；
迁移脚本包含升级、回滚逻辑，格式参考`20260802_07_operation_quota_revision.py`。
- [ ] 步骤4：原子化状态流转逻辑
`start_step`仅允许pending→running；`complete_step`/`fail_step`仅接收running状态；重复写入相同终态数据天然幂等；数据冲突抛出现有运行时异常；本任务仅完成存储，事件逻辑在任务4实现。
- [ ] 步骤5：执行迁移与仓储测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_agent_runtime_migration.py tests/test_video_agent_repository.py -v`
预期：SQLite环境、存量迁移兼容用例全部通过。
- [x] 步骤6：提交持久化代码
```bash
git add backend/packages/harness/deerflow/persistence/migrations/versions/20260804_08_video_agent_runtime.py backend/pixelflow/agent_runtime/persistence backend/pixelflow/video_agent/workspace backend/tests/test_video_agent_repository.py
git commit -m "feat: 持久化存储智能执行方案"
```

# 任务4：持久化步骤事件推送 & 前端状态映射
## 涉及文件
- 修改：`backend/pixelflow/agent_runtime/persistence/repositories.py`
- 新建事件模块：`backend/pixelflow/video_agent/executor/events.py`
- 修改前端调度：`web/src/lib/supervisor/{reducer.ts,workspaceProjection.ts}`
- 前端状态：`web/src/features/video-agent/state/{contracts.ts,reducer.ts}`
- 后端测试：`backend/tests/test_video_agent_plan_events.py`
- 前端测试：`web/tests/videoAgentTimelineReducer.test.mjs`

## 产出能力
后端事件发布方法：`publish_plan_created`、`publish_step_started`、`publish_step_progressed`、`publish_step_completed`、`publish_step_failed`、`publish_confirmation_requested`；
前端状态`VideoAgentTimelineState`，以planId、stepId作为索引存储。

- [ ] 步骤1：后端事件队列测试用例
```python
async def test_step_completion_writes_ordered_outbox_event(repository):
    await repository.complete_step("u1", "plan-1", "step-1", result, now=t3)
    events = await repository.list_events("u1", conversation_id)
    assert events[-1].type is AgentEventType.AGENT_STEP_COMPLETED
    assert events[-1].payload["duration_ms"] == 3000
```
- [x] 步骤2：前端状态更新用例
```js
const next = reduceVideoAgentEvent(initial, completedEvent);
assert.equal(next.plans["plan-1"].steps["step-1"].status, "completed");
assert.equal(next.plans["plan-1"].steps["step-1"].durationMs, 3000);
```
- [ ] 步骤3：事务内同步生成事件
工作区、方案、步骤数据写入成功后，在同一数据库事务内生成事件；事件载荷仅包含ID、标题、状态、结果摘要、素材ID、起止时间、耗时；禁止携带提示词、模型推理内容。
- [x] 步骤4：前端事件解析渲染
独立解析6类V2事件，不改动V1旧事件逻辑；运行中步骤前端实时计算已运行时长，已完成步骤直接使用后端持久化耗时，页面刷新数据稳定可复现。
- [ ] 步骤5：执行事件全量测试
后端：`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_plan_events.py -v`
前端：`cd web && node --test tests/videoAgentTimelineReducer.test.mjs tests/supervisorEvents.test.mjs`
预期：事件有序投递，全部用例通过。
- [x] 步骤6：提交时间线事件代码
```bash
git add backend/pixelflow/agent_runtime/persistence/repositories.py backend/pixelflow/video_agent/executor/events.py backend/tests/test_video_agent_plan_events.py web/src/features/video-agent/state web/tests/videoAgentTimelineReducer.test.mjs
git commit -m "feat: 发布智能体步骤时间线事件"
```

# 任务5：创作能力目录与受控工具注册中心
## 涉及文件
- 新建能力目录：`backend/pixelflow/video_agent/skills/catalog.py`
- 新建工具模块：`backend/pixelflow/video_agent/tools/{registry.py,inspect_workspace.py,__init__.py}`
- 后端测试：`backend/tests/test_video_agent_tool_registry.py`

## 接口产出
数据结构：`VideoToolSpec`、`VideoToolRegistry`；
执行标准：`VideoTool.execute(context, arguments) -> VideoToolResult`；
首期注册工具：`inspect_video_workspace` 查看项目工作区。

- [ ] 步骤1：注册中心校验用例
```python
def test_registry_exposes_only_declared_tools():
    registry = VideoToolRegistry([InspectVideoWorkspaceTool()])
    assert registry.names() == ("inspect_video_workspace",)
    assert registry.resolve("delete_database") is None
```
- [ ] 步骤2：执行工具注册测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_tool_registry.py -v`
预期失败：注册中心未实现。
- [ ] 步骤3：基于元数据匹配能力、工具入参强校验
`SkillCatalog`通过DeerFlow存储读取`SKILL.md`配置，仅返回匹配当前场景能力；
`VideoToolSpec`包含名称、描述、JSON入参模型、计费等级、是否需确认、幂等模式、故障恢复策略；
自定义异常`VideoToolValidationError`，入参错误返回结构化结果而非程序崩溃；
`InspectVideoWorkspaceTool`仅返回精简素材摘要，不返回厂商密钥、完整敏感载荷。
- [ ] 步骤4：执行注册中心测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_tool_registry.py -v`
预期：非法工具、错误参数全部拦截，用例通过。
- [ ] 步骤5：提交代码
```bash
git add backend/pixelflow/video_agent/skills backend/pixelflow/video_agent/tools backend/tests/test_video_agent_tool_registry.py
git commit -m "feat: 新增创作能力目录与工具注册中心"
```

# 任务6：DeepSeek规划器与工具循环执行器
## 涉及文件
- 新建规划器：`backend/pixelflow/video_agent/planner/{model.py,loop.py,__init__.py}`
- 新建执行服务：`backend/pixelflow/video_agent/executor/service.py`
- 测试：`backend/tests/test_video_agent_planner.py`、`backend/tests/test_video_agent_executor.py`

## 接口产出
`VideoAgentPlanner.plan_turn(context) -> AgentPlan` 单轮生成执行方案
`VideoAgentExecutor.run_plan(user_id, plan_id) -> AgentPlan` 完整执行方案
`confirm_step(user_id, plan_id, step_id) -> AgentPlan` 用户确认计费步骤
`resume_plan(user_id, plan_id) -> AgentPlan` 重连后恢复任务
依赖：工具注册中心、工作区仓储、现有`deepseek-v4-pro`模型工厂。

- [ ] 步骤1：规划器模拟测试用例
```python
async def test_planner_turn_for_reference_video_starts_with_analysis(fake_model, executor):
    plan = await executor.plan_turn(user_id="u1", content="参考这个视频，换成我的商品", materials=[reference])
    assert [step.tool_name for step in plan.steps][:2] == [
        "inspect_video_workspace", "analyze_reference_video"
    ]
```
- [ ] 步骤2：循环终止条件用例
```python
async def test_executor_stops_before_billable_tool_until_confirmation(executor):
    plan = await executor.run_plan("u1", "plan-1")
    assert plan.status is AgentPlanStatus.AWAITING_CONFIRMATION
    assert plan.steps[-1].tool_name == "generate_scenes"
```
- [ ] 步骤3：强约束模型调用边界
使用`with_structured_output`约束方案输出，仅允许调用注册工具；单轮对话最多8次工具调用，模型自动修正最多2次；工具结果结构化追加至上下文；仅持久化方案与公开摘要，不存储模型推理过程。
- [ ] 步骤4：方案执行与确认拦截逻辑
逐步骤执行：标记running、推送启动事件、执行工具、存储终态并推送结果，循环推进；计费工具执行前强制中断等待用户确认；`confirm_step`记录审批状态并恢复流程；`resume_plan`基于租约恢复未完成任务，避免重复执行。
- [ ] 步骤5：执行规划、执行器测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_planner.py tests/test_video_agent_executor.py -v`
预期：不调用未注册工具，计费操作必须确认，重连从待执行步骤恢复。
- [ ] 步骤6：提交智能体循环代码
```bash
git add backend/pixelflow/video_agent/planner backend/pixelflow/video_agent/executor backend/tests/test_video_agent_planner.py backend/tests/test_video_agent_executor.py
git commit -m "feat: 新增DeepSeek规划与工具循环执行器"
```

# 任务7：脚本、创意、参考视频工具适配
## 涉及文件
- 新建工具：`backend/pixelflow/video_agent/tools/{script.py,reference.py}`
- 新建适配层：`backend/pixelflow/video_agent/adapters/video_domain.py`
- 测试：`backend/tests/test_video_agent_script_tools.py`、`backend/tests/test_video_agent_reference_tools.py`

## 产出工具
`import_script`导入脚本、`brainstorm_script`创意生成、`analyze_reference_video`解析参考视频；
通过适配层复用`creative/plan_markdown.py`、`creative/brief_generate.py`、视频拆解底层能力，**禁止导入V1旧处理器**。

- [ ] 步骤1：完整脚本免评审用例
```python
async def test_import_script_creates_script_artifact_without_plan_review(tool_context):
    result = await ImportScriptTool().execute(tool_context, {"markdown": MATURE_SCRIPT})
    assert result.workspace_patch["script"]["source"] == "user_import"
    assert result.requires_confirmation is False
```
- [ ] 步骤2：参考视频解析用例
```python
async def test_reference_analysis_persists_scenes_and_assets(tool_context, fake_decompose_skill):
    result = await AnalyzeReferenceVideoTool().execute(tool_context, {"reference_asset_ref": "artifact:ref-1"})
    assert result.workspace_patch["reference_videos"][0]["storyboard"][0]["scene_id"]
```
- [ ] 步骤3：适配层实现，隔离V1旧代码
`ImportScriptTool`标准化用户脚本存入工作区，返回缺失需求摘要；`BrainstormScriptTool`仅生成版本化草稿；`AnalyzeReferenceVideoTool`复用调度器创建持久化任务，解析完成后标准化存储分镜与素材，全程不调用废弃V1评审流程。
- [ ] 步骤4：执行脚本、参考视频测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_script_tools.py tests/test_video_agent_reference_tools.py tests/test_reference_video_nodes.py -v`
预期：完整脚本无需强制评审，重复解析复用已有任务。
- [ ] 步骤5：提交首批业务工具
```bash
git add backend/pixelflow/video_agent/tools/script.py backend/pixelflow/video_agent/tools/reference.py backend/pixelflow/video_agent/adapters/video_domain.py backend/tests/test_video_agent_script_tools.py backend/tests/test_video_agent_reference_tools.py
git commit -m "feat: 新增脚本、创意、参考视频工具"
```

# 任务8：素材替换、镜头质检、定向生成工具适配
## 涉及文件
- 新建镜头工具：`backend/pixelflow/video_agent/tools/scene.py`
- 修改质检模块：`backend/pixelflow/qc/{video_review.py,revision_scope.py}`
- 测试：`backend/tests/test_video_agent_scene_tools.py`

## 产出工具
`replace_project_assets`素材替换、`inspect_scene`镜头质检、`patch_scene`镜头修复、`generate_scenes`批量生成、`review_generated_scenes`成片审核；
`generate_scenes`入参支持`scene_ids`指定镜头、`variant_count`多版本；产生计费操作时，必须用户确认才可执行。
专属逻辑：用户在镜头面板编辑单个镜头时，生成的方案仅限定该镜头；新版视频生成完成后，卡片展示「重新生成完成」标识，保留历史版本用于对比选用。

- [ ] 步骤1：镜头校验、定向生成测试用例
```python
async def test_inspect_scene_returns_repairable_evidence(tool_context):
    result = await InspectSceneTool().execute(tool_context, {"scene_id": "scene-3"})
    assert result.workspace_patch["qc"]["scene-3"]["repair_suggestion"]

async def test_generate_scenes_requires_confirmation_and_scopes_ids(tool_context):
    result = await GenerateScenesTool().execute(tool_context, {"scene_ids": ["scene-3"], "variant_count": 3})
    assert result.requires_confirmation is True
    assert result.preview["scene_ids"] == ["scene-3"]
```
- [ ] 步骤2：执行镜头工具测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_scene_tools.py -v`
预期失败：V2镜头工具、质检契约未实现。
- [ ] 步骤3：镜头质检与修复流程实现
标准化质检输出结构：镜头ID、问题清单、素材引用、修复建议、关联资产；
`PatchSceneTool`仅修改允许变更的镜头字段，生成新版工作区快照；
`GenerateScenesTool`校验镜头合法性，确认后每个镜头生成多版本并记录任务ID；
`ReviewGeneratedScenesTool`支持选用/废弃版本，不会静默修改无关镜头；
面板单镜头编辑仅生成对应镜头方案，生成完成展示标识并留存历史版本。
- [ ] 步骤4：镜头模块回归测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_scene_tools.py tests/test_video_quality_review.py -v`
预期：定向生成仅作用指定镜头，无废弃V1模块导入。
- [ ] 步骤5：提交镜头工具代码
```bash
git add backend/pixelflow/video_agent/tools/scene.py backend/pixelflow/qc/video_review.py backend/pixelflow/qc/revision_scope.py backend/tests/test_video_agent_scene_tools.py
git commit -m "feat: 新增素材替换、镜头质检、定向生成工具"
```

# 任务9：视频合成与导出工具适配
## 涉及文件
- 新建交付工具：`backend/pixelflow/video_agent/tools/delivery.py`
- 测试：`backend/tests/test_video_agent_delivery_tools.py`

## 产出工具
`compose_or_export_video`，支持输出mp4成片、剪映工程包；通过适配层复用现有合成、导出底层能力。

- [ ] 步骤1：导出前置校验用例
```python
async def test_export_rejects_workspace_with_unresolved_dirty_scenes(tool_context):
    with pytest.raises(VideoToolValidationError, match="dirty_scene_ids"):
        await ComposeOrExportVideoTool().execute(tool_context, {"output_type": "mp4"})
```
- [ ] 步骤2：导出校验逻辑实现
导出前校验：所有镜头必须有审核通过版本、无未处理质检问题；
MP4、剪映打包会产生厂商/存储计费，必须用户确认；导出完成后素材ID存入工作区。
- [ ] 步骤3：执行导出测试
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_delivery_tools.py -v`
预期：异常项目拦截导出，无V1导出残留逻辑。
- [ ] 步骤4：提交导出工具
```bash
git add backend/pixelflow/video_agent/tools/delivery.py backend/tests/test_video_agent_delivery_tools.py
git commit -m "feat: 新增视频合成、剪映导出工具"
```

# 任务10：前端工作台、时间线、镜头面板页面开发
## 涉及文件
- 新建组件：`web/src/features/video-agent/{VideoAgentWorkspace.tsx,AgentPlanTimeline.tsx,AgentConfirmationCard.tsx,SceneEvidencePanel.tsx}`
- 新建钩子：`web/src/features/video-agent/hooks/useVideoAgent.ts`
- 修改顶层页面：`web/src/pages/WorkspacePage.tsx`
- 前端测试：`web/tests/videoAgentWorkspace.test.mjs`

## 页面能力
`VideoAgentWorkspace`读取V2快照与SSE实时消息，**镜头面板作为核心视图**；时间线展示标题、状态、素材链接、起止时间、耗时。

- [ ] 步骤1：UI契约测试用例
```js
assert.match(read("src/features/video-agent/AgentPlanTimeline.tsx"), /durationMs/);
assert.match(read("src/features/video-agent/AgentPlanTimeline.tsx"), /正在/);
assert.match(read("src/pages/WorkspacePage.tsx"), /VideoAgentWorkspace/);
```
- [ ] 步骤2：时间线渲染实现
六种状态配套图标与简洁文案；运行中步骤每秒刷新已耗时，已完成使用后端持久化时长；全程不展示提示词、原始工具参数、模型推理内容。
- [ ] 步骤3：镜头面板、确认弹窗
`SceneEvidencePanel`展示选中镜头画面、质检问题、关联素材；
`AgentConfirmationCard`展示费用摘要、受影响镜头，提交仅传递步骤ID，不提交自定义流程指令；
镜头面板完整迁移，每个镜头卡片提供编辑入口，重绘完成展示「重新生成完成」标识并留存历史版本。
- [ ] 步骤4：顶层页面简化
页面仅保留V2导入与布局，无模式判断、无旧版降级逻辑：
```tsx
return <VideoAgentWorkspace />;
```
- [ ] 步骤5：前端测试与打包
`cd web && node --test tests/videoAgentWorkspace.test.mjs tests/videoAgentTimelineReducer.test.mjs && npm run lint && npm run build-dev`
预期：已完成步骤展示固定耗时，运行中实时刷新时长。
- [ ] 步骤6：提交V2前端代码
```bash
git add web/src/features/video-agent web/src/pages/WorkspacePage.tsx web/tests/videoAgentWorkspace.test.mjs
git commit -m "feat: 新增V2智能体工作台页面"
```

# 任务11：V2作为唯一视频入口，彻底下线V1
## 涉及文件
- 修改运行时：`backend/pixelflow/agent_runtime/{config.py,service.py,executor.py}`
- 新建视频路由：`backend/pixelflow/agent_runtime/video_router.py`
- 删除目录：`backend/pixelflow/agent_workflows/video/`、V1调度器视频分支、全部V1相关测试
- 新建测试：`backend/tests/{test_video_agent_entry.py,test_video_agent_e2e.py,test_video_agent_retirement.py}`

## 接口产出
统一入口`VideoAgentEntrypoint.submit_turn`处理所有视频对话；
存量V1任务查询返回`video_workflow_retired`，仅可读、不可执行/修改。

- [ ] 步骤1：入口、归档、恢复测试用例
```python
def test_every_new_video_turn_uses_video_agent_entrypoint(app):
    assert app.video_router.resolve("video") is app.video_agent_entrypoint

async def test_historical_v1_workflow_is_read_only(runtime):
    result = await runtime.resume_workflow("old-v1-workflow")
    assert result.code == "video_workflow_retired"

async def test_reference_remix_resumes_after_generation_operation_restart(runtime):
    plan = await runtime.submit("u1", "参考这个视频，把商品换成我的", [reference, product])
    await runtime.confirm_step("u1", plan.plan_id, plan.pending_confirmation_step_id)
    restored = await runtime.resume_plan("u1", plan.plan_id)
    assert restored.steps[-1].status in {PlanStepStatus.RUNNING, PlanStepStatus.COMPLETED}
```
- [ ] 步骤2：替换旧路由逻辑
删除`SUPERVISOR_V1`、`VIDEO_AGENT_V2`、灰度开关、模式分支；所有视频请求统一进入`VideoAgentExecutor`；保留通用调度、事件队列、额度、权限校验。
- [ ] 步骤3：存量V1归档只读逻辑
识别历史V1数据，返回只读归档信息；不迁移V1状态、不重启任务、不调用厂商接口；数据库原始记录保留用于审计，后续可通过数据清理任务删除。
- [ ] 步骤4：删除全部V1代码
移除V1视频工作流、调度器视频处理分支、接口、相关测试；统一通过V2适配层调用规划、生成、质检、合成能力。
- [ ] 步骤5：页面快照与SSE状态恢复
对话快照返回当前项目、执行方案、待确认任务；页面先加载快照，再叠加实时事件；未完成任务复用调度，仅推送进度事件，不重复创建任务。
- [ ] 步骤6：下线全量回归测试
后端：`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_entry.py tests/test_video_agent_retirement.py tests/test_video_agent_e2e.py tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_event_outbox.py -v`
前端：`cd web && node --test tests/videoAgentWorkspaceShell.test.mjs tests/videoAgentWorkspace.test.mjs && npm run lint`
预期：所有视频对话走V2入口，存量V1无法执行，重连无重复计费任务。
- [ ] 步骤7：标准场景全量评测
搭建30–50套标准用例：完整脚本、纯创意、参考视频改版、镜头修复、模糊指令、重复提交、服务重启；若规划器调用未注册工具、未确认执行计费任务则评测失败，评测通过后方可上线。
`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_evaluation.py -v`
- [ ] 步骤8：提交下线V1代码
```bash
git add -A backend/pixelflow/agent_runtime backend/pixelflow/agent_workflows backend/tests web/src/features web/src/lib/supervisor web/tests
git commit -m "refactor: 重构，下线V1视频工作流"
```

# 业务分阶段排期
## 前置假设
1名全职开发；现有视频生成/质检/合成底层服务可复用；厂商接口无变更阻塞；P0为内部测试版本，不对外发布。
## P0：5天一体化智能可用版本
| 天数 | 推进任务 | 用户可见效果与验收标准 |
| --- | --- | --- |
| Day1 | 任务2、3、4、5 | 统一VideoAgent入口替换旧调度；支持持久化项目、可视化步骤；工具库仅开放查询类安全工具 |
| Day2 | 任务6、7 | 对话支持完整脚本/创意/参考视频三类输入，智能匹配对应工具，生成精简方案并持久化素材、分镜 |
| Day3 | 任务7、8 | 用户上传素材可发起批量替换，智能识别受影响镜头，展示范围与计费，确认后每个镜头生成3版 |
| Day4 | 任务8、9 | 支持指令「检查第3镜并重做」，也可在镜头面板单点编辑；自动返回质检问题与修复方案，仅重绘指定镜头；支持版本选择、导出MP4；剪映导出需冒烟测试通过才开放 |
| Day5 | 任务1、10、11（仅切换入口） | `WorkspacePage`简化为V2外壳，前端仅提供V2工作台作为视频唯一入口；验证10个核心场景：三类创作入口、素材替换、费用确认、多版本生成、镜头质检修复、页面重连、重复提交、MP4导出 |

P0交付标准：用户单轮对话可走完三类创作全流程，可视化步骤耗时，可完成参考视频改版、单镜头局部修复；P0暂不完整删除V1代码、不跑完50套标准用例、剪映兼容与底层标准化延后至P1。

## P1：线上加固、彻底下线V1（P0完成后5-7工作日）
| 工作内容 | 对应任务 | 业务价值 |
| --- | --- | --- |
| 完善持久化与事件体系 | 2-4 | V2项目/方案数据模型完整，页面重启状态稳定，生产迁移演练通过 |
| 补全全部工具与导出能力 | 7-10 | 剪映导出、完整素材包、质检详情展示，所有规划场景线上可用 |
| 彻底清理V1代码 | 1、11 | 删除V1工作流、调度器、旧页面；存量对话归档只读，代码无任何V1导入 |
| 上线全量验收 | 最终校验 | 通过30–50套标准用例，额度、重复提交、任务重启、数据迁移全量测试完成 |

任务1–11技术定义保持不变；P0优先实现最小可用业务链路，P1补齐所有清理、线上稳定能力。

# 最终验收清单
- [ ] 后端全量V2测试：`cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_*.py -v`
- [ ] 运行时底层回归：`cd backend && PYTHONPATH=. uv run pytest tests/test_agent_runtime_* -v`
- [ ] 前端全量测试、语法校验、开发打包：`cd web && npm test && npm run lint && npm run build-dev`
- [ ] 校验页面行数：`wc -l web/src/pages/WorkspacePage.tsx` 行数控制在100–200之间
- [ ] 代码完整性校验：`git diff --check` 无语法错误；全局检索`agent_workflows.video|SUPERVISOR_V1|VIDEO_AGENT_V2|LegacyWorkspace`，业务代码无匹配结果


# Unified Video Agent V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a five-day P0 unified VideoAgent that supports script, idea, and reference-video starts plus scoped repair; complete V1 physical retirement and production hardening in P1.

**Architecture:** A thin cross-domain router assigns video turns to `VideoAgent`. `VideoAgent` reads a persistent `VideoWorkspace`, selects registered Skill guidance and controlled tools, stores `AgentPlan` / `AgentPlanStep`, and delegates async work to the existing Agent Runtime operation coordinator. During P0, V2 adapters may call reusable lower-level V1 provider services, but no V1 stage machine or UI is user-accessible; P1 removes those remaining implementation dependencies.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy/Alembic, LangGraph/LangChain, DeepSeek via `ChatOpenAI`, React 19, TypeScript, Vite, Node test runner.

## Global Constraints

- The V2 default model is `deepseek-v4-pro`; Kimi K3 is not part of this implementation.
- Preserve existing user isolation, event outbox ordering, operation idempotency, lease recovery, and quota authorization.
- The model may choose only server-registered tools; it never receives direct provider, database, FFmpeg, or shell tools.
- `Plan.md` is an optional script artifact, never a mandatory entry gate.
- New V2 feature code must not be added to `backend/pixelflow/agent_workflows/video/` or `web/src/pages/WorkspacePage.tsx`.
- P0 replaces the user-visible V1 entry and old Workspace implementation; P1 deletes V1 video workflow code, the V1 video Supervisor action path, and residual imports after the P0 acceptance suite passes. `WorkspacePage.tsx` must end as a 100-200 line V2 route/layout shell by the end of P0.
- Persisted step timestamps are the source for displayed duration. Do not expose model chain-of-thought.
- Preserve the existing **video scene package** as the primary workspace view for scene videos, assets, variants, and selection. V2 may migrate its implementation, but must not remove or degrade its existing inspection workflow.
- A user may modify one scene directly from the video scene package. The resulting plan must be scoped to that scene only; once its regenerated video is ready, the same scene card must show a visible `重新生成完成` mark with the new version and completion time, while retaining prior variants for comparison and selection.
- P0 exposes one active video mode, `VIDEO_AGENT`. Existing V1 conversations are hidden from normal entry; P1 makes them read-only historical records returning `video_workflow_retired`.
- Migration `20260804_08_video_agent_runtime.py` is additive only: create V2 tables and indexes; never drop, rename, backfill, or mutate V1 rows. Take a production SQLite snapshot and pause writes while applying schema DDL.

---

## File Structure

```text
backend/pixelflow/video_agent/
  contracts/{plan.py,workspace.py,tools.py,__init__.py}
  workspace/{repository.py,evidence.py,__init__.py}
  skills/{catalog.py,__init__.py}
  tools/{registry.py,inspect_workspace.py,script.py,reference.py,scene.py,delivery.py,__init__.py}
  adapters/{video_domain.py,__init__.py}
  planner/{model.py,loop.py,__init__.py}
  executor/{service.py,__init__.py}

backend/pixelflow/agent_runtime/
  contracts/{enums.py,events.py,api.py,__init__.py}
  persistence/{models.py,repositories.py}
  service.py
  executor.py
  config.py

backend/packages/harness/deerflow/persistence/migrations/versions/
  20260804_08_video_agent_runtime.py

web/src/
  features/video-agent/{VideoAgentWorkspace.tsx,AgentPlanTimeline.tsx,AgentConfirmationCard.tsx,SceneEvidencePanel.tsx}
  features/video-agent/hooks/useVideoAgent.ts
  features/video-agent/state/{contracts.ts,reducer.ts}
  pages/WorkspacePage.tsx
```

## P0 Progress Tracking

### Completed

- [x] **Unified VideoAgent entry**: `POST /turns/start` now routes a primary video Turn to the deterministic `VideoAgentEntrypoint`, which persists/replays its workspace and initial plan and emits `agent.plan.created`; this path no longer wakes the legacy live executor (`P0`, 2026-08-05).
- [x] **Transactional outbox publication**: `start_step_with_event` and `complete_step_with_event` atomically persist V2 step transitions and the existing runtime Outbox event in SQL; memory tests cover rollback and idempotent replay (`P0`, 2026-08-05).
- [x] **Workspace page structural extraction**: moved the 10,802-line legacy implementation to `features/legacy-workspace/LegacyWorkspace.tsx`; `WorkspacePage.tsx` is now a thin route shell and `VideoAgentWorkspace` owns the migration boundary while preserving the existing video scene package (`P0`, 2026-08-05).
- [x] **V2 timeline renderer**: added `AgentPlanTimeline`, which renders only public step title/status/summary/artifact-safe state and persisted duration, with a live elapsed time for running steps (`P0`, 2026-08-05).
- [x] **V2 contracts and public event names**: added `VideoWorkspace`, `AgentPlan`, `AgentPlanStep`, tool contracts, persisted duration calculation, and the six `agent.*` public event types. Verified by `tests/test_agent_runtime_contracts.py` and `tests/test_video_agent_contracts.py`. Commit: `c9e18f8`.
- [x] **Workspace, plan, and step persistence**: added memory/SQLite repositories, owner isolation, idempotent completed-step writes, SQLite UTC restoration, and the additive `20260804_08` migration. Verified by `tests/test_video_agent_repository.py` and `tests/test_video_agent_migration.py`. Commit: `c9e18f8`.
- [x] **Public event payload builders**: added safe plan-created and step-completed event projections without tool arguments or model reasoning. Verified by `tests/test_video_agent_plan_events.py`. Commit: `0f9ee32`.
- [x] **Frontend timeline state projection**: added the isolated V2 timeline contracts and reducer for public step status, result summaries, asset references, and persisted duration. Verified by `web/tests/videoAgentTimelineReducer.test.mjs`. Commit: `b6310c3`.

### In Progress

- [ ] **V2 workbench UI**: connect the timeline reducer to `VideoAgentWorkspace` and replace the old page shell during P0 Day 5.
- [ ] **Skill catalog and controlled tools**: the strict `VideoToolRegistry` and safe `inspect_video_workspace` tool are implemented; DeerFlow `SKILL.md` discovery and scenario matching remain pending.
- [ ] **Scene package preservation and local regeneration**: migrate the existing video scene package into the V2 workbench without losing its scene/variant inspection flow; add a per-scene edit entry that creates a scoped regeneration plan and marks that scene `重新生成完成` after its replacement video is ready.

## Task 1: Replace the Old Workspace Page With a V2 Feature Shell

**Files:**
- Create: `web/src/features/video-agent/VideoAgentWorkspace.tsx`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Create: `web/src/features/legacy-workspace/LegacyWorkspace.tsx` by moving the old page implementation without behavioral changes
- Test: `web/tests/videoAgentWorkspaceShell.test.mjs`

**Interfaces:**
- Consumes: the existing workspace route props and V2 workspace snapshot API.
- Produces: `export function VideoAgentWorkspace(): JSX.Element` and a default page shell that renders only the V2 feature.

- [x] **Step 1: Write the failing shell-size and export test**

```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../src/pages/WorkspacePage.tsx", import.meta.url), "utf8");
assert.match(page, /VideoAgentWorkspace/);
assert.ok(page.split("\n").length <= 200);
```

- [x] **Step 2: Run the test to verify it fails**

Run: `cd web && node --test tests/videoAgentWorkspaceShell.test.mjs`

Expected: FAIL because the V2 feature shell does not exist and `WorkspacePage.tsx` exceeds 200 lines.

- [x] **Step 3: Replace the legacy page body with the V2 feature shell**

Move the old page-local state, effects, handlers, and legacy workflow UI unchanged to `LegacyWorkspace.tsx`. Keep only route/layout concerns in `WorkspacePage.tsx`; place the migration boundary in `VideoAgentWorkspace.tsx`. Until V2 scene-package data and actions are wired, `VideoAgentWorkspace` renders `LegacyWorkspace` to retain the existing working scene-package experience.

```tsx
// web/src/pages/WorkspacePage.tsx
import { VideoAgentWorkspace } from "@/features/video-agent/VideoAgentWorkspace";

export default function WorkspacePage() {
  return <VideoAgentWorkspace />;
}
```

- [ ] **Step 4: Run V2 shell tests and type checking**

Run: `cd web && node --test tests/videoAgentWorkspaceShell.test.mjs && npm run lint`

Expected: PASS; the V2 workspace shell compiles, while its transitional `LegacyWorkspace` import remains until the V2 scene-package surface is feature-complete.

- [ ] **Step 5: Commit the extraction**

```bash
git add -A web/src/pages/WorkspacePage.tsx web/src/features/video-agent web/tests/videoAgentWorkspaceShell.test.mjs
git commit -m "refactor: replace legacy workspace shell"
```

## Task 2: Add V2 Wire Contracts and Event Types

**Files:**
- Create: `backend/pixelflow/video_agent/contracts/{plan.py,workspace.py,tools.py,__init__.py}`
- Modify: `backend/pixelflow/agent_runtime/contracts/{enums.py,events.py,api.py,__init__.py}`
- Create: `backend/tests/test_video_agent_contracts.py`
- Create: `web/src/features/video-agent/state/{contracts.ts,reducer.ts,workspaceProjection.ts}`
- Create: `web/tests/videoAgentContracts.test.mjs`

**Interfaces:**
- Produces: `VideoWorkspace`, `AgentPlan`, `AgentPlanStep`, `VideoToolCall`, `VideoToolResult`, `AgentPlanStatus`, `PlanStepStatus`, and the sole `OrchestrationMode.VIDEO_AGENT` value.
- Produces event values `agent.plan.created`, `agent.step.started`, `agent.step.progressed`, `agent.step.completed`, `agent.step.failed`, and `agent.confirmation.requested`.

- [x] **Step 1: Write failing Python contract tests**

```python
def test_completed_step_requires_timestamps_and_duration_source():
    step = AgentPlanStep(
        step_id="step-1", plan_id="plan-1", sequence=1,
        tool_name="inspect_video_workspace", title="读取项目",
        status=PlanStepStatus.COMPLETED,
        started_at=datetime(2026, 8, 4, tzinfo=UTC),
        completed_at=datetime(2026, 8, 4, 0, 0, 3, tzinfo=UTC),
    )
    assert step.duration_ms == 3000
```

- [ ] **Step 2: Write failing TypeScript event parsing tests**

```js
assert.equal(parseAgentEvent({ type: "agent.step.completed", payload: stepPayload }).type,
  "agent.step.completed");
assert.equal(projectVideoAgentDuration(stepPayload, new Date("2026-08-04T00:00:03Z")), 3000);
```

- [ ] **Step 3: Implement strict contracts and matching wire values**

Use Pydantic frozen models with `extra="forbid"`. Model plan status as `planning`, `running`, `awaiting_confirmation`, `completed`, `failed`, and `cancelled`; model step status as `pending`, `running`, `awaiting_confirmation`, `completed`, `failed`, and `skipped`. Require `completed_at` for terminal steps, require `started_at` for non-pending steps, and expose computed `duration_ms` only from timestamps. Mirror literals exactly in TypeScript.

```python
class VideoToolCall(ContractModel):
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    requires_confirmation: bool = False
```

- [ ] **Step 4: Run contract suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_contracts.py -v`

Run: `cd web && node --test tests/videoAgentContracts.test.mjs && npm run test:agent-runtime-contracts`

Expected: PASS with Python and TypeScript values agreeing on every enum and event name.

- [ ] **Step 5: Commit contracts**

```bash
git add backend/pixelflow/video_agent backend/pixelflow/agent_runtime/contracts backend/tests/test_video_agent_contracts.py web/src/lib/supervisor web/tests/videoAgentContracts.test.mjs
git commit -m "feat: add video agent contracts"
```

## Task 3: Persist Video Workspaces, Plans, and Steps

**Files:**
- Create: `backend/packages/harness/deerflow/persistence/migrations/versions/20260804_08_video_agent_runtime.py`
- Modify: `backend/pixelflow/agent_runtime/persistence/{models.py,repositories.py}`
- Create: `backend/pixelflow/video_agent/workspace/repository.py`
- Create: `backend/tests/test_video_agent_repository.py`

**Interfaces:**
- Produces repository methods `create_workspace`, `get_workspace`, `save_plan`, `start_step`, `complete_step`, `fail_step`, and `list_plan_steps`.
- All methods take `user_id` and reject cross-user access.

- [x] **Step 1: Write failing repository tests for ownership, idempotency, and duration**

```python
async def test_complete_step_persists_timestamps_and_rejects_other_user(repository):
    await repository.create_workspace("u1", workspace)
    await repository.save_plan("u1", plan)
    started = await repository.start_step("u1", "plan-1", "step-1", now=t0)
    completed = await repository.complete_step("u1", "plan-1", "step-1", result, now=t3)
    assert completed.duration_ms == 3000
    assert await repository.get_workspace("u2", workspace.workspace_id) is None
```

- [x] **Step 2: Run the repository test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_repository.py -v`

Expected: FAIL because V2 rows and repository methods do not exist.

- [x] **Step 3: Add rows and migration**

Create `pixelflow_video_agent_workspaces`, `pixelflow_video_agent_plans`, and `pixelflow_video_agent_plan_steps`. Store typed business payloads as JSON snapshots, use `(user_id, workspace_id)` and `(plan_id, sequence)` indexes, and use a unique `(plan_id, step_id)` identity. The migration must include upgrade and downgrade operations and follow the naming style in `20260802_07_operation_quota_revision.py`.

- [ ] **Step 4: Implement atomic repository transitions**

`start_step` changes only `pending -> running`; `complete_step` and `fail_step` accept only `running`; repeated calls with the same terminal snapshot are idempotent; conflicting payloads raise the existing runtime conflict error. Emit no events in this task; persistence is committed before Task 4 adds outbox events.

- [ ] **Step 5: Run migration and repository suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_agent_runtime_migration.py tests/test_video_agent_repository.py -v`

Expected: PASS for SQLite and existing migration compatibility fixtures.

- [x] **Step 6: Commit persistence**

```bash
git add backend/packages/harness/deerflow/persistence/migrations/versions/20260804_08_video_agent_runtime.py backend/pixelflow/agent_runtime/persistence backend/pixelflow/video_agent/workspace backend/tests/test_video_agent_repository.py
git commit -m "feat: persist video agent plans"
```

## Task 4: Publish Durable Plan-Step Events and Project Them in the UI

**Files:**
- Modify: `backend/pixelflow/agent_runtime/persistence/repositories.py`
- Create: `backend/pixelflow/video_agent/executor/events.py`
- Modify: `web/src/lib/supervisor/{reducer.ts,workspaceProjection.ts}`
- Create: `web/src/features/video-agent/state/{contracts.ts,reducer.ts}`
- Create: `backend/tests/test_video_agent_plan_events.py`
- Create: `web/tests/videoAgentTimelineReducer.test.mjs`

**Interfaces:**
- Produces `publish_plan_created`, `publish_step_started`, `publish_step_progressed`, `publish_step_completed`, `publish_step_failed`, and `publish_confirmation_requested`.
- Produces `VideoAgentTimelineState` keyed by `planId` and `stepId`.

- [ ] **Step 1: Write failing backend outbox tests**

```python
async def test_step_completion_writes_ordered_outbox_event(repository):
    await repository.complete_step("u1", "plan-1", "step-1", result, now=t3)
    events = await repository.list_events("u1", conversation_id)
    assert events[-1].type is AgentEventType.AGENT_STEP_COMPLETED
    assert events[-1].payload["duration_ms"] == 3000
```

- [x] **Step 2: Write failing reducer tests**

```js
const next = reduceVideoAgentEvent(initial, completedEvent);
assert.equal(next.plans["plan-1"].steps["step-1"].status, "completed");
assert.equal(next.plans["plan-1"].steps["step-1"].durationMs, 3000);
```

- [ ] **Step 3: Implement transactional event publication**

Construct agent events only after the corresponding workspace/plan/step write succeeds in the same repository transaction. Event payloads contain IDs, title, status, result summary, artifact references, `started_at`, `completed_at`, and `duration_ms`; they never contain prompt internals or model reasoning.

- [x] **Step 4: Implement frontend event projection**

Parse the six V2 event values in the V2 feature state module. Derive elapsed time from `startedAt` in the renderer for running steps; store completed duration from the backend event to keep reconnect behavior deterministic.

- [ ] **Step 5: Run event suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_plan_events.py -v`

Run: `cd web && node --test tests/videoAgentTimelineReducer.test.mjs tests/supervisorEvents.test.mjs`

Expected: PASS and monotonic event order preserved.

- [x] **Step 6: Commit event timeline foundation**

```bash
git add backend/pixelflow/agent_runtime/persistence/repositories.py backend/pixelflow/video_agent/executor/events.py backend/tests/test_video_agent_plan_events.py web/src/features/video-agent/state web/tests/videoAgentTimelineReducer.test.mjs
git commit -m "feat: publish video agent step timeline"
```

## Task 5: Implement the Skill Catalog and Controlled Tool Registry

**Files:**
- Create: `backend/pixelflow/video_agent/skills/catalog.py`
- Create: `backend/pixelflow/video_agent/tools/{registry.py,inspect_workspace.py,__init__.py}`
- Create: `backend/tests/test_video_agent_tool_registry.py`

**Interfaces:**
- Produces `VideoToolSpec`, `VideoToolRegistry`, and `VideoTool.execute(context, arguments) -> VideoToolResult`.
- Initial registered tool: `inspect_video_workspace`.

- [ ] **Step 1: Write failing registry tests**

```python
def test_registry_exposes_only_declared_tools():
    registry = VideoToolRegistry([InspectVideoWorkspaceTool()])
    assert registry.names() == ("inspect_video_workspace",)
    assert registry.resolve("delete_database") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_tool_registry.py -v`

Expected: FAIL because the registry does not exist.

- [ ] **Step 3: Implement metadata-first Skill selection and tool validation**

`SkillCatalog` loads enabled `SKILL.md` metadata through the existing DeerFlow storage API and returns only applicable manifests. `VideoToolSpec` contains `name`, `description`, JSON-schema-compatible input model, `cost_level`, `confirmation_required`, `idempotency_mode`, and `recovery_mode`. Define `VideoToolValidationError(ValueError)` for user-correctable missing or invalid input and map it to a structured tool result rather than an unhandled runtime failure. `InspectVideoWorkspaceTool` returns a compact evidence summary and artifact refs, never raw provider credentials or full hidden payloads.

- [ ] **Step 4: Run registry tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_tool_registry.py -v`

Expected: PASS; unknown tools and invalid arguments are rejected before execution.

- [ ] **Step 5: Commit the catalog and registry**

```bash
git add backend/pixelflow/video_agent/skills backend/pixelflow/video_agent/tools backend/tests/test_video_agent_tool_registry.py
git commit -m "feat: add video agent tool registry"
```

## Task 6: Implement DeepSeek Agent Planning and Bounded Tool Loop

**Files:**
- Create: `backend/pixelflow/video_agent/planner/{model.py,loop.py,__init__.py}`
- Create: `backend/pixelflow/video_agent/executor/service.py`
- Create: `backend/tests/test_video_agent_planner.py`
- Create: `backend/tests/test_video_agent_executor.py`

**Interfaces:**
- Produces `VideoAgentPlanner.plan_turn(context) -> AgentPlan`, `VideoAgentExecutor.run_plan(user_id, plan_id) -> AgentPlan`, `confirm_step(user_id, plan_id, step_id) -> AgentPlan`, and `resume_plan(user_id, plan_id) -> AgentPlan`.
- Consumes `VideoToolRegistry`, `VideoWorkspaceRepository`, and the existing `create_chat_model(name="deepseek-v4-pro")` factory.

- [ ] **Step 1: Write failing planner tests with a fake structured model**

```python
async def test_planner_turn_for_reference_video_starts_with_analysis(fake_model, executor):
    plan = await executor.plan_turn(user_id="u1", content="参考这个视频，换成我的商品", materials=[reference])
    assert [step.tool_name for step in plan.steps][:2] == [
        "inspect_video_workspace", "analyze_reference_video"
    ]
```

- [ ] **Step 2: Write failing loop stop-condition tests**

```python
async def test_executor_stops_before_billable_tool_until_confirmation(executor):
    plan = await executor.run_plan("u1", "plan-1")
    assert plan.status is AgentPlanStatus.AWAITING_CONFIRMATION
    assert plan.steps[-1].tool_name == "generate_scenes"
```

- [ ] **Step 3: Implement a typed model boundary**

Use `with_structured_output` for a plan proposal schema. The proposal may contain only registered tool names. Limit one turn to eight tool calls and two model repair attempts. Tool output is appended as a typed, compact result record before the next model call. Do not persist hidden reasoning; persist the plan and public tool summaries only.

- [ ] **Step 4: Implement plan execution and confirmation gate**

For each step, persist `running`, publish the start event, execute one tool, persist/publish terminal result, then continue. Stop and open a confirmation request before any tool whose spec requires confirmation. `confirm_step` records a valid approval against the persisted step and re-enters the plan; `resume_plan` recovers a persisted plan after reconnect/restart and reclaims only eligible work through existing leases and idempotency keys.

- [ ] **Step 5: Run planner and executor tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_planner.py tests/test_video_agent_executor.py -v`

Expected: PASS; no unknown tool is called, no billable tool executes before confirmation, and plan resume starts at the pending step.

- [ ] **Step 6: Commit the agent loop**

```bash
git add backend/pixelflow/video_agent/planner backend/pixelflow/video_agent/executor backend/tests/test_video_agent_planner.py backend/tests/test_video_agent_executor.py
git commit -m "feat: add deepseek video agent loop"
```

## Task 7: Adapt Script, Creative, and Reference Analysis Tools

**Files:**
- Create: `backend/pixelflow/video_agent/tools/{script.py,reference.py}`
- Create: `backend/pixelflow/video_agent/adapters/video_domain.py`
- Create: `backend/tests/test_video_agent_script_tools.py`
- Create: `backend/tests/test_video_agent_reference_tools.py`

**Interfaces:**
- Produces tools `import_script`, `brainstorm_script`, and `analyze_reference_video`.
- Consumes existing `creative/plan_markdown.py`, `creative/brief_generate.py`, `nodes._parse_reference_videos` behavior, and registered decompose skills through an adapter.

- [ ] **Step 1: Write failing mature-script tests**

```python
async def test_import_script_creates_script_artifact_without_plan_review(tool_context):
    result = await ImportScriptTool().execute(tool_context, {"markdown": MATURE_SCRIPT})
    assert result.workspace_patch["script"]["source"] == "user_import"
    assert result.requires_confirmation is False
```

- [ ] **Step 2: Write failing reference-analysis tests**

```python
async def test_reference_analysis_persists_scenes_and_assets(tool_context, fake_decompose_skill):
    result = await AnalyzeReferenceVideoTool().execute(tool_context, {"reference_asset_ref": "artifact:ref-1"})
    assert result.workspace_patch["reference_videos"][0]["storyboard"][0]["scene_id"]
```

- [ ] **Step 3: Implement adapters without importing V1 handlers**

`ImportScriptTool` normalizes a user script into the workspace script artifact and returns missing requirements as a public summary. `BrainstormScriptTool` creates a versioned draft only. `AnalyzeReferenceVideoTool` starts/reuses a durable operation through the existing coordinator, then persists normalized storyboard and asset evidence when complete. It must call only V2 adapters, never the retired V1 handler or Plan-review interrupt.

- [ ] **Step 4: Run script and reference tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_script_tools.py tests/test_video_agent_reference_tools.py tests/test_reference_video_nodes.py -v`

Expected: PASS; imported scripts do not enter a Plan review, and repeated analysis reuses the same operation.

- [ ] **Step 5: Commit first user journeys**

```bash
git add backend/pixelflow/video_agent/tools/script.py backend/pixelflow/video_agent/tools/reference.py backend/pixelflow/video_agent/adapters/video_domain.py backend/tests/test_video_agent_script_tools.py backend/tests/test_video_agent_reference_tools.py
git commit -m "feat: add script and reference video tools"
```

## Task 8: Adapt Asset Replacement, Scene Inspection, and Scoped Generation Tools

**Files:**
- Create: `backend/pixelflow/video_agent/tools/scene.py`
- Modify: `backend/pixelflow/qc/{video_review.py,revision_scope.py}`
- Create: `backend/tests/test_video_agent_scene_tools.py`

**Interfaces:**
- Produces `replace_project_assets`, `inspect_scene`, `patch_scene`, `generate_scenes`, and `review_generated_scenes`.
- `generate_scenes` accepts `{scene_ids: list[str], variant_count: int}` and always requires confirmation when it creates billable operations.

- [ ] **Step 1: Write failing scene inspection and scoped generation tests**

```python
async def test_inspect_scene_returns_repairable_evidence(tool_context):
    result = await InspectSceneTool().execute(tool_context, {"scene_id": "scene-3"})
    assert result.workspace_patch["qc"]["scene-3"]["repair_suggestion"]

async def test_generate_scenes_requires_confirmation_and_scopes_ids(tool_context):
    result = await GenerateScenesTool().execute(tool_context, {"scene_ids": ["scene-3"], "variant_count": 3})
    assert result.requires_confirmation is True
    assert result.preview["scene_ids"] == ["scene-3"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_scene_tools.py -v`

Expected: FAIL because V2 scene tools and scene-level evidence contracts do not exist.

- [ ] **Step 3: Implement scene evidence and patch flow**

Normalize VLM/QC output to `{scene_id, issues, evidence_refs, repair_suggestion, affected_assets}`. `PatchSceneTool` changes only declared mutable scene fields and writes a new workspace revision. `GenerateScenesTool` validates IDs against the workspace, creates one operation per scene/variant after confirmation, and records job IDs in the corresponding plan steps. `ReviewGeneratedScenesTool` selects or rejects variants without silently changing unrelated scenes. A scene-package initiated edit preserves all unaffected scene cards and prior variants, creates a plan scoped to the selected `scene_id`, and, once the replacement video is ready, records the new variant plus a public `重新生成完成` marker and completion timestamp.

- [ ] **Step 4: Run focused regression suites**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_scene_tools.py tests/test_video_quality_review.py -v`

Expected: PASS; V2 scoping works and no retired V1 video module is imported.

- [ ] **Step 5: Commit scene tools**

```bash
git add backend/pixelflow/video_agent/tools/scene.py backend/pixelflow/qc/video_review.py backend/pixelflow/qc/revision_scope.py backend/tests/test_video_agent_scene_tools.py
git commit -m "feat: add video agent scene tools"
```

## Task 9: Adapt Composition and Export Tools

**Files:**
- Create: `backend/pixelflow/video_agent/tools/delivery.py`
- Create: `backend/tests/test_video_agent_delivery_tools.py`

**Interfaces:**
- Produces `compose_or_export_video` with `output_type` values `mp4` and `jianying_package`.
- Consumes existing delivery and Jianying skills through the V2 adapter.

- [ ] **Step 1: Write failing delivery tests**

```python
async def test_export_rejects_workspace_with_unresolved_dirty_scenes(tool_context):
    with pytest.raises(VideoToolValidationError, match="dirty_scene_ids"):
        await ComposeOrExportVideoTool().execute(tool_context, {"output_type": "mp4"})
```

- [ ] **Step 2: Implement delivery validation and operations**

Require all selected scenes to have an approved variant and no unresolved QC/dirty state. Use the existing composition/Jianying services behind the adapter. Mark MP4/Jianying creation as billable/confirmation-gated when provider or storage cost is incurred, and persist resulting artifact refs to the workspace.

- [ ] **Step 3: Run delivery tests**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_delivery_tools.py -v`

Expected: PASS; V2 does not export an inconsistent project and no V1 delivery path remains.

- [ ] **Step 4: Commit delivery tools**

```bash
git add backend/pixelflow/video_agent/tools/delivery.py backend/tests/test_video_agent_delivery_tools.py
git commit -m "feat: add video agent delivery tool"
```

## Task 10: Render V2 Workspace, Plan Timeline, and Confirmation Cards

**Files:**
- Create: `web/src/features/video-agent/{VideoAgentWorkspace.tsx,AgentPlanTimeline.tsx,AgentConfirmationCard.tsx,SceneEvidencePanel.tsx}`
- Create: `web/src/features/video-agent/hooks/useVideoAgent.ts`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Create: `web/tests/videoAgentWorkspace.test.mjs`

**Interfaces:**
- Produces `VideoAgentWorkspace` that consumes `VideoAgentTimelineState` and V2 SSE snapshots.
- Produces a visible step row with title, status, artifact links, start/end time, and duration.

- [ ] **Step 1: Write failing UI contract tests**

```js
assert.match(read("src/features/video-agent/AgentPlanTimeline.tsx"), /durationMs/);
assert.match(read("src/features/video-agent/AgentPlanTimeline.tsx"), /正在/);
assert.match(read("src/pages/WorkspacePage.tsx"), /VideoAgentWorkspace/);
```

- [ ] **Step 2: Implement the timeline and elapsed-time renderer**

Render pending, running, waiting-confirmation, completed, failed, and skipped states with icons and concise copy. For running steps, use a one-second timer only in `AgentPlanTimeline`; calculate `Date.now() - startedAt`. For terminal steps, render the persisted `durationMs`. Never render prompts, raw tool payloads, or hidden reasoning.

- [ ] **Step 3: Implement project evidence and confirmation UI**

`SceneEvidencePanel` displays selected scene media, QC issues, repair suggestion, and related artifact links. `AgentConfirmationCard` displays the public cost summary, affected scenes, and explicit confirm/cancel controls. Its submit action sends the persisted plan-step confirmation ID, not a free-form workflow action. `VideoAgentWorkspace` keeps the existing video scene package as the primary scene surface: every scene card exposes a single-scene edit action, retains historical/generated variants, and displays `重新生成完成` with the replacement version and timestamp when the scoped regeneration completes.

- [ ] **Step 4: Render V2 from the thin page shell**

Keep only the V2 feature import and layout in `WorkspacePage.tsx`; no mode selection or legacy fallback remains:

```tsx
return <VideoAgentWorkspace />;
```

- [ ] **Step 5: Run frontend tests and build**

Run: `cd web && node --test tests/videoAgentWorkspace.test.mjs tests/videoAgentTimelineReducer.test.mjs && npm run lint && npm run build-dev`

Expected: PASS; V2 UI renders a duration for terminal steps and a live elapsed value for running steps.

- [ ] **Step 6: Commit V2 frontend**

```bash
git add web/src/features/video-agent web/src/pages/WorkspacePage.tsx web/tests/videoAgentWorkspace.test.mjs
git commit -m "feat: add video agent workspace"
```

## Task 11: Make V2 the Only Video Entry and Retire V1

**Files:**
- Modify: `backend/pixelflow/agent_runtime/{config.py,service.py,executor.py}`
- Create: `backend/pixelflow/agent_runtime/video_router.py`
- Delete: `backend/pixelflow/agent_workflows/video/`
- Delete: the V1 video decision/action modules under `backend/pixelflow/agent_runtime/supervisor/`
- Delete: V1 video workflow tests under `backend/tests/test_agent_video_workflow_*.py` and superseded Supervisor-routing tests
- Create: `backend/tests/{test_video_agent_entry.py,test_video_agent_e2e.py,test_video_agent_retirement.py}`

**Interfaces:**
- Produces one active entry, `VideoAgentEntrypoint.submit_turn`, for every video conversation.
- Produces `video_workflow_retired` for a historical V1 workflow ID; the caller can inspect its records but cannot resume or mutate it.

- [ ] **Step 1: Write failing entry, retirement, and recovery tests**

```python
def test_every_new_video_turn_uses_video_agent_entrypoint(app):
    assert app.video_router.resolve("video") is app.video_agent_entrypoint

async def test_historical_v1_workflow_is_read_only(runtime):
    result = await runtime.resume_workflow("old-v1-workflow")
    assert result.code == "video_workflow_retired"

async def test_reference_remix_resumes_after_generation_operation_restart(runtime):
    plan = await runtime.submit("u1", "参考这个视频，把商品换成我的", [reference, product])
    await runtime.confirm_step("u1", plan.plan_id, plan.pending_confirmation_step_id)
    restored = await runtime.resume_plan("u1", plan.plan_id)
    assert restored.steps[-1].status in {PlanStepStatus.RUNNING, PlanStepStatus.COMPLETED}
```

- [ ] **Step 2: Replace V1 routing with the single video entrypoint**

Remove `SUPERVISOR_V1`, `VIDEO_AGENT_V2`, rollout flags, and mode-selection branches. `VideoAgentEntrypoint` resolves every video turn to `VideoAgentExecutor`; non-video routing remains outside this change. Preserve the existing generic runtime operation coordinator, event outbox, quota checks, and ownership checks.

- [ ] **Step 3: Implement historical V1 read-only retirement**

Add a retirement lookup that recognizes existing V1 workflow rows and returns a stable public `video_workflow_retired` result containing only workflow ID, creation time, and historical artifact links. Do not migrate the V1 state payload into V2, restart any V1 job, or issue new provider calls. Existing database rows remain for audit and can be removed later by an explicit data-retention job.

- [ ] **Step 4: Delete V1 implementation and tests**

Remove `backend/pixelflow/agent_workflows/video/`, the V1 video Supervisor action/decision path, its HTTP handlers, and all tests that assert V1 video workflow stages. Remove the legacy Workspace feature and client Supervisor mode reducer. Update imports so reusable planning, scene generation, QC, composition, and Jianying services are reachable only through V2 adapters.

- [ ] **Step 5: Implement V2 snapshot/SSE restoration**

Expose current workspace, active plan, plan steps, open confirmation, and event cursor in the V2 conversation snapshot. Rehydrate frontend state from snapshot before applying live SSE events. Resume pending operations through the existing coordinator and write a new step-progress event instead of recreating the plan.

- [ ] **Step 6: Run focused retirement and full verification**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_entry.py tests/test_video_agent_retirement.py tests/test_video_agent_e2e.py tests/test_agent_runtime_operation_recovery.py tests/test_agent_runtime_event_outbox.py -v`

Run: `cd web && node --test tests/videoAgentWorkspaceShell.test.mjs tests/videoAgentWorkspace.test.mjs && npm run lint`

Expected: PASS; every video turn takes the V2 entry, retired V1 work cannot execute, and V2 can recover without duplicate billable operations.

- [ ] **Step 7: Run the golden-case evaluation suite**

Create 30-50 fixture-driven cases covering mature scripts, creative ideas, reference remix, scene repair, ambiguous targets, quota confirmation, duplicate submit, and restart recovery. Record expected first tool, confirmation boundary, scoped scene IDs, and terminal outcome. Fail the suite when the DeepSeek planner selects an unregistered tool or starts a billable step before confirmation.

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_evaluation.py -v`

Expected: PASS with the recorded baseline before deleting V1 production paths.

- [ ] **Step 8: Commit V1 retirement and V2 entry**

```bash
git add -A backend/pixelflow/agent_runtime backend/pixelflow/agent_workflows backend/tests web/src/features web/src/lib/supervisor web/tests
git commit -m "refactor: retire v1 video workflow"
```

## Business Milestone Schedule

**Assumption:** One full-time engineer, existing video-generation/QC/composition services remain reusable, no provider API change blocks integration, and P0 is evaluated as an internal usable build rather than a production release. Day 1 is the next implementation workday.

### P0: Five-Day Unified Agent Build

| Day | Tasks advanced | User-visible result and acceptance |
| --- | --- | --- |
| Day 1 | 2, 3, 4, 5 | One VideoAgent entry replaces the video Supervisor path. It creates a persistent workspace and visible plan steps; every running/completed step has elapsed duration. The tool registry initially exposes only safe read/analysis actions. |
| Day 2 | 6, 7 | The same chat accepts a mature script, an idea, or a reference video. It chooses `import_script`, `brainstorm_script`, or `analyze_reference_video`, creates a concise public plan, and persists its script/storyboard/asset result. |
| Day 3 | 7, 8 | A user can provide product/person/background assets and ask for replacement. The agent identifies affected scenes, shows scope and cost, waits for confirmation, then submits 3 variants per affected scene through the existing generation service. |
| Day 4 | 8, 9 | A user can say “检查第 3 镜并重做” or initiate an edit from that scene's video scene package card. The agent returns visual/QC evidence and a repair suggestion, regenerates only that scene after confirmation, retains its prior variants, marks the card `重新生成完成` when ready, lets the user select a variant, and exports an MP4. Jianying export is enabled only if the existing adapter passes its smoke test. |
| Day 5 | 1, 10, 11 (entry switch only) | `WorkspacePage.tsx` is a V2 shell and the V2 workbench is the only normal video entry. Verify 10 core cases: three starts, asset replacement, cost confirmation, variant generation, scene inspection, local repair, reconnect, duplicate submit, and MP4 export. |

**P0 done means:** a user can complete all three starting journeys in one agent conversation, see public step progress and duration, and finish a reference remake or local scene repair without entering a V1 stage workflow. P0 deliberately defers full source deletion, 30-50 golden cases, deep schema normalization, and Jianying compatibility work when it is not already passing.

### P1: Hardening and Physical V1 Retirement (5-7 Working Days After P0)

| Work | Tasks completed | Business outcome |
| --- | --- | --- |
| Expand persistence and event coverage | 2-4 | V2 workspace/plan schema is complete, reconnect/restart behavior is deterministic, and production migration rehearsal has passed. |
| Complete tools and delivery | 7-10 | Jianying export, fuller asset packages, richer evidence display, and all planned variant/review cases are production-ready. |
| Remove V1 | 1, 11 | Delete V1 workflow/Supervisor/UI implementation, archive historical V1 records as read-only, and eliminate V1 imports. |
| Production confidence | Final verification | Pass 30-50 golden cases, quota/duplicate/restart tests, migration backup rehearsal, and release review. |

**Task-to-business mapping:** Tasks 1-11 retain their technical definitions above. P0 prioritizes the smallest cross-cutting portion of each task that enables a real unified-agent journey; P1 completes all deferred cleanup and production hardening before the V1 code is physically removed.

## Final Verification

- [ ] Run `cd backend && PYTHONPATH=. uv run pytest tests/test_video_agent_*.py -v`.
- [ ] Run `cd backend && PYTHONPATH=. uv run pytest tests/test_agent_runtime_* -v`.
- [ ] Run `cd web && npm test && npm run lint && npm run build-dev`.
- [ ] Confirm `wc -l web/src/pages/WorkspacePage.tsx` is between 100 and 200 with only the V2 feature shell.
- [ ] Confirm `git diff --check` is clean and `rg -n 'agent_workflows.video|SUPERVISOR_V1|VIDEO_AGENT_V2|LegacyWorkspace' backend web` has no production-code matches.
