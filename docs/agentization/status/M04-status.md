# M04 全流程上下文压缩 Runtime

- phase：`ready_for_integration`
- owner：A
- base Agent SHA：`d20762935ad8bd994a24e332f4237da7a1aaf591`
- branch：`codex/agent-0.8.4-m04-context-compaction`
- 依赖：M01、M03
- 当前切片：`M04.5`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- 本切片开始时间：`2026-07-25T05:32:54+08:00`
- M04.1 完成时间：`2026-07-24T22:21:05+08:00`
- M04.2 完成时间：`2026-07-24T23:20:58+08:00`
- M04.3 完成时间：`2026-07-25T00:00:42+08:00`
- M04.4 完成时间：`2026-07-25T01:07:32+08:00`
- M04.5 完成时间：`2026-07-25T06:14:26+08:00`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m04-context-compaction`

## 切片

- [x] M04.1 StructuredSummary/版本/证据引用（2h）
- [x] M04.2 增量 SummaryBuilder（3h）
- [x] M04.3 四阈值 Coordinator（2.5h）
- [x] M04.4 压缩锁与输入队列（2h）
- [x] M04.5 事件与 SummaryVerifier（2.5h）

## M04.1 交付记录

- 产物：保持冻结 `ContextSummary` 字段不变，补齐版本前驱、非空唯一证据、连续消息前缀覆盖约束；新增不可变 `SummaryEvidenceSnapshot`、`SummaryEvidenceSource` 和组合 M01 双实现的 `StructuredSummaryRepository`。
- 版本与证据：首版必须为 v1，后续版本必须连续并指向同会话最新摘要；非空覆盖必须从 `sequence 1` 开始且只能累计扩展，既有消息 ID 前缀不得改写；消息 ID/sequence 和 Artifact 引用必须由同用户同会话权威快照证明。
- 安全边界：写入前复制调用方摘要并复制证据快照，任何版本、所有者或证据错误均 fail-closed；测试确认成功和失败路径都不修改来源摘要、原始消息或 Artifact 证据。
- 修改文件：`backend/pixelflow/agent_runtime/contracts/context.py`、`backend/pixelflow/agent_runtime/context/summaries.py`、`backend/pixelflow/agent_runtime/context/__init__.py`、`backend/tests/test_agent_runtime_structured_summaries.py`、两份既有测试合法夹具、M04.1 实施计划、测试报告和本状态文件。
- TDD 证据：schema 首轮为 `11 failed, 1 passed`；Repository 首轮因模块不存在而收集失败；审核补充的非前缀覆盖用例为 `3 failed`。逐项最小实现后 M04.1 得到 `29 passed, 1 warning`。
- 最后测试：M04.1 + M01 Repository + 冻结合同 + M03 ContextAssembler 为 `129 passed, 1 warning`；DeerFlow summarization/dynamic context 回归为 `38 passed, 1 warning`。warning 均来自既有 LangGraph pending deprecation。
- 静态检查：变更 Python 路径 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过；分支策略脚本确认正确 M04 分支、冻结基线、唯一 writer 和单 worktree。
- 独立审核：首轮 Critical 0、Important 1、Minor 1；按 TDD 修复中段覆盖并补充来源证据不变测试后，同一只读 reviewer 复审确认全部关闭，最终 Critical/Important/Minor 均为 0。
- 中文规范：新增/修改注释和 docstring 均为中文说明；本切片没有配置变更；中文工程规范脚本必须在中文独立提交后通过才允许 push。
- commit/push：本状态文件所在 M04.1 中文独立提交；提交级门禁通过后推送到 `origin/codex/agent-0.8.4-m04-context-compaction`，远端以该提交为准。
- 遗留问题：无 M04.1 硬阻塞。扩大套件确认既有 conversation CAS/Alembic 测试会污染同进程 logging，相关文件单独运行与本切片权威集合均通过；本切片未修改该链路。
- 阶段状态：M04.1 不是阶段检查点或模块最后一片，因此保持 `in_progress`，不运行 M04 Final 门禁，不更新 `status/BOARD.md`，不写任何 ready 状态。
- 下一切片第一动作：开发者手动启动 M04.2 后，恢复同一模块分支/worktree，确认 M04.1 远端提交并重新领取唯一 writer；先用失败测试固定“旧摘要 + 仅新增消息”的增量 SummaryBuilder，保持业务合同与上下文摘要分离。

## M04.2 交付记录

- 产物：新增严格 `SummarySourceMessage`、`SummarySemanticSnapshot`、`SummaryBuildRequest/Result`、`SummaryEngine`、增量 `SummaryBuilder` 和 `DeerFlowSummaryEngine`；Builder 自动生成连续版本、前驱、累计消息覆盖、UTC 时间和稳定 `sha256` 内容 hash。
- 增量语义：首版必须从 sequence 1 连续开始；后续只接收上一版覆盖终点之后的连续新消息，Engine 只看到上一版语义快照与本版新消息，不会重新读取全部历史。
- 业务隔离：输入合同拒绝额外 `business_context`，不接受 `WorkflowRecord` 或 `ContextEnvelope`；Plan、创作合同、资产清单、pending action 和 operation 不进入摘要 Builder。
- DeerFlow 复用：沿用现有 model、token counter、trim 和异步摘要实现，但使用独立 PixelFlow 结构化 prompt；不修改 DeerFlow 原 middleware/default prompt，也不复制其 token/裁剪/模型调用逻辑。
- 冻结与失败边界：调用方请求在 await 前深拷贝，count/summarize 分别获得独立副本；非法 sequence、跨会话、消息 ID 冲突、非法 token、非 JSON/非法 Engine 输出均 fail-closed，不产生持久化副作用。
- 修改文件：新增 `context/compaction.py` 和 M04.2 测试；更新 context 导出、本切片计划、测试报告和本状态文件。
- TDD 证据：初始因模块不存在而收集失败；最小实现后 `16 passed`；独立审核整改用例稳定得到 `2 failed, 16 passed`；专用结构化 prompt 和 Engine 边界深拷贝修复后最终 `18 passed`。
- 最后测试：M04.2 + M04.1/M01/M03 相邻集合为 `147 passed, 1 warning`；DeerFlow summarization/dynamic context 为 `38 passed, 1 warning`；Harness 边界为 `1 passed, 1 warning`。
- 静态检查：变更 Python 路径 `ruff check`、`ruff format --check` 和差异检查均通过；warning 仅来自既有 LangGraph pending deprecation。
- 独立审核：首轮 Critical 0、Important 1、Minor 1；两项均按 TDD 修复。复审最终 Critical/Important/Minor 均为 0，`Ready to commit: Yes`。
- 中文规范：新增/修改注释、docstring、计划、状态和测试记录均为中文主体说明；本切片没有配置变更。
- commit/push：本状态文件所在 M04.2 中文独立提交；提交级门禁通过后推送到 `origin/codex/agent-0.8.4-m04-context-compaction`，远端以该提交为准。
- 阶段状态：M04.2 不是阶段检查点或模块最后一片，因此保持 `in_progress`，不运行阶段/M04 Final 门禁，不更新 `status/BOARD.md`，不写 ready 状态，也不触发 9.10A。
- 下一切片第一动作：开发者手动启动 M04.3 后，恢复同一模块分支/worktree并领取唯一 writer；先用失败测试固定 59/60、71/72、84/85、91/92 边界、45% 回落目标和超大输入分块/层级压缩。

## M04.3 交付记录

- 产物：新增严格 `CompactionSegment/Batch/StageRequest/StageResult/Attempt`、`ContextCompactionRequest/Result`、`CompactionStageExecutor` 和 `ContextCompactionCoordinator`；为 M03 `TokenMeter` 增加复用既有预算报告的统一重计量入口。
- 四阈值：59/60、71/72、84/85、91/92 边界全部复用 M03 唯一阈值计算；60% 起外置大型载荷，72% 起增量摘要，85% 起 workflow 摘要到会话摘要的层级压缩，92% 增加同步硬闸门。
- 回落目标：使用整数公式保证成功结果严格低于 45%；`usable_input=100` 时 44 为成功，45 只记录 `target_not_reached`，不会误报。
- 摘要预算：调用方不能传裸分块上限；Coordinator 冻结摘要模型名与档案映射，通过 M03 档案解析、`TokenMeter` 和 `summary` 节点策略计算实际可用预算，缺失或过期档案按至多 128K 保守档案分块。
- 分块与层级：消息段和 workflow 摘要段按来源顺序稳定贪心分块，每块不超过摘要节点实际窗口；单段超窗 fail-closed，不会直接提交给摘要模型；85% 先处理消息增量，再做 workflow 层级汇总。
- 92% 安全边界：执行异常、非法返回、token 增长、目标未达成或分块规划失败均转为最小安全上下文；仍不能证明低于可用上限时返回 `paused` 并禁止模型调用，不会放行原上下文。
- 业务隔离：Coordinator DTO 只含预算、会话 ID 和可压缩段引用，拒绝 `business_context`；不写库、不删除/改写原始 SQL 消息，Plan、创作合同、场景蓝图、资产清单、pending action 和 operation 均保持在业务权威通道。
- TDD 证据：初始合同不存在时收集失败；最小实现后 `18 passed`；DTO 自审先得到 `6 failed, 18 passed` 再修复为 `24 passed`；独立审核整改先得到 `23 failed, 5 passed`，最终为 `28 passed, 1 warning`。
- 最后测试：M04.3 + M04.2/M04.1 + M03/M01 相邻集合为 `227 passed, 1 warning`；DeerFlow summarization/dynamic context 与 Harness 边界为 `39 passed, 1 warning`。warning 均来自既有 LangGraph pending deprecation。
- 静态检查：变更 Python 路径 `ruff check`、`ruff format --check` 和差异检查均通过；整个 `context` 目录探测出的既有 `profiles.py` formatter 差异未被本切片越界重排。
- 独立审核：首轮 Critical 0、Important 2、Minor 0；两项均按 TDD 修复。复审最终 Critical/Important/Minor 均为 0，结论“可以提交：是”。
- 中文规范：新增/修改注释、docstring、计划、状态和测试记录均为中文主体说明；本切片没有配置变更。
- commit/push：本状态文件所在 M04.3 中文独立提交；提交级门禁通过后推送到 `origin/codex/agent-0.8.4-m04-context-compaction`，远端以该提交为准。
- 阶段状态：M04.3 不是阶段检查点或模块最后一片，因此保持 `in_progress`，不运行阶段/M04 Final 门禁，不更新 `status/BOARD.md`，不写 ready 状态，也不触发 9.10A。
- 下一切片第一动作：开发者手动启动 M04.4 后，恢复同一模块分支/worktree并领取唯一 writer；先用并发失败测试固定 conversation 压缩锁、turn `queued/processing` 顺序迁移和失败恢复，确保输入不丢失、不由前端重发。

## M04.4 交付记录

- 产物：新增 `ConversationCompactionLease`、`CompactionQueueRepository`、Memory/SQL 双实现和 `ConversationCompactionRuntime`；新增 additive migration，将 conversation 压缩协调行纳入 Agent Runtime 表集合。
- 协调状态：SQL 为每个进入 Turn 路径的 conversation 建立永久协调行，严格使用 `idle | active | retry_required`；状态与租约字段组合由数据库 Check Constraint 和应用层共同 fail-closed。
- 稳定互斥：普通 `create_turn`、`enqueue_turn`、`claim_next_turn` 与压缩专用入口都先建立并锁定同一协调行；首次建行会核对 additive migration 前已有 Turn 的 owner，空 claim 不得抢占 conversation。
- 排队语义：取得租约时拒绝已有 `processing`，并把既有 `accepted` 原子迁移为 `queued`；`active/retry_required` 期间普通与专用输入都只能持久化为 `queued`，同一 client input 幂等返回原 Turn。
- 成功与恢复：成功收尾在同一短事务中校验 fencing token、切回 `idle`、清空租约字段，并只把最早待执行 Turn 迁移为 `processing`；异常或 `paused` 切为 `retry_required`，保留全部队列，后续 worker 用新随机 token 接管，陈旧 worker 不能收尾。
- 多进程边界：压缩调用期间不持有数据库事务或连接；两个独立 SQLite Engine 的竞态测试覆盖 acquire/enqueue 与 acquire/普通 claim，任一交错都不能形成 active 与 accepted/processing 同时成立。
- TDD 证据：首轮模块不存在时收集失败；最小实现为 `18 passed`；migration 冻结首轮 `2 failed, 82 passed`；通用领取绕过用例先为 `6 failed, 14 passed`；首轮独立审核对应回归先为 `2 failed, 20 deselected`；additive migration owner 漏洞用例先为 `1 failed, 25 deselected`，修复后全部转绿。
- 最后测试：全部 `test_agent_runtime_*.py` 为 `307 passed, 1 warning`；M04.4 与 Turn Inbox 为 `38 passed, 1 warning`；DeerFlow summarization、dynamic context 与 Harness 边界为 `39 passed, 1 warning`；warning 均来自既有 LangGraph pending deprecation。
- 静态检查：9 个变更 Python 路径的 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过；既有 Repository/Model 的 formatter 差异随本切片相关修改完成机械格式化，不改变额外业务语义。
- 独立审核：首轮 Critical 2、Important 1、Minor 0；逐项以失败测试整改。第二轮又发现并修复 additive migration 既有 Turn owner 抢占边界；同一只读 reviewer 最终确认 Critical/Important/Minor 均为 0，结论“是否可提交：是”。
- 中文规范：新增/修改注释、docstring、计划、状态和测试报告均为中文主体说明；本切片没有配置变更。
- commit/push：本状态文件所在 M04.4 中文独立提交；提交级门禁通过后推送到 `origin/codex/agent-0.8.4-m04-context-compaction`，远端以该提交为准。
- 阶段状态：M04.4 不是 `phased-rollout-plan.md` 明确检查点，也不是模块最后一片，因此保持 `in_progress`，不运行阶段/M04 Final 门禁，不更新 `status/BOARD.md`，不写 ready 状态，也不触发 9.10A。
- 下一切片第一动作：开发者手动启动 M04.5 后，恢复同一模块分支/worktree并领取唯一 writer；先用失败测试冻结压缩 started/progress/completed/failed Outbox 事件与 `SummaryVerifier`，再执行 M04 完整模块门禁。

## M04.5 交付记录

- 产物：新增 `SummaryVerificationBaseline`、`SummaryVerifier` 和稳定内容 hash 校验；`SummaryBuilder` 在返回候选前逐项验证用户目标、已确认决定、否定约束、Workflow 状态、未决问题、Artifact 证据与稳定 ID，缺失、篡改或跨会话均 fail-closed。
- 生命周期事件：取得租约后先写 `context.compression_started`，每个成功压缩动作写 `context.compression_progressed`；完成、暂停或异常分别写安全的 `completed/failed` Outbox，payload 不含摘要正文、用户原文、token、prompt、Authorization、密钥、异常字符串或完整 URL。
- 原子终态：Memory/SQL Repository 在同一临界区或数据库事务中校验 fencing token、追加终态 Outbox、释放或保留队列；EventSink 与 Queue 必须绑定同一个 Repository。陈旧 worker 只能留下已发生的 started/progress，不能写伪 completed/failed，也不能覆盖接管租约。
- 失败安全：started/progress 持久化失败、92% 硬闸门失败、Coordinator 异常或终态事件失败都不放行超窗上下文；暂停和失败保留 `retry_required` 与全部 queued Turn，不要求前端重发。
- TDD 证据：Verifier 首轮模块不存在而收集失败；Builder 接入先为 `1 failed`；稳定 ID 前缀碰撞先为 `1 failed`；Outbox 首轮模块不存在而收集失败。独立审核的陈旧 worker 用例稳定复现 `started → completed → failed` 后，原子收尾整改转绿。
- 最后测试：M04.5 定向回归为 `98 passed`；18 个 `test_agent_runtime_*` 为 `333 passed, 1 warning`；DeerFlow summarization、dynamic context 与 Harness 边界为 `39 passed, 1 warning`；BranchAutomation Pester 为 `36 passed, 0 failed`。
- Final 门禁：权威脚本把 Runtime 与 DeerFlow 边界拆为两个 pytest 进程，避免 Alembic `fileConfig` 污染同进程 logger，同时保持原日志断言；最终树执行 `Invoke-AgentModuleGate.ps1 -ModuleId M04 -GateType Final -ChinesePolicyBaseRef 45c4a5c12e5e873bb97e0ffea0707d68174f8b23`，结果 `Passed=True`、`CommandCount=5`。
- 静态检查：完整 Ruff 通过，9 个变更 Python 文件 `ruff format --check` 通过，`git diff --check` 通过；warning 仅来自既有 LangGraph pending deprecation。
- 独立审核：首轮 Critical 0、Important 2、Minor 0；按 TDD 修复陈旧 worker 伪终态，并补齐一致的完成材料。最终复审 Critical/Important/Minor 均为 0，结论“是否可提交：是”；reviewer 独立鲜跑 98/333/39 项 pytest、36 项 Pester、Ruff、format 和差异检查均通过。
- 中文规范：新增/修改注释、docstring、计划、状态和测试报告均使用中文主体说明；本切片没有新增或修改配置项。提交后门禁首次把 Python 列表解包的行首 `*` 误识别为人工注释，已改为行为等价的显式 `extend` 并复跑 31 项摘要测试，再由同一门禁验证。
- commit/push：本状态文件所在 M04.5 中文独立提交；提交级中文门禁通过后推送到 `origin/codex/agent-0.8.4-m04-context-compaction`，远端以该提交为准。
- 阶段状态：M04.5 是模块最后一片且不是阶段中间检查点；M04 Final 绿色后写 `ready_for_integration`，不更新 `status/BOARD.md`，不直接启动单槽集成。
- 集成前元数据规范化：最终集成任务将 `checkpoint_commit` 固定为 M04.5 远端实现提交，并把尚未集成的 `last_integrated_commit` 统一记为规范空值 `—`；本次只修正集成脚本可解析的状态元数据，不修改业务代码、测试清单或门禁结论。
- 下一步第一动作：当前自动化状态为 `automation_local_ready`。开发者新开一个 Codex 任务，复制执行手册 9.10A 话术，并在同一条消息中明确模块号 `M04`，手动启动唯一单槽最终集成；不得继续不存在的 M04.6。

## 恢复提示

业务合同永不摘要；原始消息永不删除。现有 DeerFlow middleware 是复用基础和安全网，不单独满足前端感知/排队需求。
- release_id：`R1`
- checkpoint_slice：`M04.5`
- checkpoint_commit：`5ab2f692cb525b6d59e539cc80d7696b99dda5c1`
- last_integrated_commit：`—`
- locked files：`无`
- checkpoint_status：`ready_for_integration`
- integration failure evidence：`无`
