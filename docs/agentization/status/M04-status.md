# M04 全流程上下文压缩 Runtime

- phase：`not_started`
- owner：A
- branch：计划 `codex/agent-0.8.4-m04-context-compaction`
- 依赖：M01、M03
- 当前切片：M04.1

## 切片

- [ ] M04.1 StructuredSummary/版本/证据引用（2h）
- [ ] M04.2 增量 SummaryBuilder（3h）
- [ ] M04.3 四阈值 Coordinator（2.5h）
- [ ] M04.4 压缩锁与输入队列（2h）
- [ ] M04.5 事件与 SummaryVerifier（2.5h）

## 恢复提示

业务合同永不摘要；原始消息永不删除。现有 DeerFlow middleware 是复用基础和安全网，不单独满足前端感知/排队需求。
