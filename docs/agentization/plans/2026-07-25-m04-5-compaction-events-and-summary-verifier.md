# M04.5 压缩事件与 SummaryVerifier 实施计划

> **执行约束：** 本计划只覆盖 M04.5。完成 TDD、测试、独立审核、M04 Final 门禁、状态记录、一个独立提交和推送后必须停止，不得启动 M05 或单槽集成。

**目标：** 在 M04.1–M04.4 的结构化摘要、四阈值 Coordinator 和可恢复队列之上，增加 `SummaryVerifier` 的关键事实 fail-closed 闸门，并把压缩开始、阶段进度、成功和可恢复失败按冻结 `AgentEvent` 合同写入 M01 Event Outbox。

**架构：** `SummaryBuilder` 相当于摘要 Service，`SummaryVerifier` 是保存前的 Validator：调用方显式提供本轮仍必须保留的目标、决定、否定约束、Workflow 状态、未决问题、Artifact 引用和稳定 ID，Verifier 使用精确匹配验证候选摘要并复算内容 hash，任何缺失都不返回可保存结果。`ConversationCompactionRuntime` 相当于工作流编排 Service，通过独立 `CompactionEventSink` 在取得租约后写 started、每个 Coordinator 动作后写 progress、成功/暂停/异常收尾写 completed/failed；Repository Adapter 只组合 M01 `list_events/create_event`，事件 payload 仅含状态、动作、序号、公开文案和安全 reason code。

**技术栈：** Python 3.12、Pydantic v2、asyncio、SQLAlchemy async、pytest、ruff、Windows PowerShell 5.1/Pester 3.4。

## 全局约束

- 关键事实采用精确、可审计的 100% 子集校验，不使用模糊语义相似度；已经解决或变更的事实由调用方从本轮验证基线中显式移除。
- `SummaryBuilder` 必须在返回候选前执行 Verifier；Verifier 失败不得产生 Repository 写入或继续模型调用。
- `content_hash` 必须按结构化语义、消息 ID 和覆盖范围重新计算，防止摘要内容被替换后沿用旧 hash。
- started/progressed/completed/failed 必须先进入 M01 Outbox，SSE 不在本切片实现；同一 conversation 的 sequence 继续单调，遇到并发追加冲突时重新读取尾部并有限重试，耗尽后 fail-closed。
- 事件 payload 不包含摘要正文、token 数、内部 prompt、用户原文、Authorization、API key、异常字符串、完整 URL 或思维链。
- `already_running` 不重复写 started；暂停和异常都写可恢复 failed 事件并保留队列，前端不重发输入。
- 原始消息、Plan、创作合同、场景蓝图、资产清单、pending action、pending job 和 operation 不删除、不改写。
- 本切片不新增配置、不调用真实 LLM 或付费供应商，不修改两个长期 feature 分支、`status/BOARD.md` 或 content-app 合同。

---

### 任务一：用失败测试冻结关键事实验证

**文件：**

- 新增：`backend/tests/test_agent_runtime_summary_verification.py`
- 新增：`backend/pixelflow/agent_runtime/context/verification.py`
- 修改：`backend/tests/test_agent_runtime_summary_builder.py`
- 修改：`backend/pixelflow/agent_runtime/context/compaction.py`
- 修改：`backend/pixelflow/agent_runtime/context/__init__.py`

- [x] 编写精确保留目标、决定、否定约束、Workflow 状态、未决问题、Artifact 引用和稳定 ID 的通过用例。
- [x] 为每类事实分别删除或改写一项，断言 `SummaryVerificationError` 且报告安全 reason code，不回显摘要正文。
- [x] 篡改摘要语义但保留旧 `content_hash`，断言 hash 验证失败。
- [x] 让 `SummaryBuilder` 生成缺失关键事实的候选，断言 build fail-closed；合法候选仍保持旧摘要加连续新消息的增量语义。
- [x] 运行新测试，确认 Verifier 模块、验证基线和 Builder 闸门不存在而失败。
- [x] 最小实现不可变验证 DTO、公开 hash 计算函数和 `SummaryVerifier`，并接入 `SummaryBuilder` 返回边界。

### 任务二：用失败测试冻结四类 Outbox 事件

**文件：**

- 新增：`backend/tests/test_agent_runtime_compaction_events.py`
- 新增：`backend/pixelflow/agent_runtime/context/compaction_events.py`
- 修改：`backend/tests/test_agent_runtime_compaction_queue.py`
- 修改：`backend/tests/test_agent_runtime_compaction_coordinator.py`
- 修改：`backend/pixelflow/agent_runtime/context/compaction.py`
- 修改：`backend/pixelflow/agent_runtime/context/__init__.py`

- [x] 对 Memory/SQL M01 Repository 编写事件 Adapter 合同测试，断言 `AgentEvent` sequence/cursor 连续、run/conversation/owner 正确且 payload 安全。
- [x] 编写阻塞 Coordinator 测试，证明 started 已持久化后才进入压缩；每个已完成动作产生 progressed，完成产生 completed。
- [x] 编写暂停、异常和 started/progress 持久化失败用例，断言 failed 或 fail-closed、队列保留且不领取下一 Turn。
- [x] 编写 `already_running` 用例，断言第二个 worker 不重复写 started；并发外部事件抢占 sequence 时 Adapter 重新读取尾部后成功追加。
- [x] 运行新测试，确认事件 Adapter、Coordinator progress observer 和 Runtime 事件编排不存在而失败。
- [x] 最小实现 `CompactionEventSink`、M01 Repository Adapter、安全 payload 和 Coordinator progress observer，并把四类事件接入 Runtime 生命周期。

### 任务三：建立并执行 M04 Final 权威门禁

**文件：**

- 修改：`scripts/agentization/tests/BranchAutomation.Tests.ps1`
- 修改：`scripts/agentization/Invoke-AgentModuleGate.ps1`
- 新增：`docs/agentization/test-reports/M04.5.md`
- 修改：`docs/agentization/status/M04-status.md`
- 修改：`docs/pixelflow-agent-skill-flow-latest-design.md`

- [x] 先修改 Pester 测试，要求 M04 Final 只运行 M01/M03/M04 Agent Runtime、DeerFlow summarization/dynamic context 和 Harness 边界权威清单；确认旧脚本仍因 M04 未配置而失败。
- [x] 最小补齐 `Invoke-AgentModuleGate.ps1` 的 M04 pytest/ruff 清单，并把 M04 从“未配置必须失败”列表移除。
- [x] 运行 M04.5 定向、全部 `test_agent_runtime_*`、DeerFlow summarization/dynamic context、Harness、ruff、format 和 `git diff --check`。
- [x] 启动独立只读 reviewer，检查事实保护、Outbox 顺序/恢复、安全 payload、异常路径、M04.4 队列不回归和 Final 门禁边界。
- [x] 处理全部 Critical/Important 意见并重新验证；使用 `Invoke-AgentModuleGate.ps1 -ModuleId M04 -GateType Final` 执行完整模块门禁。
- [x] 用中文同步最新设计、测试报告和 M04 状态，勾选 M04.5，写 `ready_for_integration`、检查点 commit 待提交说明、释放写入者；不更新 BOARD。
- [x] 执行中文工程规范与分支策略检查，创建一个中文独立 commit，推送 `codex/agent-0.8.4-m04-context-compaction`，核对远端 SHA 后停止并提示开发者复制 9.10A 话术。
