# M04 全流程上下文压缩 Runtime

- phase：`in_progress`
- owner：A
- base Agent SHA：`d20762935ad8bd994a24e332f4237da7a1aaf591`
- branch：`codex/agent-0.8.4-m04-context-compaction`
- 依赖：M01、M03
- 当前切片：`M04.3`（未开始）
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：无；M04.2 写锁已释放
- 本切片开始时间：`2026-07-24T22:57:05+08:00`
- M04.1 完成时间：`2026-07-24T22:21:05+08:00`
- M04.2 完成时间：`2026-07-24T23:20:58+08:00`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m04-context-compaction`

## 切片

- [x] M04.1 StructuredSummary/版本/证据引用（2h）
- [x] M04.2 增量 SummaryBuilder（3h）
- [ ] M04.3 四阈值 Coordinator（2.5h）
- [ ] M04.4 压缩锁与输入队列（2h）
- [ ] M04.5 事件与 SummaryVerifier（2.5h）

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

## 恢复提示

业务合同永不摘要；原始消息永不删除。现有 DeerFlow middleware 是复用基础和安全网，不单独满足前端感知/排队需求。
