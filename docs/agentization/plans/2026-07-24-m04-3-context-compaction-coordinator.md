# M04.3 四阈值上下文压缩 Coordinator 实施计划

> **执行约束：** 本计划只覆盖 M04.3。完成 TDD、测试、独立审核、状态记录、一个独立提交和推送后必须停止，不得进入 M04.4。

**目标：** 新增全局 `ContextCompactionCoordinator`，统一执行 60/72/85/92 四级策略，精确计算严格低于 45% 的成功目标，对摘要输入做稳定分块，并在 85% 以上执行 workflow 摘要到会话摘要的层级压缩；92% 硬闸门失败时只允许最小安全上下文或暂停，不能放行原上下文。

**架构：** Coordinator 相当于工作流编排 Service，只持有阈值、动作顺序、分块、重计量和硬闸门规则。`CompactionStageExecutor` 相当于策略 Service/Client：后续组合层把 60% 动作接到 M03 载荷外置，把 72% 动作接到 M04.2 `SummaryBuilder`，把 85% 动作接到层级摘要实现。本切片使用 fake Executor 做非付费 TDD，不提前实现 M04.4 的 conversation 锁/输入队列，也不提前实现 M04.5 的 Outbox 事件和 `SummaryVerifier`。

**技术栈：** Python 3.12、Pydantic v2、M03 `TokenMeter/ContextBudgetReport`、pytest、ruff。

## 方案选择

1. 把四阈值继续堆入 `ContextAssembler`：可以少建一个类，但会让每个业务节点自行控制摘要动作，无法统一保证 45% 目标和 92% 硬闸门，因此不采用。
2. Coordinator 只返回动作计划，不执行和重计量：接口最小，但调用方可能漏执行层级压缩或在硬闸门失败后继续调用模型，不能形成安全边界，因此不采用。
3. Coordinator 顺序调用注入的 Stage Executor，每次都用 M03 `TokenMeter` 重新计量；摘要段按 M03 已验证/保守模型档案和 `summary` 节点策略计算实际可用预算并稳定分块，92% 任一执行或分块规划失败都转入最小安全上下文，仍不安全则明确暂停。该方案保留适配器扩展点，又能在本切片内验证完整安全状态机，因此采用。

## 全局约束

- 阈值只能复用 M03 `TokenMeter`，业务代码不得复制第二套百分比判断。
- 45% 是严格小于目标：`usable_input=100` 时最大成功 token 为 44，不把恰好 45% 误报为成功。
- 60% 起先外置大型 tool/artifact；72% 起处理增量消息摘要；85% 起把 workflow 摘要分层汇总到会话摘要；92% 增加同步硬闸门。
- 所有摘要分块保持来源顺序，每块不得超过 M03 模型档案解析和 `summary` 节点策略共同算出的实际可用输入预算；缺失或过期档案自动使用至多 128K 保守档案。调用方不能提交裸分块上限，单段本身超窗时 fail-closed，不尝试直接调用摘要模型。
- Coordinator 请求不接收 Plan、创作合同、资产清单、pending action 或 operation；原消息和权威业务对象不在本切片的可变状态中。
- Stage 结果不得增加 token；92% 下 Stage 异常、非法结果或目标未达成时必须尝试最小安全上下文，仍无法证明安全则返回暂停且禁止模型调用。
- 不修改数据库、migration、配置、两个长期 feature 分支、`status/BOARD.md` 或 content-app API 文档。

---

### 任务一：用失败测试冻结四阈值与严格 45% 目标

**文件：**

- 新增：`backend/tests/test_agent_runtime_compaction_coordinator.py`
- 修改：`backend/pixelflow/agent_runtime/context/token_meter.py`
- 修改：`backend/pixelflow/agent_runtime/context/compaction.py`

- [ ] 为 59/60、71/72、84/85、91/92 编写表驱动用例，断言动作层级由统一预算报告决定。
- [ ] 编写“45% 不算成功、44% 才算成功”用例。
- [ ] 编写伪造 `compaction_level`、Stage token 增长和非法结果的 fail-closed 用例。
- [ ] 运行新测试，确认因 Coordinator 合同尚不存在而在收集阶段失败。
- [ ] 为 `TokenMeter` 增加基于既有预算报告的统一重计量入口，实现最小 Coordinator 状态机。

### 任务二：用失败测试冻结分块、层级压缩和硬闸门

**文件：**

- 修改：`backend/tests/test_agent_runtime_compaction_coordinator.py`
- 修改：`backend/pixelflow/agent_runtime/context/compaction.py`
- 修改：`backend/pixelflow/agent_runtime/context/__init__.py`

- [ ] 编写总输入超过摘要窗口但单段可装入时的稳定分块测试，断言顺序、块大小和无重复。
- [ ] 编写单段超过摘要窗口时不调用摘要 Stage 的测试，并覆盖缺失/过期档案的 128K 保守分块。
- [ ] 编写 85% workflow 分层汇总测试，断言消息增量批次先于 workflow 汇总批次。
- [ ] 编写 92% 正常硬压缩、执行异常/分块规划失败回退到最小安全上下文、最小上下文仍不安全则暂停的测试。
- [ ] 实现严格内部 DTO、贪心稳定分块、累进动作执行、每步重计量和安全结果合同。

### 任务三：回归、审核与交接

**文件：**

- 修改：`docs/agentization/status/M04-status.md`
- 新增：`docs/agentization/test-reports/M04.3.md`

- [ ] 运行 M04.3 新测试、M04.2/M04.1、M03 TokenMeter/外置/Assembler、冻结合同和 DeerFlow summarization/dynamic context 回归。
- [ ] 对变更 Python 路径运行 `ruff check`、`ruff format --check`，并运行 `git diff --check`。
- [ ] 启动独立只读 reviewer，检查阈值唯一来源、45% 严格边界、分块顺序、层级动作、92% fail-closed、业务对象隔离和 M04.4/M04.5 越界。
- [ ] 处理全部 Critical/Important 意见，重新运行本切片完整门禁。
- [ ] 用中文更新状态和测试报告，勾选 M04.3、把下一切片设为 M04.4，释放当前写入者；phase 保持 `in_progress`。
- [ ] 运行中文工程规范检查，创建一个中文独立 commit，推送 `codex/agent-0.8.4-m04-context-compaction` 并核对远端 SHA 后停止。
